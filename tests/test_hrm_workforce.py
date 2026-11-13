"""Operational workforce flows, scope boundaries and branded Excel transactions."""
import io
import sqlite3

import openpyxl
import pytest

from app import create_app
from app.hrm_common import date_value

PASSWORD = 'Workforce-test-password-123'


def login(app, name):
    client = app.test_client()
    response = client.post('/api/login', json={'username': name, 'password': PASSWORD})
    assert response.status_code == 200, response.json
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


@pytest.fixture
def workforce(tmp_path):
    app = create_app(tmp_path, True)
    admin = app.test_client()
    assert admin.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    admin, ah = login(app, 'admin')
    employees = []
    for cid, code, department in ((1, '00101', 'واحد الف'), (1, '00102', 'واحد ب'), (2, '00101', 'واحد الف')):
        response = admin.post(f'/api/data/employees?company_id={cid}', headers=ah, json={
            'code': code, 'name': 'شاغل ' + str(len(employees) + 1), 'department': department,
            'job': 'کارشناس'})
        assert response.status_code == 201, response.json
        employees.append(response.json['id'])
    all_permissions = {key: {'read': True, 'write': True} for key in ('attendance', 'performance', 'discipline', 'documents')}
    def account(name, role, scopes, permissions):
        response = admin.post('/api/hrm/access/users', headers=ah, json={
            'username': name, 'password': PASSWORD, 'role': role, 'scopes': scopes,
            'permissions': permissions, 'training_access': False})
        assert response.status_code == 201, response.json
        return login(app, name)
    hr, hh = account('holding-hr', 'holding_hr', [{'company_id': 1}, {'company_id': 2}], all_permissions)
    unit, uh = account('unit-manager', 'unit_manager', [{'company_id': 1, 'department': 'واحد الف'}], {
        key: {'read': True, 'write': True} for key in ('attendance', 'performance')})
    viewer, vh = account('executive', 'executive', [{'company_id': 1}], {
        key: {'read': True, 'write': False} for key in ('attendance', 'performance')})
    return dict(app=app, admin=admin, ah=ah, hr=hr, hh=hh, unit=unit, uh=uh, viewer=viewer, vh=vh, employees=employees)


def post(env, resource, values, client='hr', headers='hh'):
    response = env[client].post('/api/hrm/' + resource, headers=env[headers], json=values)
    assert response.status_code == 201, (resource, response.status_code, response.json)
    return response.json


def attendance(env, eid=None, **values):
    return post(env, 'attendance', dict(employee_id=eid or env['employees'][0],
                work_date='1405/01/05', kind='work', minutes=480, **values))


def period(env, cid=1, **values):
    body = dict(company_id=cid, title='ارزیابی ' + str(cid), start_date='1405/01/01',
                end_date='1405/06/31', status='open')
    body.update(values)
    return post(env, 'evaluation_periods', body)


def criterion(env, period_row, title='دقت', weight=1, max_score=10):
    return post(env, 'performance_criteria', dict(company_id=period_row['company_id'],
                period_id=period_row['id'], title=title, weight=weight, max_score=max_score))


def audit_count(env):
    with sqlite3.connect(env['app'].config['DB']) as conn:
        return conn.execute('SELECT count(*) FROM audit').fetchone()[0]


