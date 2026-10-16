import io
import json
import sqlite3
import zipfile

import openpyxl
import pytest

from app import create_app
from app.excel_forms import DATA_SHEET, SCHEMAS


PASSWORD = 'Excel-test-password-123'


@pytest.fixture
def ready(tmp_path):
    app = create_app(tmp_path, True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'excel-admin', 'password': PASSWORD}).status_code == 200
    assert client.post('/api/login', json={'username': 'excel-admin', 'password': PASSWORD}).status_code == 200
    headers = {'X-CSRF-Token': client.get('/api/me').json['csrf']}
    assert client.post('/api/data/job_profiles?company_id=1', headers=headers, json={'job': 'Engineer', 'department': 'Technical', 'duties': 'Ideal master duties', 'purpose': 'Master purpose'}).status_code == 201
    assert client.post('/api/data/instructors?company_id=1', headers=headers, json={'name': 'Trainer'}).status_code == 201
    assert client.post('/api/data/employees?company_id=1', headers=headers, json={'code': 'E1', 'name': 'Person one', 'job': 'Engineer', 'department': 'Technical'}).status_code == 201
    skill = client.post('/api/competency-bank/S001/select?company_id=1', headers=headers).json['id']
    assert client.post('/api/data/courses?company_id=1', headers=headers, json={'skill_id': skill, 'title': 'Existing course', 'date': '1405/07/23', 'hours': 2}).status_code == 201
    return app, client, headers


def workbook(kind, rows, cid=1):
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = DATA_SHEET
    for index, field in enumerate(SCHEMAS[kind]['columns'], 1):
        sheet.cell(5, index, field['key'])
        sheet.cell(6, index, field['label'])
    for row_index, row in enumerate(rows, 7):
        for column_index, field in enumerate(SCHEMAS[kind]['columns'], 1):
            if field['key'] in row:
                sheet.cell(row_index, column_index, row[field['key']])
    meta = book.create_sheet('_nikan')
    for item in [('schema_version', '1'), ('kind', kind), ('company_id', cid)]:
        meta.append(item)
    meta.sheet_state = 'veryHidden'
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


def submit(client, headers, kind, rows=None, *, payload=None, mode='preview', policy='insert', cid=1):
    return client.post(f'/api/excel/import/{kind}?company_id={cid}', headers=headers, data={'file': (io.BytesIO(payload if payload is not None else workbook(kind, rows, cid)), 'form.xlsx'), 'mode': mode, 'policy': policy})


def snapshot(app):
    with sqlite3.connect(app.config['DB']) as connection:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall() for name in names}


def imported_row(client, kind):
    base = {
        'employees': {'code': 'E2', 'name': 'Person two', 'job': 'Engineer'},
        'job_profiles': {'job': 'Technician', 'department': 'Technical', 'purpose': 'New full master'},
        'competency_selection': {'skill_code': 'S002'},
        'requirements': {'job': 'Engineer', 'skill_code': 'S001', 'level': 3},
        'assessments': {'employee_code': 'E1', 'skill_code': 'S001', 'level': 2},
        'courses': {'course_code': 'XLS-C1', 'skill_code': 'S001', 'title': 'Spreadsheet course', 'date': '1405/08/01', 'hours': 3, 'instructor': 'Trainer'},
        'records': {'employee_code': 'E1', 'course_code': client.get('/api/data/courses?company_id=1').json[0]['external_code'], 'level': 3, 'score': 90},
        'instructors': {'name': 'Second trainer', 'specialty': 'Safety', 'rate': 4},
        'needs': {'skill_code': 'S001', 'reason': 'Spreadsheet need', 'year': 1405, 'priority': 3},
        'personal_requirements': {'employee_code': 'E1', 'skill_code': 'S001', 'level': 4},
        'personal_documents': {'employee_code': 'E1', 'duties': 'Actual person duties'},
        'competency-bank': {'code': 'XLS-SKILL', 'title': 'Spreadsheet competency', 'importance': 2},
        'companies': {'name': 'Spreadsheet company'},
    }
    return base[kind]


