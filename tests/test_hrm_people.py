"""Real personnel/contract/document APIs with company, site and operation grants."""
import io
import sqlite3
import zipfile
from datetime import date, timedelta

import pytest

from app import create_app
from app.hrm_common import today_iso

PASSWORD = 'Secure-HRM-people-password-123'
PDF = b'%PDF-1.4\nSynthetic personnel document\n%%EOF'


def login(app, username='people-admin'):
    client = app.test_client()
    assert client.post('/api/login', json={'username': username, 'password': PASSWORD}).status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


def post(client, headers, resource, body, cid=1):
    return client.post(f'/api/hrm/{resource}?company_id={cid}', headers=headers, json=body)


def create(client, headers, resource, body, cid=1):
    response = post(client, headers, resource, body, cid)
    assert response.status_code == 201, response.json
    return response.json


@pytest.fixture
def hrm(tmp_path):
    app = create_app(tmp_path, testing=True)
    first = app.test_client()
    assert first.post('/api/setup', json={'username': 'people-admin', 'password': PASSWORD}).status_code == 200
    client, headers = login(app)
    sites = [create(client, headers, 'sites', {'name': name, 'kind': 'factory'}, cid)
             for cid, name in ((1, 'Factory A'), (1, 'Factory B'), (2, 'Factory C'))]
    units = [create(client, headers, 'units', {'name': 'Production', 'site_id': site['id']}, site['company_id'])
             for site in sites]
    person = create(client, headers, 'people', {'code': 'STAFF-123456', 'name': 'Synthetic worker',
                    'national_id': '۰۰۱۲۳۴۵۶۷۸', 'site_id': sites[0]['id'], 'unit_id': units[0]['id'],
                    'job': 'Welder', 'email': 'normal@example.com', 'location': 'Hall A'})
    return dict(app=app, client=client, headers=headers, sites=sites, units=units, person=person,
                root=tmp_path)


def account(hrm, name, permissions, scopes=None):
    response = hrm['client'].post('/api/hrm/access/users', headers=hrm['headers'], json={
        'username': name, 'password': PASSWORD, 'role': 'holding_manager',
        'scopes': scopes if scopes is not None else [{'company_id': 1, 'site_id': hrm['sites'][0]['id']}],
        'permissions': permissions, 'training_access': False,
    })
    assert response.status_code == 201, response.json
    return login(hrm['app'], name)


def document(hrm, group='documents', *, cid=1, client=None, headers=None, **extra):
    return (client or hrm['client']).post(f'/api/hrm/documents?company_id={cid}',
        headers=headers or hrm['headers'], data={'employee_id': str(hrm['person']['id']),
             'permission_group': group, 'title': 'Synthetic confidential ' + group,
             'file': (io.BytesIO(PDF), 'document.pdf'), **extra})


def contract(hrm, **extra):
    return create(hrm['client'], hrm['headers'], 'contracts', dict(
        employee_id=hrm['person']['id'], number='HR-001', contract_type='Organizational type',
        start_date='1405/01/01', end_date='1405/12/29', **extra))


def sql_rows(hrm, table):
    with sqlite3.connect(hrm['app'].config['DB']) as connection:
        return connection.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()


def test_current_identity_email_national_id_and_missing_identifiers(hrm):
    person = hrm['person']; client = hrm['client']; headers = hrm['headers']
    assert person['email'] == 'normal@example.com'
    assert person['national_id'] == '0012345678' and not person['identity_incomplete']
    before = sql_rows(hrm, 'hrm_employee_details')
    for bad in ('not-an-email', 'hello @example.com', 'x@example', 'user@example com'):
        response = client.put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=headers,
                              json={'version': person['version'], 'email': bad})
        assert response.status_code == 400, (bad, response.json)
    assert sql_rows(hrm, 'hrm_employee_details') == before
    duplicate = post(client, headers, 'people', {'code': 'ANOTHER-1', 'name': 'Duplicate identity',
                     'national_id': '0012345678', 'site_id': hrm['sites'][2]['id']}, cid=2)
    assert duplicate.status_code == 409 and len(sql_rows(hrm, 'employees')) == 1
    unknown = create(client, headers, 'people', {'code': 'UNKNOWN-2', 'name': 'Identity incomplete',
                     'site_id': hrm['sites'][0]['id']})
    assert unknown['national_id'] == '' and unknown['identity_incomplete']


