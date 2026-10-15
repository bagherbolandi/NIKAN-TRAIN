import io,json,sqlite3
from pathlib import Path
import pytest
from app import create_app
@pytest.fixture
def ready(tmp_path):
 app=create_app(tmp_path,True);c=app.test_client();c.post('/api/setup',json={'username':'admin','password':'Secure-password-123'});c.post('/api/login',json={'username':'admin','password':'Secure-password-123'});return app,c,{'X-CSRF-Token':c.get('/api/me').json['csrf']}
def test_bank_job_passport(ready):
 app,c,h=ready;bank=c.get('/api/competency-bank').json;assert len(bank)==281
 r=c.post('/api/competency-bank/adopt?company_id=1',headers=h);assert r.status_code==200,r.json
 jobs=c.get('/api/data/job_profiles?company_id=1').json;assert len(jobs)>80
 assert c.get('/api/dashboard?company_id=1').json['employees']==0
 assert c.get('/api/dashboard?company_id=2').json['skills']==0
 job=jobs[0]['job'];eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'E1','name':'Test','job':job}).json['id']
 r=c.get(f'/api/passports/{eid}?company_id=1');assert r.json['job']['job']==job
 assert c.put(f'/api/passports/{eid}/approval?company_id=1',headers=h,json={'job':job}).status_code==200
 assert c.get(f'/api/passports/{eid}?company_id=1').json['meta']['hr_approved']==1
 assert c.get('/api/management-report?company_id=1').json['unapproved']==0
 assert c.get(f'/api/passports/{eid}?company_id=2').status_code==404

def test_certificates_isolation_backup(ready,tmp_path):
 app,c,h=ready;eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'E1','name':'Test'}).json['id']
 pdf=b'%PDF-1.4\nTest certificate\n%%EOF'
 def upload(cid=1,payload=pdf):return c.post(f'/api/certificates?company_id={cid}',headers=h,data={'employee_id':str(eid),'title':'Safety certificate','file':(io.BytesIO(payload),'cert.pdf')})
 assert upload(2).status_code==400
 assert upload(payload=b'<script>bad</script>').status_code==400
 r=upload();assert r.status_code==201,r.json;rid=r.json['id']
 assert c.get(f'/api/certificates/{rid}?company_id=1').data==pdf
 assert c.get(f'/api/certificates/{rid}?company_id=2').status_code==404
 assert c.get(f'/api/passports/{eid}?company_id=1').json['certificates'][0]['title']=='Safety certificate'
 backup=c.post('/api/backup',headers=h);path=tmp_path/'backup.sqlite3';path.write_bytes(backup.data)
 with sqlite3.connect(path) as db:assert db.execute('SELECT content FROM certificates').fetchone()[0]==pdf
 c.post('/api/users',headers=h,json={'username':'reader','password':'Secure-password-123','role':'viewer','company_id':1})
 c.post('/api/logout',headers=h);c.post('/api/login',json={'username':'reader','password':'Secure-password-123'});h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
 assert c.delete(f'/api/certificates/{rid}?company_id=1',headers=h).status_code==403
 assert upload().status_code==403
 assert c.get(f'/api/certificates/{rid}?company_id=1').status_code==200

def test_import_preserves_assessment_provenance(ready):
 app,c,h=ready
 with next(Path('.').glob('*.xlsx')).open('rb') as f:r=c.post('/api/import?company_id=1',headers=h,data={'file':(f,'source.xlsx'),'personnel':'yes'})
 assert r.status_code==200,r.json;assert r.json['employee_metadata']>100
 employee=c.get('/api/data/employees?company_id=1').json[0]
 passport=c.get(f'/api/passports/{employee["id"]}?company_id=1').json
 assert passport['meta'];assert passport['meta']['assessment_status'];assert not passport['meta']['hr_approved']

@pytest.mark.parametrize('payload',[None,[],['Engineer'],{}, {'job':None}, {'job':['Engineer']}, {'job':{}}, {'job':'Missing job'}])
def test_invalid_job_approval_preserves_employee_metadata_and_audit(ready,payload):
 app,c,h=ready
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'SAFE','name':'Keep person','job':'Original job'}).json['id']
 with sqlite3.connect(app.config['DB']) as conn:
  tables=['employees','employee_meta','audit'];before={name:conn.execute('SELECT * FROM '+name).fetchall() for name in tables}
 response=c.put(f'/api/passports/{eid}/approval?company_id=1',headers=h,data='null' if payload is None else json.dumps(payload),content_type='application/json')
 assert response.status_code==400,response.get_data(as_text=True)
 with sqlite3.connect(app.config['DB']) as conn:
  assert {name:conn.execute('SELECT * FROM '+name).fetchall() for name in tables}==before

@pytest.mark.parametrize('field,value',[('employee_id','bad'),('employee_id','1.5'),('employee_id','0'),('employee_id','9'*100),('course_id','bad'),('course_id','1.5'),('course_id','0'),('course_id','9'*100)])
def test_invalid_certificate_ids_never_silently_create_unlinked_documents(ready,field,value):
 app,c,h=ready
 eid=c.post('/api/data/employees?company_id=1',headers=h,json={'code':'CERT','name':'Certificate owner'}).json['id']
 data={'employee_id':str(eid),'course_id':'','title':'Keep association','file':(io.BytesIO(b'%PDF-1.4\n%%EOF'),'cert.pdf'),field:value}
 response=c.post('/api/certificates?company_id=1',headers=h,data=data)
 assert response.status_code==400,response.get_data(as_text=True)
 assert c.get('/api/certificates?company_id=1').json==[]

@pytest.mark.parametrize('path',['/api/passports/{}/approval','/api/passports/{}','/api/certificates/{}','/api/assessment-workspace/{}'])
def test_oversize_route_identifiers_are_controlled(ready,path):
 app,c,h=ready;url=path.format('9'*100)+'?company_id=1'
 response=c.put(url,headers=h,json={'job':'Engineer'}) if path.endswith('/approval') else c.get(url)
 assert response.status_code==400