def test_attendance_dates_edits_versions_and_scope(workforce):
    e = workforce
    first = attendance(e)
    second = attendance(e, eid=e['employees'][1])
    attendance(e, eid=e['employees'][2])
    assert first['work_date'] == date_value('1405/01/05')
    assert first['company_id'] == 1 and first['version'] == 1
    assert {row['id'] for row in e['unit'].get('/api/hrm/attendance').json} == {first['id']}
    assert {row['id'] for row in e['viewer'].get('/api/hrm/attendance').json} == {first['id'], second['id']}
    assert e['admin'].get('/api/hrm/attendance').status_code == 200
    path = f"/api/hrm/attendance/{first['id']}"
    changed = e['unit'].put(path, headers=e['uh'], json={'version': 1, 'minutes': 420})
    assert changed.status_code == 200 and changed.json['version'] == 2
    before = audit_count(e)
    assert e['hr'].put(path, headers=e['hh'], json={'version': 1, 'minutes': 300}).status_code == 409
    assert audit_count(e) == before
    assert e['hr'].get(path).json['minutes'] == 420
    assert e['viewer'].put(path, headers=e['vh'], json={'version': 2, 'minutes': 300}).status_code == 403
    assert e['unit'].get(f"/api/hrm/attendance/{second['id']}").status_code == 403
    assert e['unit'].post('/api/hrm/attendance', headers=e['uh'], json={
        'employee_id': e['employees'][1], 'work_date': '1405/01/06', 'kind': 'leave', 'minutes': 30}).status_code == 403
    assert e['hr'].put(path, headers=e['hh'], json={'version': 2, 'employee_id': e['employees'][1]}).status_code == 400
    assert e['hr'].put(path, json={'version': 2, 'minutes': 50}).status_code == 403


@pytest.mark.parametrize('patch', [
    {'minutes': True}, {'minutes': 1441}, {'minutes': -1}, {'minutes': .5},
    {'work_date': '1405/07/31'}, {'kind': 'magic'}, {'source': 'forged'},
    {'work_date': '2026-02-30'}, {'minutes': 'NaN'}])
def test_invalid_attendance_is_atomic(workforce, patch):
    e = workforce
    body = dict(employee_id=e['employees'][0], work_date='1405/01/04', kind='work', minutes=480)
    body.update(patch)
    before = audit_count(e)
    response = e['hr'].post('/api/hrm/attendance', headers=e['hh'], json=body)
    assert response.status_code == 400, response.json
    assert e['hr'].get('/api/hrm/attendance').json == []
    assert audit_count(e) == before


def test_leave_policy_configured_explicitly_and_full_year_balance(workforce):
    e = workforce
    assert e['hr'].get('/api/hrm/attendance_policies').json == []
    policy = post(e, 'attendance_policies', dict(company_id=1, year=1405, title='ضابطه مصوب شرکت',
        annual_leave_minutes=600, max_leave_record_minutes=240))
    assert e['unit'].put(f"/api/hrm/attendance_policies/{policy['id']}", headers=e['uh'], json={
        'version': 1, 'annual_leave_minutes': 900}).status_code == 403
    for day, minutes in [('1405/01/01', 120), ('1405/04/01', 180)]:
        post(e, 'attendance', dict(employee_id=e['employees'][0], work_date=day, kind='leave', minutes=minutes))
    response = e['hr'].post('/api/hrm/attendance', headers=e['hh'], json=dict(
        employee_id=e['employees'][0], work_date='1405/04/02', kind='leave', minutes=241))
    assert response.status_code == 400
    report = e['hr'].get('/api/hrm/workforce_report?company_id=1&from=1405/04/01&to=1405/04/31').json
    assert report['attendance'][0]['leave_minutes'] == 180
    balances = {row['employee_id']: row for row in report['leave_balances']}
    assert balances[e['employees'][0]]['recorded_leave_minutes'] == 300
    assert balances[e['employees'][0]]['remaining_minutes'] == 300
    assert balances[e['employees'][1]]['remaining_minutes'] == 600
    assert e['employees'][2] not in balances
    unit_balances = e['unit'].get('/api/hrm/workforce_report?company_id=1').json['leave_balances']
    assert {row['employee_id'] for row in unit_balances} == {e['employees'][0]}