def test_site_unit_company_relationships_and_optimistic_updates(hrm):
    client, headers = hrm['client'], hrm['headers']
    for fields in ({'site_id': hrm['sites'][2]['id']},
                   {'site_id': hrm['sites'][0]['id'], 'unit_id': hrm['units'][1]['id']}):
        response = post(client, headers, 'people', {'code': 'WRONG-9', 'name': 'Invalid location', **fields})
        assert response.status_code == 400, response.json
    person = hrm['person']
    response = client.put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=headers,
                          json={'version': person['version'], 'phone': '09123456789'})
    assert response.status_code == 200
    assert response.json['version'] == person['version'] + 1
    assert client.put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=headers,
                      json={'version': person['version'], 'phone': 'Stale overwrite'}).status_code == 409
    assert client.get(f'/api/hrm/people/{person["id"]}?company_id=1').json['phone'] == '09123456789'


def test_create_only_pickers_are_minimal_and_never_authorize_records_read(hrm):
    client, headers = account(hrm, 'contract-creator', {'contracts': {'create': True}})
    for resource in ('people', 'sites', 'units'):
        response = client.get(f'/api/hrm/{resource}?company_id=1&module=contracts&operation=create')
        assert response.status_code == 200 and response.json
        assert 'national_id' not in response.json[0] and 'email' not in response.json[0]
    assert client.get('/api/hrm/people?company_id=1').status_code == 403
    assert client.get('/api/hrm/contracts?company_id=1').status_code == 403
    created = create(client, headers, 'contracts', {'employee_id': hrm['person']['id'], 'number': 'CREATE-ONLY',
                      'contract_type': 'Temporary', 'start_date': '1405/01/01'})
    assert created['site_id'] == hrm['sites'][0]['id']
    assert client.get(f'/api/hrm/contracts/{created["id"]}?company_id=1').status_code == 403


def test_unit_scope_cannot_create_people_or_configuration_outside_scope(hrm):
    client, headers = account(hrm, 'unit-editor', {'personnel': {'read': True, 'create': True, 'edit': True}},
        scopes=[{'company_id': 1, 'site_id': hrm['sites'][0]['id'], 'department': 'Production'}])
    assert client.get('/api/hrm/people?company_id=1').json[0]['id'] == hrm['person']['id']
    assert post(client, headers, 'sites', {'name': 'Unauthorized new site'}).status_code == 403
    assert post(client, headers, 'units', {'name': 'Another unit', 'site_id': hrm['sites'][0]['id']}).status_code == 403
    response = post(client, headers, 'people', {'code': 'OTHER-44', 'name': 'Outside unit',
                    'site_id': hrm['sites'][0]['id'], 'department': 'Another unit'})
    assert response.status_code == 403
    assert len(sql_rows(hrm, 'employees')) == 1


def test_transfer_keeps_training_identity_and_historical_contract_site(hrm):
    old = contract(hrm)
    source, _ = account(hrm, 'old-site-contract', {'contracts': {'read': True}})
    target, _ = account(hrm, 'new-site-contract', {'contracts': {'read': True}},
        scopes=[{'company_id': 1, 'site_id': hrm['sites'][1]['id']}])
    person = hrm['person']; before_training = sql_rows(hrm, 'employees')
    result = hrm['client'].put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=hrm['headers'], json={
        'version': person['version'], 'site_id': hrm['sites'][1]['id'], 'unit_id': hrm['units'][1]['id'],
        'job': 'Supervisor', 'effective_date': today_iso()})
    assert result.status_code == 200, result.json
    assert result.json['passport_number'] == person['passport_number']
    assert sql_rows(hrm, 'employees') == before_training
    assert source.get('/api/hrm/contracts?company_id=1').json[0]['id'] == old['id']
    assert target.get(f'/api/hrm/contracts/{old["id"]}?company_id=1').status_code == 403
    rows = hrm['client'].get('/api/hrm/employment_history?company_id=1').json
    assert len(rows) == 2 and rows[0]['site_id'] == hrm['sites'][1]['id']
    assert rows[1]['end_date'] == today_iso() and rows[1]['start_date'] == today_iso()


