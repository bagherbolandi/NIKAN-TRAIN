import io
import json
import sqlite3
import openpyxl
from app import create_app
from app.documents import FIELDS,save_personal_document
from test_catalog import ready

def test_master_and_personal_documents_are_independent(ready):
 app,c,h=ready
 jid=c.post('/api/data/job_profiles?company_id=1',headers=h,json={'job':'Engineer','definition':'Master definition','commitments':'Master commitments'}).json['id']
 master=c.get('/api/data/job_profiles?company_id=1').json[0]
 assert master['document_number'].startswith('NT-J-')
 ids=[c.post('/api/data/employees?company_id=1',headers=h,json={'code':str(i),'name':f'Person {i}','job':'Engineer'}).json['id'] for i in range(2)]
 p=f'/api/passports/{ids[0]}/document?company_id=1'
 assert c.put(p,headers=h,json={'overrides':{'duties':'Individual duties','commitments':'Individual commitments'}}).status_code==200
 first=c.get(f'/api/passports/{ids[0]}?company_id=1').json
 assert first['job']['commitments']=='Individual commitments'
 assert c.get(f'/api/passports/{ids[1]}?company_id=1').json['job']['commitments']=='Master commitments'
 assert c.get('/api/data/job_profiles?company_id=1').json[0]['commitments']=='Master commitments'
 assert c.put(f'/api/data/job_profiles/{jid}?company_id=1',headers=h,json={'definition':'Revised'}).status_code==200
 assert c.get(p).json['master']['document_number']==master['document_number']
 assert c.get(p).json['effective']['definition']=='Master definition'
 assert c.get(p).json['master']['definition']=='Revised'
 assert c.get(p).json['is_snapshot'] and not c.get(p).json['requires_review']
 assert c.put(p,headers=h,json={'overrides':{'company_id':'2'}}).status_code==400
 assert c.get(p.replace('company_id=1','company_id=2')).status_code==404
 c.delete(p,headers=h)
 assert c.get(p).json['effective']['commitments']=='Master commitments'

def test_personal_requirements_and_ordered_excel(ready):
 app,c,h=ready
 ids=[c.post('/api/data/employees?company_id=1',headers=h,json={'code':str(i),'name':f'Person {i}','job':'Engineer'}).json['id'] for i in range(2)]
 c.put('/api/job-workspace?company_id=1&job=Engineer',headers=h,json={'items':[{'code':'S001','level':3},{'code':'S002','level':2}]})
 p=f'/api/passports/{ids[0]}/requirements?company_id=1'
 assert c.put(p,headers=h,json={'items':[{'code':'S001','level':0},{'code':'S002','level':1}]}).status_code==200
 gaps=c.get('/api/gaps?company_id=1').json
 assert len([r for r in gaps if r['employee_id']==ids[0]])==1
 assert len([r for r in gaps if r['employee_id']==ids[1]])==2
 assert next(r for r in c.get(f'/api/passports/{ids[0]}?company_id=1').json['matrix'] if r['code']=='S002')['required']==1
 assert c.put(p,headers=h,json={'items':[{'code':'S002','level':4},{'code':'MISSING','level':1}]}).status_code==400
 assert next(r for r in c.get(p).json['matrix'] if r['code']=='S002')['level']==1
 response=c.get('/api/export.xlsx?company_id=1');assert response.status_code==200
 w=openpyxl.load_workbook(io.BytesIO(response.data));assert len(w.sheetnames)==3
 assert w['افراد'].max_row==3;assert w['شایستگی‌ها'].max_row==3
 assert w['افراد'].sheet_view.rightToLeft;assert w['افراد'].freeze_panes=='A2'
 c.delete(p,headers=h);assert len(c.get('/api/gaps?company_id=1').json)==4


