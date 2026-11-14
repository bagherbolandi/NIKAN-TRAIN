"""The canonical skill matrix distinguishes missing facts, zero and obligations."""
from datetime import date
import io
import sqlite3

import openpyxl
import pytest

from app import create_app

PASSWORD = 'Matrix-test-password-123'
CODE = 'MATRIX-SYNTHETIC-SKILL'
ALL = dict(read=True, create=True, edit=True, delete=True, export=True)


def post(client, headers, resource, body, cid=1):
    response=client.post('/api/hrm/'+resource+'?company_id='+str(cid),headers=headers,json=body)
    assert response.status_code==201,(resource,response.status_code,response.json)
    return response.json


@pytest.fixture
def matrix(tmp_path):
    app=create_app(tmp_path,testing=True);admin=app.test_client()
    assert admin.post('/api/setup',json={'username':'matrix-admin','password':PASSWORD}).status_code==200
    assert admin.post('/api/login',json={'username':'matrix-admin','password':PASSWORD}).status_code==200
    h={'X-CSRF-Token':admin.get('/api/me').json['csrf']}
    sites=[post(admin,h,'sites',{'name':name,'kind':'factory'},cid) for cid,name in [(1,'Matrix A'),(1,'Matrix B'),(2,'Matrix C')]]
    people=[post(admin,h,'people',{'code':'000'+str(index),'name':'Matrix person '+str(index),'company_id':site['company_id'],
             'site_id':site['id'],'department':'Production','job':'Operator','location':'Line A','start_date':'2026-01-01','effective_date':'2026-01-01'},site['company_id'])
            for index,site in enumerate(sites,1)]
    assert admin.post('/api/competency-bank',headers=h,json={'code':CODE,'title':'Synthetic competency','category':'Matrix test','importance':3}).status_code==201
    skills={cid:admin.post('/api/competency-bank/'+CODE+'/select?company_id='+str(cid),headers=h).json['id'] for cid in (1,2)}
    requirement=admin.post('/api/data/requirements?company_id=1',headers=h,json={'job':'Operator','skill_id':skills[1],'level':3})
    assert requirement.status_code==201,requirement.json
    def account(name, flags, site=sites[0]['id'], company=1):
        response=admin.post('/api/hrm/access/users',headers=h,json={'username':name,'password':PASSWORD,'role':'training_officer',
             'scopes':[{'company_id':company,'site_id':site,'department':''}], 'permissions':{'training':flags},'training_access':False})
        assert response.status_code==201,response.json
        client=app.test_client();assert client.post('/api/login',json={'username':name,'password':PASSWORD}).status_code==200
        return client,{'X-CSRF-Token':client.get('/api/me').json['csrf']}
    return dict(app=app,admin=admin,h=h,sites=sites,people=people,skills=skills,requirement=requirement.json['id'],account=account)


def report(data, client=None, **filters):
    filters.setdefault('employee_id',data['people'][0]['id'])
    response=(client or data['admin']).get('/api/hrm/skill-matrix',query_string=filters)
    assert response.status_code==200,response.json
    return response.json


def cell(data, client=None, **filters):
    rows=report(data,client,**filters)['rows']
    return next(row for row in rows if row['competency_code']==CODE)


def assess(data, level, person=0, **extra):
    return post(data['admin'],data['h'],'training_assessments',dict(employee_id=data['people'][person]['id'],competency_code=CODE,
               level=level,assessment_date='2026-10-03',**extra),data['sites'][person]['company_id'])


def course_record(data, when, level=4, actual=False):
    course=post(data['admin'],data['h'],'training_courses',{'company_id':1,'site_id':data['sites'][0]['id'],'department':'Production',
                'competency_code':CODE,'title':'Synthetic class '+when,'date':when,'hours':2,'status':'completed','calendar_scope':'internal'})
    record=post(data['admin'],data['h'],'training_records',{'employee_id':data['people'][0]['id'],'course_id':course['id'],
                 'level':level,'record_date':when,'attended':int(actual)})
    return course,record


def test_missing_assessment_and_assessed_zero_have_different_meaning(matrix):
    d=matrix;before=cell(d)
    assert before['required_level']==3 and before['current_level'] is None and before['gap'] is None
    assert before['status']=='unassessed' and not before['has_current_level']
    assert report(d)['metrics']['critical_unknowns']==1
    assessed=assess(d,0)
    after=cell(d)
    assert after['current_level']==0 and after['gap']==3 and after['has_current_level']
    assert after['assessment_id']==assessed['id'] and after['status']=='gap'
    assert report(d)['metrics']['critical_unknowns']==0
    assert report(d)['metrics']['critical_gaps']==1