def test_cross_company_transfer_checks_both_sides_without_rewriting_legacy(hrm):
    person = hrm['person']; before = sql_rows(hrm, 'employees')
    source, sh = account(hrm, 'source-only-editor', {'personnel': {'read': True, 'edit': True}})
    body = {'version': person['version'], 'company_id': 2, 'site_id': hrm['sites'][2]['id'],
            'unit_id': hrm['units'][2]['id'], 'job': 'Technician', 'effective_date': today_iso()}
    assert source.put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=sh, json=body).status_code == 403
    assert sql_rows(hrm, 'employees') == before
    result = hrm['client'].put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=hrm['headers'], json=body)
    assert result.status_code == 200, result.json
    assert result.json['company_id'] == 2 and result.json['training_company_id'] == 1
    assert result.json['id'] == person['id'] and sql_rows(hrm, 'employees') == before
    assert hrm['client'].get('/api/hrm/people?company_id=1').json == []


def test_contract_renewal_chain_versions_and_configurable_alert_window(hrm):
    original = contract(hrm)
    response = hrm['client'].post(f'/api/hrm/contracts/{original["id"]}/renew?company_id=1', headers=hrm['headers'],
        json={'version': original['version'], 'number': 'HR-001', 'contract_type': 'Organizational type',
              'start_date': '1406/01/01', 'end_date': '1406/12/29'})
    assert response.status_code == 201, response.json
    renewed = response.json
    assert renewed['parent_contract_id'] == original['id'] and renewed['renewal_count'] == 1
    assert renewed['site_id'] == original['site_id']
    assert hrm['client'].post(f'/api/hrm/contracts/{original["id"]}/renew?company_id=1', headers=hrm['headers'],
        json={'version': original['version'], 'number': 'HR-002', 'contract_type': 'Other', 'start_date': '1407/01/01'}).status_code == 409
    parent = hrm['client'].get(f'/api/hrm/contracts/{original["id"]}?company_id=1').json
    assert parent['status'] == 'renewed'
    assert hrm['client'].put(f'/api/hrm/contracts/{original["id"]}?company_id=1', headers=hrm['headers'],
                            json={'version': parent['version'], 'number': 'Rewritten'}).status_code == 409
    assert hrm['client'].get('/api/hrm/contracts/expiring?company_id=1&days=0').status_code == 200
    assert hrm['client'].get('/api/hrm/contracts/expiring?company_id=1&days=3650').json[0]['id'] == renewed['id']
    assert hrm['client'].get('/api/hrm/contracts/expiring?company_id=1&days=-1').status_code == 400


def test_edit_only_permission_does_not_require_read_and_cannot_renew(hrm):
    row = contract(hrm)
    editor, headers = account(hrm, 'contracts-editor', {'contracts': {'edit': True}})
    assert editor.get('/api/hrm/contracts?company_id=1').status_code == 403
    result = editor.put(f'/api/hrm/contracts/{row["id"]}?company_id=1', headers=headers,
                        json={'version': row['version'], 'notes': 'Corrected administrative note'})
    assert result.status_code == 200, result.json
    assert editor.post(f'/api/hrm/contracts/{row["id"]}/renew?company_id=1', headers=headers,
                       json={'version': result.json['version'], 'number': 'HR-next', 'contract_type': 'Other',
                             'start_date': '1406/01/01'}).status_code == 403


