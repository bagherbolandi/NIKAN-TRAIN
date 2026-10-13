"""Run with the bundled Windows interpreter after installation."""
import sys,tempfile,threading,urllib.request,json
from pathlib import Path
base=Path(__file__).resolve().parent
sys.path.insert(0,str(base if (base/'app').exists() else base.parent))
from app import create_app
from waitress import create_server
with tempfile.TemporaryDirectory() as root:
 app=create_app(root,True)
 c=app.test_client();assert c.get('/api/status').json['setup_required']
 assert len(c.post('/api/setup',json={'username':'validation-admin','password':'Test-only-password-123'}).json)>0
 assert c.post('/api/login',json={'username':'validation-admin','password':'Test-only-password-123'}).status_code==200
 me=c.get('/api/me').json;assert len(me['companies'])==6
 assert len(c.get('/api/competency-bank').json)==281
 h={'X-CSRF-Token':me['csrf']}
 assert c.post('/api/competency-bank/adopt?company_id=1',headers=h).status_code==200
 assert c.get('/api/data/job_profiles?company_id=1').json
 assert c.get('/api/dashboard?company_id=2').json['employees']==0
 server=create_server(app,host='127.0.0.1',port=0);thread=threading.Thread(target=server.run,daemon=True);thread.start()
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{server.effective_port}/api/status') as response:assert response.status==200
 finally:server.task_dispatcher.shutdown();server.close();thread.join(timeout=3)
print('Payload validation passed:',sys.platform,sys.version.split()[0])
# Test the actual packaged desktop entry point when the browser launcher exits.
import os,subprocess,time,http.cookiejar
with tempfile.TemporaryDirectory(prefix='nikan-desktop-validation-') as data:
 env={**os.environ,'LOCALAPPDATA':data};env.pop('NIKAN_HEADLESS_DESKTOP',None)
 code="import sys,subprocess;sys.path.insert(0,sys.argv[1]);import desktop;desktop.open_window=lambda url,data: subprocess.Popen([sys.executable,'-c','pass']);desktop.main()"
 app_root=base if (base/'desktop.py').exists() else base.parent
 child=subprocess.Popen([sys.executable,'-I','-c',code,str(app_root)],env=env)
 endpoint=Path(data)/'NikanTraining'/'desktop-endpoint.json'
 try:
  for _ in range(100):
   if endpoint.exists():break
   if child.poll() is not None:raise RuntimeError('Desktop exited before readiness')
   time.sleep(.1)
  assert endpoint.exists();url=json.loads(endpoint.read_text())['url']
  time.sleep(1);assert child.poll() is None,'Browser launcher exit stopped packaged desktop'
  with urllib.request.urlopen(url+'/api/status',timeout=3) as response:assert response.status==200
  if os.name=='nt':
   second=subprocess.run([sys.executable,'-I','-c',code,str(app_root)],env=env,timeout=10);assert second.returncode==0
   assert child.poll() is None,'Second launch stopped the running instance'
   closed=subprocess.run([sys.executable,'-I',str(app_root/'stop_desktop.py')],env=env,timeout=10);assert closed.returncode==0
  else:
   client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
   def post(path,data,csrf=''):
    r=client.open(urllib.request.Request(url+'/api/'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':csrf}));return json.loads(r.read())
   post('setup',{'username':'desktop-validation','password':'Temporary-test-password-123'})
   post('login',{'username':'desktop-validation','password':'Temporary-test-password-123'})
   me=json.loads(client.open(url+'/api/me').read());post('desktop-exit',{},me['csrf'])
  assert child.wait(timeout=10)==0
  assert not endpoint.exists()
 finally:
  if child.poll() is None:child.terminate();child.wait(timeout=5)
print('Packaged desktop: browser handoff survival, server readiness and clean shutdown passed.')