def test_weighted_performance_and_historical_locks(workforce):
    e = workforce
    term = period(e)
    one = criterion(e, term)
    two = criterion(e, term, 'کیفیت', 3, 100)
    first = post(e, 'performance_scores', dict(employee_id=e['employees'][0], period_id=term['id'],
        criterion_id=one['id'], score=5))
    partial = e['hr'].get('/api/hrm/workforce_report?company_id=1').json['performance'][0]
    assert partial['score_percent'] == 50 and partial['completion_percent'] == 25
    assert not partial['is_complete'] and partial['expected_criteria_count'] == 2
    post(e, 'performance_scores', dict(employee_id=e['employees'][0], period_id=term['id'], criterion_id=two['id'], score=100))
    summary = e['hr'].get('/api/hrm/workforce_report?company_id=1').json['performance'][0]
    assert summary['score_percent'] == 87.5 and summary['is_complete']
    assert summary['score_basis'] == 'recorded_criteria_only'
    assert e['hr'].put(f"/api/hrm/performance_criteria/{one['id']}", headers=e['hh'], json={
        'version': 1, 'weight': 99}).status_code == 409
    assert e['unit'].put(f"/api/hrm/evaluation_periods/{term['id']}", headers=e['uh'], json={
        'version': 1, 'status': 'closed'}).status_code == 403
    assert e['hr'].put(f"/api/hrm/evaluation_periods/{term['id']}", headers=e['hh'], json={
        'version': 1, 'status': 'closed'}).status_code == 200
    assert e['hr'].put(f"/api/hrm/performance_scores/{first['id']}", headers=e['hh'], json={
        'version': 1, 'score': 8}).status_code == 409
    assert e['hr'].delete(f"/api/hrm/performance_scores/{first['id']}", headers=e['hh'], json={'version': 1}).status_code == 409
    assert e['hr'].get(f"/api/hrm/performance_scores/{first['id']}").json['score'] == 5
    assert e['hr'].put(f"/api/hrm/evaluation_periods/{term['id']}", headers=e['hh'], json={
        'version': 2, 'status': 'open'}).status_code == 200
    assert e['hr'].put(f"/api/hrm/performance_scores/{first['id']}", headers=e['hh'], json={
        'version': 1, 'score': 8}).status_code == 200


def test_performance_cross_company_criteria_and_unit_write_are_guarded(workforce):
    e = workforce
    first_term, other_term = period(e), period(e, 2)
    first_metric, other_metric = criterion(e, first_term), criterion(e, other_term)
    assert e['unit'].post('/api/hrm/performance_criteria', headers=e['uh'], json=dict(
        company_id=1, period_id=first_term['id'], title='اثر بر سایر واحدها', weight=1, max_score=10)).status_code == 403
    body = dict(employee_id=e['employees'][0], period_id=first_term['id'], criterion_id=other_metric['id'], score=5)
    assert e['hr'].post('/api/hrm/performance_scores', headers=e['hh'], json=body).status_code == 400
    body['criterion_id'] = first_metric['id']
    assert e['unit'].post('/api/hrm/performance_scores', headers=e['uh'], json=body).status_code == 201
    body['employee_id'] = e['employees'][1]
    assert e['unit'].post('/api/hrm/performance_scores', headers=e['uh'], json=body).status_code == 403
    body.update(employee_id=e['employees'][1], score=11)
    assert e['hr'].post('/api/hrm/performance_scores', headers=e['hh'], json=body).status_code == 400


def test_discipline_requires_separate_permission_and_same_person_document(workforce):
    e = workforce
    with sqlite3.connect(e['app'].config['DB']) as conn:
        # Same real document table; an attachment is optional but when supplied
        # must be in the proper confidentiality group and the same person's file.
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='hrm_documents'").fetchone()
    own_doc = e['hr'].post('/api/hrm/documents', headers=e['hh'], data={
        'employee_id': str(e['employees'][0]), 'title': 'مستند تذکر', 'kind': 'warning',
        'permission_group': 'discipline', 'file': (io.BytesIO(b'%PDF-1.4\n%%EOF'), 'warning.pdf')})
    assert own_doc.status_code == 201, own_doc.json
    entry = post(e, 'discipline', dict(employee_id=e['employees'][0], event_date='1405/01/05',
                 kind='warning', title='رویداد مستند', document_id=own_doc.json['id']))
    assert e['admin'].get('/api/hrm/discipline').status_code == 200
    assert e['viewer'].get('/api/hrm/discipline').status_code == 403
    assert e['unit'].get(f"/api/hrm/discipline/{entry['id']}").status_code == 403
    assert e['hr'].post('/api/hrm/discipline', headers=e['hh'], json=dict(
        employee_id=e['employees'][1], event_date='1405/01/05', kind='warning', title='سند فرد دیگر',
        document_id=own_doc.json['id'])).status_code == 400