def test_temporary_activity_preserves_employer_and_requires_destination_scope(hrm):
    person = hrm['person']; before = sql_rows(hrm, 'hrm_employee_details')
    source, headers = account(hrm, 'temporary-source', {'personnel': {'create': True}})
    body = {'employee_id': person['id'], 'target_site_id': hrm['sites'][2]['id'],
            'start_date': '1405/08/20', 'end_date': '1405/08/22', 'activity_location': 'Temporary repair work'}
    assert post(source, headers, 'temporary_assignments', body).status_code == 403
    row = create(hrm['client'], hrm['headers'], 'temporary_assignments', body)
    assert row['company_id'] == 1 and row['target_company_id'] == 2
    assert row['site_id'] == hrm['sites'][0]['id'] and row['target_site_id'] == hrm['sites'][2]['id']
    assert sql_rows(hrm, 'hrm_employee_details') == before
    body['end_date'] = '1405/08/19'
    assert post(hrm['client'], hrm['headers'], 'temporary_assignments', body).status_code == 400


def test_health_document_has_own_group_and_site_scope_even_after_transfer(hrm):
    created = document(hrm, 'health')
    assert created.status_code == 201, created.json
    row = created.json
    assert row['site_id'] == hrm['sites'][0]['id'] and 'content' not in row
    clinical, _ = account(hrm, 'clinical-reader', {'health': {'read': True}})
    ordinary, _ = account(hrm, 'ordinary-HR', {'personnel': {'read': True}, 'documents': {'read': True}})
    assert clinical.get('/api/hrm/documents?company_id=1').json[0]['id'] == row['id']
    assert ordinary.get(f'/api/hrm/documents/{row["id"]}/file?company_id=1').status_code == 403
    response = clinical.get(f'/api/hrm/documents/{row["id"]}/file?company_id=1')
    assert response.status_code == 200 and response.data == PDF
    assert response.headers['Content-Disposition'].startswith('attachment;')
    assert clinical.get('/api/hrm/reports/documents.xlsx?company_id=1').status_code == 403


def test_file_revision_keeps_old_bytes_and_historical_site_without_current_access(hrm):
    first = document(hrm)
    assert first.status_code == 201
    row = first.json
    person = hrm['person']
    assert hrm['client'].put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=hrm['headers'],
          json={'version': person['version'], 'site_id': hrm['sites'][1]['id'], 'unit_id': hrm['units'][1]['id']}).status_code == 200
    source, sh = account(hrm, 'historical-doc-editor', {'documents': {'read': True, 'create': True}})
    second = document(hrm, client=source, headers=sh, supersedes_id=str(row['id']))
    assert second.status_code == 201, second.json
    assert second.json['revision'] == 2 and second.json['site_id'] == row['site_id']
    assert source.get(f'/api/hrm/documents/{row["id"]}/file?company_id=1').data == PDF
    assert document(hrm, client=source, headers=sh, supersedes_id=str(row['id'])).status_code == 409


@pytest.mark.parametrize('payload,name', [
    (b'\x89PNG\r\n\x1a\nNot an image', 'bad.png'),
    (b'Not a document', 'bad.pdf'),
])
def test_invalid_files_do_not_create_document_or_audit(hrm, payload, name):
    before = sql_rows(hrm, 'hrm_documents'), sql_rows(hrm, 'audit')
    response = hrm['client'].post('/api/hrm/documents?company_id=1', headers=hrm['headers'], data={
        'employee_id': str(hrm['person']['id']), 'title': 'Invalid content',
        'file': (io.BytesIO(payload), name)})
    assert response.status_code == 400
    assert (sql_rows(hrm, 'hrm_documents'), sql_rows(hrm, 'audit')) == before