def test_ideal_job_and_individual_requirement_gaps_are_both_visible(matrix):
    d=matrix
    assert d['admin'].put('/api/data/requirements/'+str(d['requirement'])+'?company_id=1',headers=d['h'],json={'level':4}).status_code==200
    assert d['admin'].put('/api/passports/'+str(d['people'][0]['id'])+'/requirements?company_id=1',headers=d['h'],
            json={'items':[{'code':CODE,'level':2}]}).status_code==200
    assess(d,2)
    row=cell(d)
    assert (row['job_required_level'],row['personal_required_level'],row['required_level'])==(4,2,2)
    assert (row['job_gap'],row['personal_gap'],row['gap'])==(2,0,0)
    assert row['requirement_variance'] and row['status']=='review_required'
    assert {source['kind'] for source in row['requirement_sources']}=={'job_profile','personal_profile'}
    metrics=report(d)['metrics']
    assert metrics['job_ideal_gaps']==metrics['critical_job_ideal_gaps']==1
    assert 'average' not in metrics


def test_latest_authorized_grade_not_highest_and_assessment_zero_wins(matrix):
    d=matrix;course_record(d,'2026-09-01',4);newer,record=course_record(d,'2026-10-01',1)
    row=cell(d)
    assert row['current_level']==1 and row['level_source']=='completed_course_record'
    assert row['level_record_id']==record['id'] and row['last_training_date']==''
    assert row['actual_course_id'] is None
    assess(d,0)
    assert cell(d)['current_level']==0 and cell(d)['level_source']=='current_assessment'


def test_grade_without_actual_attendance_never_establishes_renewal_completion(matrix):
    d=matrix;course_record(d,'2026-10-01',4,False)
    post(d['admin'],d['h'],'training_requirements',{'company_id':1,'site_id':d['sites'][0]['id'],
            'competency_code':CODE,'required_level':3,'renewal_days':10,'active':1})
    row=cell(d)
    assert row['current_level']==4 and row['last_training_date']==''
    assert row['renewal_due'] is None and row['renewal_status']=='unknown_actual_completion'
    assert row['status']=='review_required'


def test_actual_attendance_drives_expiry_and_evidence_reference(matrix):
    d=matrix;course,record=course_record(d,'2026-10-01',4,True)
    policy=post(d['admin'],d['h'],'training_requirements',{'company_id':1,'site_id':d['sites'][0]['id'],
            'competency_code':CODE,'required_level':3,'renewal_days':10,'active':1})
    row=cell(d,as_of='2026-10-20')
    assert row['actual_course_id']==course['id'] and row['level_record_id']==record['id']
    assert row['last_training_date']=='2026-10-01' and row['renewal_due_date']=='2026-10-11'
    assert row['renewal_due'] and row['status']=='renewal_due'
    assert any(source['record_id']==policy['id'] for source in row['requirement_sources'])
    earlier=cell(d,as_of='2026-09-01')
    assert earlier['current_level'] is None and earlier['last_training_date']==''


def test_current_job_and_site_after_transfer_do_not_retag_historical_evidence(matrix):
    d=matrix;assess(d,4)
    response=d['admin'].post('/api/data/requirements?company_id=1',headers=d['h'],json={'job':'Technician','skill_id':d['skills'][1],'level':2})
    assert response.status_code==201,response.json
    moved=d['admin'].put('/api/hrm/people/'+str(d['people'][0]['id']),headers=d['h'],json={
            'site_id':d['sites'][1]['id'],'job':'Technician','department':'Production','effective_date':'2026-10-04','version':d['people'][0]['version']})
    assert moved.status_code==200,moved.json
    trainer_a,_=d['account']('matrix-site-a',{'read':True})
    trainer_b,hb=d['account']('matrix-site-b',ALL,d['sites'][1]['id'])
    assert trainer_a.get('/api/hrm/skill-matrix?employee_id='+str(d['people'][0]['id'])).status_code==403
    row=cell(d,trainer_b)
    assert row['job']=='Technician' and row['job_required_level']==2 and row['current_level'] is None
    new=post(trainer_b,hb,'training_assessments',{'employee_id':d['people'][0]['id'],'competency_code':CODE,
             'level':0,'assessment_date':'2026-10-05'})
    assert cell(d,trainer_b)['assessment_id']==new['id'] and cell(d)['current_level']==0
    with sqlite3.connect(d['app'].config['DB']) as conn:
        assert conn.execute('SELECT job FROM employees WHERE id=?',(d['people'][0]['id'],)).fetchone()[0]=='Operator'


def test_shared_bank_code_is_one_column_across_distinct_company_skill_ids(matrix):
    d=matrix;assess(d,2);assess(d,3,person=2)
    result=report(d,employee_id='')
    columns=[row for row in result['competencies'] if row['code']==CODE]
    assert len(columns)==1 and d['skills'][1]!=d['skills'][2]
    first=next(row for row in result['rows'] if row['employee_id']==d['people'][0]['id'] and row['competency_code']==CODE)
    other=next(row for row in result['rows'] if row['employee_id']==d['people'][2]['id'] and row['competency_code']==CODE)
    assert first['skill_id']!=other['skill_id'] and (first['current_level'],other['current_level'])==(2,3)
    assert other['required_level'] is None and other['gap'] is None and other['status']=='requirement_missing'


