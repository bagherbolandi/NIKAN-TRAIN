import sqlite3
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