def test_short_personal_reference_is_stable_and_deleted_identity_is_not_reissued(ready):
 app,c,h=ready
 employee=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'SHORT-1','name':'First person'}).json['id']
 path=f'/api/passports/{employee}/document?company_id=1'
 document=c.get(path).json
 assert document['number']=='NP-01-000001'
 assert document['legacy_number']==''
 assert c.put(f'/api/data/employees/{employee}?company_id=1',headers=h,json={'job':'New role'}).status_code==200
 assert c.put('/api/companies/1',headers=h,json={'name':'Renamed company'}).status_code==200
 assert c.get(path).json['number']==document['number']
 assert c.delete(f'/api/data/employees/{employee}?company_id=1',headers=h).status_code==200
 replacement=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'SHORT-2','name':'Different person'}).json['id']
 assert replacement==employee  # SQLite may reuse the primary key; the contract reference cannot.
 assert c.get(f'/api/passports/{replacement}/document?company_id=1').json['number']=='NP-01-000002'
 with sqlite3.connect(app.config['DB']) as connection:
  before=connection.execute('SELECT passport_high_watermark FROM company_registry').fetchone()[0]
 assert c.post('/api/data/employees?company_id=1',headers=h,json={'code':'SHORT-2','name':'Duplicate'}).status_code==400
 with sqlite3.connect(app.config['DB']) as connection:
  assert connection.execute('SELECT passport_high_watermark FROM company_registry').fetchone()[0]==before


def test_legacy_reference_migration_alias_collision_custom_values_and_backup(tmp_path):
 database=tmp_path/'nikan.sqlite3'
 old_reference='NT-P-'+'A'*24
 other_reference='NT-P-'+'B'*24
 with sqlite3.connect(database) as connection:
  connection.executescript('''
  CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL);
  INSERT INTO companies VALUES (1,'Legacy company'),(2,'Other company');
  CREATE TABLE employees(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),code TEXT NOT NULL,name TEXT NOT NULL,department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',passport_number TEXT NOT NULL DEFAULT '',UNIQUE(company_id,code));
  ''')
  connection.executemany('INSERT INTO employees(id,company_id,code,name,passport_number) VALUES (?,?,?,?,?)',[
   (1,1,'OLD1','Old first person',old_reference),
   (2,1,'CUSTOM-NP','Custom short reference','NP-01-000001'),
   (3,1,'CUSTOM','Custom contract reference','CONTRACT-REFERENCE-77'),
   (4,2,'OLD2','Old second person',other_reference),
  ])
 app=create_app(tmp_path,True);c=app.test_client()
 assert c.post('/api/setup',json={'username':'admin','password':'Secure-password-123'}).status_code==200
 assert c.post('/api/login',json={'username':'admin','password':'Secure-password-123'}).status_code==200
 h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
 references={row['id']:(row['passport_number'],row['passport_legacy_number']) for cid in (1,2) for row in c.get(f'/api/data/employees?company_id={cid}').json}
 assert references=={1:('NP-01-000005',old_reference),2:('NP-01-000001',''),3:('CONTRACT-REFERENCE-77',''),4:('NP-02-000004',other_reference)}
 assert c.get('/api/passports/1/document?company_id=1').json['legacy_number']==old_reference
 backup=c.post('/api/backup',headers=h)
 restored=tmp_path/'restored';restored.mkdir();(restored/'nikan.sqlite3').write_bytes(backup.data)
 for directory in (tmp_path,restored):
  current=create_app(directory,True).test_client()
  assert current.post('/api/login',json={'username':'admin','password':'Secure-password-123'}).status_code==200
  after={row['id']:(row['passport_number'],row['passport_legacy_number']) for cid in (1,2) for row in current.get(f'/api/data/employees?company_id={cid}').json}
  assert after==references
  assert current.get('/api/passports/1/document?company_id=1').json['number']=='NP-01-000005'
  assert current.get('/api/passports/1/document?company_id=1').json['legacy_number']==old_reference