def test_independent_export_grant_scope_and_safe_branded_workbook(matrix):
    d=matrix
    renamed=d['admin'].put('/api/hrm/people/'+str(d['people'][0]['id']),headers=d['h'],json={
             'version':d['people'][0]['version'],'name':'=UNSAFE()'})
    assert renamed.status_code==200,renamed.json
    exporter,_=d['account']('matrix-export-only',{'export':True})
    reader,_=d['account']('matrix-read-only',{'read':True})
    assert exporter.get('/api/hrm/skill-matrix?company_id=1').status_code==403
    assert reader.get('/api/hrm/skill-matrix?company_id=1').status_code==200
    assert reader.get('/api/hrm/reports/skill-matrix.xlsx?company_id=1').status_code==403
    response=exporter.get('/api/hrm/reports/skill-matrix.xlsx?company_id=1')
    assert response.status_code==200,response.json
    wb=openpyxl.load_workbook(io.BytesIO(response.data))
    assert wb.sheetnames==['ماتریس شایستگی','جزئیات و شواهد','تعریف سطح‌ها']
    assert all(sheet.sheet_view.rightToLeft for sheet in wb)
    assert wb.worksheets[0]['B5'].value=="'=UNSAFE()"
    assert 'Matrix person 2' not in str(list(wb.worksheets[0].values))
    assert all(cell.data_type!='f' for sheet in wb for line in sheet for cell in line)
    assert exporter.get('/api/hrm/skill-matrix.xlsx?site_id='+str(d['sites'][1]['id'])).status_code==403


def test_modern_evaluator_and_source_metadata_are_traceable_unknown_legacy_stays_unknown(matrix):
    d=matrix;created=assess(d,3,assessor_name='Actual evaluator',assessment_method='Practical demonstration',evidence='Observed task reference MX-7')
    row=cell(d)
    assert row['assessment_id']==created['id'] and row['assessment_date']=='2026-10-03'
    assert row['assessor_name']=='Actual evaluator' and row['assessment_method']=='Practical demonstration'
    assert row['assessment_evidence']=='Observed task reference MX-7'
    assert row['recorded_by']['username']=='matrix-admin'
    legacy=d['admin'].post('/api/data/assessments?company_id=2',headers=d['h'],json={
            'employee_id':d['people'][2]['id'],'skill_id':d['skills'][2],'level':0})
    assert legacy.status_code==201,legacy.json
    unknown=cell(d,employee_id=d['people'][2]['id'])
    assert unknown['current_level']==0 and unknown['assessment_date']==''
    assert unknown['assessor_name'] is None and unknown['recorded_by'] is None


def test_hse_curriculum_is_an_applicable_requirement_without_exposing_hse_notes(matrix):
    d=matrix
    policy=post(d['admin'],d['h'],'hse_training_requirements',{'site_id':d['sites'][0]['id'],'job':'Operator',
             'domain':'safety','competency_code':CODE,'required_level':4,'renewal_days':30,'active':1,'notes':'HSE-PRIVATE-POLICY-NOTE'})
    trainer,_=d['account']('matrix-curriculum-read',{'read':True})
    row=cell(d,trainer)
    assert row['required_level']==4 and row['renewal_days']==30
    source=next(source for source in row['requirement_sources'] if source['kind']=='hse_curriculum')
    assert source['record_id']==policy['id'] and source['domain']=='safety'
    assert 'HSE-PRIVATE-POLICY-NOTE' not in trainer.get('/api/hrm/skill-matrix?company_id=1').get_data(as_text=True)
    assert trainer.get('/api/hrm/health_records?company_id=1').status_code==403


def test_legacy_read_matrices_delegate_current_context_without_rewriting_full_document(matrix):
    d=matrix
    assert d['admin'].post('/api/data/requirements?company_id=1',headers=d['h'],json={'job':'Technician','skill_id':d['skills'][1],'level':1}).status_code==201
    changed=d['admin'].put('/api/hrm/people/'+str(d['people'][0]['id']),headers=d['h'],json={
        'job':'Technician','effective_date':'2026-10-01','version':d['people'][0]['version']})
    assert changed.status_code==200,changed.json
    workspace=d['admin'].get('/api/assessment-workspace/'+str(d['people'][0]['id'])+'?company_id=1')
    assert workspace.status_code==200,workspace.json
    row=next(row for row in workspace.json['matrix'] if row['code']==CODE)
    assert row['required']==1 and row['current'] is None and row['gap'] is None
    passport=d['admin'].get('/api/passports/'+str(d['people'][0]['id'])+'?company_id=1')
    assert passport.status_code==200,passport.json
    assert passport.json['current_assignment']['job']=='Technician'
    assert next(row for row in passport.json['matrix'] if row['code']==CODE)['required']==1
    assert passport.json['employee']['passport_number']==d['people'][0]['passport_number']


def test_dated_grade_without_real_evaluator_and_evidence_is_not_green(matrix):
    d=matrix
    assess(d,4)
    row=cell(d)
    assert row['gap']==0 and row['current_level']==4
    assert row['status']=='review_required'
    assert {'assessor_not_recorded','assessment_method_not_recorded','assessment_evidence_not_recorded'} <= set(row['data_quality_flags'])
