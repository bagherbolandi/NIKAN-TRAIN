"""HSE records reuse real staff/training and keep occupational health private."""
import io
import json
import sqlite3
from datetime import date

import pytest
from app import create_app

PASSWORD='Management-only-password-123'


def login(app,username):
    client=app.test_client()
    response=client.post('/api/login',json={'username':username,'password':PASSWORD})
    assert response.status_code==200,response.json
    return client,{'X-CSRF-Token':client.get('/api/me').json['csrf']}


def post(client,headers,path,data,status=201):
    response=client.post('/api/hrm/'+path,headers=headers,json=data)
    assert response.status_code==status,(path,response.status_code,response.json)
    return response.json


def changes(path):
    with sqlite3.connect(path) as conn:
        return conn.execute('SELECT count(*) FROM audit').fetchone()[0]


@pytest.fixture
def hse(tmp_path,monkeypatch):
    import app.hrm_hse as module
    monkeypatch.setattr(module,'today_iso',lambda:'2026-11-12')
    app=create_app(tmp_path,testing=True)
    admin=app.test_client()
    assert admin.post('/api/setup',json={'username':'administrator','password':PASSWORD}).status_code==200
    admin,ah=login(app,'administrator')
    ids=[]
    for code,cid,job,department,location in [('1001',1,'Engineer','Production','Line A'),('1002',1,'Engineer','Office','Line B'),('1003',1,'Technician','Production','Line A'),('2001',2,'Engineer','Production','Line A')]:
        r=admin.post('/api/data/employees?company_id='+str(cid),headers=ah,json={'code':code,'name':'Synthetic person '+code,'job':job,'department':department})
        assert r.status_code==201,r.json
        ids.append(r.json['id'])
        with sqlite3.connect(app.config['DB']) as conn:
            conn.execute('INSERT INTO hrm_employee_details(employee_id,company_id,job,department,location) VALUES (?,?,?,?,?)',(ids[-1],cid,job,department,location))
    specs=[('hse-officer','hse_officer',1,'',{'hse':{'read':True,'write':True}}),
           ('medical-manager','company_hr',1,'',{'health':{'read':True,'write':True}}),
           ('other-company','hse_officer',2,'',{'hse':{'read':True,'write':True},'health':{'read':True,'write':True}}),
           ('production-head','unit_manager',1,'Production',{'hse':{'read':True,'write':True},'health':{'read':True,'write':True}})]
    for username,role,cid,department,permissions in specs:
        post(admin,ah,'access/users',{'username':username,'password':PASSWORD,'role':role,'scopes':[{'company_id':cid,'department':department}],'permissions':permissions})
    actors={name:login(app,name) for name,*_ in specs}
    return {'app':app,'admin':admin,'ah':ah,'ids':ids,'actors':actors,'root':tmp_path}


def equipment(hse,company=1,**values):
    client,headers=hse['actors']['hse-officer' if company==1 else 'other-company']
    return post(client,headers,'ppe_catalog?company_id='+str(company),{'name':'Safety helmet','category':'Head protection',**values})


def requirement(hse,ppe_id,**values):
    client,headers=hse['actors']['hse-officer']
    return post(client,headers,'ppe_requirements?company_id=1',{'job':'Engineer','activity_location':'Line A','ppe_id':ppe_id,'quantity':2,**values})


def issue(hse,ppe_id,**values):
    client,headers=hse['actors']['hse-officer']
    return post(client,headers,'ppe_issues?company_id=1',{'employee_id':hse['ids'][0],'ppe_id':ppe_id,'site_id':'','issue_date':'1405/08/21','quantity':1,**values})


def test_main_administrator_has_all_access_but_hse_delegation_has_no_medical_grant(hse):
    for resource in ['ppe_catalog','health_records','hse_report','health_report','hse_training_options']:
        assert hse['admin'].get('/api/hrm/'+resource+'?company_id=1').status_code==200
    officer,_=hse['actors']['hse-officer']
    assert officer.get('/api/hrm/health_records?company_id=1').status_code==403
    assert officer.get('/api/hrm/health_report?company_id=1').status_code==403
    options=officer.get('/api/hrm/hse_training_options')
    assert options.status_code==200 and options.json
    # Selecting from the existing bank creates no second HSE competency bank.
    assert options.json[0]['id']==options.json[0]['code']
    assert officer.post('/api/competency-bank',headers=hse['actors']['hse-officer'][1],json={'code':'UNAUTHORIZED','title':'Cannot create'}).status_code==403


