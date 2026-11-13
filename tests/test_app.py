import io,sqlite3
from pathlib import Path
import pytest
from app import create_app, COMPANIES
@pytest.fixture
def ready(tmp_path):
 app=create_app(tmp_path,testing=True);c=app.test_client();assert c.post('/api/setup',json={'username':'admin','password':'Secure-password-123'}).status_code==200
 assert c.post('/api/login',json={'username':'admin','password':'Secure-password-123'}).status_code==200
 csrf=c.get('/api/me').json['csrf'];return app,c,{'X-CSRF-Token':csrf}
def post(c,h,t,d,cid=1):return c.post(f'/api/data/{t}?company_id={cid}',json=d,headers=h)
def test_isolation_and_csrf(ready):
 app,c,h=ready
 assert c.post('/api/data/employees?company_id=1',json={'code':'x','name':'X'}).status_code==403
 e=post(c,h,'employees',{'code':'1','name':'Employee'}).json['id']
 s=post(c,h,'skills',{'code':'S','title':'Skill'},2).json['id']
 assert post(c,h,'assessments',{'employee_id':e,'skill_id':s,'level':2}).status_code==400
 assert c.post('/api/users',json={'username':'manager','password':'Secure-password-123','role':'manager','company_id':1},headers=h).status_code==201
 c.post('/api/logout',headers=h);c.post('/api/login',json={'username':'manager','password':'Secure-password-123'});h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
 assert c.get('/api/data/employees?company_id=2').status_code==403
 assert post(c,h,'employees',{'code':'2','name':'Y'},2).status_code==403
 assert c.delete(f'/api/data/employees/{e}?company_id=2',headers=h).status_code==403
 assert c.post('/api/backup',headers=h).status_code==403
 assert c.get('/api/users').status_code==403

def test_training_cycle(ready):
 app,c,h=ready
 e=post(c,h,'employees',{'code':'1','name':'Ali','job':'Engineer'}).json['id'];s=post(c,h,'skills',{'code':'S1','title':'Safety'}).json['id']
 assert post(c,h,'requirements',{'job':'Engineer','skill_id':s,'level':3}).status_code==201
 assert post(c,h,'assessments',{'employee_id':e,'skill_id':s,'level':1}).status_code==201
 assert c.get('/api/gaps?company_id=1').json[0]['gap']==2
 course=post(c,h,'courses',{'skill_id':s,'title':'Safety','date':'1405/03/25','hours':4,'cost':5}).json['id']
 assert post(c,h,'records',{'employee_id':e,'course_id':course,'level':3,'score':90,'reaction':4,'behavior':4,'result':80}).status_code==201
 assert len(c.get('/api/gaps?company_id=1').json)==1
 assert c.put(f'/api/data/courses/{course}?company_id=1',json={'status':'completed'},headers=h).status_code==200
 # The uploaded 0.11.2 source deliberately gives the explicit current
 # assessment precedence over an older completed course result.
 assert c.get('/api/gaps?company_id=1').json[0]['current']==1
 assessment=c.get('/api/data/assessments?company_id=1').json[0]['id']
 assert c.put(f'/api/data/assessments/{assessment}?company_id=1',json={'level':3},headers=h).status_code==200
 assert c.get('/api/gaps?company_id=1').json==[]
 backup=c.post('/api/backup',headers=h);assert backup.data.startswith(b'SQLite format 3')
 assert c.get('/api/audit?company_id=1').json

def test_auth_and_viewer(ready):
 app,c,h=ready
 assert c.post('/api/setup',json={}).status_code==403
 assert c.post('/api/users',json={'username':'reader','password':'Secure-password-123','role':'viewer','company_id':1},headers=h).status_code==201
 c.post('/api/logout',headers=h);assert c.get('/api/me').status_code==401
 c.post('/api/login',json={'username':'reader','password':'Secure-password-123'});h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
 assert post(c,h,'skills',{'code':'S','title':'X'}).status_code==403
 assert c.get('/api/dashboard?company_id=1').status_code==200
 for _ in range(5):assert c.post('/api/login',json={'username':'bad','password':'wrong'}).status_code==401
 assert c.post('/api/login',json={'username':'bad','password':'wrong'}).status_code==429

def test_excel_import_repeatable(ready):
 app,c,h=ready;p=next(Path('.').glob('*.xlsx'))
 def run():
  with p.open('rb') as f:return c.post('/api/import?company_id=1',data={'file':(io.BytesIO(f.read()),'source.xlsx'),'personnel':'yes'},headers=h)
 r=run();assert r.status_code==200,r.json;assert r.json['employees']>100;assert r.json['skills']>200;assert r.json['requirements']>1000
 r=run();assert r.status_code==200;assert all(v==0 for v in r.json.values())
 assert c.get('/api/dashboard?company_id=2').json['employees']==0
 assert len(c.get('/api/gaps?company_id=1').json)>100

def test_validation_and_user_revocation(ready):
 app,c,h=ready
 s=post(c,h,'skills',{'code':'S','title':'Skill'}).json['id']
 assert post(c,h,'courses',{'skill_id':s,'title':'X','date':'bad','hours':1}).status_code==400
 assert post(c,h,'courses',{'skill_id':s,'title':'X','date':'1405/07/31','hours':1}).status_code==400
 assert post(c,h,'courses',{'skill_id':s,'title':'X','date':'۱۴۰۵/۰۳/۲۵','hours':1}).status_code==201
 assert post(c,h,'courses',{'skill_id':s,'title':'X','date':'1405/01/01','hours':'NaN'}).status_code==400
 assert c.post('/api/logout',headers={**h,'Origin':'https://evil.example'}).status_code==403
 assert c.put('/api/users/1',headers=h,json={'active':0}).status_code==400
 c.post('/api/users',headers=h,json={'username':'reader','password':'Secure-password-123','role':'viewer','company_id':1})
 other=app.test_client();other.post('/api/login',json={'username':'reader','password':'Secure-password-123'})
 uid=c.get('/api/users').json[-1]['id']
 assert c.put(f'/api/users/{uid}',headers=h,json={'active':0}).status_code==200
 assert other.get('/api/me').status_code==401

def test_backup_restore_and_export_safety(ready,tmp_path):
 app,c,h=ready
 post(c,h,'employees',{'code':'1','name':'=HYPERLINK("evil")','job':'Engineer'})
 sid=post(c,h,'skills',{'code':'S','title':'Safety'}).json['id']
 post(c,h,'requirements',{'job':'Engineer','skill_id':sid,'level':3})
 export=c.get('/api/export?company_id=1');assert b"'=HYPERLINK" in export.data
 backup=c.post('/api/backup',headers=h).data;path=tmp_path/'restored.sqlite3';path.write_bytes(backup)
 with sqlite3.connect(path) as db:
  assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
  assert db.execute('SELECT COUNT(*) FROM employees').fetchone()[0]==1
  assert db.execute('SELECT COUNT(*) FROM companies').fetchone()[0]==len(COMPANIES)
