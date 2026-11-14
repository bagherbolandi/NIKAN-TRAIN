"""Central employee identity and scoped dossier facts across retained routes."""
import io
import sqlite3
from datetime import date

import openpyxl
import pytest

from app import create_app
from app.hrm_common import today_iso

PASSWORD = 'Central-identity-test-password-123'


@pytest.fixture
def system(tmp_path):
    app = create_app(tmp_path, True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'central-admin', 'password': PASSWORD}).status_code == 200
    assert client.post('/api/login', json={'username': 'central-admin', 'password': PASSWORD}).status_code == 200
    headers = {'X-CSRF-Token': client.get('/api/me').json['csrf']}
    def create(resource, body, cid=1):
        response = client.post('/api/hrm/' + resource + '?company_id=' + str(cid), headers=headers, json=body)
        assert response.status_code == 201, (resource, response.json)
        return response.json
    sites = [create('sites', {'name': title}, cid) for cid, title in [(1, 'Source A'), (1, 'Source B'), (2, 'Other company')]]
    people = [create('people', {'code': code, 'name': name, 'site_id': site['id'], 'job': 'Original job', 'department': 'Original unit'}, site['company_id'])
              for code, name, site in [('CENTRAL-1234', 'First central person', sites[0]), ('CENTRAL-5678', 'Second central person', sites[1])]]
    return app, client, headers, sites, people, create


def account(system, name, permissions, scopes, legacy=False):
    app, admin, headers, *_ = system
    response = admin.post('/api/hrm/access/users', headers=headers, json={
        'username': name, 'password': PASSWORD, 'role': 'holding_manager', 'scopes': scopes,
        'permissions': permissions, 'training_access': legacy})
    assert response.status_code == 201, response.json
    client = app.test_client()
    assert client.post('/api/login', json={'username': name, 'password': PASSWORD}).status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


def move(system, person, site):
    _, admin, headers, *_ = system
    response = admin.put(f'/api/hrm/people/{person["id"]}?company_id={person["company_id"]}', headers=headers,
        json={'version': person['version'], 'company_id': site['company_id'], 'site_id': site['id'],
              'job': 'Current job', 'department': 'Current unit', 'effective_date': today_iso()})
    assert response.status_code == 200, response.json
    return response.json


def form_file(admin, kind, cid, values):
    response = admin.get(f'/api/excel/templates/{kind}?company_id={cid}')
    assert response.status_code == 200, response.json
    workbook = openpyxl.load_workbook(io.BytesIO(response.data))
    sheet = workbook['ورود اطلاعات']
    keys = [cell.value for cell in sheet[5]]
    for index, row in enumerate([values] if isinstance(values, dict) else values, 7):
        for key, value in row.items():
            sheet.cell(index, keys.index(key) + 1, value)
    stream = io.BytesIO(); workbook.save(stream)
    return stream.getvalue()


def test_legacy_current_roster_duplicate_prevention_and_former_owner_write_denial(system):
    app, admin, headers, sites, people, _ = system
    person = move(system, people[0], sites[2])
    before = None
    with sqlite3.connect(app.config['DB']) as conn:
        before = conn.execute('SELECT company_id,department,job,passport_number FROM employees WHERE id=?', (person['id'],)).fetchone()
    assert person['id'] not in {row['id'] for row in admin.get('/api/data/employees?company_id=1').json}
    current = admin.get('/api/data/employees?company_id=2').json
    assert [(row['id'], row['company_id'], row['training_company_id'], row['job']) for row in current] == [(person['id'], 2, 1, 'Current job')]
    duplicate = admin.post('/api/data/employees?company_id=2', headers=headers,
                           json={'code': person['code'], 'name': 'Would duplicate current person'})
    assert duplicate.status_code == 409
    source, sh = account(system, 'former-employer-editor', {'training': {'read': True, 'edit': True},
                       'personnel': {'edit': True}}, [{'company_id': 1}], legacy=True)
    assert source.put(f'/api/data/employees/{person["id"]}?company_id=1', headers=sh,
                       json={'name': 'Forbidden source edit'}).status_code == 404
    assert admin.put(f'/api/data/employees/{person["id"]}?company_id=2', headers=headers,
                      json={'name': 'Central name corrected', 'job': 'Current job revised', 'version': person['version']}).status_code == 200
    actual = admin.get(f'/api/hrm/people/{person["id"]}?company_id=2').json
    assert actual['name'] == 'Central name corrected' and actual['job'] == 'Current job revised'
    assert admin.put(f'/api/data/employees/{person["id"]}?company_id=2', headers=headers,
                      json={'name': 'Stale overwrite', 'version': person['version']}).status_code == 409
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT company_id,department,job,passport_number FROM employees WHERE id=?', (person['id'],)).fetchone() == before
        assert conn.execute('SELECT count(*) FROM employees').fetchone()[0] == 2
    assert admin.delete(f'/api/data/employees/{person["id"]}?company_id=2', headers=headers).status_code == 409


