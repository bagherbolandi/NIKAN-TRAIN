"""Run with the bundled Windows interpreter after installation."""
import sys,tempfile,threading,urllib.request,json,argparse
from pathlib import Path
parser=argparse.ArgumentParser()
parser.add_argument('--install',type=Path,help='Extracted or installed application directory')
args=parser.parse_args()
base=args.install.resolve() if args.install else Path(__file__).resolve().parent
sys.path.insert(0,str(base if (base/'app').exists() else base.parent))
from app import create_app
from waitress import create_server
with tempfile.TemporaryDirectory() as root:
 app=create_app(root,True)
 c=app.test_client();assert c.get('/api/status').json['setup_required']
 assert len(c.post('/api/setup',json={'username':'validation-admin','password':'Test-only-password-123'}).json)>0
 assert c.post('/api/login',json={'username':'validation-admin','password':'Test-only-password-123'}).status_code==200
 me=c.get('/api/me').json;assert len(me['companies'])==6
 assert me['network'] is False
 assert len(c.get('/api/competency-bank').json)==281
 h={'X-CSRF-Token':me['csrf']}
 assert c.post('/api/competency-bank/adopt?company_id=1',headers=h).status_code==200
 assert c.get('/api/data/job_profiles?company_id=1').json
 assert c.get('/api/dashboard?company_id=2').json['employees']==0
 # Validate the shared 0.6 features using the standalone payload and its database.
 assert c.post('/api/competency-bank',headers=h,json={'code':'PAYLOAD-SHARED','title':'Shared training competency'}).status_code==201
 skills={}
 for cid in (1,2):
  response=c.post(f'/api/competency-bank/PAYLOAD-SHARED/select?company_id={cid}',headers=h)
  assert response.status_code==200,response.json
  skills[cid]=response.json['id']
 assert c.put('/api/competency-bank/PAYLOAD-SHARED',headers=h,json={'title':'Updated shared competency'}).status_code==200
 for cid in (1,2):assert any(r['title']=='Updated shared competency' for r in c.get(f'/api/data/skills?company_id={cid}').json)
 assert c.post('/api/competency-bank',headers=h,json={'code':'PAYLOAD-UNUSED','title':'Unused competency'}).status_code==201
 assert c.delete('/api/competency-bank/PAYLOAD-UNUSED',headers=h).status_code==200
 employees=[];courses=[]
 for cid in (1,2):
  response=c.post(f'/api/data/employees?company_id={cid}',headers=h,json={'code':f'P-{cid}','name':f'Person company {cid}'})
  assert response.status_code==201,response.json
  employees.append(response.json['id'])
  response=c.post(f'/api/data/courses?company_id={cid}',headers=h,json={'skill_id':skills[cid],'title':f'Joint training {cid}','date':'1405/07/23','hours':2})
  assert response.status_code==201,response.json
  courses.append(response.json['id'])
 response=c.post('/api/data/courses?company_id=2',headers=h,json={'skill_id':skills[2],'title':'Internal training','date':'1405/07/24','hours':1,'calendar_scope':'internal'})
 assert response.status_code==201,response.json
 internal=response.json['id']
 assert {r['id'] for r in c.get('/api/calendar').json}==set(courses)
 assert [r['id'] for r in c.get('/api/calendar?scope=internal&company_id=2').json]==[internal]
 assert {r['id'] for r in c.get('/api/holding/personnel').json}==set(employees)
 roster=f'/api/courses/{courses[0]}/participants'
 response=c.put(roster,headers=h,json={'employee_ids':employees})
 assert response.status_code==200,response.json
 assert {r['id'] for r in c.get(roster).json['participants']}==set(employees)
 assert c.get('/api/data/records?company_id=1').json==[]
 assert c.delete('/api/competency-bank/PAYLOAD-SHARED',headers=h).status_code==409
 for role in ('manager','viewer'):
  name='validation-'+role
  assert c.post('/api/users',headers=h,json={'username':name,'password':'Temporary-role-password-123','role':role,'company_id':2}).status_code==201
  scoped=app.test_client();assert scoped.post('/api/login',json={'username':name,'password':'Temporary-role-password-123'}).status_code==200
  sh={'X-CSRF-Token':scoped.get('/api/me').json['csrf']}
  assert {r['id'] for r in scoped.get('/api/calendar').json}==set(courses)
  assert scoped.get('/api/calendar?scope=internal&company_id=1').status_code==403
  assert scoped.get('/api/holding/personnel').status_code==403
  assert scoped.get(roster).status_code==403
  assert scoped.put(roster,headers=sh,json={'employee_ids':[]}).status_code==403
  assert scoped.post('/api/competency-bank',headers=sh,json={'code':'BYPASS','title':'Forbidden'}).status_code==403
  assert scoped.put('/api/competency-bank/PAYLOAD-SHARED',headers=sh,json={'title':'Forbidden'}).status_code==403
  assert scoped.delete('/api/competency-bank/PAYLOAD-SHARED',headers=sh).status_code==403
  assert scoped.post('/api/competency-bank/PAYLOAD-SHARED/select?company_id=2',headers=sh).status_code==(200 if role=='manager' else 403)
 snapshot=c.post('/api/backup',headers=h)
 assert snapshot.data.startswith(b'SQLite format 3'),snapshot.status_code
 restored_dir=Path(root)/'restored';restored_dir.mkdir()
 (restored_dir/'nikan.sqlite3').write_bytes(snapshot.data)
 restored=create_app(restored_dir,True).test_client()
 assert restored.post('/api/login',json={'username':'validation-admin','password':'Test-only-password-123'}).status_code==200
 assert {r['id'] for r in restored.get(roster).json['participants']}==set(employees)
 print('Standalone 0.6 features passed: holding/internal calendars, admin bank CRUD, company select/use, cross-company personnel planning and SQLite backup recovery.')
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
