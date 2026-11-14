"""Legacy training clients share scoped canonical readers without editing audited facts."""
import pytest

from app import create_app
from app.hrm_common import today_iso

PASSWORD='Canonical-training-test-123'

@pytest.fixture
def bridged(tmp_path):
    app=create_app(tmp_path,testing=True);admin=app.test_client()
    assert admin.post('/api/setup',json={'username':'admin','password':PASSWORD}).status_code==200
    assert admin.post('/api/login',json={'username':'admin','password':PASSWORD}).status_code==200
    headers={'X-CSRF-Token':admin.get('/api/me').json['csrf']}
    site=admin.post('/api/hrm/sites?company_id=1',headers=headers,json={'name':'Canonical site','kind':'factory'}).json['id']
    people=[]
    for code in ('CAN-1001','CAN-1002'):
        response=admin.post('/api/hrm/people?company_id=1',headers=headers,json={'code':code,'name':code,'job':'Operator','site_id':site})
        assert response.status_code==201,response.json
        people.append(response.json['id'])
    skill=admin.post('/api/competency-bank/S001/select?company_id=1',headers=headers).json['id']
    assert admin.post('/api/data/requirements?company_id=1',headers=headers,json={'job':'Operator','skill_id':skill,'level':3}).status_code==201
    grade=admin.post('/api/hrm/training_assessments?company_id=1',headers=headers,json={'employee_id':people[0],'competency_code':'S001','level':2,'assessment_date':today_iso()})
    assert grade.status_code==201,grade.json
    assert admin.post('/api/users',headers=headers,json={'username':'legacy-company-manager','password':PASSWORD,'role':'manager','company_id':1}).status_code==201
    client=app.test_client()
    assert client.post('/api/login',json={'username':'legacy-company-manager','password':PASSWORD}).status_code==200
    return app,admin,headers,client,{'X-CSRF-Token':client.get('/api/me').json['csrf']},people,grade.json['id']


def test_old_training_account_projects_only_its_existing_training_rights(bridged):
    _,_,_,client,_,people,_=bridged
    me=client.get('/api/me').json['hrm']
    assert me['permissions']['training']['read'] and me['permissions']['training']['edit']
    assert not me['permissions']['personnel']['read']
    assert not me['permissions']['health']['read'] and not me['permissions']['productivity']['read']
    assert client.get(f'/api/assessment-workspace/{people[0]}?company_id=1').status_code==200
    assert client.get('/api/hrm/skill-matrix?company_id=1').status_code==200
    assert client.get('/api/hrm/skill-matrix?company_id=2').status_code==403
    assert client.get('/api/hrm/health_records?company_id=1').status_code==403


def test_old_write_paths_cannot_overwrite_versioned_assessment(bridged):
    _,admin,headers,client,local_headers,people,grade=bridged
    response=client.put(f'/api/assessment-workspace/{people[0]}?company_id=1',headers=local_headers,json={'items':[{'code':'S001','level':4}]})
    assert response.status_code==409,response.json
    response=admin.put(f'/api/data/assessments/{grade}?company_id=1',headers=headers,json={'level':4})
    assert response.status_code==409,response.json
    result=next(row for row in admin.get('/api/hrm/training_assessments?company_id=1').json if row['id']==grade)
    assert result['level']==2


def test_old_gap_reports_keep_unknown_competence_separate_from_zero(bridged):
    _,admin,_,client,_,people,_=bridged
    rows=client.get('/api/gaps?company_id=1')
    assert rows.status_code==200,rows.json
    unknown=next(row for row in rows.json if row['employee_id']==people[1])
    assert unknown['current'] is None and unknown['gap'] is None and unknown['requires_assessment']
    known=next(row for row in rows.json if row['employee_id']==people[0])
    assert known['current']==2 and known['gap']==1
    local=client.get('/api/needs-summary?company_id=1')
    assert local.status_code==200,local.json
    group=next(row for row in local.json if row['code']=='S001')
    assert group['gap_total']==1 and group['unknown_count']==1 and group['gap_total_is_partial']
    holding=admin.get('/api/holding/gaps')
    assert holding.status_code==200,holding.json
    assert next(row for row in holding.json if row['code']=='S001')['unknown_count']==1


def test_old_xlsx_handles_unknown_levels_and_marks_them_explicitly(bridged):
    import io
    import openpyxl
    _,admin,_,_,_,_,_=bridged
    response=admin.get('/api/export.xlsx?company_id=1')
    assert response.status_code==200,response.json
    book=openpyxl.load_workbook(io.BytesIO(response.data),read_only=True)
    rows=list(book['شایستگی‌ها'].values)
    assert rows[1][3]==1 and rows[1][-1]==1
    people=' '.join(str(value) for row in book['افراد'].values for value in row)
    assert 'نیازمند ارزیابی' in people and 'نامشخص' in people
    book.close()


def test_retained_staff_reports_count_current_assignment_after_transfer(bridged):
    _,admin,headers,_,_,people,_=bridged
    site=admin.post('/api/hrm/sites?company_id=2',headers=headers,json={'name':'Current company B site','kind':'factory'}).json['id']
    person=next(row for row in admin.get('/api/hrm/people?company_id=1').json if row['id']==people[0])
    moved=admin.put(f"/api/hrm/people/{people[0]}?company_id=1",headers=headers,json={
        'version':person['version'],'company_id':2,'site_id':site,'job':'Current B role','effective_date':today_iso()})
    assert moved.status_code==200,moved.json
    old=admin.get('/api/dashboard?company_id=1');new=admin.get('/api/dashboard?company_id=2')
    assert old.status_code==new.status_code==200
    assert old.json['employees']==new.json['employees']==1
    jobs=admin.get('/api/job-summary?company_id=2')
    assert jobs.status_code==200,jobs.json
    assert next(row for row in jobs.json if row['job']=='Current B role')['employees']==1
    report=admin.get('/api/management-report?company_id=2')
    assert report.status_code==200,report.json
    assert report.json['employee_count']==1 and report.json['unapproved']==1
    assert report.json['staffing_basis']=='current_assignment'