def workbook(env, rows):
    payload = env['hr'].get('/api/hrm/attendance/template.xlsx?company_id=1')
    assert payload.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(payload.data))
    sheet = book['ورود اطلاعات']
    keys = {cell.value: cell.column for cell in sheet[5]}
    for row_number, values in enumerate(rows, 7):
        for key, value in values.items():
            sheet.cell(row_number, keys[key], value)
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def import_sheet(env, payload, mode='preview', policy='insert', client='hr', headers='hh', cid=1):
    return env[client].post(f'/api/hrm/attendance/import?company_id={cid}', headers=env[headers], data={
        'mode': mode, 'policy': policy, 'file': (io.BytesIO(payload), 'attendance.xlsx')})


def test_attendance_excel_real_preview_atomic_commit_and_repeat(workforce):
    e = workforce
    one = dict(employee_code='00101', work_date='1405/03/01', kind='work', minutes=480, note='از سیستم تردد')
    bad = workbook(e, [one, dict(employee_code='00102', work_date='1405/03/01', kind='late', minutes=-1)])
    for mode in ('preview', 'commit'):
        response = import_sheet(e, bad, mode)
        assert response.status_code == 200 and not response.json['can_commit'], response.json
        assert response.json['errors'][0]['row'] == 8
        assert e['hr'].get('/api/hrm/attendance').json == []
    valid = workbook(e, [one, dict(employee_code='00102', work_date='1405/03/01', kind='late', minutes=10)])
    before_audit = audit_count(e)
    preview = import_sheet(e, valid)
    assert preview.json['can_commit'] and preview.json['added'] == 2 and not preview.json['applied']
    assert e['hr'].get('/api/hrm/attendance').json == [] and audit_count(e) == before_audit
    applied = import_sheet(e, valid, 'commit')
    assert applied.json['applied'] and applied.json['added'] == 2
    records = e['hr'].get('/api/hrm/attendance').json
    repeated = import_sheet(e, valid, 'commit')
    assert repeated.json['unchanged'] == 2 and e['hr'].get('/api/hrm/attendance').json == records
    changed = workbook(e, [dict(one, minutes=420, note='')])
    assert not import_sheet(e, changed, 'commit').json['can_commit']
    updated = import_sheet(e, changed, 'commit', 'upsert')
    assert updated.json['updated'] == 1
    first = next(row for row in e['hr'].get('/api/hrm/attendance').json if row['employee_code'] == '00101')
    assert first['minutes'] == 420 and first['note'] == 'از سیستم تردد'
    assert import_sheet(e, valid, cid=2).status_code == 400
    assert import_sheet(e, valid, client='viewer', headers='vh').status_code == 403
    assert not import_sheet(e, valid, client='unit', headers='uh').json['can_commit']


def test_attendance_excel_rejects_formula_and_duplicate_natural_key(workforce):
    e = workforce
    item = dict(employee_code='00101', work_date='1405/03/01', kind='work', minutes=480)
    duplicate = workbook(e, [item, item])
    response = import_sheet(e, duplicate, 'commit')
    assert response.status_code == 200 and not response.json['can_commit']
    assert 'تکراری' in response.json['errors'][0]['message']
    formula = workbook(e, [dict(item, note='=1+1')])
    assert not import_sheet(e, formula).json['can_commit']
    assert e['hr'].get('/api/hrm/attendance').json == []


