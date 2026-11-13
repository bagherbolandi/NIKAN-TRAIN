"""The dashboards and exports must use real, authorized management records."""
import io
import json
import secrets
import sqlite3

import openpyxl
import pytest

from app import create_app
from app.hrm_common import today_iso
from network.backup import decrypt, recovery_check, encrypt

PASSWORD = 'Core-test-password-123'


@pytest.fixture
def core(tmp_path):
    app=create_app(tmp_path,True);client=app.test_client()
    assert client.post('/api/setup',json={'username':'admin','password':PASSWORD}).status_code==200
    assert client.post('/api/login',json={'username':'admin','password':PASSWORD}).status_code==200
    headers={'X-CSRF-Token':client.get('/api/me').json['csrf']}
    def post(resource,body):
        response=client.post('/api/hrm/'+resource+'?company_id=1',json=body,headers=headers)
        assert response.status_code==201,(resource,response.json)
        return response.json
    site=post('sites',{'name':'Core plant','kind':'factory'})['id']
    person=post('people',{'company_id':1,'site_id':site,'code':'CORE-1234','name':'Core person','job':'Welder','department':'Production','location':'Hall A'})
    return app,client,headers,site,person['id'],tmp_path


def account(core,name,permissions):
    app,admin,h,site,_,_=core
    result=admin.post('/api/hrm/access/users',headers=h,json={
        'username':name,'password':PASSWORD,'role':'executive',
        'scopes':[{'company_id':1,'site_id':site,'department':''}],
        'permissions':permissions,'training_access':False})
    assert result.status_code==201,result.json
    client=app.test_client()
    assert client.post('/api/login',json={'username':name,'password':PASSWORD}).status_code==200
    return client


def test_four_specialized_dashboards_and_primary_holding_dashboard(core):
    _,admin,_,_,_,_=core
    assert len(admin.get('/api/me').json['companies'])==7
    assert 'موج نیکان' in {r['name'] for r in admin.get('/api/me').json['companies']}
    for section in ('holding','personnel','training','hse','health_environment'):
        response=admin.get('/api/hrm/dashboard?section='+section)
        assert response.status_code==200,response.json
        assert response.json['section']==section
        assert 'metrics' in response.json and 'data_quality' in response.json
    safety=admin.get('/api/hrm/dashboard?section=hse').json
    values={row['key']:row['value'] for row in safety['metrics']}
    assert values['safety_ppe_missing'] is None
    health=admin.get('/api/hrm/dashboard?section=health_environment').json
    values={row['key']:row['value'] for row in health['metrics']}
    assert values['medical_missing'] is None
    assert health['data_quality']


def test_training_hours_use_actual_presence_not_plans_or_grades(core):
    _,admin,h,_,eid,_=core
    sid=admin.post('/api/competency-bank/S001/select?company_id=1',headers=h).json['id']
    result=admin.post('/api/data/courses?company_id=1',headers=h,json={
        'skill_id':sid,'title':'Real training','date':'1405/08/21','hours':4,'status':'completed'})
    assert result.status_code==201,result.json
    course=result.json['id']
    assert admin.put(f'/api/courses/{course}/participants',headers=h,json={'employee_ids':[eid]}).status_code==200
    assert admin.post('/api/data/records?company_id=1',headers=h,json={'employee_id':eid,'course_id':course,'level':3}).status_code==201
    before=admin.get('/api/hrm/dashboard?section=training&company_id=1').json['training']
    assert before['planned_courses']==1 and before['completed_courses']==1
    assert before['actual_person_hours']==0 and before['actual_attendances']==0
    assert admin.put(f'/api/courses/{course}/attendance',headers=h,json={'employee_ids':[eid]}).status_code==200
    after=admin.get('/api/hrm/dashboard?section=training&company_id=1').json['training']
    assert after['actual_person_hours']==4 and after['hours_per_employee']==4


def test_combined_health_environment_has_no_medical_data_without_medical_grant(core):
    app,admin,h,site,eid,_=core
    created=admin.post('/api/hrm/health_records?company_id=1',headers=h,json={
        'employee_id':eid,'exam_date':today_iso(),'exam_type':'Occupational examination',
        'fitness':'fit','restrictions':'CLINICAL-PRIVATE-123'})
    assert created.status_code==201,created.json
    observer=account(core,'environment-only',{'health_environment':{'read':True}})
    response=observer.get('/api/hrm/dashboard?section=health_environment&company_id=1&site_id='+str(site))
    assert response.status_code==200,response.json
    assert 'CLINICAL-PRIVATE' not in response.get_data(as_text=True)
    assert response.json['medical_alerts']==[]
    assert observer.get('/api/hrm/health_records?company_id=1').status_code==403
    assert observer.get('/api/hrm/reports/health_records.xlsx?company_id=1').status_code==403
    assert admin.get('/api/hrm/health_records?company_id=1').status_code==200