def test_ppe_matrix_matches_both_current_job_and_activity_location(hse):
    ppe=equipment(hse,lifetime_days=90)
    requirement(hse,ppe['id'],renewal_days=30)
    officer,_=hse['actors']['hse-officer']
    before=changes(hse['app'].config['DB'])
    report=officer.get('/api/hrm/hse_report?company_id=1').json
    assert len(report['ppe'])==1
    row=report['ppe'][0]
    assert row['employee_id']==hse['ids'][0]
    assert row['missing_quantity']==2 and row['job']=='Engineer' and row['activity_location']=='Line A'
    assert report['metrics']['ppe_missing']==1 and not row['overdue']
    delivered=issue(hse,ppe['id'])
    assert delivered['issue_date']=='2026-11-12' and delivered['next_due_date']=='2026-12-12'
    assert delivered['expiry_basis']=='configured_interval'
    assert officer.get('/api/hrm/hse_report?company_id=1').json['ppe'][0]['missing_quantity']==1
    # Configuration changes do not rewrite the expiry printed on an old delivery.
    updated=officer.put('/api/hrm/ppe_catalog/'+str(ppe['id']),headers=hse['actors']['hse-officer'][1],json={'version':ppe['version'],'lifetime_days':365})
    assert updated.status_code==200,updated.json
    saved=officer.get('/api/hrm/ppe_issues/'+str(delivered['id'])).json
    assert saved['next_due_date']=='2026-12-12'
    assert changes(hse['app'].config['DB'])==before+2


def test_delivery_history_status_expiry_and_stale_write_are_explicit(hse):
    ppe=equipment(hse)
    requirement(hse,ppe['id'],quantity=1)
    saved=issue(hse,ppe['id'],issue_date='1405/01/01',next_due_date='1405/02/01')
    officer,headers=hse['actors']['hse-officer']
    row=officer.get('/api/hrm/hse_report?company_id=1').json['ppe'][0]
    assert row['overdue'] and row['missing_quantity']==1
    current=issue(hse,ppe['id'],next_due_date='1406/01/01')
    row=officer.get('/api/hrm/hse_report?company_id=1').json['ppe'][0]
    assert row['missing_quantity']==0 and not row['overdue']
    before=changes(hse['app'].config['DB'])
    r=officer.put('/api/hrm/ppe_issues/'+str(current['id']),headers=headers,json={'version':current['version']+99,'status':'returned'})
    assert r.status_code==409
    assert changes(hse['app'].config['DB'])==before
    r=officer.put('/api/hrm/ppe_issues/'+str(current['id']),headers=headers,json={'version':current['version'],'status':'returned'})
    assert r.status_code==200,r.json
    assert officer.get('/api/hrm/hse_report?company_id=1').json['ppe'][0]['missing_quantity']==1
    assert len(officer.get('/api/hrm/ppe_issues?company_id=1').json)==2
    assert saved['id']!=current['id']


def test_company_unit_and_foreign_reference_controls_are_atomic(hse):
    ppe=equipment(hse);foreign=equipment(hse,2)
    officer,headers=hse['actors']['hse-officer']
    before=changes(hse['app'].config['DB'])
    for data in [dict(employee_id=hse['ids'][0],ppe_id=foreign['id']),dict(employee_id=hse['ids'][3],ppe_id=ppe['id'])]:
        r=officer.post('/api/hrm/ppe_issues?company_id=1',headers=headers,json={'issue_date':'1405/08/21','quantity':1,**data})
        assert r.status_code in (400,403),r.json
    assert changes(hse['app'].config['DB'])==before
    assert officer.get('/api/hrm/ppe_issues?company_id=2').status_code==403
    production,ph=hse['actors']['production-head']
    assert production.post('/api/hrm/ppe_catalog?company_id=1',headers=ph,json={'name':'Cannot edit company policy'}).status_code==403
    r=production.post('/api/hrm/ppe_issues?company_id=1',headers=ph,json={'employee_id':hse['ids'][1],'ppe_id':ppe['id'],'issue_date':'1405/08/21','quantity':1})
    assert r.status_code==403
    saved=issue(hse,ppe['id'])
    assert production.get('/api/hrm/ppe_issues/'+str(saved['id'])).status_code==200


