import io,sqlite3
from pathlib import Path
import pytest
from app import create_app
@pytest.fixture
def ready(tmp_path):
 app=create_app(tmp_path,True);c=app.test_client();c.post('/api/setup',json={'username':'admin','password':'Secure-password-123'});c.post('/api/login',json={'username':'admin','password':'Secure-password-123'});return app,c,{'X-CSRF-Token':c.get('/api/me').json['csrf']}
def test_shared_bank_updates_without_changing_personal_levels(ready):
 app,c,h=ready
 ids=[]
 for cid in [1,2]:ids.append(c.post(f'/api/data/skills?company_id={cid}',headers=h,json={'code':'SHARED','title':'Shared competency','category':'Technical'}).json['id'])
 assert len([r for r in c.get('/api/competency-bank').json if r['code']=='SHARED'])==1
 e=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'E1','name':'Person','job':'Engineer'}).json['id']
 c.post('/api/data/assessments?company_id=1',headers=h,json={'employee_id':e,'skill_id':ids[0],'level':2})
 assert c.put('/api/competency-bank/SHARED',headers=h,json={'title':'Updated holding title','category':'Safety'}).status_code==200
 for cid in [1,2]:assert c.get(f'/api/data/skills?company_id={cid}').json[0]['title']=='Updated holding title'
 assert c.get('/api/data/assessments?company_id=1').json[0]['level']==2
 c.post('/api/users',headers=h,json={'username':'manager','password':'Secure-password-123','role':'manager','company_id':1});c.post('/api/logout',headers=h);c.post('/api/login',json={'username':'manager','password':'Secure-password-123'});mh={'X-CSRF-Token':c.get('/api/me').json['csrf']}
 assert c.put('/api/competency-bank/SHARED',headers=mh,json={'title':'Unauthorized change'}).status_code==403

def test_atomic_job_and_person_editors_and_summary(ready):
 app,c,h=ready
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'E1','name':'Person','job':'Engineer'}).json['id']
 p='/api/job-workspace?company_id=1&job=Engineer'
 assert c.put(p,headers=h,json={'items':[{'code':'S001','level':3},{'code':'S002','level':2}]}).status_code==200
 assert len(c.get('/api/data/requirements?company_id=1').json)==2
 assert c.put(p,headers=h,json={'items':[{'code':'S001','level':1},{'code':'DOES-NOT-EXIST','level':2}]}).status_code==400
 assert next(r for r in c.get(p).json['matrix'] if r['code']=='S001')['level']==3
 a=f'/api/assessment-workspace/{eid}?company_id=1'
 assert c.put(a,headers=h,json={'items':[{'code':'S001','level':1},{'code':'S002','level':2}]}).status_code==200
 summary=c.get('/api/needs-summary?company_id=1').json;assert len(summary)==1;assert summary[0]['code']=='S001';assert summary[0]['people']==1;assert summary[0]['gap_total']==2
 assert c.get(f'/api/assessment-workspace/{eid}?company_id=2').status_code==404
 assert c.put(f'/api/assessment-workspace/{eid}?company_id=2',headers=h,json={'items':[]}).status_code==404
 assert c.put(p,headers=h,json={'items':[{'code':'S001','level':0}]}).status_code==200
 assert c.get('/api/needs-summary?company_id=1').json==[]

def test_search_scope_and_escaped_query(ready):
 app,c,h=ready
 for cid in [1,2]:c.post(f'/api/data/employees?company_id={cid}',headers=h,json={'code':str(cid),'name':f'Target person {cid}'})
 c.post('/api/users',headers=h,json={'username':'manager','password':'Secure-password-123','role':'manager','company_id':1});c.post('/api/logout',headers=h);c.post('/api/login',json={'username':'manager','password':'Secure-password-123'})
 hits=c.get('/api/search?company_id=1&q=Target').json;assert [r['title'] for r in hits]==['Target person 1']
 assert c.get('/api/search?company_id=2&q=Target').status_code==403
 assert c.get('/api/search?company_id=1&q=%25').json==[]
 assert c.get('/api/search?company_id=1&q=خط').json