def test_valid_docx_and_rejected_external_xml_and_macros(hrm):
    from docx import Document
    output = io.BytesIO(); word = Document(); word.add_paragraph('Actual synthetic employment document'); word.save(output)
    response = hrm['client'].post('/api/hrm/documents?company_id=1', headers=hrm['headers'], data={
        'employee_id': str(hrm['person']['id']), 'title': 'Word document',
        'file': (io.BytesIO(output.getvalue()), 'contract.docx')})
    assert response.status_code == 201, response.json
    for entry, value in [('word/_rels/document.xml.rels', b'<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'),
                         ('word/vbaProject.bin', b'Synthetic macro'),
                         ('word/document.xml', '<!DOCTYPE x><x/>'.encode('utf-16'))]:
        forged = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(output.getvalue())) as source, zipfile.ZipFile(forged, 'w') as target:
            for item in source.infolist():
                if item.filename != entry:
                    target.writestr(item.filename, source.read(item))
            target.writestr(entry, value)
        bad = hrm['client'].post('/api/hrm/documents?company_id=1', headers=hrm['headers'], data={
            'employee_id': str(hrm['person']['id']), 'title': 'Unsafe document',
            'file': (io.BytesIO(forged.getvalue()), 'unsafe.docx')})
        assert bad.status_code == 400, (entry, bad.json)
    assert len(sql_rows(hrm, 'hrm_documents')) == 1


def test_personnel_action_is_explicit_future_safe_and_atomic(hrm):
    person = hrm['person']
    tomorrow = (date.fromisoformat(today_iso()) + timedelta(days=1)).isoformat()
    pending = create(hrm['client'], hrm['headers'], 'personnel_actions', {
        'employee_id': person['id'], 'action_type': 'termination', 'effective_date': tomorrow})
    before = sql_rows(hrm, 'hrm_employee_details')
    assert hrm['client'].post(f'/api/hrm/personnel_actions/{pending["id"]}/apply?company_id=1', headers=hrm['headers'],
                             json={'version': pending['version']}).status_code == 400
    assert sql_rows(hrm, 'hrm_employee_details') == before
    applied = create(hrm['client'], hrm['headers'], 'personnel_actions', {
        'employee_id': person['id'], 'action_type': 'termination', 'effective_date': today_iso(), 'apply_now': 1})
    assert applied['applied'] == 1
    current = hrm['client'].get(f'/api/hrm/people/{person["id"]}?company_id=1').json
    assert current['status'] == 'terminated' and current['end_date'] == today_iso()
    assert hrm['client'].post(f'/api/hrm/personnel_actions/{applied["id"]}/apply?company_id=1', headers=hrm['headers'],
                             json={'version': applied['version']}).status_code == 409


def test_site_limited_termination_retains_site_unit_and_training_identity(hrm):
    operator, headers = account(hrm, 'site-personnel-operator', {
        'personnel': {'read': True, 'create': True, 'edit': True}})
    original = hrm['person']; legacy_before = sql_rows(hrm, 'employees')
    response = post(operator, headers, 'personnel_actions', {
        'employee_id': original['id'], 'action_type': 'termination',
        'effective_date': today_iso(), 'apply_now': 1})
    assert response.status_code == 201, response.json
    after = operator.get(f'/api/hrm/people/{original["id"]}?company_id=1').json
    assert after['status'] == 'terminated'
    assert after['site_id'] == original['site_id'] and after['unit_id'] == original['unit_id']
    assert after['department'] == original['department'] and after['job'] == original['job']
    assert sql_rows(hrm, 'employees') == legacy_before


def test_create_only_personnel_user_cannot_execute_a_profile_change(hrm):
    creator, headers = account(hrm, 'personnel-create-no-edit', {'personnel': {'create': True}})
    before = {name: sql_rows(hrm, name) for name in ('hrm_employee_details', 'hrm_personnel_actions', 'hrm_employment_history', 'audit')}
    response = post(creator, headers, 'personnel_actions', {
        'employee_id': hrm['person']['id'], 'action_type': 'termination',
        'effective_date': today_iso(), 'apply_now': 1})
    assert response.status_code == 403
    assert {name: sql_rows(hrm, name) for name in before} == before