def test_required_training_reuses_existing_evidence_without_raising_current_level(hse):
    admin,ah=hse['admin'],hse['ah'];eid=hse['ids'][0]
    sid=admin.post('/api/competency-bank/S001/select?company_id=1',headers=ah).json['id']
    course=admin.post('/api/data/courses?company_id=1',headers=ah,json={'skill_id':sid,'title':'Historical HSE training','date':'1405/08/01','hours':2,'status':'completed'}).json['id']
    assert admin.post('/api/data/records?company_id=1',headers=ah,json={'employee_id':eid,'course_id':course,'level':4}).status_code==201
    assert admin.post('/api/data/assessments?company_id=1',headers=ah,json={'employee_id':eid,'skill_id':sid,'level':0}).status_code==201
    officer,headers=hse['actors']['hse-officer']
    post(officer,headers,'hse_training_requirements?company_id=1',{'job':'Engineer','activity_location':'Line A','domain':'safety','competency_code':'S001','required_level':3,'renewal_days':15})
    before=changes(hse['app'].config['DB'])
    row=officer.get('/api/hrm/hse_report?company_id=1').json['training'][0]
    assert row['current_level']==0 and row['level_source']=='current_assessment' and row['gap']==3 and row['renewal_due']
    assert row['last_training_date']=='2026-10-23'
    # Newer actual attendance updates renewal evidence; it is not a skill assessment.
    next_course=admin.post('/api/data/courses?company_id=1',headers=ah,json={'skill_id':sid,'title':'Recently attended','date':'1405/08/20','hours':1,'status':'completed'}).json['id']
    assert admin.put(f'/api/courses/{next_course}/participants',headers=ah,json={'employee_ids':[eid]}).status_code==200
    assert admin.put(f'/api/courses/{next_course}/attendance',headers=ah,json={'employee_ids':[eid]}).status_code==200
    row=officer.get('/api/hrm/hse_report?company_id=1').json['training'][0]
    assert row['last_training_date']=='2026-11-11' and not row['renewal_due']
    assert row['current_level']==0 and row['gap']==3
    assert changes(hse['app'].config['DB'])==before+3


def test_health_requires_separate_permission_and_never_leaks_into_hse_report(hse):
    medical,mh=hse['actors']['medical-manager']
    health=post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/08/21','exam_type':'Occupational exam','fitness':'restricted','restrictions':'CONFIDENTIAL-restriction','clinical_notes':'PRIVATE-medical-record','next_review_date':'1405/09/21'})
    assert health['exam_date']=='2026-11-12'
    officer,_=hse['actors']['hse-officer']
    assert officer.get('/api/hrm/health_records/'+str(health['id'])).status_code==403
    assert officer.get('/api/hrm/health_records?company_id=1').status_code==403
    assert 'CONFIDENTIAL' not in officer.get('/api/hrm/hse_report?company_id=1').text
    assert 'PRIVATE' not in officer.get('/api/hrm/hse_report?company_id=1').text
    assert 'health' not in officer.get('/api/hrm/hse_report?company_id=1').json
    assert medical.get('/api/hrm/health_records/'+str(health['id'])).json['clinical_notes']=='PRIVATE-medical-record'
    other,oh=hse['actors']['other-company']
    foreign=post(other,oh,'health_records?company_id=2',{'employee_id':hse['ids'][3],'exam_date':'1405/08/21','exam_type':'Exam other company','fitness':'fit'})
    assert medical.get('/api/hrm/health_records/'+str(foreign['id'])).status_code==403
    assert medical.get('/api/hrm/health_records?company_id=2').status_code==403
    production,_=hse['actors']['production-head']
    office_record=post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][1],'exam_date':'1405/08/21','exam_type':'Office exam','fitness':'fit'})
    assert production.get('/api/hrm/health_records/'+str(office_record['id'])).status_code==403
    assert [r['id'] for r in production.get('/api/hrm/health_records?company_id=1').json]==[health['id']]