def test_medical_document_exports_require_the_actual_group_export_grant(core):
    _,admin,h,_,eid,_=core
    for group,title in [('documents','Public personnel document'),('health','MEDICAL-PRIVATE-DOCUMENT')]:
        response=admin.post('/api/hrm/documents?company_id=1',headers=h,data={
            'employee_id':str(eid),'permission_group':group,'title':title,
            'file':(io.BytesIO(b'%PDF-1.4\nTest document\n%%EOF'),group+'.pdf')})
        assert response.status_code==201,response.json
    viewer=account(core,'split-export',{
        'documents':{'read':True,'export':True},'health':{'read':True,'export':False}})
    response=viewer.get('/api/hrm/reports/documents.xlsx?company_id=1')
    assert response.status_code==200,response.json
    workbook=openpyxl.load_workbook(io.BytesIO(response.data),read_only=True)
    cells=' '.join(str(cell.value) for row in workbook.active for cell in row)
    assert 'Public personnel document' in cells and 'MEDICAL-PRIVATE' not in cells
    private=account(core,'medical-only',{'health':{'read':True}})
    listing=private.get('/api/hrm/documents?company_id=1')
    assert listing.status_code==200,listing.json
    assert [r['title'] for r in listing.json]==['MEDICAL-PRIVATE-DOCUMENT']
    assert private.get('/api/hrm/reports/documents.xlsx?company_id=1').status_code==403


def test_automatic_preupgrade_snapshot_is_verified_and_preserves_facts(core):
    app,_,_,_,eid,root=core
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute("DELETE FROM hrm_schema_migrations WHERE version='hrm-0.13'")
        expected=conn.execute('SELECT * FROM employees ORDER BY id').fetchall()
    upgraded=create_app(root,True)
    report=upgraded.extensions['hrm_upgrade_backup']
    assert report['verified'] and not report['encrypted']
    snapshot=root/'pre-upgrade-backups'/report['file']
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert conn.execute('SELECT * FROM employees ORDER BY id').fetchall()==expected
    assert create_app(root,True).extensions['hrm_upgrade_backup'] is None


def test_network_preupgrade_snapshot_is_authenticated_and_old_markers_can_append(core):
    app,_,_,_,_,root=core
    key=secrets.token_bytes(32);(root/'keys').mkdir();(root/'keys/recovery.key').write_bytes(key)
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute("DELETE FROM hrm_schema_migrations WHERE version='hrm-0.13'")
        expected=conn.execute('SELECT * FROM employees ORDER BY id').fetchall()
    upgraded=create_app(root,True);report=upgraded.extensions['hrm_upgrade_backup']
    assert report['encrypted'] and report['file'].endswith('.nikbak')
    archive=root/'pre-upgrade-backups'/report['file'];raw=root/'verified.sqlite3'
    decrypt(archive,raw,key)
    with sqlite3.connect(raw) as conn:
        assert conn.execute('SELECT * FROM employees ORDER BY id').fetchall()==expected
    assert recovery_check(archive,key,root)['verified']
    with sqlite3.connect(raw) as conn:conn.execute("INSERT INTO hrm_schema_migrations(version) VALUES ('UNSUPPORTED')")
    bad=root/'unsupported.nikbak';encrypt(raw,bad,key)
    with pytest.raises(ValueError):recovery_check(bad,key,root)


def test_accord_permissions_are_independent_and_period_references_need_source_read(core):
    _,admin,h,site,_,_=core
    created=admin.post('/api/hrm/evaluation_periods?company_id=1',headers=h,json={
        'company_id':1,'site_id':site,'title':'Scoped accord period','start_date':'2026-11-01',
        'end_date':'2026-11-30','status':'open','note':'SOURCE-PERFORMANCE-NOTE'})
    assert created.status_code==201,created.json
    period=created.json['id']
    performance_only=account(core,'ordinary-performance',{'performance':{'read':True}})
    assert performance_only.get('/api/hrm/accord_results?company_id=1').status_code==403
    assert 'accord_results' not in performance_only.get('/api/hrm/catalog').json
    creator=account(core,'accord-creator',{'productivity':{'create':True},'performance':{'read':True}})
    references=creator.get('/api/hrm/options/evaluation_periods?company_id=1&module=productivity&operation=create')
    assert references.status_code==200,references.json
    assert any(row['id']==period for row in references.json)
    assert 'SOURCE-PERFORMANCE-NOTE' not in references.get_data(as_text=True)
    assert creator.get('/api/hrm/accord_results?company_id=1').status_code==403
    no_source=account(core,'accord-without-score-source',{'productivity':{'create':True}})
    assert no_source.get('/api/hrm/options/evaluation_periods?company_id=1&module=productivity&operation=create').status_code==403


def test_structure_catalog_write_controls_belong_to_primary_admin(core):
    app,admin,h,_,_,_=core
    response=admin.post('/api/hrm/access/users',headers=h,json={
        'username':'company-personnel-full','password':PASSWORD,'role':'company_hr',
        'scopes':[{'company_id':1,'site_id':None,'department':''}],
        'permissions':{'personnel':{'read':True,'create':True,'edit':True,'delete':True,'export':True}},
        'training_access':False})
    assert response.status_code==201,response.json
    local=app.test_client()
    assert local.post('/api/login',json={'username':'company-personnel-full','password':PASSWORD}).status_code==200
    schema=local.get('/api/hrm/catalog').json
    primary=admin.get('/api/hrm/catalog').json
    for name in ('sites','units'):
        assert schema[name]['can_read']
        assert not any(schema[name]['can_'+operation] for operation in ('create','edit','delete','write'))
        assert all(primary[name]['can_'+operation] for operation in ('create','edit','delete','write'))