def test_workforce_report_is_read_only_and_company_scoped(workforce):
    e = workforce
    attendance(e)
    attendance(e, eid=e['employees'][1])
    attendance(e, eid=e['employees'][2])
    before = audit_count(e)
    report = e['unit'].get('/api/hrm/workforce_report?company_id=1')
    assert report.status_code == 200
    assert [row['employee_id'] for row in report.json['attendance']] == [e['employees'][0]]
    assert e['unit'].get('/api/hrm/workforce_report?company_id=2').status_code == 403
    assert e['admin'].get('/api/hrm/workforce_report').status_code == 200
    assert audit_count(e) == before
    assert e['hr'].get('/api/hrm/workforce_report?from=1405/02/01&to=1405/01/01').status_code == 400


def scoped_account(env, name, scopes, flags, modules=('attendance',)):
    response = env['admin'].post('/api/hrm/access/users', headers=env['ah'], json={
        'username': name, 'password': PASSWORD, 'role': 'company_hr',
        'scopes': scopes, 'permissions': {module: flags for module in modules}, 'training_access': False})
    assert response.status_code == 201, response.json
    return login(env['app'], name)


def site(env, name):
    response = env['admin'].post('/api/hrm/sites?company_id=1', headers=env['ah'], json={'name': name})
    assert response.status_code == 201, response.json
    return response.json['id']


def place(env, eid, site_id, effective_date):
    path = f'/api/hrm/people/{eid}?company_id=1'
    current = env['admin'].get(path)
    assert current.status_code == 200, current.json
    changed = env['admin'].put(path, headers=env['ah'], json={
        'version': current.json['version'], 'site_id': site_id, 'effective_date': effective_date})
    assert changed.status_code == 200, changed.json
    return changed.json


def test_site_snapshots_preserve_old_history_after_person_transfers(workforce):
    e = workforce
    a, b = site(e, 'کارخانه یک'), site(e, 'کارخانه دو')
    eid = e['employees'][0]
    place(e, eid, a, '1405/01/01')
    place(e, e['employees'][1], b, '1405/01/01')
    flags = {op: True for op in ('read', 'create', 'edit', 'delete', 'export')}
    ca, ha = scoped_account(e, 'site-a', [{'company_id': 1, 'site_id': a}], flags, ('attendance', 'performance'))
    cb, hb = scoped_account(e, 'site-b', [{'company_id': 1, 'site_id': b}], flags, ('attendance', 'performance'))
    data = dict(employee_id=eid, work_date='1405/01/05', kind='work', minutes=480)
    old = ca.post('/api/hrm/attendance', headers=ha, json=data)
    assert old.status_code == 201, old.json
    old = old.json
    assert old['site_id'] == a and old['department'] == 'واحد الف'
    term = ca.post('/api/hrm/evaluation_periods', headers=ha, json=dict(company_id=1, site_id=a,
        title='دوره سایت یک', start_date='1405/01/01', end_date='1405/02/31', status='open'))
    assert term.status_code == 201, term.json
    metric = ca.post('/api/hrm/performance_criteria', headers=ha, json=dict(company_id=1, site_id=a,
        period_id=term.json['id'], title='شاخص سایت یک', weight=1, max_score=10))
    assert metric.status_code == 201, metric.json
    score = ca.post('/api/hrm/performance_scores', headers=ha, json=dict(employee_id=eid,
        period_id=term.json['id'], criterion_id=metric.json['id'], score=8))
    assert score.status_code == 201, score.json
    assert cb.get(f"/api/hrm/attendance/{old['id']}").status_code == 403
    assert cb.get('/api/hrm/attendance').json == []
    assert ca.get(f'/api/hrm/attendance?company_id=1&site_id={b}').status_code == 403
    place(e, eid, b, '1405/02/01')
    # The former site's authorized manager retains its historical facts; the
    # destination manager does not acquire that historical site's permissions.
    assert ca.get(f"/api/hrm/attendance/{old['id']}").status_code == 200
    assert cb.get(f"/api/hrm/attendance/{old['id']}").status_code == 403
    changed = ca.put(f"/api/hrm/attendance/{old['id']}", headers=ha, json={'version': 1, 'minutes': 420})
    assert changed.status_code == 200 and changed.json['site_id'] == a
    new = cb.post('/api/hrm/attendance', headers=hb, json=dict(data, work_date='1405/02/05'))
    assert new.status_code == 201 and new.json['site_id'] == b
    assert ca.post('/api/hrm/attendance', headers=ha, json=dict(data, work_date='1405/02/06')).status_code == 403
    report_a = ca.get(f'/api/hrm/workforce_report?company_id=1&site_id={a}')
    assert report_a.status_code == 200, report_a.json
    assert report_a.json['attendance'][0]['site_id'] == a
    assert report_a.json['performance'][0]['score_percent'] == 80
    assert not cb.get('/api/hrm/performance_scores').json
    assert cb.get('/api/hrm/workforce_report?company_id=1').json['performance'] == []
    # Company-wide definitions apply to each authorized site; personal facts
    # with an unknown site do not gain the same exception.
    global_period = period(e, title='تعریف مشترک شرکت')
    assert global_period['id'] in {row['id'] for row in ca.get('/api/hrm/evaluation_periods?company_id=1').json}
    unknown = e['admin'].post('/api/data/employees?company_id=1', headers=e['ah'], json={'code': 'NOSITE-9', 'name': 'تعیین سایت نشده'}).json['id']
    unknown_attendance = attendance(e, eid=unknown)
    assert ca.get(f"/api/hrm/attendance/{unknown_attendance['id']}").status_code == 403
    assert cb.get(f"/api/hrm/attendance/{unknown_attendance['id']}").status_code == 403