def test_medical_document_link_cannot_be_laundered_as_ppe_evidence(hse):
    ppe=equipment(hse)
    medical,mh=hse['actors']['medical-manager']
    r=medical.post('/api/hrm/documents?company_id=1',headers=mh,data={'employee_id':str(hse['ids'][0]),'title':'Private examination report','permission_group':'health','file':(io.BytesIO(b'%PDF-1.4\nSynthetic medical\n%%EOF'),'medical.pdf')})
    assert r.status_code==201,r.json
    did=r.json['id']
    officer,headers=hse['actors']['hse-officer']
    assert officer.get('/api/hrm/documents/'+str(did)+'/file').status_code==403
    before=changes(hse['app'].config['DB'])
    r=officer.post('/api/hrm/ppe_issues?company_id=1',headers=headers,json={'employee_id':hse['ids'][0],'ppe_id':ppe['id'],'issue_date':'1405/08/21','quantity':1,'document_id':did})
    assert r.status_code==400
    assert changes(hse['app'].config['DB'])==before
    record=post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/08/21','exam_type':'With attachment','fitness':'fit','document_id':did})
    assert record['document_id']==did


def test_health_report_selects_latest_exam_by_date_not_insertion_id(hse):
    medical,mh=hse['actors']['medical-manager']
    newer=post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/08/21','exam_type':'Current examination','fitness':'fit','next_review_date':'1406/01/01'})
    post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/01/01','exam_type':'Current examination','fitness':'restricted','next_review_date':'1405/02/01'})
    report=medical.get('/api/hrm/health_report?company_id=1').json
    assert [r['id'] for r in report['latest_examinations']]==[newer['id']]
    assert report['due_reviews']==[]


def test_iso_dates_malformed_fields_and_csrf_preserve_records(hse):
    ppe=equipment(hse);officer,headers=hse['actors']['hse-officer']
    before=changes(hse['app'].config['DB'])
    base={'employee_id':hse['ids'][0],'ppe_id':ppe['id'],'issue_date':'1405/08/21','quantity':1}
    for patch in [{'quantity':True},{'quantity':0},{'next_due_date':'1404/01/01'},{'issue_date':'1405/13/01'},{'company_id':2},{'employee_id':2**65}]:
        r=officer.post('/api/hrm/ppe_issues?company_id=1',headers=headers,json={**base,**patch})
        assert r.status_code==400,(patch,r.status_code,r.json)
    assert officer.post('/api/hrm/ppe_issues?company_id=1',json=base).status_code==403
    assert officer.get('/api/hrm/ppe_issues?company_id=1').json==[]
    assert changes(hse['app'].config['DB'])==before


def test_hse_data_survives_explicit_full_backup_and_restart(hse,tmp_path):
    ppe=equipment(hse);requirement(hse,ppe['id'],quantity=1)
    delivered=issue(hse,ppe['id'],next_due_date='1406/01/01')
    medical,mh=hse['actors']['medical-manager']
    health=post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/08/21','exam_type':'Confidential exam','fitness':'fit','clinical_notes':'DO-NOT-LOSE'})
    # The designated holding administrator has explicit full authority; the
    # delegated HSE user cannot obtain raw medical data via a legacy backup.
    officer,oh=hse['actors']['hse-officer']
    assert officer.post('/api/backup',headers=oh).status_code==403
    backup=hse['admin'].post('/api/backup',headers=hse['ah'])
    assert backup.status_code==200 and backup.data.startswith(b'SQLite format 3'),backup.json
    folder=tmp_path/'restored';folder.mkdir();(folder/'nikan.sqlite3').write_bytes(backup.data)
    restored=create_app(folder,testing=True)
    officer,_=login(restored,'hse-officer');medical,_=login(restored,'medical-manager')
    assert officer.get('/api/hrm/ppe_issues/'+str(delivered['id'])).json['next_due_date']=='2027-03-21'
    assert medical.get('/api/hrm/health_records/'+str(health['id'])).json['clinical_notes']=='DO-NOT-LOSE'
    with sqlite3.connect(restored.config['DB']) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert conn.execute('PRAGMA foreign_key_check').fetchall()==[]


def sites(hse):
    a=post(hse['admin'],hse['ah'],'sites?company_id=1',{'name':'Factory A','kind':'factory'})
    b=post(hse['admin'],hse['ah'],'sites?company_id=1',{'name':'Factory B','kind':'factory'})
    c=post(hse['admin'],hse['ah'],'sites?company_id=2',{'name':'Factory other company','kind':'factory'})
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        for eid,site in zip(hse['ids'],[a['id'],b['id'],a['id'],c['id']]):
            conn.execute('UPDATE hrm_employee_details SET site_id=? WHERE employee_id=?',(site,eid))
    return a['id'],b['id'],c['id']


def account(hse,name,permissions,site=None,department=''):
    post(hse['admin'],hse['ah'],'access/users',{'username':name,'password':PASSWORD,'role':'hse_officer',
         'scopes':[{'company_id':1,'site_id':site,'department':department}],'permissions':permissions})
    return login(hse['app'],name)


