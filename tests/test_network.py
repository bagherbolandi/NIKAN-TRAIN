import io,sqlite3,time
from pathlib import Path
from datetime import datetime,timedelta
import pytest
from app import create_app
from network.config import initialize
from network.backup import BackupManager,decrypt,digest,TEHRAN
from network.storage import FileLock,write_json
from recovery import restore

@pytest.fixture
def central(tmp_path):
 root=tmp_path/'server';config=initialize(root,'nikan.test');app=create_app(root,True);manager=app.extensions['register_network'](root,config)
 c=app.test_client();url='https://localhost'
 assert c.post('/api/setup',base_url=url,json={'username':'admin','password':'Network-password-123'}).status_code==200
 assert c.post('/api/login',base_url=url,json={'username':'admin','password':'Network-password-123'}).status_code==200
 h={'X-CSRF-Token':c.get('/api/me',base_url=url).json['csrf']}
 c.post('/api/data/job_profiles?company_id=1',base_url=url,headers=h,json={'job':'Engineer','duties':'Master duties'})
 e=c.post('/api/data/employees?company_id=1',base_url=url,headers=h,json={'code':'E1','name':'Person','job':'Engineer'}).json['id']
 c.put(f'/api/passports/{e}/document?company_id=1',base_url=url,headers=h,json={'overrides':{'duties':'Personal duties'}})
 c.post('/api/certificates?company_id=1',base_url=url,headers=h,data={'employee_id':str(e),'title':'Certificate','file':(io.BytesIO(b'%PDF-test-certificate-unique'),'cert.pdf')})
 c.post('/api/branding/holding',base_url=url,headers=h,data={'file':(io.BytesIO(b'\x89PNG\r\n\x1a\nbranding-bytes'),'logo.png')})
 return root,app,manager,c,h,e

def test_verified_encrypted_backup_and_full_recovery(central,tmp_path):
 root,app,m,c,h,e=central;mirror=tmp_path/'share';mirror.mkdir();m.configure(dict(enabled=True,time='02:00',retain=3,mirror=str(mirror)))
 name=m.run();archive=m.selected(name);assert archive.read_bytes()[:8]==b'NIKANBK1';assert b'Personal duties' not in archive.read_bytes();assert b'%PDF-test-certificate-unique' not in archive.read_bytes()
 assert digest(archive)==digest(mirror/name);assert m.state()['verification']['verified'];assert not m.state()['mirror_error']
 assert m.verify(name)['tables']['certificates']==1
 target=tmp_path/'restored';assert restore(archive,root/'keys/recovery.key',target)['restored']
 recovered=create_app(target,True);rc=recovered.test_client();assert rc.post('/api/login',json={'username':'admin','password':'Network-password-123'}).status_code==200
 assert rc.get(f'/api/passports/{e}?company_id=1').json['job']['duties']=='Personal duties'
 before=c.get(f'/api/passports/{e}?company_id=1',base_url='https://localhost').json['document']['number'];assert rc.get(f'/api/passports/{e}?company_id=1').json['document']['number']==before
 certid=rc.get('/api/certificates?company_id=1').json[0]['id'];assert rc.get(f'/api/certificates/{certid}?company_id=1').data==b'%PDF-test-certificate-unique'
 assert rc.get('/api/branding/logo/holding').data==b'\x89PNG\r\n\x1a\nbranding-bytes'
 with pytest.raises(ValueError):restore(archive,root/'keys/recovery.key',target)
 # Replace a stopped database only after producing a verified safety backup.
 result=restore(archive,root/'keys/recovery.key',root,True);assert result['safety_backup'];assert (root/'backups'/result['safety_backup']).exists()

def test_corruption_wrong_key_and_active_server_never_replace(central,tmp_path):
 root,app,m,c,h,e=central;archive=m.selected(m.run());before=digest(root/'nikan.sqlite3')
 broken=tmp_path/'broken.nikbak';data=bytearray(archive.read_bytes());data[-20]^=1;broken.write_bytes(data)
 with pytest.raises(ValueError):restore(broken,root/'keys/recovery.key',root,True)
 assert digest(root/'nikan.sqlite3')==before
 wrong=tmp_path/'wrong.key';wrong.write_bytes(b'X'*32)
 with pytest.raises(ValueError):restore(archive,wrong,root,True)
 with FileLock(root/'server.lock'):
  with pytest.raises(RuntimeError):restore(archive,root/'keys/recovery.key',root,True)
 assert digest(root/'nikan.sqlite3')==before;assert not list(root.glob('restore-*'))

def test_schedule_retention_and_network_failure(central,tmp_path):
 root,app,m,c,h,e=central;share=tmp_path/'share';share.mkdir();m.configure(dict(enabled=True,time='02:00',retain=2,mirror=str(share)))
 assert m.due();names=[m.run() for _ in range(3)];assert len(m.inventory())==2;assert m.selected(names[-1]).exists();assert len(list(share.glob('*.nikbak')))==2
 now=datetime.now(TEHRAN);state=m.state();state['last_success']=(now-timedelta(days=1)).isoformat();write_json(m.state_path,state)
 assert m.due(now.replace(hour=2,minute=1));assert not m.due(now.replace(hour=1,minute=59))
 share.rmdir() if not list(share.iterdir()) else None
 for path in share.iterdir():path.unlink()
 share.rmdir();name=m.run();assert m.selected(name).exists();assert m.state()['mirror_error'];assert not m.state()['error']
 with pytest.raises(ValueError):m.configure(dict(enabled=True,time='25:00',retain=1,mirror=''))
 with pytest.raises(ValueError):m.configure(dict(enabled=True,time='02:00',retain=2,mirror=str(root)))
 assert not m.due(datetime.now(TEHRAN));assert m.due(datetime.now(TEHRAN)+timedelta(minutes=6))