def test_pending_action_detects_intervening_profile_update(hrm):
    original = hrm['person']
    pending = create(hrm['client'], hrm['headers'], 'personnel_actions', {
        'employee_id': original['id'], 'action_type': 'promotion', 'job': 'Lead welder',
        'effective_date': today_iso()})
    changed = hrm['client'].put(f'/api/hrm/people/{original["id"]}?company_id=1', headers=hrm['headers'],
                               json={'version': original['version'], 'phone': '09120000000'})
    assert changed.status_code == 200
    before = {name: sql_rows(hrm, name) for name in ('hrm_employee_details', 'hrm_personnel_actions', 'hrm_employment_history', 'audit')}
    response = hrm['client'].post(f'/api/hrm/personnel_actions/{pending["id"]}/apply?company_id=1', headers=hrm['headers'],
                                 json={'version': pending['version']})
    assert response.status_code == 409
    assert {name: sql_rows(hrm, name) for name in before} == before


def test_restart_preserves_identity_contract_blob_and_foreign_keys(hrm):
    row = contract(hrm); uploaded = document(hrm, 'contracts', contract_id=str(row['id']))
    assert uploaded.status_code == 201
    tables = ('employees', 'hrm_sites', 'hrm_units', 'hrm_employee_details', 'hrm_contracts', 'hrm_documents', 'hrm_employment_history')
    before = {table: sql_rows(hrm, table) for table in tables}
    again = create_app(hrm['root'], testing=True)
    hrm['app'] = again
    assert {table: sql_rows(hrm, table) for table in tables} == before
    with sqlite3.connect(again.config['DB']) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_only_primary_admin_can_modify_organization_even_with_full_delegated_grants(hrm):
    permissions = {key: {operation: True for operation in ('read', 'create', 'edit', 'delete', 'export')}
                   for key in ('personnel', 'settings')}
    client, headers = account(hrm, 'whole-company-organizer', permissions,
                              scopes=[{'company_id': 1}])
    site, unit = hrm['sites'][0], hrm['units'][0]
    assert client.get('/api/hrm/sites?company_id=1').status_code == 200
    assert client.get('/api/hrm/units?company_id=1').status_code == 200
    for resource, row, payload in [('sites', site, {'name': 'Delegated site'}),
                                   ('units', unit, {'name': 'Delegated unit', 'site_id': site['id']})]:
        assert post(client, headers, resource, payload).status_code == 403
        assert client.put(f'/api/hrm/{resource}/{row["id"]}?company_id=1', headers=headers,
                          json={'version': row['version'], 'name': 'Forbidden update'}).status_code == 403
        assert client.delete(f'/api/hrm/{resource}/{row["id"]}?company_id=1', headers=headers,
                             json={'version': row['version']}).status_code == 403
    assert client.get('/api/companies').status_code == 403
    assert client.post('/api/companies', headers=headers, json={'name': 'Forbidden company'}).status_code == 403
    # Personnel editing within the delegated company remains available.
    response = client.put(f'/api/hrm/people/{hrm["person"]["id"]}?company_id=1', headers=headers,
                          json={'version': hrm['person']['version'], 'phone': '09123456780'})
    assert response.status_code == 200, response.json