def test_site_owned_delivery_and_independent_create_edit_delete_export(hse):
    a,b,_=sites(hse);ppe=equipment(hse)
    saved=issue(hse,ppe['id'])
    foreign=issue(hse,ppe['id'],employee_id=hse['ids'][1])
    editor,eh=account(hse,'site-editor',{'hse':{'read':True,'create':False,'edit':True,'delete':False,'export':False}},a)
    assert [r['id'] for r in editor.get('/api/hrm/ppe_issues?company_id=1').json]==[saved['id']]
    assert editor.get('/api/hrm/ppe_issues/'+str(foreign['id'])).status_code==403
    assert editor.get('/api/hrm/ppe_issues?company_id=1&site_id='+str(b)).json==[]
    base={'employee_id':hse['ids'][0],'ppe_id':ppe['id'],'issue_date':'1405/08/21','quantity':1}
    assert editor.post('/api/hrm/ppe_issues?company_id=1',headers=eh,json=base).status_code==403
    assert editor.delete('/api/hrm/ppe_issues/'+str(saved['id']),headers=eh,json={'version':saved['version']}).status_code==403
    edited=editor.put('/api/hrm/ppe_issues/'+str(saved['id']),headers=eh,json={'version':saved['version'],'receiver_reference':'Receipt updated by permitted editor'})
    assert edited.status_code==200,edited.json
    assert editor.get('/api/hrm/reports/ppe_issues.xlsx?company_id=1').status_code==403
    exporter,_=account(hse,'site-export-only',{'hse':{'read':False,'create':False,'edit':False,'delete':False,'export':True}},a)
    assert exporter.get('/api/hrm/ppe_issues?company_id=1').status_code==403
    export=exporter.get('/api/hrm/reports/ppe_issues.xlsx?company_id=1')
    assert export.status_code==200,export.json
    import openpyxl
    workbook=openpyxl.load_workbook(io.BytesIO(export.data),read_only=True)
    text=' '.join(str(v) for row in workbook.active.values for v in row if v is not None)
    assert 'Synthetic person 1001' in text and 'Synthetic person 1002' not in text
    deleter,dh=account(hse,'site-void-only',{'hse':{'read':True,'delete':True}},a)
    result=deleter.delete('/api/hrm/ppe_issues/'+str(saved['id']),headers=dh,json={'version':edited.json['version']})
    assert result.status_code==200,result.json
    assert deleter.get('/api/hrm/ppe_issues?company_id=1').json==[]
    archived=deleter.get('/api/hrm/ppe_issues?company_id=1&include_deleted=1').json
    assert [r['id'] for r in archived]==[saved['id']] and archived[0]['is_deleted']==1
    assert deleter.get('/api/hrm/ppe_issues/'+str(saved['id'])+'?include_deleted=1').json['is_deleted']==1
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM hrm_ppe_issues').fetchone()[0]==2


def test_ppe_site_policies_can_differ_without_duplicate_or_cross_site_collision(hse):
    a,b,_=sites(hse);ppe=equipment(hse)
    first=requirement(hse,ppe['id'],site_id=a,quantity=1)
    second=requirement(hse,ppe['id'],site_id=b,quantity=3)
    assert first['id']!=second['id']
    officer,headers=hse['actors']['hse-officer']
    report=officer.get('/api/hrm/hse_report?company_id=1').json
    assert {(r['employee_id'],r['required_quantity']) for r in report['ppe']}=={(hse['ids'][0],1)}
    # The Line A condition still excludes the employee at Line B despite a
    # site-specific rule; adjusting the configured location changes matching.
    r=officer.put('/api/hrm/ppe_requirements/'+str(second['id']),headers=headers,json={'version':second['version'],'activity_location':'Line B'})
    assert r.status_code==200,r.json
    report=officer.get('/api/hrm/hse_report?company_id=1').json
    assert {(r['employee_id'],r['required_quantity']) for r in report['ppe']}=={(hse['ids'][0],1),(hse['ids'][1],3)}
    duplicate=officer.post('/api/hrm/ppe_requirements?company_id=1',headers=headers,json={'ppe_id':ppe['id'],'site_id':a,'job':'Engineer','activity_location':'Line A','quantity':5})
    assert duplicate.status_code==400


