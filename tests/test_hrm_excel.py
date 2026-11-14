"""Real canonical write/import integration: scopes, private refs and atomic rollback."""
from contextlib import closing
from datetime import date
import io
import json
import sqlite3
import zipfile

import openpyxl
import pytest
from PIL import Image

from app import create_app
from app.hrm_excel import DATA_START, DATA_SHEET, CLEAR

PASSWORD = 'Excel-integration-password-123'


@pytest.fixture
def ready(tmp_path):
    app = create_app(tmp_path, True)
    admin = app.test_client()
    assert admin.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    assert admin.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    headers = {'X-CSRF-Token': admin.get('/api/me').json['csrf']}
    def post(resource, body, company=1):
        response = admin.post(f'/api/hrm/{resource}?company_id={company}', headers=headers, json=body)
        assert response.status_code == 201, response.json
        return response.json
    site1 = post('sites', {'name': 'Excel site A', 'active': 1})['id']
    site2 = post('sites', {'name': 'Excel site B', 'active': 1})['id']
    first = post('people', {'code': '00011', 'name': 'ALLOWED-PERSON', 'company_id': 1,
                           'site_id': site1, 'phone': '09120000000', 'department': 'A'})
    second = post('people', {'code': '00022', 'name': 'PRIVATE-OTHER-SITE', 'company_id': 1,
                            'site_id': site2, 'department': 'B'})
    post('people', {'code': '00033', 'name': 'PRIVATE-OTHER-COMPANY', 'company_id': 2}, 2)
    return dict(app=app, client=admin, headers=headers, site=site1, other_site=site2,
                person=first, other=second, post=post)


def account(ready, permissions, site=None):
    admin = ready['client']
    name = 'user-' + str(len(admin.get('/api/hrm/access/users').json))
    response = admin.post('/api/hrm/access/users', headers=ready['headers'], json={
        'username': name, 'password': PASSWORD, 'role': 'company_hr',
        'scopes': [{'company_id': 1, 'site_id': site, 'department': ''}],
        'permissions': permissions, 'training_access': False})
    assert response.status_code == 201, response.json
    client = ready['app'].test_client()
    assert client.post('/api/login', json={'username': name, 'password': PASSWORD}).status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


def template(ready, resource, company=1, site=None, client=None):
    query = f'company_id={company}' + (f'&site_id={site}' if site else '')
    response = (client or ready['client']).get(f'/api/hrm/excel/templates/{resource}?{query}')
    assert response.status_code == 200, response.json
    return response.data


def filled(payload, rows, mutate=None):
    workbook = openpyxl.load_workbook(io.BytesIO(payload))
    sheet = workbook[DATA_SHEET]
    columns = {cell.value: cell.column for cell in sheet[5] if cell.value is not None}
    for number, values in enumerate(rows, DATA_START):
        for key, value in values.items():
            assert key in columns, (key, columns)
            sheet.cell(number, columns[key], value)
    if mutate:
        mutate(workbook)
    output = io.BytesIO(); workbook.save(output)
    return output.getvalue()


def upload(ready, resource, payload, mode='preview', policy='insert', company=1, site=None,
           client=None, headers=None):
    query = f'company_id={company}' + (f'&site_id={site}' if site else '')
    return (client or ready['client']).post(f'/api/hrm/excel/import/{resource}?{query}',
        headers=headers or ready['headers'], data={'mode': mode, 'policy': policy,
        'file': (io.BytesIO(payload), 'standard.xlsx')})


