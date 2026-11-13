"""Exercise the installed HTTPS server with verified TLS, concurrent users and real recovery."""
import argparse,json,sys,time,ssl,secrets,urllib.request,urllib.error,http.cookiejar,tempfile,subprocess,io,zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

def validate(install,root):
 sys.path.insert(0,str(install))
 from network.config import load
 from app import COMPANIES
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
  def spreadsheet(self,path,payload,mode):
   boundary='NikanValidation'+secrets.token_hex(12);parts=[]
   for name,value in (('mode',mode),('policy','upsert')):
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
   parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="assessment.xlsx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n'.encode()+payload+b'\r\n')
   parts.append(f'--{boundary}--\r\n'.encode())
   request=urllib.request.Request(base+path,data=b''.join(parts),headers={'X-CSRF-Token':self.csrf,'Content-Type':'multipart/form-data; boundary='+boundary},method='POST')
   with self.opener.open(request,timeout=45) as response:return json.loads(response.read())
 admin=Client();deadline=time.monotonic()+45
 while True:
  try:status=admin.request('/api/status');break
  except (OSError,urllib.error.URLError):
   if time.monotonic()>deadline:raise
   time.sleep(.2)
 password=secrets.token_urlsafe(24);username='network-smoke-'+secrets.token_hex(4)
 assert status['setup_required'];admin.request('/api/setup',{'username':username,'password':password});admin.login(username,password)
 admin.request('/api/users',dict(username='manager1',password=password,role='manager',company_id=1));admin.request('/api/users',dict(username='manager2',password=password,role='manager',company_id=2))
 admin.request('/api/users',dict(username='viewer2',password=password,role='viewer',company_id=2))
 manager=Client();manager.login('manager2',password)
 try:manager.request('/api/data/employees?company_id=1');raise AssertionError('Company isolation failed')
 except urllib.error.HTTPError as exc:assert exc.code==403;assert json.loads(exc.read())['error']
 try:manager.request('/api/network/recovery-key');raise AssertionError('Recovery key exposed')
 except urllib.error.HTTPError as exc:assert exc.code==403;assert json.loads(exc.read())['error']
 # An early authorization response must also arrive intact with an unread POST body.
 try:manager.request('/api/network/backups',{'unused':'body'},'POST');raise AssertionError('Backup permission bypass')
 except urllib.error.HTTPError as exc:assert exc.code==403;assert json.loads(exc.read())['error']
 def worker(index):
  c=Client();c.login('manager1',password);result=c.request('/api/data/employees?company_id=1',dict(code=f'NET-{index:03d}',name=f'Network person {index}'));assert result['id'];return result
 # Actual separate HTTPS connections write to the same central database.
 with ThreadPoolExecutor(max_workers=8) as pool:assert len(list(pool.map(worker,range(16))))==16
 assert len(admin.request('/api/data/employees?company_id=1'))==16
 # Exercise the 0.7 role policy through real HTTPS and separate sessions.
 def rejected(client,path,status,data=None,method=None):
  try:client.request(path,data,method);raise AssertionError('Unexpected access: '+path)
  except urllib.error.HTTPError as exc:assert exc.code==status;assert json.loads(exc.read())['error']
 original_companies=admin.request('/api/companies');assert len(original_companies)==len(COMPANIES)
 company_id=admin.request('/api/companies',dict(name='Network disposable company'))['id']
 company_path=f'/api/companies/{company_id}'
 assert any(r['id']==company_id for r in admin.request('/api/me')['companies'])
 admin.request(company_path,dict(name='Network renamed company'),'PUT')
 assert next(r['name'] for r in admin.request('/api/companies') if r['id']==company_id)=='Network renamed company'
 admin.request(company_path,method='DELETE')
 assert admin.request('/api/companies')==original_companies
 assert admin.request('/api/me')['companies']==original_companies
 rejected(admin,'/api/companies/1',409,method='DELETE')
 admin.request('/api/competency-bank',dict(code='NET-SHARED',title='Network shared competency'))
 courses=[]
 for cid in (1,2):
  sid=admin.request(f'/api/competency-bank/NET-SHARED/select?company_id={cid}',{},'POST')['id']
  courses.append(admin.request(f'/api/data/courses?company_id={cid}',dict(skill_id=sid,title=f'Joint training {cid}',date='1405/07/23',hours=2))['id'])
 internal=admin.request('/api/data/courses?company_id=2',dict(skill_id=sid,title='Internal company training',date='1405/07/24',hours=1,calendar_scope='internal'))['id']
 assert {r['id'] for r in admin.request('/api/calendar')}==set(courses)
 viewer=Client();viewer.login('viewer2',password)
 for scoped in (manager,viewer):
  rejected(scoped,'/api/companies',403)
  rejected(scoped,'/api/companies',403,dict(name='Forbidden company'),'POST')
  rejected(scoped,'/api/companies/2',403,dict(name='Forbidden rename'),'PUT')
  rejected(scoped,'/api/companies/2',403,method='DELETE')
  rejected(scoped,'/api/calendar',403)
  rejected(scoped,'/api/calendar?scope=holding',403)
  assert [r['id'] for r in scoped.request('/api/calendar?scope=internal&company_id=2')]==[internal]
  rejected(scoped,'/api/calendar?scope=internal&company_id=1',403)
  assert courses[1] in {r['id'] for r in scoped.request('/api/data/courses?company_id=2')}
  rejected(scoped,f'/api/data/courses/{courses[1]}?company_id=2',403,dict(title='Forbidden holding edit'),'PUT')
  rejected(scoped,f'/api/data/courses/{courses[1]}?company_id=2',403,method='DELETE')
 course=dict(skill_id=sid,title='Manager internal default',date='1405/07/25',hours=1)
 rejected(manager,'/api/data/courses?company_id=2',403,dict(course,calendar_scope='holding'),'POST')
 own_internal=manager.request('/api/data/courses?company_id=2',course)['id']
 assert any(r['id']==own_internal and r['calendar_scope']=='internal' for r in manager.request('/api/calendar?scope=internal&company_id=2'))
 rejected(manager,f'/api/data/courses/{own_internal}?company_id=2',403,dict(calendar_scope='holding'),'PUT')
 rejected(viewer,'/api/data/courses?company_id=2',403,course,'POST')
 assert {r['id'] for r in admin.request('/api/calendar')}==set(courses)
 rejected(manager,'/api/competency-bank',403,dict(code='BYPASS',title='Forbidden'),'POST')
 rejected(manager,'/api/competency-bank/NET-SHARED',403,dict(title='Forbidden'),'PUT')
 rejected(manager,'/api/competency-bank/NET-SHARED',403,method='DELETE')
 rejected(admin,'/api/competency-bank/NET-SHARED',409,method='DELETE')
 joint_person=admin.request('/api/data/employees?company_id=2',dict(code='JOINT-1',name='Joint person company 2'))['id']
 company_person=admin.request('/api/data/employees?company_id=1')[0]['id']
 directory=admin.request('/api/holding/personnel');assert len(directory)==17;assert {p['company_id'] for p in directory}=={1,2}
 roster_path=f'/api/courses/{courses[0]}/participants';selected={company_person,joint_person}
 planned=admin.request(roster_path,dict(employee_ids=sorted(selected)),'PUT')
 assert {p['id'] for p in planned['participants']}==selected
 rejected(manager,'/api/holding/personnel',403);rejected(manager,roster_path,403)
 rejected(manager,roster_path,403,dict(employee_ids=[]),'PUT')
 assert admin.request('/api/data/records?company_id=1')==[]
 attendance_path=f'/api/courses/{courses[0]}/attendance';allocation_path=f'/api/courses/{courses[0]}/cost-allocation'
 assert all(not person['attended'] for person in admin.request(attendance_path)['participants'])
 admin.request(f'/api/data/courses/{courses[0]}?company_id=1',dict(cost=1.000001),'PUT')
 allocation=admin.request(allocation_path)
 assert allocation['allocation_basis']=='actual_attendance' and allocation['planned_count']==2 and allocation['attendee_count']==0
 assert allocation['allocated_cost_rials']==0 and allocation['unallocated_cost_rials']==1000001
 extra=next(row['id'] for row in admin.request('/api/data/employees?company_id=1') if row['id']!=company_person)
 selected.add(extra)
 admin.request(roster_path,dict(employee_ids=sorted(selected)),'PUT')
 admin.request(attendance_path,dict(employee_ids=[company_person]),'PUT')
 allocation=admin.request(allocation_path)
 assert allocation['planned_count']==3 and allocation['attendee_count']==1
 assert {row['company_id']:row['cost_rials'] for row in allocation['companies']}=={1:1000001,2:0}
 admin.request(attendance_path,dict(employee_ids=sorted(selected)),'PUT')
 allocation=admin.request(allocation_path)
 assert allocation['attendee_count']==3 and allocation['allocated_cost_rials']==1000001 and allocation['unallocated_cost_rials']==0
 assert {row['company_id']:row['cost_rials'] for row in allocation['companies']}=={1:666667,2:333334}
 assert sum(row['cost_rials'] for row in allocation['companies'])==allocation['total_cost_rials']
 assert admin.request(allocation_path)==allocation
 admin.request(roster_path,dict(employee_ids=sorted(selected)),'PUT')
 assert admin.request(allocation_path)==allocation
 rejected(admin,roster_path,400,dict(employee_ids=[]),'PUT')
 rejected(admin,f'/api/courses/{internal}/cost-allocation',400)
 for scoped in (manager,viewer):
  rejected(scoped,attendance_path,403);rejected(scoped,allocation_path,403)
  rejected(scoped,attendance_path,403,dict(employee_ids=[]),'PUT')
 assert admin.request('/api/companies')==original_companies
 # Include actual attachment and personal amendment bytes in the production backup.
 import sqlite3
 conn=sqlite3.connect(root/'nikan.sqlite3');eid=conn.execute('SELECT id FROM employees LIMIT 1').fetchone()[0]
 conn.execute("INSERT INTO certificates(company_id,employee_id,title,filename,mime,content) VALUES (1,?,'Payload certificate','certificate.pdf','application/pdf',?)",(eid,b'%PDF-native-network-proof'));conn.commit();conn.close()
 admin.request(f'/api/passports/{eid}/document?company_id=1',{'overrides':{'commitments':'Native independent commitment'}},'PUT')
 # Actual XLSX multipart requests must validate without writing and commit once.
 import openpyxl
 forms=admin.request('/api/excel/forms?company_id=1')
 assert {'employees','job_profiles','personal_documents','assessments','courses','records'}.issubset({r['kind'] for r in forms})
 workbook=openpyxl.load_workbook(io.BytesIO(admin.request('/api/excel/templates/assessments?company_id=1',raw=True)))
 sheet=workbook['ورود اطلاعات'];keys={cell.value:cell.column for cell in sheet[5]}
 for field,value in {'employee_code':'NET-000','skill_code':'NET-SHARED','level':3}.items():sheet.cell(7,keys[field],value)
 output=io.BytesIO();workbook.save(output);workbook.close();payload=output.getvalue()
 preview=admin.spreadsheet('/api/excel/import/assessments?company_id=1',payload,'preview');assert preview['can_commit']
 assert admin.request('/api/data/assessments?company_id=1')==[]
 assert admin.spreadsheet('/api/excel/import/assessments?company_id=1',payload,'commit')['applied']
 assert admin.spreadsheet('/api/excel/import/assessments?company_id=1',payload,'commit')['unchanged']==1
 document_path=f'/api/passports/{eid}/document?company_id=1';before_export=admin.request(document_path)
 assert before_export['number']=='NP-01-'+str(int(next(row['code'].rsplit('-',1)[1] for row in admin.request('/api/data/employees?company_id=1') if row['id']==eid))).zfill(4) and before_export['is_snapshot'] and before_export['aliases']==[]
 for suffix in ('commitment.docx','profile.docx'):
  blob=admin.request(f'/api/passports/{eid}/{suffix}?company_id=1',raw=True)
  with zipfile.ZipFile(io.BytesIO(blob)) as archive:assert 'Native independent commitment' in archive.read('word/document.xml').decode()
 assert admin.request(document_path)==before_export
 admin.request(f'/api/data/employees/{eid}?company_id=1',dict(code='NET-9876'),'PUT')
 assert admin.request(document_path)==before_export
 for scoped in (manager,viewer):
  rejected(scoped,'/api/excel/templates/competency-bank?company_id=2',403)
  rejected(scoped,f'/api/passports/{eid}/commitment.docx?company_id=1',403)
 rejected(viewer,'/api/excel/import/employees?company_id=2',403,{},'POST')
 before={f['name'] for f in admin.request('/api/network/backups')['files']};admin.request('/api/network/backups',{},'POST')
 deadline=time.monotonic()+60
 while True:
  backups=admin.request('/api/network/backups');state=backups['status']
  new=[f for f in backups['files'] if f['name'] not in before]
  if new and not state.get('running'):break
  if state.get('error'):raise AssertionError(state['error'])
  if time.monotonic()>deadline:raise AssertionError('Backup did not finish')
  time.sleep(.2)
 name=state['file'];assert state['verification']['verified'];assert state['verification']['tables']['employees']==17
 assert state['verification']['tables']['course_participants']==3
 assert state['verification']['tables']['company_registry']==1
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
  assert {p['id'] for p in c.get(roster_path).json['participants']}==selected
  assert all(p['attended'] for p in c.get(attendance_path).json['participants'])
  assert c.get(allocation_path).json==allocation
  assert c.get(document_path).json==before_export
  assert c.get(f'/api/passports/{eid}?company_id=1').json['job']['commitments']=='Native independent commitment'
  assert c.get('/api/certificates/1?company_id=1').data==b'%PDF-native-network-proof'
  assert c.get('/api/companies').json==original_companies
 print('Network 0.10 payload passed: separate planned/actual attendance, exact whole-rial company allocation, protected readonly reporting and encrypted attendance recovery, verified HTTPS, 16 concurrent writes, Excel multipart preview/import/repeat safety, short personal references and actual snapshots, readonly full/commitment Word exports, protected company/calendar/bank scopes, encrypted backup and full recovery, active-server protection.')

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--install',type=Path,required=True);p.add_argument('--data-dir',type=Path,required=True);a=p.parse_args();validate(a.install,a.data_dir)