def test_upgrade_preserves_legacy_conflicts_and_references(ready,tmp_path):
 app,c,h=ready
 dbfile=app.config['DB']
 with sqlite3.connect(dbfile) as d:
  d.execute('DROP TABLE competency_links')
  d.execute('INSERT INTO skills(company_id,code,title,category) VALUES (1,?,?,?)',('S001','Custom different meaning','Custom'))
  sid=d.execute('SELECT MAX(id) FROM skills').fetchone()[0]
  d.execute("INSERT INTO employees(company_id,code,name,job) VALUES (1,'OLD','Old person','Old job')")
  eid=d.execute('SELECT MAX(id) FROM employees').fetchone()[0]
  d.execute('INSERT INTO assessments(company_id,employee_id,skill_id,level) VALUES (1,?,?,3)',(eid,sid))
 upgraded=create_app(Path(dbfile).parent,True)
 with sqlite3.connect(dbfile) as d:
  assert d.execute('SELECT level FROM assessments WHERE employee_id=? AND skill_id=?',(eid,sid)).fetchone()[0]==3
  code=d.execute('SELECT code FROM competency_links WHERE skill_id=?',(sid,)).fetchone()[0]
  assert code.startswith('LEGACY-')
  assert d.execute('SELECT title FROM holding_competencies WHERE code=?',(code,)).fetchone()[0]=='Custom different meaning'
  assert d.execute("SELECT title FROM holding_competencies WHERE code='S001'").fetchone()[0]=='خط مشی کیفیت'

def test_reimport_uses_current_holding_definition(ready):
 app,c,h=ready
 assert c.post('/api/competency-bank/adopt?company_id=1',headers=h).status_code==200
 assert c.put('/api/competency-bank/S001',headers=h,json={'title':'Holding revised title'}).status_code==200
 assert c.post('/api/competency-bank/adopt?company_id=2',headers=h).status_code==200
 bank=c.get('/api/competency-bank').json
 assert len(bank)==281
 assert not any(r['code'].startswith('LEGACY-') for r in bank)
 assert next(r for r in c.get('/api/data/skills?company_id=2').json if r['code']=='S001')['title']=='Holding revised title'
 assert c.get('/api/data/requirements?company_id=2').json

def login_company_user(c,h,role='manager'):
 assert c.post('/api/users',headers=h,json={'username':'company-'+role,'password':'Secure-password-123','role':role,'company_id':1}).status_code==201
 c.post('/api/logout',headers=h)
 assert c.post('/api/login',json={'username':'company-'+role,'password':'Secure-password-123'}).status_code==200
 return {'X-CSRF-Token':c.get('/api/me').json['csrf']}

def test_admin_bank_crud_removes_only_unused_company_selections(ready):
 app,c,h=ready
 assert c.post('/api/competency-bank',headers=h,json={'code':'NEW','title':'New competency','category':'Technical','importance':'3','method':'Review','reference':'Policy'}).status_code==201
 ids=[]
 for cid in (1,2):
  response=c.post(f'/api/competency-bank/NEW/select?company_id={cid}',headers=h)
  assert response.status_code==200,response.json
  ids.append(response.json['id'])
 assert ids[0]!=ids[1]
 assert c.put('/api/competency-bank/NEW',headers=h,json={'title':'Shared revised title','category':'Safety','importance':2}).status_code==200
 for cid,sid in zip((1,2),ids):
  local=c.get(f'/api/data/skills?company_id={cid}').json
  assert [(r['id'],r['title'],r['importance']) for r in local]==[(sid,'Shared revised title',2)]
 assert c.delete('/api/competency-bank/NEW',headers=h).status_code==200
 assert not any(r['code']=='NEW' for r in c.get('/api/competency-bank').json)
 for cid in (1,2):assert c.get(f'/api/data/skills?company_id={cid}').json==[]
 with sqlite3.connect(app.config['DB']) as conn:
  assert conn.execute('SELECT count(*) FROM competency_links WHERE code=?',('NEW',)).fetchone()[0]==0
  assert conn.execute("SELECT count(*) FROM audit WHERE action='delete_holding_competency' AND detail='NEW'").fetchone()[0]==1
  assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
 assert c.delete('/api/competency-bank/NEW',headers=h).status_code==404

@pytest.mark.parametrize('payload',[
 {'code':'BAD','title':'   '}, {'code':'BAD','title':None}, {'code':'BAD/PATH','title':'Invalid code'},
 {'code':'BAD','title':'Invalid importance','importance':1.5}, {'code':'BAD','title':'Invalid importance','importance':True},
 {'code':'BAD','title':'Invalid importance','importance':None}, {'code':'BAD','title':'Invalid category','category':['Safety']},
 ['BAD','Invalid payload'],
])
def test_bank_creation_rejects_malformed_data(ready,payload):
 app,c,h=ready
 assert c.post('/api/competency-bank',headers=h,json=payload).status_code==400
 assert not any(r['code'].startswith('BAD') for r in c.get('/api/competency-bank').json)