def snapshot(ready):
    with closing(sqlite3.connect(ready['app'].config['DB'])) as connection:
        return {name: connection.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall()
                for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def test_all_catalog_resources_have_uniform_descriptions_and_all_editable_templates(ready):
    response = ready['client'].get('/api/hrm/excel/forms?company_id=1')
    assert response.status_code == 200, response.json
    forms = {form['resource']: form for form in response.json['forms']}
    assert set(forms) == set(ready['app'].extensions['hrm_catalog'])
    assert forms['accord_results']['read_only'] and not forms['accord_results']['importable']
    assert forms['documents']['binary_attachment'] and not forms['documents']['importable']
    assert {form['kind'] for form in response.json['legacy_forms']} >= {'job_profiles', 'personal_documents'}
    assert not {'employees', 'assessments', 'courses', 'records'} & {form['kind'] for form in response.json['legacy_forms']}
    assert response.json['canonical_replacements']['employees'] == 'people'
    for resource, form in forms.items():
        if not form['can_download']:
            continue
        workbook = openpyxl.load_workbook(io.BytesIO(template(ready, resource)))
        assert DATA_SHEET in workbook and 'راهنما' in workbook and '_nikan' in workbook
        assert 'نمونه تکمیل؛ وارد نمی‌شود' in workbook
        assert all(sheet.sheet_view.rightToLeft for sheet in workbook)
        assert workbook['_nikan'].sheet_state == 'veryHidden'
        assert workbook[DATA_SHEET].cell(5, 1).value == 'id'
        assert not any(cell.value for row in workbook[DATA_SHEET].iter_rows(min_row=DATA_START) for cell in row)
        workbook.close()


def test_preview_uses_canonical_people_api_and_rolls_back_every_table_and_identity_counter(ready):
    file = filled(template(ready, 'people'), [dict(code='000101', name='FIRST-IMPORT'), dict(code='000102', name='SECOND-IMPORT')])
    before = snapshot(ready)
    preview = upload(ready, 'people', file)
    assert preview.status_code == 200, preview.json
    assert preview.json['ok'] and preview.json['planned_created'] == 2
    assert not preview.json['applied'] and preview.json['created'] == 0 and preview.json['records'] == []
    assert snapshot(ready) == before
    commit = upload(ready, 'people', file, mode='commit')
    assert commit.status_code == 200, commit.json
    assert commit.json['applied'] and commit.json['created'] == 2
    people = ready['client'].get('/api/hrm/people?company_id=1').json
    assert {'000101', '000102'} <= {row['code'] for row in people}
    assert len({row['id'] for row in people}) == len(people)
    repeat = upload(ready, 'people', file, mode='commit')
    assert repeat.status_code == 409, repeat.json
    assert 'قبلاً' in repeat.json['error']


@pytest.mark.parametrize('mode,status', [('preview', 200), ('commit', 400)])
def test_late_domain_error_rolls_back_all_preceding_rows_and_audit(ready, mode, status):
    file = filled(template(ready, 'people'), [dict(code='NEW-VALID', name='Valid before failure'), dict(code='00011', name='Duplicate existing')])
    before = snapshot(ready)
    response = upload(ready, 'people', file, mode=mode)
    assert response.status_code == status, response.json
    assert not response.json['ok'] and not response.json['applied']
    assert response.json['errors'][0]['row'] == DATA_START + 1
    assert response.json['created'] == 0 and response.json['planned_created'] == 1
    assert snapshot(ready) == before


def test_updates_require_explicit_current_id_version_and_keep_blank_fields(ready):
    source = template(ready, 'people')
    person = ready['person']
    file = filled(source, [dict(id=str(person['id']), version=str(person['version']), name='Explicitly updated')])
    preview = upload(ready, 'people', file, policy='update')
    assert preview.status_code == 200 and preview.json['ok'], preview.json
    assert ready['client'].get(f'/api/hrm/people/{person["id"]}?company_id=1').json['name'] == person['name']
    committed = upload(ready, 'people', file, policy='update', mode='commit')
    assert committed.status_code == 200 and committed.json['updated'] == 1, committed.json
    current = ready['client'].get(f'/api/hrm/people/{person["id"]}?company_id=1').json
    assert current['phone'] == '09120000000' and current['code'] == person['code']
    clear_file = filled(source, [dict(id=current['id'], version=current['version'], phone=CLEAR)])
    assert upload(ready, 'people', clear_file, policy='update', mode='commit').json['applied']
    assert ready['client'].get(f'/api/hrm/people/{person["id"]}?company_id=1').json['phone'] == ''
    missing_id = filled(source, [dict(version=current['version'], name='No implicit merge')])
    assert upload(ready, 'people', missing_id, policy='update').status_code == 400
    stale_file = filled(source, [dict(id=person['id'], version=person['version'], name='Stale edit')])
    stale = upload(ready, 'people', stale_file, policy='update', mode='commit')
    assert stale.status_code == 400 and stale.json['errors'][0]['status'] == 409


def test_company_and_site_metadata_tampering_and_cross_scope_rows_are_rejected(ready):
    file = filled(template(ready, 'attendance', site=ready['site']), [dict(employee_code='00011', work_date='1405/08/01', kind='حضور', minutes=480)])
    assert upload(ready, 'attendance', file, site=ready['other_site']).status_code == 400
    assert upload(ready, 'attendance', file, company=2).status_code == 400
    def tamper(workbook):
        for row in workbook['_nikan']:
            if row[0].value == 'company_id':
                row[1].value = 2
    altered = filled(file, [], tamper)
    assert upload(ready, 'attendance', altered, company=2).status_code == 400
    wrong_person = filled(template(ready, 'attendance', site=ready['site']), [dict(employee_id=ready['other']['id'], work_date='1405/08/01', kind='work', minutes=480)])
    before = snapshot(ready)
    response = upload(ready, 'attendance', wrong_person, site=ready['site'], mode='commit')
    assert response.status_code == 400 and not response.json['applied'], response.json
    assert snapshot(ready) == before
    success = upload(ready, 'attendance', file, site=ready['site'], mode='commit')
    assert success.status_code == 200 and success.json['applied'], success.json
    record = ready['client'].get('/api/hrm/attendance?company_id=1').json[0]
    assert record['employee_id'] == ready['person']['id'] and record['site_id'] == ready['site']


def test_create_only_templates_expose_minimal_scoped_refs_and_actual_grant_controls_import(ready):
    client, headers = account(ready, {'attendance': {'create': True}}, ready['site'])
    forms = client.get('/api/hrm/excel/forms?company_id=1').json
    assert forms['legacy_forms'] == []
    assert 'health_records' not in {form['resource'] for form in forms['forms']}
    payload = template(ready, 'attendance', site=ready['site'], client=client)
    workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True)
    values = ' '.join(str(cell.value) for sheet in workbook for row in sheet for cell in row)
    assert 'ALLOWED-PERSON' in values and 'PRIVATE-OTHER-SITE' not in values and 'PRIVATE-OTHER-COMPANY' not in values
    workbook.close()
    file = filled(payload, [dict(employee_code='00011', work_date='1405/08/02', kind='work', minutes=0)])
    response = upload(ready, 'attendance', file, mode='commit', site=ready['site'], client=client, headers=headers)
    assert response.status_code == 200 and response.json['applied'], response.json
    assert client.get('/api/hrm/attendance?company_id=1').status_code == 403
    assert client.get('/api/hrm/excel/templates/health_records?company_id=1').status_code == 403
    update = upload(ready, 'attendance', file, policy='update', site=ready['site'], client=client, headers=headers)
    assert update.status_code == 403