def test_health_environment_is_one_operational_domain_and_missing_exams_are_configured(hse):
    a,_,_=sites(hse)
    both,bh=account(hse,'health-environment-lead',{'health_environment':{'read':True,'write':True},'health':{'read':True,'write':True}},a)
    public,ph=account(hse,'health-environment-records',{'health_environment':{'read':True,'write':True}},a)
    req=post(public,ph,'medical_requirements?company_id=1',{'site_id':a,'job':'Engineer','activity_location':'Line A','exam_type':'Periodic occupational exam','frequency_days':30})
    post(public,ph,'health_environment_training_requirements?company_id=1',{'site_id':a,'job':'Engineer','activity_location':'Line A','domain':'environment','competency_code':'S001','required_level':2})
    post(public,ph,'harmful_exposures?company_id=1',{'employee_id':hse['ids'][0],'exposure_date':'1405/08/21','factor':'Noise','measurement':'85','measurement_unit':'dB','controls':'Recorded organizational measurement, no automatic clinical conclusion'})
    post(public,ph,'environment_participation?company_id=1',{'employee_id':hse['ids'][0],'activity_date':'1405/08/21','program':'Waste sorting program','contribution':'Training team participation'})
    assert public.get('/api/hrm/health_records?company_id=1').status_code==403
    public_report=public.get('/api/hrm/health_environment_report?company_id=1').json
    assert len(public_report['training'])==1 and public_report['training'][0]['domain']=='environment'
    assert public_report['metrics']['exposures']==1 and public_report['metrics']['environment_participations']==1
    assert public_report['medical']['authorized'] is False and public_report['metrics']['medical_missing'] is None
    private_report=both.get('/api/hrm/health_environment_report?company_id=1').json
    assert private_report['metrics']['medical_missing']==1
    assert private_report['medical']['missing'][0]['requirement_id']==req['id']
    medical,mh=hse['actors']['medical-manager']
    post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/01/01','exam_type':'Periodic occupational exam','fitness':'restricted','clinical_notes':'CLINICAL-PRIVATE'})
    private_report=both.get('/api/hrm/health_environment_report?company_id=1').json
    assert private_report['metrics']['medical_missing']==0 and private_report['metrics']['medical_due']==1
    assert 'CLINICAL-PRIVATE' not in json.dumps(private_report)
    assert 'fitness' not in json.dumps(private_report)
    officer,oh=hse['actors']['hse-officer']
    assert officer.get('/api/hrm/health_environment_training_requirements?company_id=1').status_code==403
    assert officer.get('/api/hrm/hse_training_requirements?company_id=1').json==[]
    assert public.post('/api/hrm/hse_training_requirements?company_id=1',headers=ph,json={'domain':'safety','competency_code':'S001','required_level':1}).status_code==403


def test_old_unit_medical_exam_cannot_be_inferred_from_new_unit_report(hse):
    a,_,_=sites(hse)
    medical,mh=hse['actors']['medical-manager']
    # The historical clinical record belongs to Office, then the employee moves
    # to Production at the same site. Its date/status remains Office-owned.
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        conn.execute("UPDATE hrm_employee_details SET department='Office' WHERE employee_id=?",(hse['ids'][0],))
    old=post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/08/21','exam_type':'Unit-specific examination','fitness':'fit','clinical_notes':'OLD-UNIT-PRIVATE'})
    post(hse['admin'],hse['ah'],'medical_requirements?company_id=1',{'site_id':a,'department':'Production','job':'Engineer','activity_location':'Line A','exam_type':'Unit-specific examination','frequency_days':365})
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        conn.execute("UPDATE hrm_employee_details SET department='Production' WHERE employee_id=?",(hse['ids'][0],))
    production,_=account(hse,'new-unit-clinician',{'health':{'read':True},'health_environment':{'read':True}},a,'Production')
    assert production.get('/api/hrm/health_records/'+str(old['id'])).status_code==403
    report=production.get('/api/hrm/health_environment_report?company_id=1').json
    assert report['medical']['missing'][0]['last_exam_date']==''
    assert report['metrics']['medical_missing']==1 and report['metrics']['medical_due']==0
    assert '2026-11-12' not in json.dumps(report['medical']['requirements'])
    assert 'OLD-UNIT-PRIVATE' not in json.dumps(report)