@pytest.mark.parametrize('kind', list(SCHEMAS))
def test_all_forms_preview_rolls_back_and_repeated_commit_is_idempotent(ready, kind):
    app, client, headers = ready
    row = imported_row(client, kind)
    template = client.get(f'/api/excel/templates/{kind}?company_id=1')
    assert template.status_code == 200
    book = openpyxl.load_workbook(io.BytesIO(template.data))
    for index, field in enumerate(SCHEMAS[kind]['columns'], 1):
        if field['key'] in row:
            book[DATA_SHEET].cell(7, index, row[field['key']])
    output = io.BytesIO()
    book.save(output)
    book.close()
    payload = output.getvalue()
    before = snapshot(app)
    preview = submit(client, headers, kind, payload=payload)
    assert preview.status_code == 200, preview.json
    assert preview.json['can_commit'] and preview.json['created'] == 1
    assert not preview.json['applied']
    assert snapshot(app) == before, 'Preview changed records, audit, attachments or metadata counters'
    committed = submit(client, headers, kind, payload=payload, mode='commit')
    assert committed.status_code == 200, committed.json
    assert committed.json['applied'] and committed.json['created'] == 1
    after = snapshot(app)
    repeated = submit(client, headers, kind, payload=payload, mode='commit')
    assert repeated.status_code == 200, repeated.json
    assert repeated.json['unchanged'] == 1 and not repeated.json['updated'] and not repeated.json['created']
    assert snapshot(app) == after


def test_default_insert_never_updates_existing_and_explicit_update_preserves_blank_fields(ready):
    app, client, headers = ready
    rows = [{'code': 'E1', 'name': 'Reviewed new name'}]
    before = snapshot(app)
    preview = submit(client, headers, 'employees', rows)
    assert not preview.json['can_commit'] and preview.json['errors'][0]['row'] == 7
    assert submit(client, headers, 'employees', rows, mode='commit').status_code == 400
    assert snapshot(app) == before
    preview = submit(client, headers, 'employees', rows, policy='upsert')
    assert preview.json['updated'] == 1 and preview.json['can_commit']
    assert snapshot(app) == before
    committed = submit(client, headers, 'employees', rows, policy='upsert', mode='commit')
    assert committed.json['updated'] == 1
    employee = client.get('/api/data/employees?company_id=1').json[0]
    assert employee['name'] == 'Reviewed new name'
    assert employee['job'] == 'Engineer' and employee['department'] == 'Technical'


def test_whitespace_only_optional_fields_never_clear_existing_values(ready):
    _, client, headers = ready
    result = submit(client, headers, 'employees', [{'code': 'E1', 'name': 'Name from Excel',
                                                 'job': '  \n ', 'department': '\t  '}],
                    mode='commit', policy='upsert')
    assert result.status_code == 200, result.json
    employee = client.get('/api/data/employees?company_id=1').json[0]
    assert employee['job'] == 'Engineer' and employee['department'] == 'Technical'


def test_bank_update_preserves_optional_metadata_and_propagates_title(ready):
    _, client, headers = ready
    assert client.put('/api/competency-bank/S001', headers=headers, json={
        'category': 'Existing category', 'importance': 3, 'method': 'Existing method',
        'reference': 'Existing reference'}).status_code == 200
    result = submit(client, headers, 'competency-bank', [{'code': 'S001', 'title': 'Reviewed title'}],
                    mode='commit', policy='upsert')
    assert result.status_code == 200 and result.json['updated'] == 1
    bank = next(row for row in client.get('/api/competency-bank').json if row['code'] == 'S001')
    assert {field: bank[field] for field in ('category', 'importance', 'method', 'reference')} == {
        'category': 'Existing category', 'importance': 3, 'method': 'Existing method', 'reference': 'Existing reference'}
    local = next(row for row in client.get('/api/data/skills?company_id=1').json if row['code'] == 'S001')
    assert local['title'] == 'Reviewed title' and local['importance'] == 3


@pytest.mark.parametrize('year', [1299, 1500])
def test_needs_reject_year_outside_standard_template_range(ready, year):
    app, client, headers = ready
    row = {**imported_row(client, 'needs'), 'year': year}
    before = snapshot(app)
    result = submit(client, headers, 'needs', [row], mode='commit')
    assert result.status_code == 400 and result.json['errors'][0]['row'] == 7
    assert snapshot(app) == before


