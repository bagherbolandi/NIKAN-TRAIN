import pytest
import json,os,subprocess,sys,time,urllib.request,http.cookiejar
from pathlib import Path

@pytest.mark.parametrize("browser_exits_immediately",[False,True])
def test_desktop_server_lifecycle(tmp_path,browser_exits_immediately):
 env={**os.environ,'LOCALAPPDATA':str(tmp_path),'NIKAN_HEADLESS_DESKTOP':'1'}
 root=Path(__file__).resolve().parents[1]
 if browser_exits_immediately:
  env.pop('NIKAN_HEADLESS_DESKTOP',None)
  code="import sys,subprocess;sys.path.insert(0,sys.argv[1]);import desktop;desktop.open_window=lambda url,data: subprocess.Popen([sys.executable,'-c','pass']);desktop.main()"
  args=[sys.executable,'-I','-c',code,str(root)]
 else:args=[sys.executable,'-I',str(root/'desktop.py')]
 process=subprocess.Popen(args,cwd=root,env=env)
 endpoint=tmp_path/'NikanTraining'/'desktop-endpoint.json'
 try:
  for _ in range(100):
   if endpoint.exists():break
   if process.poll() is not None:raise AssertionError('Desktop exited early')
   time.sleep(.1)
  assert endpoint.exists()
  url=json.loads(endpoint.read_text())['url']
  if browser_exits_immediately:
   time.sleep(1)
   assert process.poll() is None,'Browser launcher exit killed the service'
   with urllib.request.urlopen(url+'/api/status',timeout=3) as response:assert response.status==200
  client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
  def post(path,data,csrf=''):
   r=client.open(urllib.request.Request(url+'/api/'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-CSRF-Token':csrf}));return json.loads(r.read())
  post('setup',{'username':'desktop-test','password':'Temporary-test-password-123'})
  post('login',{'username':'desktop-test','password':'Temporary-test-password-123'})
  me=json.loads(client.open(url+'/api/me').read());assert me['desktop']
  assert post('desktop-exit',{},me['csrf'])['ok']
  assert process.wait(timeout=10)==0
  assert not endpoint.exists()
 finally:
  if process.poll() is None:process.terminate();process.wait(timeout=5)