def test_existing_custom_aliases_advance_counter_and_explicit_numbers_are_preserved(ready):
 app,c,h=ready
 with sqlite3.connect(app.config['DB']) as connection:
  connection.execute("INSERT INTO employees(company_id,code,name,passport_number,passport_legacy_number) VALUES (1,'CUSTOM','Custom person','NP-01-000800','NP-02-000850')")
  row=connection.execute("SELECT passport_number,passport_legacy_number FROM employees WHERE code='CUSTOM'").fetchone()
  assert row==('NP-01-000800','NP-02-000850')
 employee=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'NEXT','name':'Next person'}).json['id']
 assert c.get(f'/api/passports/{employee}/document?company_id=1').json['number']=='NP-01-000851'
 with sqlite3.connect(app.config['DB']) as connection:
  connection.execute("UPDATE employees SET passport_legacy_number='NP-01-000900' WHERE code='CUSTOM'")
 later=c.post('/api/data/employees?company_id=2',headers=h,json={'code':'LATER','name':'Later person'}).json['id']
 assert c.get(f'/api/passports/{later}/document?company_id=2').json['number']=='NP-02-000901'


def test_old_client_legacy_insert_after_deletion_cannot_reuse_retired_short_reference(ready, tmp_path):
 app,c,h=ready
 employee=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'FIRST','name':'First person'}).json['id']
 assert c.get(f'/api/passports/{employee}/document?company_id=1').json['number']=='NP-01-000001'
 assert c.delete(f'/api/data/employees/{employee}?company_id=1',headers=h).status_code==200
 legacy='NT-P-'+'C'*24
 with sqlite3.connect(app.config['DB']) as connection:
  connection.execute('INSERT INTO employees(id,company_id,code,name,passport_number) VALUES (?,1,?,?,?)',(employee,'OLD-CLIENT','Different legacy person',legacy))
  connection.execute("UPDATE employees SET passport_legacy_number='KEEP-FIRST-ALIAS' WHERE id=?",(employee,))
 current=create_app(tmp_path,True).test_client()
 assert current.post('/api/login',json={'username':'admin','password':'Secure-password-123'}).status_code==200
 document=current.get(f'/api/passports/{employee}/document?company_id=1').json
 assert document['number']=='NP-01-000002'
 assert document['legacy_number']=='KEEP-FIRST-ALIAS'


def test_personal_actual_snapshot_freezes_full_ideal_format_including_explicit_blanks(ready):
 _,c,h=ready
 jid=c.post('/api/data/job_profiles?company_id=1',headers=h,json={
  'job':'Engineer','definition':'Ideal definition','duties':'Ideal duties','hse':'Ideal HSE',
  'contract_notes':'Ideal terms',
 }).json['id']
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'ACTUAL','name':'Actual person','job':'Engineer'}).json['id']
 path=f'/api/passports/{eid}/document?company_id=1'
 proposed=c.get(path).json
 assert proposed['requires_review'] and not proposed['is_snapshot']
 assert proposed['effective']['duties']=='Ideal duties'
 saved=c.put(path,headers=h,json={'overrides':{'duties':'Actual duties','hse':''}})
 assert saved.status_code==200
 actual=saved.json
 assert actual['is_snapshot'] and not actual['requires_review']
 assert set(actual['overrides'])==set(FIELDS)
 assert actual['effective']['hse']==''
 assert actual['effective']['definition']=='Ideal definition'
 assert c.put(f'/api/data/job_profiles/{jid}?company_id=1',headers=h,json={
  'definition':'Revised ideal','duties':'New ideal duties','hse':'New ideal HSE','contract_notes':'New ideal terms',
 }).status_code==200
 after=c.get(path).json
 assert {field:after['effective'][field] for field in FIELDS}==actual['overrides']
 assert after['master']['definition']=='Revised ideal'
 assert after['effective']['duties']=='Actual duties'
 assert after['effective']['hse']==''
 assert after['number']==actual['number']
 assert after['revision']==actual['revision']