def test_excel_employee_upsert_uses_current_identity_and_preserves_training_provenance(system):
    app, admin, headers, sites, people, _ = system
    person = move(system, people[0], sites[2])
    payload = form_file(admin, 'employees', 2, {'code': person['code'], 'name': 'Excel central name', 'job': 'Current job'})
    response = admin.post('/api/excel/import/employees?company_id=2', headers=headers,
                          data={'mode': 'commit', 'policy': 'upsert', 'file': (io.BytesIO(payload), 'people.xlsx')})
    assert response.status_code == 200, response.json
    assert response.json['updated'] == 1 and response.json['created'] == 0
    current = admin.get('/api/hrm/people?company_id=2').json
    assert len(current) == 1 and current[0]['id'] == person['id'] and current[0]['name'] == 'Excel central name'
    repeat = admin.post('/api/excel/import/employees?company_id=2', headers=headers,
                        data={'mode': 'commit', 'policy': 'upsert', 'file': (io.BytesIO(payload), 'people.xlsx')})
    assert repeat.status_code == 200 and repeat.json['unchanged'] == 1
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT company_id,job FROM employees WHERE id=?', (person['id'],)).fetchone() == (1, 'Original job')
        assert conn.execute('SELECT count(*) FROM employees').fetchone()[0] == 2


def test_all_personnel_history_collections_honor_employee_id_without_bypassing_scope(system):
    _, admin, headers, sites, people, create = system
    for person in people:
        create('education', {'employee_id': person['id'], 'degree': 'Degree ' + person['code']})
        create('contracts', {'employee_id': person['id'], 'number': person['code'], 'contract_type': 'Fixed', 'start_date': today_iso()})
    for resource in ('people', 'education', 'contracts', 'employment_history'):
        response = admin.get(f'/api/hrm/{resource}?company_id=1&employee_id={people[0]["id"]}')
        assert response.status_code == 200, response.json
        assert response.json and {row.get('employee_id', row['id']) for row in response.json} == {people[0]['id']}
    scoped, _ = account(system, 'site-one-history', {'personnel': {'read': True}, 'contracts': {'read': True}},
                         [{'company_id': 1, 'site_id': sites[0]['id']}])
    for resource in ('people', 'education', 'contracts', 'employment_history'):
        assert scoped.get(f'/api/hrm/{resource}?company_id=1&employee_id={people[1]["id"]}').json == []


def test_personnel_read_is_not_training_read_and_certificate_export_is_independent(system):
    _, admin, headers, sites, people, _ = system
    person = people[0]
    certificate = admin.post('/api/certificates?company_id=1', headers=headers, data={
        'employee_id': str(person['id']), 'title': 'Legacy company certificate',
        'file': (io.BytesIO(b'%PDF-1.4\nSynthetic training certificate\n%%EOF'), 'certificate.pdf')})
    assert certificate.status_code == 201, certificate.json
    personnel, _ = account(system, 'only-personnel', {'personnel': {'read': True}}, [{'company_id': 1}])
    assert personnel.get(f'/api/hrm/people/{person["id"]}/training?company_id=1').status_code == 403
    assert personnel.get(f'/api/hrm/people/{person["id"]}/training-certificates/{certificate.json["id"]}/file').status_code == 403
    reader, _ = account(system, 'training-read-only', {'training': {'read': True}}, [{'company_id': 1}])
    assert reader.get(f'/api/hrm/people/{person["id"]}/training').status_code == 200
    assert reader.get(f'/api/hrm/people/{person["id"]}/training-certificates/{certificate.json["id"]}/file').status_code == 403
    exporter, _ = account(system, 'training-export', {'training': {'export': True}}, [{'company_id': 1}])
    assert exporter.get(f'/api/hrm/people/{person["id"]}/training-certificates/{certificate.json["id"]}/file').status_code == 200
    narrow, _ = account(system, 'site-only-export', {'training': {'read': True, 'export': True}},
                        [{'company_id': 1, 'site_id': sites[0]['id']}])
    assert narrow.get(f'/api/hrm/people/{person["id"]}/training').json['certificates'] == []
    assert narrow.get(f'/api/hrm/people/{person["id"]}/training-certificates/{certificate.json["id"]}/file').status_code == 403


