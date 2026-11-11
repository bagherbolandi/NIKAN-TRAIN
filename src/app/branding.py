"""Original branding assets, with no dependency on an external logo service."""
import struct,json,os,subprocess,ctypes
from pathlib import Path
from flask import request,jsonify,Response,g
from .validation import positive_id
SCHEMA='''CREATE TABLE IF NOT EXISTS branding(key TEXT PRIMARY KEY,mime TEXT NOT NULL,content BLOB NOT NULL,icon BLOB,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);'''
def write_icon(png,root):
 root.mkdir(parents=True,exist_ok=True);path=root/'holding.ico'
 icon=struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(png),22)+png
 temp=root/'holding.ico.tmp';temp.write_bytes(icon);temp.replace(path)
 if os.name=='nt':
  paths=[]
  for folder in [16,2]:
   buf=ctypes.create_unicode_buffer(260)
   if ctypes.windll.shell32.SHGetFolderPathW(None,folder,None,0,buf)==0:
    base=Path(buf.value)
    paths += ([base/'Nikan Training.lnk'] if folder==16 else [base/'Nikan Training'/'Nikan Training.lnk',base/'Nikan Training'/'Close Nikan Training.lnk'])
  script=root/'refresh-branding.js'
  lines=['var shell = new ActiveXObject("WScript.Shell");']
  for link in paths:
   if link.exists():lines.append('var s=shell.CreateShortcut('+json.dumps(str(link))+');s.IconLocation='+json.dumps(str(path))+',0;s.Save();')
  # IconLocation must be one string, including the resource index.
  lines=[line.replace(json.dumps(str(path))+',0;',json.dumps(str(path)+',0')+';') for line in lines]
  script.write_text('\n'.join(lines),encoding='ascii')
  try:subprocess.run(['cscript.exe','//NoLogo','//B',str(script)],check=True,timeout=10,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
  finally:script.unlink(missing_ok=True)
  ctypes.windll.shell32.SHChangeNotify(0x08000000,0,None,None)
 return path

def register(app,db,authenticated,audit,error):
 root=Path(app.config['DB']).parent
 with app.app_context():
  db().executescript(SCHEMA)
  r=db().execute("SELECT icon FROM branding WHERE key='holding'").fetchone()
  if r and r['icon'] and not app.testing:
   try:write_icon(r['icon'],root)
   except (OSError,subprocess.SubprocessError):app.logger.warning('Shortcut logo refresh unavailable; continuing startup',exc_info=True)
 @app.get('/api/branding')
 def metadata():
  return jsonify([{'key':r['key'],'updated':r['updated']} for r in db().execute('SELECT key,updated FROM branding')])
 @app.get('/api/branding/logo/<key>')
 def logo(key):
  r=db().execute('SELECT mime,content FROM branding WHERE key=?',(key,)).fetchone()
  if not r:return error('لوگویی ثبت نشده است',404)
  return Response(r['content'],mimetype=r['mime'])
 @app.post('/api/branding/<key>')
 @authenticated
 def upload(key):
  if g.user['role']!='admin':raise PermissionError()
  cid=None
  if key!='holding':
   if not key.startswith('company-'):raise ValueError('شناسه لوگو نامعتبر است')
   cid=positive_id(key[8:])
   if key!='company-'+str(cid):raise ValueError('شناسه لوگو نامعتبر است')
   if not db().execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone():raise ValueError('شرکت نامعتبر است')
  f=request.files.get('file')
  if not f:raise ValueError('فایل لوگو را انتخاب کنید')
  content=f.read(2*1024*1024+1)
  if len(content)>2*1024*1024:raise ValueError('حجم لوگو حداکثر ۲ مگابایت باشد')
  if content.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
  elif content.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
  else:raise ValueError('فقط PNG و JPEG پذیرفته می‌شوند')
  icon=None
  if key=='holding' and request.files.get('icon'):
   icon=request.files['icon'].read(512*1024+1)
   if len(icon)>512*1024 or not icon.startswith(b'\x89PNG\r\n\x1a\n') or len(icon)<24 or struct.unpack('>II',icon[16:24])!=(256,256):raise ValueError('نماد برنامه نامعتبر است')
  db().execute('BEGIN IMMEDIATE')
  if cid is not None and not db().execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone():raise ValueError('شرکت حذف شده است؛ فهرست شرکت‌ها را تازه کنید')
  db().execute('INSERT INTO branding(key,mime,content,icon) VALUES (?,?,?,?) ON CONFLICT(key) DO UPDATE SET mime=excluded.mime,content=excluded.content,icon=excluded.icon,updated=CURRENT_TIMESTAMP',(key,mime,content,icon));audit('update_branding',detail=key);db().commit()
  icon_ok=True
  if icon:
   try:write_icon(icon,root)
   except (OSError,subprocess.SubprocessError):app.logger.exception('Shortcut logo refresh failed');icon_ok=False
  return jsonify(ok=True,icon_updated=icon_ok)