def test_reference_jobs_include_existing_company_jobs_without_master_profiles(ready):
    _, client, headers = ready
    assert client.post('/api/data/employees?company_id=1', headers=headers,
                       json={'code': 'LEGACY', 'name': 'Existing employee', 'job': 'Legacy title',
                             'department': 'Historical unit'}).status_code == 201
    response = client.get('/api/excel/templates/employees?company_id=1')
    book = openpyxl.load_workbook(io.BytesIO(response.data))
    assert ('Legacy title', 'Historical unit') in list(book['مرجع مشاغل'].values)
    result = submit(client, headers, 'employees', [{'code': 'FROM-LEGACY', 'name': 'New employee',
                                                 'job': 'Legacy title'}], mode='commit')
    assert result.status_code == 200, result.json


@pytest.mark.parametrize('kind,rows', [
    ('employees', [{'code': 'NEW', 'name': 'Valid'}, {'code': 'INVALID', 'name': 'Invalid', 'job': 'Unknown job'}]),
    ('requirements', [{'job': 'Engineer', 'skill_code': 'S002', 'level': 3}, {'job': 'Engineer', 'skill_code': 'S003', 'level': 0}]),
    ('assessments', [{'employee_code': 'E1', 'skill_code': 'S002', 'level': 2}, {'employee_code': 'OTHER-COMPANY', 'skill_code': 'S003', 'level': 2}]),
    ('courses', [{'course_code': 'VALID', 'skill_code': 'S002', 'title': 'Valid', 'date': '1405/08/01', 'hours': 2}, {'course_code': 'INVALID', 'skill_code': 'S003', 'title': 'Invalid date', 'date': '1405/07/31', 'hours': 2}]),
])
def test_invalid_later_row_rolls_back_everything_including_attached_skills_and_counters(ready, kind, rows):
    app, client, headers = ready
    before = snapshot(app)
    for mode, status in [('preview', 200), ('commit', 400)]:
        response = submit(client, headers, kind, rows, mode=mode, policy='upsert')
        assert response.status_code == status, response.json
        assert not response.json['can_commit'] and not response.json['applied']
        assert response.json['errors'][0]['row'] == 8
        assert snapshot(app) == before


def test_repeated_keys_within_file_are_rejected_atomically(ready):
    app, client, headers = ready
    rows = [{'code': 'E2', 'name': 'First entry'}, {'code': 'E2', 'name': 'Last entry'}]
    before = snapshot(app)
    result = submit(client, headers, 'employees', rows, mode='commit', policy='upsert')
    assert result.status_code == 400
    assert result.json['errors'][0]['row'] == 8 and 'تکرار' in result.json['errors'][0]['message']
    assert snapshot(app) == before


def test_full_personal_snapshot_import_preserves_master_and_all_full_content(ready):
    app, client, headers = ready
    from app.documents import FIELDS
    long_text = 'شرح کامل وظایف و مسئولیت‌ها؛ ' * 200
    assert len(long_text) < 10000
    master = client.get('/api/data/job_profiles?company_id=1').json[0]
    employee = client.get('/api/data/employees?company_id=1').json[0]
    personal = [{'employee_code': 'E1', 'duties': long_text}]
    result = submit(client, headers, 'personal_documents', personal, mode='commit')
    assert result.status_code == 200, result.json
    with sqlite3.connect(app.config['DB']) as connection:
        actual = connection.execute('SELECT overrides,is_snapshot FROM personal_documents WHERE employee_id=?', (employee['id'],)).fetchone()
        stored = json.loads(actual[0])
        assert actual[1] == 1 and set(stored) == set(FIELDS)
        assert stored['duties'] == long_text.strip() and stored['purpose'] == 'Master purpose'
        assert connection.execute('SELECT duties FROM job_profiles WHERE id=?', (master['id'],)).fetchone()[0] == 'Ideal master duties'
    profile_fields = [field['key'] for field in SCHEMAS['job_profiles']['columns'] if field['key'] not in ('job', 'department')]
    personal_fields = [field['key'] for field in SCHEMAS['personal_documents']['columns'] if field['key'] != 'employee_code']
    assert profile_fields == personal_fields == list(FIELDS)
    assert client.put(f"/api/data/job_profiles/{master['id']}?company_id=1", headers=headers, json={'purpose': 'Later ideal master'}).status_code == 200
    actual_doc = client.get(f"/api/passports/{employee['id']}?company_id=1").json['document']
    assert actual_doc['effective']['duties'] == long_text.strip() and actual_doc['effective']['purpose'] == 'Master purpose'