def test_read_only_and_export_only_do_not_gain_import_or_clinical_access(ready):
    reader, rh = account(ready, {'attendance': {'read': True}})
    assert reader.get('/api/hrm/excel/templates/attendance?company_id=1').status_code == 403
    exporter, eh = account(ready, {'attendance': {'export': True}})
    payload = template(ready, 'attendance', client=exporter)
    file = filled(payload, [dict(employee_code='00011', work_date='1405/08/03', kind='work', minutes=1)])
    assert upload(ready, 'attendance', file, mode='commit', client=exporter, headers=eh).status_code == 403
    assert exporter.get('/api/hrm/excel/templates/health_records?company_id=1').status_code == 403


def test_formula_duplicate_rows_and_modified_columns_are_rejected_without_writes(ready):
    source = template(ready, 'people')
    for file in [filled(source, [dict(code='FORMULA', name='=HYPERLINK("https://example.com","bad")')]),
                 filled(source, [dict(code='DUP', name='Duplicate'), dict(code='DUP', name='Duplicate')]),
                 filled(source, [dict(code='KEY', name='Changed')], lambda workbook: setattr(workbook[DATA_SHEET]['A5'], 'value', 'arbitrary_path'))]:
        before = snapshot(ready)
        response = upload(ready, 'people', file, mode='commit')
        assert response.status_code == 400, response.json
        assert snapshot(ready) == before


def test_zero_score_and_exact_integer_money_roundtrip_through_real_domain_validation(ready):
    source = template(ready, 'training_assessments')
    file = filled(source, [dict(employee_code='00011', competency_code='S001', assessment_date='1405/08/04', level=0)])
    response = upload(ready, 'training_assessments', file, mode='commit')
    assert response.status_code == 200 and response.json['applied'], response.json
    assert ready['client'].get('/api/hrm/training_assessments?company_id=1').json[0]['level'] == 0
    money = template(ready, 'loans')
    file = filled(money, [dict(employee_code='00011', title='Exact amount', loan_kind='internal', amount_rials='9000000000000001', status='registered')])
    response = upload(ready, 'loans', file, mode='commit')
    assert response.status_code == 200 and response.json['applied'], response.json
    assert ready['client'].get('/api/hrm/loans?company_id=1').json[0]['amount_rials'] == 9000000000000001
    rounded = filled(money, [dict(employee_code='00011', title='Numeric cannot be trusted', loan_kind='internal', amount_rials=9000000000000001, status='registered')])
    assert upload(ready, 'loans', rounded).status_code == 400


