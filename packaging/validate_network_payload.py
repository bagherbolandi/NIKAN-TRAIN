"""Exercise the installed HTTPS server with verified TLS, concurrent users and real recovery."""
import argparse,json,sys,time,ssl,secrets,urllib.request,urllib.error,http.cookiejar,tempfile,subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

def validate(install,root):
 sys.path.insert(0,str(install))
 from network.config import load
 from network.backup import recovery_check
 from recovery import restore
 config=load(root);base=f"https://localhost:{config['port']}";context=ssl.create_default_context(cafile=config['certificate'])
 class Client:
  def __init__(self):self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),urllib.request.HTTPSHandler(context=context));self.csrf=None
  def request(self,path,data=None,method=None,raw=False):
   headers={}
   if self.csrf:headers['X-CSRF-Token']=self.csrf
   if data is not None:headers['Content-Type']='application/json';data=json.dumps(data).encode()
   request=urllib.request.Request(base+path,data=data,headers=headers,method=method)
   with self.opener.open(request,timeout=45) as response:
    body=response.read();return body if raw else json.loads(body)
  def login(self,user,password):
   self.request('/api/login',{'username':user,'password':password});self.csrf=self.request('/api/me')['csrf']
 admin=Client();deadline=time.monotonic()+45
 while True:
  try:status=admin.request('/api/status');break
  except (OSError,urllib.error.URLError):
   if time.monotonic()>deadline:raise
   time.sleep(.2)
 password=secrets.token_urlsafe(24);username='network-smoke-'+secrets.token_hex(4)
 assert status['setup_required'];admin.request('/api/setup',{'username':username,'password':password});admin.login(username,password)
 admin.request('/api/users',dict(username='manager1',password=password,role='manager',company_id=1));admin.request('/api/users',dict(username='manager2',password=password,role='manager',company_id=2))
 manager=Client();manager.login('manager2',password)
 try:manager.request('/api/data/employees?company_id=1');raise AssertionError('Company isolation failed')
 except urllib.error.HTTPError as exc:assert exc.code==403
 try:manager.request('/api/network/recovery-key');raise AssertionError('Recovery key exposed')
 except urllib.error.HTTPError as exc:assert exc.code==403
 def worker(index):
  c=Client();c.login('manager1',password);result=c.request('/api/data/employees?company_id=1',dict(code=f'NET-{index:03d}',name=f'Network person {index}'));assert result['id'];return result
 # Actual separate HTTPS connections write to the same central database.
 with ThreadPoolExecutor(max_workers=8) as pool:assert len(list(pool.map(worker,range(16))))==16
 assert len(admin.request('/api/data/employees?company_id=1'))==16
 # Include actual attachment and personal amendment bytes in the production backup.
 import sqlite3
 conn=sqlite3.connect(root/'nikan.sqlite3');eid=conn.execute('SELECT id FROM employees LIMIT 1').fetchone()[0]
 conn.execute("INSERT INTO certificates(company_id,employee_id,title,filename,mime,content) VALUES (1,?,'Payload certificate','certificate.pdf','application/pdf',?)",(eid,b'%PDF-native-network-proof'));conn.commit();conn.close()
 admin.request(f'/api/passports/{eid}/document?company_id=1',{'overrides':{'commitments':'Native independent commitment'}},'PUT')
 before={f['name'] for f in admin.request('/api/network/backups')['files']};admin.request('/api/network/backups',{},'POST')
 deadline=time.monotonic()+60
 while True:
  backups=admin.request('/api/network/backups');state=backups['status']
  new=[f for f in backups['files'] if f['name'] not in before]
  if new and not state.get('running'):break
  if state.get('error'):raise AssertionError(state['error'])
  if time.monotonic()>deadline:raise AssertionError('Backup did not finish')
  time.sleep(.2)
 name=state['file'];assert state['verification']['verified'];assert state['verification']['tables']['employees']==16
 key=admin.request('/api/network/recovery-key',raw=True);assert len(key)==32
 archive=root/'backups'/name
 # A running server must refuse destructive recovery.
 try:restore(archive,root/'keys/recovery.key',root,True);raise AssertionError('Active database replacement allowed')
 except RuntimeError:pass
 with tempfile.TemporaryDirectory() as folder:
  target=Path(folder)/'restored';result=restore(archive,root/'keys/recovery.key',target);assert result['restored']
  from app import create_app
  app=create_app(target,True);c=app.test_client();assert c.post('/api/login',json={'username':username,'password':password}).status_code==200
  assert len(c.get('/api/data/employees?company_id=1').json)==16
  assert c.get(f'/api/passports/{eid}?company_id=1').json['job']['commitments']=='Native independent commitment'
  assert c.get('/api/certificates/1?company_id=1').data==b'%PDF-native-network-proof'
 print('Network payload passed: verified HTTPS, 16 concurrent writes, company isolation, encrypted backup, attachment/password/personal-document recovery, active-server protection.')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--install',type=Path,required=True);p.add_argument('--data-dir',type=Path,required=True);a=p.parse_args();validate(a.install,a.data_dir)