def company_user(client, headers, app, role):
    name = 'excel-' + role
    assert client.post('/api/users', headers=headers, json={'username': name, 'password': PASSWORD, 'role': role, 'company_id': 1}).status_code == 201
    scoped = app.test_client()
    assert scoped.post('/api/login', json={'username': name, 'password': PASSWORD}).status_code == 200
    return scoped, {'X-CSRF-Token': scoped.get('/api/me').json['csrf']}


@pytest.mark.parametrize('role', ['manager', 'viewer'])
def test_company_template_access_and_bank_write_permissions(ready, role):
    app, client, headers = ready
    scoped, scope_headers = company_user(client, headers, app, role)
    forms = scoped.get('/api/excel/forms?company_id=1').json
    assert len(forms) == len(SCHEMAS) - 2
    assert all(not form['admin_only'] and form['can_import'] == (role != 'viewer') for form in forms)
    course = next(form for form in forms if form['kind'] == 'courses')
    assert next(field for field in course['columns'] if field['key'] == 'calendar_scope')['choices'] == [{'value': 'internal', 'label': 'داخلی شرکت'}]
    assert scoped.get('/api/excel/forms?company_id=2').status_code == 403
    for kind in ('competency-bank', 'companies'):
        assert scoped.get(f'/api/excel/templates/{kind}?company_id=1').status_code == 403
        assert submit(scoped, scope_headers, kind, [imported_row(client, kind)], mode='commit').status_code == 403
    before = snapshot(app)
    result = submit(scoped, scope_headers, 'employees', [{'code': 'E2', 'name': 'Company employee'}], mode='commit')
    assert result.status_code == (200 if role == 'manager' else 403)
    if role == 'viewer':
        assert snapshot(app) == before


def test_manager_can_select_existing_bank_and_create_only_internal_courses(ready):
    app, client, headers = ready
    scoped, scope_headers = company_user(client, headers, app, 'manager')
    bank_before = client.get('/api/competency-bank').json
    selected = submit(scoped, scope_headers, 'competency_selection', [{'skill_code': 'S002'}], mode='commit')
    assert selected.json['created'] == 1
    invalid_bank = submit(scoped, scope_headers, 'competency_selection', [{'skill_code': 'UNAUTHORIZED-NEW'}], mode='commit')
    assert invalid_bank.status_code == 400
    course = imported_row(client, 'courses')
    response = submit(scoped, scope_headers, 'courses', [course], mode='commit')
    assert response.status_code == 200, response.json
    new_course = next(row for row in scoped.get('/api/data/courses?company_id=1').json if row['external_code'] == course['course_code'])
    assert new_course['calendar_scope'] == 'internal'
    forbidden = submit(scoped, scope_headers, 'courses', [{**course, 'course_code': 'HOLDING', 'calendar_scope': 'holding'}], mode='commit')
    assert forbidden.status_code == 400 and forbidden.json['errors']
    old = next(row for row in scoped.get('/api/data/courses?company_id=1').json if row['title'] == 'Existing course')
    forbidden = submit(scoped, scope_headers, 'courses', [{**course, 'course_code': old['external_code']}], mode='commit', policy='upsert')
    assert forbidden.status_code == 400
    assert client.get('/api/competency-bank').json == bank_before


def test_persian_choice_labels_map_to_stored_enums(ready):
    app, client, headers = ready
    row = {**imported_row(client, 'courses'), 'status': 'برگزارشده', 'calendar_scope': 'داخلی شرکت'}
    assert submit(client, headers, 'courses', [row], mode='commit').status_code == 200
    saved = next(item for item in client.get('/api/data/courses?company_id=1').json if item['external_code'] == row['course_code'])
    assert saved['status'] == 'completed' and saved['calendar_scope'] == 'internal'


def test_preview_is_revalidated_against_current_bank_before_commit(ready):
    app, client, headers = ready
    payload = workbook('competency_selection', [{'skill_code': 'S002'}])
    assert submit(client, headers, 'competency_selection', payload=payload).json['can_commit']
    assert client.delete('/api/competency-bank/S002', headers=headers).status_code == 200
    result = submit(client, headers, 'competency_selection', payload=payload, mode='commit')
    assert result.status_code == 400
    assert not any(row['code'] == 'S002' for row in client.get('/api/data/skills?company_id=1').json)