def test_binary_documents_get_instruction_template_but_never_fake_file_import(ready):
    payload = template(ready, 'documents')
    file = filled(payload, [dict(employee_code='00011', title='No actual file')])
    before = snapshot(ready)
    response = upload(ready, 'documents', file, mode='commit')
    assert response.status_code == 400 and 'فایل' in response.json['error']
    assert snapshot(ready) == before


def test_stored_holding_logo_and_leading_zero_text_format_are_preserved(ready):
    image = io.BytesIO(); Image.new('RGB', (90, 40), 'navy').save(image, format='PNG')
    response = ready['client'].post('/api/branding/holding', headers=ready['headers'], data={'file': (io.BytesIO(image.getvalue()), 'logo.png')})
    assert response.status_code == 200, response.json
    payload = template(ready, 'people')
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        assert any(name.startswith('xl/media/') for name in package.namelist())
    workbook = openpyxl.load_workbook(io.BytesIO(payload))
    sheet = workbook[DATA_SHEET]
    code = next(cell.column for cell in sheet[5] if cell.value == 'code')
    assert sheet.cell(DATA_START, code).number_format == '@'
    assert workbook['نمونه تکمیل؛ وارد نمی‌شود'].max_row >= 3
    workbook.close()


def test_prefilled_correction_template_requires_read_edit_export_and_carries_current_versions(ready):
    editor, eh = account(ready, {'personnel': {'read': True, 'edit': True}}, ready['site'])
    denied = editor.get(f'/api/hrm/excel/templates/people?company_id=1&site_id={ready["site"]}&include_existing=1')
    assert denied.status_code == 403
    reader, rh = account(ready, {'personnel': {'read': True, 'edit': True, 'export': True}}, ready['site'])
    response = reader.get(f'/api/hrm/excel/templates/people?company_id=1&site_id={ready["site"]}&include_existing=1')
    assert response.status_code == 200, response.json
    workbook = openpyxl.load_workbook(io.BytesIO(response.data))
    sheet = workbook[DATA_SHEET]
    columns = {cell.value: cell.column for cell in sheet[5] if cell.value}
    assert sheet.cell(DATA_START, columns['id']).value == str(ready['person']['id'])
    assert sheet.cell(DATA_START, columns['version']).value == str(ready['person']['version'])
    assert sheet.cell(DATA_START, columns['code']).value == '00011'
    assert sheet.cell(DATA_START, columns['phone']).value == '09120000000'
    all_values = ' '.join(str(cell.value) for tab in workbook for row in tab for cell in row)
    assert 'PRIVATE-OTHER-SITE' not in all_values and 'PRIVATE-OTHER-COMPANY' not in all_values
    sheet.cell(DATA_START, columns['name'], 'Corrected through standard export')
    file = io.BytesIO(); workbook.save(file)
    response = upload(ready, 'people', file.getvalue(), mode='commit', policy='update',
                      site=ready['site'], client=reader, headers=rh)
    assert response.status_code == 200 and response.json['updated'] == 1, response.json


def test_clinical_values_are_never_in_blank_templates_and_prefilled_values_need_actual_medical_export(ready):
    ready['post']('health_records', {'employee_id': ready['person']['id'], 'exam_date': '1405/08/01',
        'exam_type': 'Occupational exam', 'fitness': 'fit', 'clinical_notes': 'CONFIDENTIAL-CLINICAL-NOTE'})
    blank = openpyxl.load_workbook(io.BytesIO(template(ready, 'health_records')), read_only=True)
    assert 'CONFIDENTIAL-CLINICAL-NOTE' not in ' '.join(str(cell.value) for tab in blank for row in tab for cell in row)
    blank.close()
    medical, mh = account(ready, {'health': {'read': True, 'edit': True, 'export': True}}, ready['site'])
    response = medical.get(f'/api/hrm/excel/templates/health_records?company_id=1&site_id={ready["site"]}&include_existing=1')
    assert response.status_code == 200, response.json
    book = openpyxl.load_workbook(io.BytesIO(response.data), read_only=True)
    assert 'CONFIDENTIAL-CLINICAL-NOTE' in ' '.join(str(cell.value) for tab in book for row in tab for cell in row)
    book.close()
    environment, _ = account(ready, {'health_environment': {'read': True, 'edit': True, 'export': True}}, ready['site'])
    assert environment.get('/api/hrm/excel/templates/health_records?company_id=1&include_existing=1').status_code == 403