def test_independent_create_edit_delete_and_export_permissions(workforce):
    e = workforce
    original = attendance(e)
    flags = dict(read=True, create=True, edit=False, delete=False, export=False)
    creator, ch = scoped_account(e, 'creator-only', [{'company_id': 1}], flags)
    path = f"/api/hrm/attendance/{original['id']}"
    assert creator.get('/api/hrm/attendance').status_code == 200
    assert creator.post('/api/hrm/attendance', headers=ch, json=dict(employee_id=e['employees'][0],
        work_date='1405/03/02', kind='work', minutes=480)).status_code == 201
    assert creator.put(path, headers=ch, json={'version': 1, 'minutes': 20}).status_code == 403
    assert creator.delete(path, headers=ch, json={'version': 1}).status_code == 403
    assert creator.get('/api/hrm/attendance/template.xlsx?company_id=1').status_code == 403
    payload = workbook(e, [dict(employee_code='00101', work_date='1405/03/03', kind='work', minutes=480)])
    def upload(mode, policy):
        return creator.post('/api/hrm/attendance/import?company_id=1', headers=ch, data={
            'file': (io.BytesIO(payload), 'attendance.xlsx'), 'mode': mode, 'policy': policy})
    assert upload('commit', 'insert').json['applied']
    assert upload('preview', 'upsert').status_code == 403
    exporter, eh = scoped_account(e, 'export-only', [{'company_id': 1}], dict(
        read=True, create=False, edit=False, delete=False, export=True))
    assert exporter.get('/api/hrm/attendance/template.xlsx?company_id=1').status_code == 200
    assert exporter.post('/api/hrm/attendance', headers=eh, json=dict(employee_id=e['employees'][0],
        work_date='1405/03/04', kind='work', minutes=480)).status_code == 403
    deleter, dh = scoped_account(e, 'delete-only', [{'company_id': 1}], dict(
        read=True, create=False, edit=False, delete=True, export=False))
    assert deleter.delete(path, headers=dh, json={'version': 1}).status_code == 200
    assert e['admin'].get(path).status_code == 404