@pytest.mark.parametrize('value', ['=1+1', '#N/A'])
def test_formula_and_excel_error_cells_never_write(ready, value):
    app, client, headers = ready
    before = snapshot(app)
    result = submit(client, headers, 'employees', [{'code': 'FORMULA', 'name': value}], mode='commit')
    assert result.status_code == 400 and result.json['errors'][0]['row'] == 7
    assert snapshot(app) == before


def transform_zip(payload, transform):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as original, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as changed:
        for item in original.infolist():
            changed.writestr(item, transform(item.filename, original.read(item)))
    return output.getvalue()


@pytest.mark.parametrize('failure', ['company', 'kind', 'version', 'headers', 'oversized_metadata', 'oversized_data', 'extra_columns', 'doctype'])
def test_wrong_or_unsafe_template_structure_is_rejected_before_writes(ready, failure):
    app, client, headers = ready
    payload = workbook('employees', [{'code': 'E2', 'name': 'New person'}], cid=2 if failure == 'company' else 1)
    if failure in ('kind', 'version', 'headers', 'extra_columns'):
        book = openpyxl.load_workbook(io.BytesIO(payload))
        if failure == 'kind':
            book['_nikan']['B2'] = 'assessments'
        elif failure == 'version':
            book['_nikan']['B1'] = '999'
        elif failure == 'headers':
            book[DATA_SHEET]['A5'] = 'company_id'
        else:
            book[DATA_SHEET]['E7'] = 'Unapproved field'
        out = io.BytesIO(); book.save(out); payload = out.getvalue()
    elif failure in ('oversized_metadata', 'oversized_data'):
        sheet = 'xl/worksheets/sheet2.xml' if failure == 'oversized_metadata' else 'xl/worksheets/sheet1.xml'
        import re
        payload = transform_zip(payload, lambda name, data: re.sub(rb'<dimension ref="[^"]+"', b'<dimension ref="A1:XFD1048576"', data) if name == sheet else data)
    elif failure == 'doctype':
        payload = transform_zip(payload, lambda name, data: b'<!DOCTYPE worksheet [<!ENTITY x "bad">]>' + data if name == 'xl/worksheets/sheet1.xml' else data)
    before = snapshot(app)
    result = submit(client, headers, 'employees', payload=payload, mode='commit')
    assert result.status_code == 400 and result.json['error']
    assert snapshot(app) == before


def test_external_link_zip_and_non_xlsx_files_are_rejected(ready):
    app, client, headers = ready
    payload = workbook('employees', [{'code': 'E2', 'name': 'New person'}])
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(output, 'w') as target:
        for item in source.infolist():
            target.writestr(item, source.read(item))
        target.writestr('xl/externalLinks/externalLink1.xml', '<externalLink/>')
    for content in (b'not an Excel file', output.getvalue()):
        response = submit(client, headers, 'employees', payload=content, mode='commit')
        assert response.status_code == 400 and response.json['error']


def test_template_and_zip_endpoints_are_scoped_and_generated_files_roundtrip(ready):
    app, client, headers = ready
    response = client.get('/api/excel/templates/employees?company_id=1')
    assert response.status_code == 200 and response.data[:2] == b'PK'
    book = openpyxl.load_workbook(io.BytesIO(response.data))
    assert book[DATA_SHEET].sheet_view.rightToLeft
    sheet = book[DATA_SHEET]
    for column, field in enumerate(SCHEMAS['employees']['columns'], 1):
        sheet.cell(7, column, {'code': 'TEMPLATE', 'name': 'Downloaded template', 'job': 'Engineer'}.get(field['key']))
    content = io.BytesIO(); book.save(content)
    assert submit(client, headers, 'employees', payload=content.getvalue(), mode='commit').json['created'] == 1
    archive = client.get('/api/excel/templates.zip?company_id=1')
    with zipfile.ZipFile(io.BytesIO(archive.data)) as zipped:
        assert len(zipped.namelist()) == len(SCHEMAS)
    scoped, _ = company_user(client, headers, app, 'viewer')
    archive = scoped.get('/api/excel/templates.zip?company_id=1')
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.data)) as zipped:
        assert len(zipped.namelist()) == len(SCHEMAS) - 2
        assert not any('competency-bank' in name or '-companies-' in name for name in zipped.namelist())
    assert scoped.get('/api/excel/templates/employees?company_id=2').status_code == 403