def test_active_temporary_activity_requires_review_instead_of_silent_compliance(hse):
    a,b,_=sites(hse);ppe=equipment(hse)
    requirement(hse,ppe['id'],site_id=a,quantity=1)
    issue(hse,ppe['id'],next_due_date='1406/01/01')
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        conn.execute("INSERT INTO hrm_temporary_assignments(employee_id,company_id,site_id,department,target_company_id,target_site_id,start_date,end_date,activity_location,status) VALUES (?,?,?,?,?,?,?,?,?,'active')",(hse['ids'][0],1,a,'Production',1,b,'2026-11-01','2026-11-30','Different harmful exposure location'))
    local,_=account(hse,'source-site-safety',{'hse':{'read':True}},a)
    report=local.get('/api/hrm/hse_report?company_id=1').json
    assert report['data_status']=='assignment_review_required'
    assert report['metrics']['temporary_assignment_reviews']==1
    assert report['ppe'][0]['missing_quantity']==0 and report['ppe'][0]['review_required']
    alert=report['assignment_reviews'][0]
    assert alert['review_required'] and alert['target_scope_authorized'] is False
    assert 'target_site_id' not in alert and 'activity_location' not in alert
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        assert conn.execute('SELECT site_id FROM hrm_employee_details WHERE employee_id=?',(hse['ids'][0],)).fetchone()[0]==a


def test_recorded_clinical_review_due_is_not_lost_when_job_policy_is_unconfigured(hse):
    medical,mh=hse['actors']['medical-manager']
    post(medical,mh,'health_records?company_id=1',{'employee_id':hse['ids'][0],'exam_date':'1405/01/01','exam_type':'Explicit physician follow-up','fitness':'fit','next_review_date':'1405/02/01'})
    report=medical.get('/api/hrm/health_report?company_id=1').json
    assert report['medical']['metrics']['due']==1
    assert report['medical']['due'][0]['requirement_source']=='recorded_review'
    assert report['medical']['due'][0]['requirement_id'] is None


def test_voided_current_training_assessment_is_hidden_in_hse_and_legacy_editors(hse):
    admin,ah=hse['admin'],hse['ah'];eid=hse['ids'][0]
    assert admin.put('/api/job-workspace?company_id=1&job=Engineer',headers=ah,json={'items':[{'code':'S001','level':3}]}).status_code==200
    assessment=post(admin,ah,'training_assessments?company_id=1',{'employee_id':eid,'competency_code':'S001','level':4,'assessment_date':'1405/08/21'})
    officer,oh=hse['actors']['hse-officer']
    post(officer,oh,'hse_training_requirements?company_id=1',{'job':'Engineer','activity_location':'Line A','domain':'safety','competency_code':'S001','required_level':3})
    assert officer.get('/api/hrm/hse_report?company_id=1').json['training'][0]['current_level']==4
    result=admin.delete('/api/hrm/training_assessments/'+str(assessment['id']),headers=ah,json={'version':assessment['version']})
    assert result.status_code==200,result.json
    assert officer.get('/api/hrm/hse_report?company_id=1').json['training'][0]['current_level']==0
    passport=admin.get(f'/api/passports/{eid}?company_id=1').json
    assert next(r for r in passport['matrix'] if r['code']=='S001')['current']==0
    workspace=admin.get(f'/api/assessment-workspace/{eid}?company_id=1').json
    assert next(r for r in workspace['matrix'] if r['code']=='S001')['current']==0
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        assert conn.execute('SELECT level FROM assessments WHERE id=?',(assessment['id'],)).fetchone()[0]==4
        tables=['assessments','skills','competency_links','audit','hrm_training_assessment_context']
        before={table:conn.execute('SELECT * FROM '+table).fetchall() for table in tables}
    # The first item would attach a new competency. The voided second item must
    # roll back that attachment as well, preserving the retained historical grade.
    result=admin.put(f'/api/assessment-workspace/{eid}?company_id=1',headers=ah,json={'items':[{'code':'S002','level':1},{'code':'S001','level':2}]})
    assert result.status_code==409,result.json
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        assert {table:conn.execute('SELECT * FROM '+table).fetchall() for table in tables}==before
    assert admin.put(f'/api/assessment-workspace/{eid}?company_id=1',headers=ah,json={'items':[{'code':'S003','level':2}]}).status_code==200