def test_training_dossier_hides_voided_and_former_site_facts_after_transfer(system):
    _, admin, headers, sites, people, create = system
    person = people[0]
    course = create('training_courses', {'company_id': 1, 'site_id': sites[0]['id'], 'title': 'Historical site A training',
                     'date': today_iso(), 'hours': 2, 'competency_code': 'S001', 'calendar_scope': 'internal'})
    record = create('training_records', {'employee_id': person['id'], 'course_id': course['id'], 'level': 2})
    grade = create('training_assessments', {'employee_id': person['id'], 'competency_code': 'S001', 'level': 3,
                   'assessment_date': today_iso()})
    initial = admin.get(f'/api/hrm/people/{person["id"]}/training').json
    assert [row['id'] for row in initial['records']] == [record['id']]
    assert [row['id'] for row in initial['assessments']] == [grade['id']]
    move(system, person, sites[1])
    current, _ = account(system, 'new-site-training', {'training': {'read': True}},
                         [{'company_id': 1, 'site_id': sites[1]['id']}])
    response = current.get(f'/api/hrm/people/{person["id"]}/training')
    assert response.status_code == 200 and response.json['records'] == [] and response.json['assessments'] == []
    assert admin.delete(f'/api/hrm/training_records/{record["id"]}', headers=headers,
                        json={'version': record['version']}).status_code == 200
    assert admin.delete(f'/api/hrm/training_assessments/{grade["id"]}', headers=headers,
                        json={'version': grade['version']}).status_code == 200
    after = admin.get(f'/api/hrm/people/{person["id"]}/training').json
    assert after['records'] == [] and after['assessments'] == []


def test_legacy_approval_uses_current_assignment_writer_and_keeps_original_job(system):
    app, admin, headers, sites, people, _ = system
    person = people[0]
    assert admin.post('/api/data/job_profiles?company_id=1', headers=headers,
                       json={'job': 'Approved current job'}).status_code == 201
    response = admin.put(f'/api/passports/{person["id"]}/approval?company_id=1', headers=headers,
                          json={'job': 'Approved current job'})
    assert response.status_code == 200, response.json
    assert admin.get(f'/api/hrm/people/{person["id"]}?company_id=1').json['job'] == 'Approved current job'
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT job FROM employees WHERE id=?', (person['id'],)).fetchone() == ('Original job',)
        assert conn.execute('SELECT job FROM hrm_employment_history WHERE employee_id=? ORDER BY id DESC LIMIT 1',
                            (person['id'],)).fetchone() == ('Approved current job',)
    reader, rh = account(system, 'training-job-no-personnel', {'training': {'read': True, 'edit': True}},
                          [{'company_id': 1}], legacy=True)
    assert reader.put(f'/api/passports/{person["id"]}/approval?company_id=1', headers=rh,
                       json={'job': 'Approved current job'}).status_code == 403
    current = admin.get(f'/api/hrm/people/{person["id"]}?company_id=1').json
    moved = move(system, current, sites[2])
    assert admin.post('/api/data/job_profiles?company_id=2', headers=headers,
                       json={'job': 'Different employer approval'}).status_code == 201
    denied = admin.put(f'/api/passports/{person["id"]}/approval?company_id=2', headers=headers,
                       json={'job': 'Different employer approval'})
    assert denied.status_code == 409
    assert admin.get(f'/api/hrm/people/{person["id"]}?company_id=2').json['job'] == moved['job']
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT company_id,hr_approved FROM employee_meta WHERE employee_id=?',
                            (person['id'],)).fetchone() == (1, 1)