def test_network_admin_auth_tls_and_scoped_users(central):
 root,app,m,c,h,e=central;url='https://localhost'
 assert c.get('/api/me',base_url=url).json['network'];assert c.get('/api/network/settings').status_code==403
 assert c.get('/api/network/settings',base_url='https://untrusted.example').status_code==400
 assert c.put('/api/network/settings',base_url=url,json={'enabled':False,'time':'02:00','retain':2,'mirror':''}).status_code==403
 assert c.post('/api/users',base_url=url,headers=h,json={'username':'manager','password':'Network-password-123','role':'manager','company_id':2}).status_code==201
 c.post('/api/logout',base_url=url,headers=h);c.post('/api/login',base_url=url,json={'username':'manager','password':'Network-password-123'})
 mh={'X-CSRF-Token':c.get('/api/me',base_url=url).json['csrf']}
 for path in ['/api/network/settings','/api/network/backups','/api/network/recovery-key']:
  assert c.get(path,base_url=url).status_code==403
 assert c.post('/api/network/backups',base_url=url,headers=mh).status_code==403
 assert c.get('/api/data/employees?company_id=1',base_url=url).status_code==403
 assert c.post('/api/setup',base_url=url,environ_overrides={'REMOTE_ADDR':'192.168.1.20'},json={'username':'another','password':'Network-password-123'}).status_code==403

def test_standalone_database_migration(central,tmp_path):
 from recovery import import_database
 root,app,m,c,h,e=central
 name=m.run();plain=tmp_path/'standalone.sqlite3';decrypt(m.selected(name),plain,m.key)
 target=tmp_path/'central-migrated';assert import_database(plain,target)['restored']
 recovered=create_app(target,True);rc=recovered.test_client();assert rc.post('/api/login',json={'username':'admin','password':'Network-password-123'}).status_code==200
 assert rc.get(f'/api/passports/{e}?company_id=1').json['job']['duties']=='Personal duties'
 with FileLock(target/'server.lock'):
  with pytest.raises(RuntimeError):import_database(plain,target,True)

def test_failed_status_write_releases_backup_lock(central,monkeypatch):
 import network.backup as module
 root,app,m,c,h,e=central
 original=module.write_json
 def full(*args,**kwargs):raise OSError('Disk full simulation')
 monkeypatch.setattr(module,'write_json',full)
 with pytest.raises(OSError):m.run()
 assert not m.gate.locked();assert 'Disk full' in m.state()['error'];assert not m.state()['running']
 monkeypatch.setattr(module,'write_json',original)
 assert m.run();assert not m.state()['error']

def test_background_job_is_reserved_before_acceptance(central,monkeypatch):
 import threading
 import network.backup as module
 root,app,m,c,h,e=central;name=m.run();release=threading.Event();finished=threading.Event();original=module.recovery_check
 def controlled(*args):
  assert release.wait(5)
  try:return original(*args)
  finally:finished.set()
 monkeypatch.setattr(module,'recovery_check',controlled)
 m.background(m.verify,name)
 try:
  assert m.state()['running']
  with pytest.raises(ValueError):m.background(m.verify,name)
 finally:release.set()
 assert finished.wait(5)
 deadline=time.monotonic()+5
 while m.gate.locked() and time.monotonic()<deadline:time.sleep(.01)
 assert not m.state()['running'];assert m.state()['recovery_test']['verified']

def test_all_factory_connections_close_without_garbage_collection(tmp_path,monkeypatch):
 import app as module
 real=sqlite3.connect;connections=[]
 class TrackedConnection(sqlite3.Connection):
  explicitly_closed=False
  def close(self):self.explicitly_closed=True;return super().close()
 def connect(*args,**kwargs):
  kwargs['factory']=TrackedConnection
  conn=real(*args,**kwargs);connections.append(conn);return conn
 monkeypatch.setattr(module.sqlite3,'connect',connect)
 # Strong references keep every connection alive, so garbage collection cannot mask a leak.
 app=create_app(tmp_path/'factory',True)
 assert connections and all(c.explicitly_closed for c in connections)
 assert app.test_client().get('/api/status').status_code==200
 assert all(c.explicitly_closed for c in connections)

def test_recovery_validation_does_not_change_desktop_icons(central,monkeypatch):
 import app.branding as branding
 root,app,m,c,h,e=central
 conn=sqlite3.connect(root/'nikan.sqlite3');conn.execute("UPDATE branding SET icon=? WHERE key='holding'",(b'\x89PNG\r\n\x1a\nbackup-icon',));conn.commit();conn.close()
 def forbidden(*args):raise AssertionError('A recovery test must not modify live desktop shortcuts')
 monkeypatch.setattr(branding,'write_icon',forbidden)
 name=m.run();assert m.verify(name)['verified']

def test_recovery_key_write_failure_keeps_current_database(central,monkeypatch):
 import recovery as module
 root,app,m,c,h,e=central;archive=m.selected(m.run())
 conn=sqlite3.connect(root/'nikan.sqlite3');conn.execute("INSERT INTO employees(company_id,code,name) VALUES (1,'LIVE','Live record after backup')");conn.commit();conn.close();before=digest(root/'nikan.sqlite3')
 original=module.atomic_write
 def fail(path,data):
  if Path(path).name=='session.key':raise OSError('Recovery disk failure simulation')
  return original(path,data)
 monkeypatch.setattr(module,'atomic_write',fail)
 with pytest.raises(OSError):restore(archive,root/'keys/recovery.key',root,True)
 assert digest(root/'nikan.sqlite3')==before
 assert len(m.inventory())==2 # Original archive plus verified safety snapshot.