def test_voided_course_grade_is_not_reused_as_hse_skill_or_legacy_training_history(hse):
    admin,ah=hse['admin'],hse['ah'];eid=hse['ids'][0]
    sid=admin.post('/api/competency-bank/S001/select?company_id=1',headers=ah).json['id']
    course=admin.post('/api/data/courses?company_id=1',headers=ah,json={'skill_id':sid,'title':'Completed historical course','date':'1405/08/01','hours':2,'status':'completed'}).json['id']
    record=post(admin,ah,'training_records?company_id=1',{'employee_id':eid,'course_id':course,'level':4,'record_date':'1405/08/01'})
    officer,oh=hse['actors']['hse-officer']
    post(officer,oh,'hse_training_requirements?company_id=1',{'job':'Engineer','activity_location':'Line A','domain':'safety','competency_code':'S001','required_level':3,'renewal_days':90})
    current=officer.get('/api/hrm/hse_report?company_id=1').json['training'][0]
    assert current['current_level']==4 and current['level_source']=='completed_course' and not current['renewal_due']
    result=admin.delete('/api/hrm/training_records/'+str(record['id']),headers=ah,json={'version':record['version']})
    assert result.status_code==200,result.json
    current=officer.get('/api/hrm/hse_report?company_id=1').json['training'][0]
    assert current['current_level']==0 and current['last_training_date']=='' and current['renewal_due']
    passport=admin.get(f'/api/passports/{eid}?company_id=1').json
    assert passport['records']==[]
    assert admin.get('/api/data/records?company_id=1').json==[]
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        assert conn.execute('SELECT level FROM records WHERE id=?',(record['id'],)).fetchone()[0]==4
        assert conn.execute('SELECT is_deleted FROM hrm_training_record_context WHERE record_id=?',(record['id'],)).fetchone()[0]==1


def test_transferred_worker_uses_current_company_assessment_before_original_history(hse):
    admin,ah=hse['admin'],hse['ah'];eid=hse['ids'][0]
    sid=admin.post('/api/competency-bank/S001/select?company_id=1',headers=ah).json['id']
    original=admin.post('/api/data/assessments?company_id=1',headers=ah,json={'employee_id':eid,'skill_id':sid,'level':4}).json['id']
    before=admin.get(f'/api/hrm/people/{eid}').json
    moved=admin.put(f'/api/hrm/people/{eid}?company_id=1',headers=ah,json={'version':before['version'],'company_id':2,'effective_date':'1405/08/21'})
    assert moved.status_code==200,moved.json
    other,oh=hse['actors']['other-company']
    post(other,oh,'hse_training_requirements?company_id=2',{'job':'Engineer','activity_location':'Line A','domain':'safety','competency_code':'S001','required_level':3})
    def evidence():
        rows=other.get('/api/hrm/hse_report?company_id=2').json['training']
        return next(row for row in rows if row['employee_id']==eid)
    assert evidence()['current_level']==4
    current=post(admin,ah,'training_assessments?company_id=2',{'employee_id':eid,'competency_code':'S001','level':0,'assessment_date':'1405/08/21'})
    assert next(r for r in admin.get('/api/hrm/training_assessments?company_id=2').json if r['id']==current['id'])['company_id']==2
    assert evidence()['current_level']==0 and evidence()['gap']==3
    assert admin.delete('/api/hrm/training_assessments/'+str(current['id']),headers=ah,json={'version':current['version']}).status_code==200
    assert evidence()['current_level']==4
    # With both assessments explicitly voided, a new-company completed grade
    # remains a valid fallback without moving the canonical employee or history.
    assert admin.delete('/api/hrm/training_assessments/'+str(original),headers=ah,json={'version':0}).status_code==200
    sid2=admin.post('/api/competency-bank/S001/select?company_id=2',headers=ah).json['id']
    course=admin.post('/api/data/courses?company_id=2',headers=ah,json={'skill_id':sid2,'title':'New-company completed course','date':'1405/08/01','hours':1,'status':'completed'}).json['id']
    grade=post(admin,ah,'training_records?company_id=2',{'employee_id':eid,'course_id':course,'level':2,'record_date':'1405/08/01'})
    assert next(r for r in admin.get('/api/hrm/training_records?company_id=2').json if r['id']==grade['id'])['company_id']==2
    assert evidence()['current_level']==2 and evidence()['level_source']=='completed_course'
    with sqlite3.connect(hse['app'].config['DB']) as conn:
        assert conn.execute('SELECT company_id FROM employees WHERE id=?',(eid,)).fetchone()[0]==1
        assert conn.execute('SELECT level FROM assessments WHERE id=?',(original,)).fetchone()[0]==4
