"""Online SQLite snapshots, authenticated streaming encryption and verified recovery."""
import hashlib,json,os,re,secrets,sqlite3,struct,tempfile,shutil,threading,logging
from datetime import datetime,timezone,timedelta
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from cryptography.exceptions import InvalidTag
from .storage import FileLock,atomic_write,read_json,write_json
MAGIC=b'NIKANBK1';CHUNK=1024*1024
TEHRAN=timezone(timedelta(hours=3,minutes=30))
NAME=re.compile(r'^nikan-\d{8}T\d{6}Z-[0-9a-f]{16}\.nikbak$')
REQUIRED={'companies','users','employees','skills','assessments','records','certificates','branding','personal_documents'}

def digest(path):
 h=hashlib.sha256()
 with Path(path).open('rb') as stream:
  for chunk in iter(lambda:stream.read(CHUNK),b''):h.update(chunk)
 return h.hexdigest()

def inspect_db(path):
 conn=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
 try:
  if conn.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError('یکپارچگی پایگاه داده تأیید نشد')
  if conn.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('ارتباط داده‌ها نامعتبر است')
  tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
  if not REQUIRED.issubset(tables):raise ValueError('این فایل پایگاه داده کامل نیکان نیست')
  return {name:conn.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0] for name in sorted(tables)}
 finally:conn.close()

def ensure_key(root):
 path=Path(root)/'keys'/'recovery.key'
 if not path.exists():atomic_write(path,secrets.token_bytes(32))
 key=path.read_bytes()
 if len(key)!=32:raise ValueError('کلید بازیابی باید ۳۲ بایت باشد')
 return key

def encrypt(snapshot,output,key):
 header=dict(version=1,created=datetime.now(timezone.utc).isoformat(),sha256=digest(snapshot),tables=inspect_db(snapshot),key_id=hashlib.sha256(key).hexdigest()[:16])
 raw=json.dumps(header,sort_keys=True,separators=(',',':')).encode();nonce=secrets.token_bytes(12)
 aad=MAGIC+struct.pack('!I',len(raw))+raw+nonce
 enc=Cipher(algorithms.AES(key),modes.GCM(nonce)).encryptor();enc.authenticate_additional_data(aad)
 with Path(snapshot).open('rb') as src,Path(output).open('xb') as dst:
  dst.write(aad)
  for chunk in iter(lambda:src.read(CHUNK),b''):dst.write(enc.update(chunk))
  dst.write(enc.finalize());dst.write(enc.tag);dst.flush();os.fsync(dst.fileno())
 return header

def decrypt(backup,output,key):
 output=Path(output)
 try:
  with Path(backup).open('rb') as src:
   if src.read(8)!=MAGIC:raise ValueError('قالب پشتیبان معتبر نیست')
   size=src.read(4)
   if len(size)!=4:raise ValueError('پشتیبان ناقص است')
   count=struct.unpack('!I',size)[0]
   if not 1<=count<=65536:raise ValueError('سرآیند پشتیبان معتبر نیست')
   raw=src.read(count);header=json.loads(raw);nonce=src.read(12);start=src.tell();src.seek(0,2);end=src.tell()-16
   if end<=start or header.get('version')!=1 or len(nonce)!=12:raise ValueError('پشتیبان ناقص یا ناسازگار است')
   src.seek(end);tag=src.read(16);src.seek(start)
   dec=Cipher(algorithms.AES(key),modes.GCM(nonce,tag)).decryptor();dec.authenticate_additional_data(MAGIC+size+raw+nonce)
   with output.open('xb') as dst:
    remaining=end-start
    while remaining:
     chunk=src.read(min(CHUNK,remaining))
     if not chunk:raise ValueError('پشتیبان ناقص است')
     remaining-=len(chunk);dst.write(dec.update(chunk))
    dst.write(dec.finalize());dst.flush();os.fsync(dst.fileno())
  output.chmod(0o600)
  if digest(output)!=header['sha256'] or inspect_db(output)!=header['tables']:raise ValueError('محتوای بازیابی با پشتیبان برابر نیست')
  return header
 except (InvalidTag,ValueError,KeyError,TypeError,json.JSONDecodeError,sqlite3.DatabaseError) as exc:
  output.unlink(missing_ok=True);raise ValueError('پشتیبان آسیب‌دیده، نامعتبر یا کلید بازیابی نادرست است') from exc
 except BaseException:output.unlink(missing_ok=True);raise