def test_admin_empty_site_unit_delete_and_recreate_never_reuses_identifiers(hrm):
    admin, headers = hrm['client'], hrm['headers']
    site = create(admin, headers, 'sites', {'name': 'Disposable site'})
    unit = create(admin, headers, 'units', {'name': 'Disposable unit', 'site_id': site['id']})
    assert admin.delete(f'/api/hrm/sites/{site["id"]}?company_id=1', headers=headers,
                        json={'version': site['version']}).status_code == 409
    assert admin.delete(f'/api/hrm/units/{unit["id"]}?company_id=1', headers=headers,
                        json={'version': unit['version'] - 1}).status_code == 409
    assert admin.delete(f'/api/hrm/units/{unit["id"]}?company_id=1', headers=headers,
                        json={'version': unit['version']}).status_code == 200
    next_unit = create(admin, headers, 'units', {'name': 'Next unit', 'site_id': site['id']})
    assert next_unit['id'] > unit['id']
    assert admin.delete(f'/api/hrm/units/{next_unit["id"]}?company_id=1', headers=headers,
                        json={'version': next_unit['version']}).status_code == 200
    assert admin.delete(f'/api/hrm/sites/{site["id"]}?company_id=1', headers=headers,
                        json={'version': site['version']}).status_code == 200
    assert admin.get(f'/api/hrm/sites/{site["id"]}?company_id=1').status_code == 404
    newer = create(admin, headers, 'sites', {'name': 'Next site'})
    assert newer['id'] > site['id']
    restarted = create_app(hrm['root'], testing=True)
    admin, headers = login(restarted)
    latest = create(admin, headers, 'sites', {'name': 'After restart'})
    assert latest['id'] > newer['id']
    latest_unit = create(admin, headers, 'units', {'name': 'After restart unit', 'site_id': latest['id']})
    assert latest_unit['id'] > next_unit['id']
    with sqlite3.connect(restarted.config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM hrm_org_sequences').fetchone()[0] == 2
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute("SELECT count(*) FROM audit WHERE action IN ('hrm_delete_sites','hrm_delete_units')").fetchone()[0] == 3


def test_linked_organization_locations_preserve_current_and_historical_ownership(hrm):
    admin, headers, person = hrm['client'], hrm['headers'], hrm['person']
    source, target = hrm['sites'][:2]
    original_unit = hrm['units'][0]
    for resource, row in [('sites', source), ('units', original_unit)]:
        result = admin.delete(f'/api/hrm/{resource}/{row["id"]}?company_id=1', headers=headers,
                              json={'version': row['version']})
        assert result.status_code == 409 and 'سوابق' in result.json['error']
    assert admin.put(f'/api/hrm/sites/{source["id"]}?company_id=1', headers=headers,
                     json={'version': source['version'], 'company_id': 2}).status_code == 400
    assert admin.put(f'/api/hrm/units/{original_unit["id"]}?company_id=1', headers=headers,
                     json={'version': original_unit['version'], 'site_id': target['id']}).status_code == 409
    moved = admin.put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=headers,
                       json={'version': person['version'], 'site_id': target['id'],
                             'unit_id': hrm['units'][1]['id'], 'effective_date': today_iso()})
    assert moved.status_code == 200, moved.json
    # Leaving a site does not authorize erasing its historical unit/assignment.
    assert admin.delete(f'/api/hrm/units/{original_unit["id"]}?company_id=1', headers=headers,
                        json={'version': original_unit['version']}).status_code == 409
    with sqlite3.connect(hrm['app'].config['DB']) as conn:
        history = conn.execute('SELECT site_id,previous_site_id FROM hrm_employment_history WHERE employee_id=? ORDER BY id',
                               (person['id'],)).fetchall()
        assert history[-1] == (target['id'], source['id'])
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_site_delete_checks_future_foreign_keys_and_target_columns(hrm):
    admin, headers = hrm['client'], hrm['headers']
    site = create(admin, headers, 'sites', {'name': 'Future linked site'})
    with sqlite3.connect(hrm['app'].config['DB']) as conn:
        conn.execute('CREATE TABLE future_location_facts(id INTEGER PRIMARY KEY,location_id INTEGER REFERENCES hrm_sites(id))')
        conn.execute('INSERT INTO future_location_facts(location_id) VALUES (?)', (site['id'],))
    assert admin.delete(f'/api/hrm/sites/{site["id"]}?company_id=1', headers=headers,
                        json={'version': site['version']}).status_code == 409
    with sqlite3.connect(hrm['app'].config['DB']) as conn:
        conn.execute('DELETE FROM future_location_facts')
        conn.execute('CREATE TABLE future_temp_locations(id INTEGER PRIMARY KEY,target_site_id INTEGER)')
        conn.execute('INSERT INTO future_temp_locations(target_site_id) VALUES (?)', (site['id'],))
    assert admin.delete(f'/api/hrm/sites/{site["id"]}?company_id=1', headers=headers,
                        json={'version': site['version']}).status_code == 409