def test_archive_retains_original_keys_and_requires_explicit_versioned_restore(workforce):
    e = workforce
    original = attendance(e)
    path = f"/api/hrm/attendance/{original['id']}"
    archived = e['hr'].delete(path, headers=e['hh'], json={'version': 1})
    assert archived.status_code == 200 and archived.json['version'] == 2
    assert e['hr'].get(path).status_code == 404
    assert e['hr'].get('/api/hrm/attendance').json == []
    history = e['hr'].get(path + '?include_deleted=1').json
    assert history['is_deleted'] == 1 and history['work_date'] == original['work_date']
    assert history['employee_id'] == original['employee_id'] and history['deleted_at']
    with sqlite3.connect(e['app'].config['DB']) as connection:
        assert connection.execute('SELECT count(*) FROM hrm_attendance').fetchone()[0] == 1
    assert e['hr'].get('/api/hrm/workforce_report?include_deleted=1').json['attendance'] == []
    duplicate = e['hr'].post('/api/hrm/attendance', headers=e['hh'], json={
        'employee_id': original['employee_id'], 'work_date': original['work_date'], 'kind': 'work', 'minutes': 30})
    assert duplicate.status_code == 409 and 'بازیابی' in duplicate.json['error']
    payload = workbook(e, [dict(employee_code='00101', work_date='1405/01/05', kind='work', minutes=30)])
    blocked = import_sheet(e, payload, 'commit', 'upsert')
    assert not blocked.json['can_commit'] and not blocked.json['applied']
    restore = path + '/restore'
    assert e['hr'].post(restore, headers=e['hh'], json={'version': 1}).status_code == 409
    assert e['viewer'].post(restore, headers=e['vh'], json={'version': 2}).status_code == 403
    restored = e['hr'].post(restore, headers=e['hh'], json={'version': 2})
    assert restored.status_code == 200 and restored.json['id'] == original['id']
    assert restored.json['version'] == 3 and restored.json['is_deleted'] == 0
    assert import_sheet(e, payload, 'commit', 'upsert').json['updated'] == 1
    final = e['hr'].get(path).json
    assert final['minutes'] == 30 and final['id'] == original['id']
    assert final['work_date'] == original['work_date']
    assert e['hr'].get('/api/hrm/workforce_report').json['attendance'][0]['work_minutes'] == 30


def test_archived_scores_leave_history_and_do_not_drop_criteria_weights(workforce):
    e = workforce
    term = period(e)
    one, two = criterion(e, term), criterion(e, term, 'کیفیت', 3, 100)
    score = post(e, 'performance_scores', dict(employee_id=e['employees'][0], period_id=term['id'], criterion_id=one['id'], score=5))
    post(e, 'performance_scores', dict(employee_id=e['employees'][0], period_id=term['id'], criterion_id=two['id'], score=100))
    assert e['hr'].get('/api/hrm/workforce_report').json['performance'][0]['score_percent'] == 87.5
    path = f"/api/hrm/performance_scores/{score['id']}"
    assert e['hr'].delete(path, headers=e['hh'], json={'version': 1}).status_code == 200
    summary = e['hr'].get('/api/hrm/workforce_report').json['performance'][0]
    assert summary['score_percent'] == 100 and not summary['is_complete']
    assert summary['expected_criteria_count'] == 2
    assert e['hr'].get(path + '?include_deleted=1').json['score'] == 5
    assert e['hr'].delete(f"/api/hrm/performance_criteria/{one['id']}", headers=e['hh'], json={'version': 1}).status_code == 409
    assert e['hr'].delete(f"/api/hrm/evaluation_periods/{term['id']}", headers=e['hh'], json={'version': 1}).status_code == 409
    restored = e['hr'].post(path + '/restore', headers=e['hh'], json={'version': 2})
    assert restored.status_code == 200 and restored.json['id'] == score['id']
    assert e['hr'].get('/api/hrm/workforce_report').json['performance'][0]['score_percent'] == 87.5


def test_export_only_grant_can_export_without_acquiring_view_permission(workforce):
    e = workforce
    attendance(e)
    exporter, eh = scoped_account(e, 'true-export-only', [{'company_id': 1}], dict(
        read=False, create=False, edit=False, delete=False, export=True))
    assert exporter.get('/api/hrm/attendance').status_code == 403
    assert exporter.get('/api/hrm/attendance/template.xlsx?company_id=1').status_code == 200
    report = exporter.get('/api/hrm/reports/attendance.xlsx?company_id=1')
    assert report.status_code == 200, report.json
    book = openpyxl.load_workbook(io.BytesIO(report.data))
    try:
        values = [value for row in book.active.iter_rows(values_only=True) for value in row if value is not None]
        assert any('شاغل 1' in str(value) for value in values)
    finally:
        book.close()