def test_bank_code_is_stable_and_edits_are_validated(ready):
 app,c,h=ready
 before=next(r for r in c.get('/api/competency-bank').json if r['code']=='S001')
 for payload in ({'code':'NEW','title':'Rename code'}, {'title':None}, {'importance':2.5}, {'importance':False}, {'category':None}, ['Invalid']):
  assert c.put('/api/competency-bank/S001',headers=h,json=payload).status_code==400
 assert next(r for r in c.get('/api/competency-bank').json if r['code']=='S001')==before

def test_company_can_select_and_use_but_cannot_change_shared_bank(ready):
 app,c,h=ready
 assert c.post('/api/competency-bank',headers=h,json={'code':'CHOOSE','title':'Holding competency'}).status_code==201
 before=c.get('/api/competency-bank').json
 h=login_company_user(c,h)
 for method,url,body in [
  ('post','/api/competency-bank',{'code':'COMPANY','title':'Company addition'}),
  ('put','/api/competency-bank/CHOOSE',{'title':'Company revision'}),
  ('delete','/api/competency-bank/CHOOSE',None),
  ('post','/api/data/skills?company_id=1',{'code':'COMPANY','title':'Legacy addition'}),
 ]:
  assert getattr(c,method)(url,headers=h,json=body).status_code==403
 response=c.post('/api/competency-bank/CHOOSE/select?company_id=1',headers=h)
 assert response.status_code==200,response.json
 sid=response.json['id']
 assert c.post('/api/competency-bank/CHOOSE/select?company_id=1',headers=h).json['id']==sid
 assert len(c.get('/api/data/skills?company_id=1').json)==1
 for method in ('put','delete'):
  assert getattr(c,method)(f'/api/data/skills/{sid}?company_id=1',headers=h,json={'title':'Legacy revision'}).status_code==403
 assert c.post('/api/competency-bank/CHOOSE/select?company_id=2',headers=h).status_code==403
 assert c.post('/api/competency-bank/UNKNOWN/select?company_id=1',headers=h).status_code==404
 assert c.post('/api/competency-bank/S001/select?company_id=1').status_code==403
 assert c.post('/api/data/requirements?company_id=1',headers=h,json={'job':'Engineer','skill_id':sid,'level':3}).status_code==201
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'EMP','name':'Company person','job':'Engineer'}).json['id']
 assert c.put(f'/api/assessment-workspace/{eid}?company_id=1',headers=h,json={'items':[{'code':'CHOOSE','level':1}]}).status_code==200
 assert c.get('/api/needs-summary?company_id=1').json[0]['code']=='CHOOSE'
 assert c.get('/api/competency-bank').json==before
 assert c.get('/api/data/requirements?company_id=1').json[0]['skill_id']==sid

def test_company_excel_import_cannot_add_to_shared_bank(ready):
 from openpyxl import Workbook
 app,c,h=ready
 h=login_company_user(c,h)
 before=c.get('/api/competency-bank').json
 workbook=Workbook();sheet=workbook.active;sheet.title='کاتالوگ_مهارت'
 sheet.cell(6,2,'IMPORT-NEW');sheet.cell(6,3,'Unauthorized imported competency')
 content=io.BytesIO();workbook.save(content);content.seek(0)
 response=c.post('/api/import?company_id=1',headers=h,data={'file':(content,'company.xlsx')})
 assert response.status_code==400,response.json
 assert c.get('/api/competency-bank').json==before
 assert c.get('/api/data/skills?company_id=1').json==[]

def test_viewer_remains_read_only(ready):
 app,c,h=ready
 sid=c.post('/api/competency-bank/S001/select?company_id=1',headers=h).json['id']
 h=login_company_user(c,h,'viewer')
 assert c.get('/api/competency-bank').status_code==200
 assert c.get('/api/data/skills?company_id=1').status_code==200
 for method,url,body in [
  ('post','/api/competency-bank/S002/select?company_id=1',None),
  ('post','/api/competency-bank/adopt?company_id=1',None),
  ('post','/api/competency-bank',{'code':'VIEWER','title':'Viewer addition'}),
  ('put','/api/competency-bank/S001',{'title':'Viewer revision'}),
  ('delete','/api/competency-bank/S001',None),
  ('post','/api/data/skills?company_id=1',{'code':'VIEWER','title':'Legacy addition'}),
  ('put',f'/api/data/skills/{sid}?company_id=1',{'title':'Legacy revision'}),
  ('delete',f'/api/data/skills/{sid}?company_id=1',None),
  ('put','/api/job-workspace?company_id=1&job=Engineer',{'items':[{'code':'S001','level':3}]}),
 ]:
  assert getattr(c,method)(url,headers=h,json=body).status_code==403