def test_legacy_workbook_personnel_import_reuses_transferred_central_employee(system):
    app, admin, headers, sites, people, _ = system
    person = move(system, people[0], sites[2])
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    bank = workbook.create_sheet('کاتالوگ_مهارت')
    bank.cell(6, 2, 'S001'); bank.cell(6, 3, 'Existing competency')
    jobs = workbook.create_sheet('شناسنامه_شغل')
    jobs.cell(4, 5, 'Current job')
    matrix = workbook.create_sheet('ماتریس_مهارت')
    matrix.cell(4, 13, 'S001')
    for column, value in [(2, person['code']), (3, 'STALE WORKBOOK NAME'), (4, 'Current job'),
                          (5, 'Current unit'), (13, 'I')]:
        matrix.cell(6, column, value)
    payload = io.BytesIO(); workbook.save(payload)
    response = admin.post('/api/import?company_id=2', headers=headers,
                          data={'personnel': 'yes', 'file': (io.BytesIO(payload.getvalue()), 'legacy.xlsx')})
    assert response.status_code == 200, response.json
    assert response.json['employees'] == 0
    assert admin.get('/api/hrm/people?company_id=2').json[0]['name'] == person['name']
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM employees').fetchone()[0] == 2
        assert conn.execute('SELECT employee_id,company_id FROM assessments').fetchall() == [(person['id'], 2)]


def test_authorized_old_training_facts_expose_history_identity_without_current_placement(system):
    _, admin, headers, sites, people, _ = system
    person = people[0]
    certificate = admin.post('/api/certificates?company_id=1', headers=headers, data={
        'employee_id': str(person['id']), 'title': 'Authorized historical certificate',
        'file': (io.BytesIO(b'%PDF-1.4\nHistorical authorized certificate\n%%EOF'), 'old.pdf')}).json
    move(system, person, sites[2])
    old, _ = account(system, 'original-training-owner', {'training': {'read': True, 'export': True}},
                     [{'company_id': 1}])
    response = old.get(f'/api/hrm/people/{person["id"]}/training')
    assert response.status_code == 200, response.json
    assert response.json['employee'] == {'id': person['id'], 'code': person['code'], 'name': person['name'], 'history_only': True}
    assert [row['id'] for row in response.json['certificates']] == [certificate['id']]
    assert old.get(f'/api/hrm/people/{person["id"]}/training-certificates/{certificate["id"]}/file').status_code == 200
    assert old.get(f'/api/hrm/people/{person["id"]}').status_code == 403


def test_reassessment_history_authorizes_old_site_without_replacing_current_zero(system):
    _, admin, _, sites, people, create = system
    person = people[0]
    grade = create('training_assessments', {'employee_id': person['id'], 'competency_code': 'S001',
                   'level': 4, 'assessment_date': today_iso()})
    move(system, person, sites[1])
    renewed = create('training_assessments', {'employee_id': person['id'], 'competency_code': 'S001',
                     'level': 0, 'assessment_date': today_iso()})
    assert renewed['id'] == grade['id']
    former, _ = account(system, 'former-site-assessment-history', {'training': {'read': True}},
                        [{'company_id': 1, 'site_id': sites[0]['id']}])
    response = former.get(f'/api/hrm/people/{person["id"]}/training')
    assert response.status_code == 200, response.json
    assert response.json['employee']['history_only'] is True
    assert 'site_id' not in response.json['employee'] and 'job' not in response.json['employee']
    assert response.json['assessments'] == []
    assert [row['level'] for row in response.json['assessment_history']] == [4]
    current, _ = account(system, 'current-site-assessment-zero', {'training': {'read': True}},
                         [{'company_id': 1, 'site_id': sites[1]['id']}])
    response = current.get(f'/api/hrm/people/{person["id"]}/training')
    assert response.status_code == 200, response.json
    assert [row['level'] for row in response.json['assessments']] == [0]
    assert response.json['assessment_history'] == []