def test_archived_foreign_site_rows_cannot_be_read_or_restored(workforce):
    e = workforce
    a, b = site(e, 'سایت بایگانی یک'), site(e, 'سایت بایگانی دو')
    place(e, e['employees'][0], a, '1405/01/01')
    row = attendance(e)
    flags = {op: True for op in ('read', 'create', 'edit', 'delete', 'export')}
    ca, ha = scoped_account(e, 'archive-site-a', [{'company_id': 1, 'site_id': a}], flags)
    cb, hb = scoped_account(e, 'archive-site-b', [{'company_id': 1, 'site_id': b}], flags)
    path = f"/api/hrm/attendance/{row['id']}"
    assert ca.delete(path, headers=ha, json={'version': 1}).status_code == 200
    assert ca.get(path + '?include_deleted=1').status_code == 200
    assert cb.get(path + '?include_deleted=1').status_code == 403
    assert cb.get('/api/hrm/attendance?include_deleted=1').json == []
    assert cb.post(path + '/restore', headers=hb, json={'version': 2}).status_code == 403
    assert ca.post(path + '/restore', headers=ha, json={'version': 2}).status_code == 200


def test_frontend_date_aliases_filter_attendance_and_trends_without_shortening_annual_balance(workforce):
    e = workforce
    eid = e['employees'][0]
    post(e, 'attendance_policies', dict(company_id=1, year=1405, title='سهمیه مصوب', annual_leave_minutes=600))
    for day, kind, minutes in [('1405/01/05', 'work', 480), ('1405/01/05', 'leave', 120),
                               ('1405/04/05', 'work', 420), ('1405/04/05', 'leave', 180)]:
        post(e, 'attendance', dict(employee_id=eid, work_date=day, kind=kind, minutes=minutes))
    first_period = period(e, title='دوره زمستان', start_date='1405/01/01', end_date='1405/01/31')
    next_period = period(e, title='دوره تابستان', start_date='1405/04/01', end_date='1405/04/31')
    for term, score in ((first_period, 5), (next_period, 9)):
        metric = criterion(e, term)
        post(e, 'performance_scores', dict(employee_id=eid, period_id=term['id'], criterion_id=metric['id'], score=score))
    filters = 'company_id=1&from_date=1405/04/01&to_date=1405/04/31'
    response = e['hr'].get('/api/hrm/attendance?' + filters)
    assert response.status_code == 200, response.json
    assert {row['work_date'] for row in response.json} == {date_value('1405/04/05')}
    old_aliases = e['hr'].get('/api/hrm/attendance?company_id=1&from=1405/04/01&to=1405/04/31')
    assert old_aliases.json == response.json
    before_audit = audit_count(e)
    report = e['hr'].get('/api/hrm/workforce_report?' + filters)
    assert report.status_code == 200, report.json
    assert report.json['attendance'][0]['work_minutes'] == 420
    assert report.json['attendance'][0]['leave_minutes'] == 180
    balance = next(row for row in report.json['leave_balances'] if row['employee_id'] == eid)
    assert balance['recorded_leave_minutes'] == 300 and balance['remaining_minutes'] == 300
    assert [row['period_id'] for row in report.json['performance']] == [next_period['id']]
    assert report.json['performance'][0]['score_percent'] == 90
    assert audit_count(e) == before_audit
    for endpoint in ('attendance', 'workforce_report'):
        assert e['hr'].get('/api/hrm/' + endpoint + '?from_date=1405/05/01&to_date=1405/04/01').status_code == 400
        assert e['hr'].get('/api/hrm/' + endpoint + '?from_date=1405/04/01&from=1405/01/01').status_code == 400