@pytest.mark.parametrize('action', ['commit', 'rollback'])
def test_domain_transaction_escape_is_blocked_and_entire_batch_is_unchanged(ready, monkeypatch, action):
    from flask import g
    endpoint = 'hrm_people_list'
    canonical = ready['app'].view_functions[endpoint]
    def unsafe_domain_helper():
        response = canonical()
        getattr(g.db, action)()
        return response
    monkeypatch.setitem(ready['app'].view_functions, endpoint, unsafe_domain_helper)
    file = filled(template(ready, 'people'), [dict(code='ESCAPE-1', name='No escaped write')])
    before = snapshot(ready)
    response = upload(ready, 'people', file, mode='commit')
    assert response.status_code == 400 and not response.json['applied'], response.json
    assert snapshot(ready) == before


def test_macro_external_relationship_duplicate_archive_and_oversize_are_rejected(ready):
    from app.hrm_excel import MAX_BYTES
    original = template(ready, 'people')
    def changed(extra_name, contents, duplicate=False):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(output, 'w') as target:
            for name in source.namelist():
                target.writestr(name, source.read(name))
            if duplicate:
                with pytest.warns(UserWarning):
                    target.writestr(extra_name, contents)
            else:
                target.writestr(extra_name, contents)
        return output.getvalue()
    for payload in [changed('xl/vbaProject.bin', b'fake macro'),
                    changed('xl/externalLinks/_rels/externalLink1.xml.rels', b'<Relationship TargetMode="External" Target="https://example.com"/>'),
                    changed('[Content_Types].xml', b'duplicate', True), b'x' * (MAX_BYTES + 1)]:
        before = snapshot(ready)
        response = upload(ready, 'people', payload, mode='commit')
        assert response.status_code == 400, response.json
        assert snapshot(ready) == before


def test_holding_training_delegate_does_not_get_primary_admin_only_legacy_forms(ready):
    response = ready['client'].post('/api/hrm/access/users', headers=ready['headers'], json={
        'username': 'holding-training-delegate', 'password': PASSWORD, 'role': 'holding_manager',
        'all_companies': True, 'scopes': [], 'training_access': True,
        'permissions': {'training': {'read': True, 'create': True, 'edit': True, 'delete': True, 'export': True}}})
    assert response.status_code == 201, response.json
    client = ready['app'].test_client()
    assert client.post('/api/login', json={'username': 'holding-training-delegate', 'password': PASSWORD}).status_code == 200
    response = client.get('/api/hrm/excel/forms?company_id=1')
    assert response.status_code == 200, response.json
    kinds = {form['kind'] for form in response.json['legacy_forms']}
    assert 'competency_selection' in kinds
    assert not {'companies', 'competency-bank'} & kinds


def test_reference_operation_and_user_identity_never_leak_out_of_nested_dispatch(ready, monkeypatch):
    from flask import g, request, session
    ready['post']('loans', {'employee_id': ready['person']['id'], 'title': 'REFERENCE-ONLY-LOAN',
                          'loan_kind': 'internal', 'amount_rials': 123456, 'status': 'registered'})
    client, headers = account(ready, {'loans': {'create': True}}, ready['site'])
    original = ready['app'].view_functions['hrm_excel_template']
    checked = []
    def inspect_after_minimal_references(resource):
        response = original(resource)
        assert not hasattr(g, 'hrm_reference_operation')
        assert g.user['id'] == session['uid'] == g.hrm_original_user['id']
        environ = dict(request.environ)
        environ.update(REQUEST_METHOD='GET', PATH_INFO='/api/hrm/loans', QUERY_STRING='company_id=1',
                       CONTENT_LENGTH='0', **{'wsgi.input': io.BytesIO()})
        environ.pop('werkzeug.request', None)
        with ready['app'].request_context(environ):
            endpoint, arguments = ready['app'].url_map.bind_to_environ(environ).match('/api/hrm/loans', method='GET')
            with pytest.raises(PermissionError):
                ready['app'].view_functions[endpoint](**arguments)
        checked.append(True)
        return response
    monkeypatch.setitem(ready['app'].view_functions, 'hrm_excel_template', inspect_after_minimal_references)
    response = client.get(f'/api/hrm/excel/templates/loan_payments?company_id=1&site_id={ready["site"]}')
    assert response.status_code == 200, response.json
    assert checked == [True]
    workbook = openpyxl.load_workbook(io.BytesIO(response.data), read_only=True)
    values = ' '.join(str(cell.value) for tab in workbook for row in tab for cell in row)
    assert 'REFERENCE-ONLY-LOAN' in values and '123456' not in values
    workbook.close()
    assert client.get('/api/hrm/loans?company_id=1').status_code == 403