@pytest.mark.parametrize('kind', ['assessments', 'records'])
def test_legacy_excel_cannot_write_a_modern_training_fact_or_its_context(system, kind):
    app, admin, headers, sites, people, create = system
    person = people[0]
    if kind == 'assessments':
        modern = create('training_assessments', {'employee_id': person['id'], 'competency_code': 'S001',
                        'level': 0, 'assessment_date': today_iso()})
        values = {'employee_code': person['code'], 'skill_code': 'S001', 'level': 4}
        context = 'hrm_training_assessment_context'
    else:
        course = create('training_courses', {'company_id': 1, 'site_id': sites[0]['id'],
                        'title': 'Versioned course', 'date': today_iso(), 'hours': 2,
                        'competency_code': 'S001', 'calendar_scope': 'internal'})
        modern = create('training_records', {'employee_id': person['id'], 'course_id': course['id'],
                        'level': 0, 'record_date': today_iso(), 'score': 20})
        with sqlite3.connect(app.config['DB']) as conn:
            course_code = conn.execute('SELECT external_code FROM courses WHERE id=?', (course['id'],)).fetchone()[0]
        values = {'employee_code': person['code'], 'course_code': course_code, 'level': 4, 'score': 95}
        context = 'hrm_training_record_context'
    with sqlite3.connect(app.config['DB']) as conn:
        before = (conn.execute('SELECT * FROM ' + kind).fetchall(), conn.execute('SELECT * FROM ' + context).fetchall())
    payload = form_file(admin, kind, 1, values)
    response = admin.post('/api/excel/import/' + kind + '?company_id=1', headers=headers,
                          data={'mode': 'commit', 'policy': 'upsert', 'file': (io.BytesIO(payload), 'legacy.xlsx')})
    assert response.status_code == 400 and response.json['applied'] is False, response.json
    assert 'یکپارچه' in response.json['errors'][0]['message']
    with sqlite3.connect(app.config['DB']) as conn:
        assert (conn.execute('SELECT * FROM ' + kind).fetchall(), conn.execute('SELECT * FROM ' + context).fetchall()) == before


def test_late_managed_assessment_conflict_rolls_back_whole_legacy_excel_batch(system):
    app, admin, headers, _, people, create = system
    modern = create('training_assessments', {'employee_id': people[0]['id'], 'competency_code': 'S001',
                    'level': 0, 'assessment_date': today_iso()})
    payload = form_file(admin, 'assessments', 1, [
        {'employee_code': people[1]['code'], 'skill_code': 'S001', 'level': 2},
        {'employee_code': people[0]['code'], 'skill_code': 'S001', 'level': 4},
    ])
    response = admin.post('/api/excel/import/assessments?company_id=1', headers=headers,
                          data={'mode': 'commit', 'policy': 'upsert', 'file': (io.BytesIO(payload), 'batch.xlsx')})
    assert response.status_code == 400 and response.json['applied'] is False
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT id,employee_id,level FROM assessments').fetchall() == [(modern['id'], people[0]['id'], 0)]
        assert conn.execute('SELECT version FROM hrm_training_assessment_context').fetchall() == [(modern['version'],)]


def test_late_managed_assessment_conflict_rolls_back_raw_workbook_people_and_passport_counter(system):
    app, admin, headers, _, people, create = system
    modern = create('training_assessments', {'employee_id': people[0]['id'], 'competency_code': 'S001',
                    'level': 0, 'assessment_date': today_iso()})
    with sqlite3.connect(app.config['DB']) as conn:
        before_people = conn.execute('SELECT * FROM employees ORDER BY id').fetchall()
        before_registry = conn.execute('SELECT * FROM company_registry').fetchall()
        before_context = conn.execute('SELECT * FROM hrm_training_assessment_context').fetchall()
    workbook = openpyxl.Workbook(); workbook.remove(workbook.active)
    bank = workbook.create_sheet('کاتالوگ_مهارت')
    bank.cell(6, 2, 'S001'); bank.cell(6, 3, 'Existing competency')
    workbook.create_sheet('شناسنامه_شغل').cell(4, 5, 'Original job')
    matrix = workbook.create_sheet('ماتریس_مهارت'); matrix.cell(4, 13, 'S001')
    for row, code in [(6, 'NEW-ROLLBACK-1234'), (7, people[0]['code'])]:
        for column, value in [(2, code), (3, 'Workbook person'), (4, 'Original job'), (13, 'O')]:
            matrix.cell(row, column, value)
    payload = io.BytesIO(); workbook.save(payload)
    response = admin.post('/api/import?company_id=1', headers=headers,
                          data={'personnel': 'yes', 'file': (io.BytesIO(payload.getvalue()), 'batch.xlsx')})
    assert response.status_code == 400 and 'یکپارچه' in response.json['error'], response.json
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT * FROM employees ORDER BY id').fetchall() == before_people
        assert conn.execute('SELECT * FROM company_registry').fetchall() == before_registry
        assert conn.execute('SELECT * FROM hrm_training_assessment_context').fetchall() == before_context
        assert conn.execute('SELECT id,employee_id,level FROM assessments').fetchall() == [(modern['id'], people[0]['id'], 0)]