def recovery_check(backup,key,temp_root):
 with tempfile.TemporaryDirectory(prefix='recovery-test-',dir=temp_root) as folder:
  candidate=Path(folder)/'nikan.sqlite3';header=decrypt(backup,candidate,key)
  # Start the actual application's schema migrations against an isolated restored database.
  from app import create_app
  app=create_app(folder,testing=True)
  assert app.test_client().get('/api/status').status_code==200
  if inspect_db(candidate)!=header['tables']:raise ValueError('آزمون اجرای پایگاه بازیابی‌شده ناموفق بود')
  return dict(verified=True,checked_at=datetime.now(timezone.utc).isoformat(),sha256=header['sha256'],tables=header['tables'])

class BackupManager:
 def __init__(self,root):
  self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True);self.key=ensure_key(root);self.gate=threading.Lock();self.stop_event=threading.Event();self.thread=None;self.runtime_error=''
  self.settings_path=self.root/'backup-settings.json';self.state_path=self.root/'backup-status.json'
  if not self.settings_path.exists():write_json(self.settings_path,dict(enabled=True,time='02:00',retain=30,mirror='',timezone='Asia/Tehran'))
  self.local=self.root/'backups';self.local.mkdir(exist_ok=True)
  state=self.state()
  if state.get('running'):
   state.update(running=False,error='عملیات قبلی ناتمام مانده است؛ پشتیبان کامل قبلی حفظ شده است');write_json(self.state_path,state)
 def settings(self):return read_json(self.settings_path)
 def state(self):
  state=read_json(self.state_path,{})
  if self.runtime_error:state.update(error=self.runtime_error,running=self.gate.locked())
  return state
 def configure(self,data):
  if not isinstance(data,dict) or type(data.get('enabled')) is not bool or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d',str(data.get('time',''))) or type(data.get('retain')) is not int or not 2<=data['retain']<=365:raise ValueError('زمان و تعداد نسخه‌های پشتیبان معتبر نیست')
  mirror=str(data.get('mirror','')).strip()
  if mirror:
   path=Path(mirror)
   if not path.is_absolute() or path.resolve() in (self.root.resolve(),self.local.resolve()):raise ValueError('مسیر پشتیبان دوم باید پوشه‌ای مستقل و مطلق باشد')
   if not path.is_dir():raise ValueError('پوشه شبکه موجود نیست یا حساب سرور دسترسی ندارد')
   probe=path/('.nikan-probe-'+secrets.token_hex(8))
   try:
    with probe.open('xb') as f:f.write(b'permission check');f.flush();os.fsync(f.fileno())
   except OSError as exc:raise ValueError('حساب اجرای سرور اجازه نوشتن در پوشه شبکه ندارد') from exc
   finally:
    try:probe.unlink(missing_ok=True)
    except OSError:pass
  write_json(self.settings_path,dict(enabled=data['enabled'],time=data['time'],retain=data['retain'],mirror=mirror,timezone='Asia/Tehran'))
 def inventory(self):
  return [dict(name=p.name,bytes=p.stat().st_size) for p in sorted(self.local.glob('*.nikbak'),key=lambda p:p.stat().st_mtime_ns,reverse=True) if NAME.fullmatch(p.name)]
 def selected(self,name):
  if not isinstance(name,str) or not NAME.fullmatch(name):raise ValueError('نام پشتیبان معتبر نیست')
  path=self.local/name
  if not path.is_file():raise ValueError('پشتیبان یافت نشد')
  return path
 def run(self,reason='manual'):
  if not self.gate.acquire(blocking=False):raise ValueError('عملیات پشتیبان یا آزمون بازیابی در حال اجراست')
  state=self.state();state.update(running=True,operation='backup',started=datetime.now(timezone.utc).isoformat(),error='',mirror_error='');self.runtime_error=''
  try:
   write_json(self.state_path,state)
   with FileLock(self.root/'backup.lock'),tempfile.TemporaryDirectory(prefix='snapshot-',dir=self.root) as folder:
    folder=Path(folder);snapshot=folder/'snapshot.sqlite3'
    source=sqlite3.connect(str(self.root/'nikan.sqlite3'),timeout=30);target=sqlite3.connect(str(snapshot))
    try:source.backup(target,pages=256,sleep=.05)
    finally:target.close();source.close()
    name='nikan-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(8)+'.nikbak'
    temp=self.local/(name+'.tmp')
    try:
     encrypt(snapshot,temp,self.key);verification=recovery_check(temp,self.key,self.root);os.replace(temp,self.local/name)
    finally:temp.unlink(missing_ok=True)
    state.update(last_success=datetime.now(timezone.utc).isoformat(),file=name,verification=verification)
    settings=self.settings()
    if settings['mirror']:
     destination=Path(settings['mirror']);temp_copy=destination/(name+'.tmp')
     try:
      with (self.local/name).open('rb') as src,temp_copy.open('xb') as dst:
       shutil.copyfileobj(src,dst,CHUNK);dst.flush();os.fsync(dst.fileno())
      if digest(temp_copy)!=digest(self.local/name):raise ValueError('نسخه شبکه با نسخه اصلی برابر نیست')
      recovery_check(temp_copy,self.key,self.root);os.replace(temp_copy,destination/name)
      state.update(mirror_success=datetime.now(timezone.utc).isoformat(),mirror_file=name)
      self.prune(destination,settings['retain'])
     except (OSError,ValueError) as exc:state['mirror_error']=str(exc)
     finally:
      try:temp_copy.unlink(missing_ok=True)
      except OSError:pass
    self.prune(self.local,settings['retain']);return name
  except Exception as exc:state['error']=str(exc);self.runtime_error=str(exc);raise
  finally:
   state['running']=False
   try:write_json(self.state_path,state)
   except OSError as exc:self.runtime_error=str(exc);logging.exception('Cannot persist backup status')
   finally:self.gate.release()
 def verify(self,name):
  if not self.gate.acquire(blocking=False):raise ValueError('عملیات دیگری در حال اجراست')
  state=self.state();state.update(running=True,operation='verify',error='');self.runtime_error=''
  try:
   write_json(self.state_path,state)
   result=recovery_check(self.selected(name),self.key,self.root);state['recovery_test']=dict(file=name,**result);return result
  except Exception as exc:state['error']=str(exc);self.runtime_error=str(exc);raise
  finally:
   state['running']=False
   try:write_json(self.state_path,state)
   except OSError as exc:self.runtime_error=str(exc);logging.exception('Cannot persist backup status')
   finally:self.gate.release()
 def prune(self,folder,retain):
  files=sorted((p for p in Path(folder).glob('*.nikbak') if NAME.fullmatch(p.name)),key=lambda p:p.stat().st_mtime_ns,reverse=True)
  # Only remove authenticated archives made with this installation's recovery key.
  owned=[]
  for path in files:
   with tempfile.TemporaryDirectory(dir=self.root) as temp:
    try:decrypt(path,Path(temp)/'check.sqlite3',self.key);owned.append(path)
    except ValueError:continue
  for path in owned[retain:]:path.unlink()
 def due(self,now=None):
  now=now or datetime.now(TEHRAN);settings=self.settings()
  if not settings['enabled']:return False
  state=self.state()
  if state.get('started') and (state.get('error') or state.get('mirror_error')):
   return (now-datetime.fromisoformat(state['started']).astimezone(TEHRAN)).total_seconds()>=300
  if not state.get('last_success'):return True
  last=datetime.fromisoformat(state['last_success']).astimezone(TEHRAN)
  return now.strftime('%H:%M')>=settings['time'] and last.date()<now.date()
 def background(self,operation,*args):
  if self.gate.locked():raise ValueError('عملیات دیگری در حال اجراست')
  def job():
   try:operation(*args)
   except Exception:logging.exception('Backup/recovery operation failed')
  threading.Thread(target=job,daemon=True).start()
 def start(self):
  def schedule():
   while not self.stop_event.is_set():
    if self.due():
     try:self.run('scheduled')
     except Exception:logging.exception('Scheduled backup failed')
    self.stop_event.wait(60)
  self.thread=threading.Thread(target=schedule,daemon=True);self.thread.start()
 def close(self):
  self.stop_event.set()
  if self.thread:self.thread.join(timeout=30)