def test_legacy_partial_actual_document_is_not_rewritten_until_explicit_review(ready, tmp_path):
 app,c,h=ready
 jid=c.post('/api/data/job_profiles?company_id=1',headers=h,json={
  'job':'Engineer','definition':'Old ideal','duties':'Ideal duties','commitments':'Ideal commitment',
 }).json['id']
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'LEGACY-PARTIAL','name':'Legacy person','job':'Engineer'}).json['id']
 path=f'/api/passports/{eid}/document?company_id=1'
 legacy=json.dumps({'duties':'Existing actual duties'})
 with sqlite3.connect(app.config['DB']) as connection:
  connection.execute('ALTER TABLE personal_documents DROP COLUMN is_snapshot')
  connection.execute('INSERT INTO personal_documents(employee_id,company_id,overrides,revision) VALUES (?,?,?,3)',(eid,1,legacy))
  original=connection.execute('SELECT employee_id,company_id,overrides,revision,updated FROM personal_documents').fetchone()
 current=create_app(tmp_path,True).test_client()
 assert current.post('/api/login',json={'username':'admin','password':'Secure-password-123'}).status_code==200
 headers={'X-CSRF-Token':current.get('/api/me').json['csrf']}
 document=current.get(path).json
 assert document['requires_review'] and not document['is_snapshot']
 assert document['overrides']=={'duties':'Existing actual duties'}
 assert current.put(f'/api/data/job_profiles/{jid}?company_id=1',headers=headers,json={'definition':'Current ideal'}).status_code==200
 assert current.get(path).json['effective']['definition']=='Current ideal'
 with sqlite3.connect(app.config['DB']) as connection:
  assert connection.execute('SELECT employee_id,company_id,overrides,revision,updated FROM personal_documents').fetchone()==original
  assert connection.execute('SELECT is_snapshot FROM personal_documents').fetchone()[0]==0
 reviewed=current.put(path,headers=headers,json={'overrides':{'commitments':'Reviewed actual commitment'}}).json
 assert reviewed['is_snapshot'] and not reviewed['requires_review']
 assert reviewed['revision']==4
 assert reviewed['effective']['definition']=='Current ideal'
 assert reviewed['effective']['duties']=='Existing actual duties'
 assert current.put(f'/api/data/job_profiles/{jid}?company_id=1',headers=headers,json={'definition':'Future ideal'}).status_code==200
 assert current.get(path).json['effective']['definition']=='Current ideal'
 assert current.delete(path,headers=headers).status_code==200
 reset=current.get(path).json
 assert reset['requires_review'] and not reset['is_snapshot']
 assert reset['effective']['definition']=='Future ideal'
 assert reset['overrides']=={}


def test_shared_actual_document_saver_keeps_transaction_ownership_with_caller(ready):
 app,c,h=ready
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'TRANSACTION','name':'Transactional person'}).json['id']
 with sqlite3.connect(app.config['DB']) as connection:
  connection.row_factory=sqlite3.Row
  connection.execute('BEGIN')
  save_personal_document(connection,1,eid,{'duties':'Uncommitted actual duties'})
  assert connection.execute('SELECT is_snapshot FROM personal_documents WHERE employee_id=?',(eid,)).fetchone()[0]==1
  connection.rollback()
  assert connection.execute('SELECT count(*) FROM personal_documents WHERE employee_id=?',(eid,)).fetchone()[0]==0
 assert c.get(f'/api/passports/{eid}/document?company_id=1').json['requires_review']


def test_custom_job_document_reference_is_preserved_on_insert_and_restart(ready, tmp_path):
 app,c,h=ready
 with sqlite3.connect(app.config['DB']) as connection:
  jid=connection.execute("INSERT INTO job_profiles(company_id,job,document_number) VALUES (1,'Custom role','JOB-CONTRACT-77')").lastrowid
  assert connection.execute('SELECT document_number FROM job_profiles WHERE id=?',(jid,)).fetchone()[0]=='JOB-CONTRACT-77'
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'CUSTOM-JOB','name':'Custom job person','job':'Custom role'}).json['id']
 path=f'/api/passports/{eid}/document?company_id=1'
 assert c.get(path).json['master_number']=='JOB-CONTRACT-77'
 current=create_app(tmp_path,True).test_client()
 assert current.post('/api/login',json={'username':'admin','password':'Secure-password-123'}).status_code==200
 assert current.get(path).json['master_number']=='JOB-CONTRACT-77'