@pytest.mark.parametrize('reference',['requirements','assessments','courses','needs','personal_requirements','future_history'])
def test_bank_delete_preserves_referenced_data_and_all_company_links(ready,reference):
 app,c,h=ready
 assert c.post('/api/competency-bank',headers=h,json={'code':'USED','title':'Keep history'}).status_code==201
 for cid in (1,2):assert c.post(f'/api/competency-bank/USED/select?company_id={cid}',headers=h).status_code==200
 sid=c.get('/api/data/skills?company_id=2').json[0]['id']
 eid=c.post('/api/data/employees?company_id=2',headers=h,json={'code':'HISTORY','name':'Historical person'}).json['id']
 with sqlite3.connect(app.config['DB']) as conn:
  if reference=='requirements':conn.execute('INSERT INTO requirements(company_id,job,skill_id,level) VALUES (2,?,?,3)',('Engineer',sid))
  elif reference=='assessments':conn.execute('INSERT INTO assessments(company_id,employee_id,skill_id,level) VALUES (2,?,?,2)',(eid,sid))
  elif reference=='courses':conn.execute("INSERT INTO courses(company_id,skill_id,title,date,hours) VALUES (2,?,'Historical course','1405/07/23',2)",(sid,))
  elif reference=='needs':conn.execute("INSERT INTO needs(company_id,skill_id,reason) VALUES (2,?,'Training need')",(sid,))
  elif reference=='personal_requirements':conn.execute('INSERT INTO personal_requirements(employee_id,skill_id,level) VALUES (?,?,0)',(eid,sid))
  else:
   conn.execute('CREATE TABLE future_history(code TEXT REFERENCES holding_competencies(code) ON DELETE CASCADE,note TEXT)')
   conn.execute("INSERT INTO future_history VALUES ('USED','Preserve cascading history too')")
  conn.commit()
  tables=['holding_competencies','skills','competency_links',reference,'audit']
  before={table:conn.execute('SELECT * FROM '+table).fetchall() for table in tables}
 response=c.delete('/api/competency-bank/USED',headers=h)
 assert response.status_code==409,response.json
 assert 'قابل حذف نیست' in response.json['error']
 with sqlite3.connect(app.config['DB']) as conn:
  assert {table:conn.execute('SELECT * FROM '+table).fetchall() for table in tables}==before
  assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]
 # The rejected transaction also releases its lock, leaving normal edits usable.
 assert c.put('/api/competency-bank/USED',headers=h,json={'title':'Updated without losing history'}).status_code==200

@pytest.mark.parametrize('role',['admin','manager'])
def test_template_adoption_respects_admin_deletions(ready,role):
 app,c,h=ready
 assert c.delete('/api/competency-bank/S001',headers=h).status_code==200
 if role=='manager':h=login_company_user(c,h)
 response=c.post('/api/competency-bank/adopt?company_id=1',headers=h)
 assert response.status_code==200,response.json
 assert len(c.get('/api/competency-bank').json)==280
 assert not any(r['code']=='S001' for r in c.get('/api/data/skills?company_id=1').json)
 assert c.get('/api/data/requirements?company_id=1').json

def test_deleted_empty_bank_is_not_reseeded_on_restart(ready):
 app,c,h=ready
 # Use the same endpoint for the last entry, after simulating an earlier batch
 # of successful unused-entry deletions.
 with sqlite3.connect(app.config['DB']) as conn:
  conn.execute("DELETE FROM holding_competencies WHERE code!='S001'")
 assert c.delete('/api/competency-bank/S001',headers=h).status_code==200
 upgraded=create_app(Path(app.config['DB']).parent,True)
 with sqlite3.connect(upgraded.config['DB']) as conn:
  assert conn.execute('SELECT count(*) FROM holding_competencies').fetchone()[0]==0
  assert conn.execute('SELECT count(*) FROM skills').fetchone()[0]==0

def test_old_catalog_upgrade_sets_seed_marker_without_replacing_bank(ready):
 app,c,h=ready
 with sqlite3.connect(app.config['DB']) as conn:
  conn.execute('DROP TABLE holding_catalog_state')
  conn.execute("DELETE FROM holding_competencies WHERE code!='S001'")
 create_app(Path(app.config['DB']).parent,True)
 with sqlite3.connect(app.config['DB']) as conn:
  assert conn.execute('SELECT count(*) FROM holding_competencies').fetchone()[0]==1
  assert conn.execute("SELECT value FROM holding_catalog_state WHERE key='initial_seed_complete'").fetchone()[0]=='1'