def test_roster_current_members_temporary_visitors_unassigned_and_scopes(hrm):
    admin, headers = hrm['client'], hrm['headers']
    first, second, foreign = hrm['sites']
    person = hrm['person']
    local = create(admin, headers, 'people', {'code': 'SITE-B-2', 'name': 'Second site employee', 'site_id': second['id']})
    other = create(admin, headers, 'people', {'code': 'SITE-C-3', 'name': 'Foreign site employee', 'site_id': foreign['id']}, 2)
    unassigned = admin.post('/api/data/employees?company_id=1', headers=headers,
                            json={'code': 'UNPLACED-1', 'name': 'Site not assigned yet'})
    assert unassigned.status_code == 201, unassigned.json
    assignment = create(admin, headers, 'temporary_assignments', {
        'employee_id': person['id'], 'target_site_id': foreign['id'], 'start_date': today_iso(),
        'end_date': (date.today() + timedelta(days=5)).isoformat(), 'activity_location': 'Temporary hall'})
    all_local = admin.get('/api/hrm/site-roster?company_id=1')
    assert all_local.status_code == 200, all_local.json
    assert {row['id'] for row in all_local.json['people']} == {person['id'], local['id']}
    assert all_local.json['totals']['unassigned'] == 1
    assert all_local.json['unassigned'][0]['id'] == unassigned.json['id']
    assert all('national_id' not in row and 'phone' not in row and 'passport_number' not in row
               for row in all_local.json['people'] + all_local.json['unassigned'])
    response = admin.get(f'/api/hrm/site-roster?company_id=2&site_id={foreign["id"]}')
    assert response.status_code == 200, response.json
    assert [row['id'] for row in response.json['people']] == [other['id']]
    assert [row['id'] for row in response.json['temporary_assignments']] == [assignment['id']]
    assert response.json['sites'][0]['employee_count'] == 1
    assert response.json['sites'][0]['temporary_employee_count'] == 1
    assert response.json['people_basis'] == 'current_assignment'
    assert response.json['temporary_basis'] == 'active_on_as_of'
    before = (date.today() - timedelta(days=1)).isoformat()
    assert admin.get(f'/api/hrm/site-roster?site_id={foreign["id"]}&as_of={before}').json['temporary_assignments'] == []
    assert admin.get(f'/api/hrm/site-roster?company_id=1&site_id={foreign["id"]}').status_code == 400
    assert admin.get('/api/hrm/site-roster?as_of=invalid').status_code == 400
    local_c, _ = account(hrm, 'local-site-C', {'personnel': {'read': True}},
                         scopes=[{'company_id': 2, 'site_id': foreign['id']}])
    scoped = local_c.get(f'/api/hrm/site-roster?company_id=2&site_id={foreign["id"]}')
    assert scoped.status_code == 200 and [row['id'] for row in scoped.json['people']] == [other['id']]
    assert scoped.json['temporary_assignments'] == []
    assert 'Synthetic worker' not in scoped.get_data(as_text=True)
    assert local_c.get(f'/api/hrm/site-roster?site_id={first["id"]}').status_code == 403
    denied, _ = account(hrm, 'site-roster-no-personnel', {'contracts': {'read': True}})
    assert denied.get('/api/hrm/site-roster?company_id=1').status_code == 403
    holding, _ = account(hrm, 'site-roster-cross-company', {'personnel': {'read': True}},
                         scopes=[{'company_id': 1, 'site_id': first['id']},
                                 {'company_id': 2, 'site_id': foreign['id']}])
    assert len(holding.get(f'/api/hrm/site-roster?site_id={foreign["id"]}').json['temporary_assignments']) == 1
    moved = admin.put(f'/api/hrm/people/{person["id"]}?company_id=1', headers=headers,
                       json={'version': person['version'], 'site_id': second['id'],
                             'unit_id': hrm['units'][1]['id'], 'effective_date': today_iso()})
    assert moved.status_code == 200, moved.json
    assert admin.get(f'/api/hrm/site-roster?site_id={first["id"]}').json['people'] == []
    assert {row['id'] for row in admin.get(f'/api/hrm/site-roster?site_id={second["id"]}').json['people']} == {person['id'], local['id']}
    # The temporary assignment remains its own fact, distinct from current ownership.
    assert admin.get(f'/api/hrm/site-roster?site_id={foreign["id"]}').json['temporary_assignments'][0]['company_id'] == 1
