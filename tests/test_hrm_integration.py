"""The dossier links authorized facts without another people bank or privilege bypass."""
import io
import sqlite3

import pytest

from app import create_app
from app.hrm_common import today_iso

PASSWORD = 'Integration-personnel-password-123'


@pytest.fixture
def integrated(tmp_path):
    app = create_app(tmp_path, testing=True)
    admin = app.test_client()
    assert admin.post('/api/setup', json={'username': 'integration-admin', 'password': PASSWORD}).status_code == 200
    assert admin.post('/api/login', json={'username': 'integration-admin', 'password': PASSWORD}).status_code == 200
    headers = {'X-CSRF-Token': admin.get('/api/me').json['csrf']}

    def post(resource, body, company=1):
        response = admin.post('/api/hrm/' + resource + '?company_id=' + str(company), json=body, headers=headers)
        assert response.status_code == 201, (resource, response.json)
        return response.json

    sites = [post('sites', {'name': name, 'kind': 'factory'}, company)
             for name, company in [('Dossier A', 1), ('Dossier B', 1), ('Dossier C', 2)]]
    first = post('people', {'code': 'DOS-1234', 'name': 'Canonical dossier person',
                          'company_id': 1, 'site_id': sites[0]['id'], 'department': 'Production',
                          'job': 'Operator', 'start_date': '2026-01-01',
                          'national_id': '0012345678', 'phone': '09000000000',
                          'address': 'PRIVATE-PERSONNEL-ADDRESS'})
    peer = post('people', {'code': 'DOS-5678', 'name': 'Other site person',
                         'company_id': 1, 'site_id': sites[1]['id'], 'department': 'Other', 'job': 'Other'})
    contract = post('contracts', {'employee_id': first['id'], 'number': 'DOS-C-001',
                                 'contract_type': 'Organization type', 'start_date': '2026-01-01',
                                 'end_date': '2027-01-01', 'notes': 'PRIVATE-CONTRACT-NOTE'})
    attendance = post('attendance', {'employee_id': first['id'], 'work_date': '2026-10-01',
                                     'kind': 'work', 'minutes': 480})
    post('health_records', {'employee_id': first['id'], 'exam_date': '2026-10-01',
                           'exam_type': 'Occupational', 'fitness': 'fit',
                           'restrictions': 'CLINICAL-PRIVATE-RESTRICTION'})
    return dict(app=app, admin=admin, headers=headers, post=post, first=first, peer=peer,
                sites=sites, contract=contract, attendance=attendance)


def account(fixture, name, permissions, scopes=None):
    response = fixture['admin'].post('/api/hrm/access/users', headers=fixture['headers'], json={
        'username': name, 'password': PASSWORD, 'role': 'holding_manager',
        'scopes': scopes if scopes is not None else [{'company_id': 1, 'site_id': fixture['sites'][0]['id'], 'department': ''}],
        'permissions': permissions, 'training_access': False})
    assert response.status_code == 201, response.json
    client = fixture['app'].test_client()
    assert client.post('/api/login', json={'username': name, 'password': PASSWORD}).status_code == 200
    return client


def transfer(fixture, site_index=1):
    destination = fixture['sites'][site_index]
    response = fixture['admin'].put('/api/hrm/people/' + str(fixture['first']['id']) + '?company_id=1',
        headers=fixture['headers'], json={'version': fixture['first']['version'],
        'company_id': destination['company_id'], 'site_id': destination['id'],
        'department': 'SECRET-CURRENT-UNIT', 'job': 'SECRET-CURRENT-JOB',
        'effective_date': today_iso()})
    assert response.status_code == 200, response.json
    fixture['first'] = response.json


def overview(client, fixture, company=1, **filters):
    query = {'company_id': company, **filters}
    return client.get('/api/hrm/people/' + str(fixture['first']['id']) + '/overview', query_string=query)


def test_primary_dossier_uses_one_identity_and_only_minimal_person_fields(integrated):
    admin = integrated['admin'];eid = integrated['first']['id']
    response = overview(admin, integrated)
    assert response.status_code == 200, response.json
    data = response.json
    assert data['person']['id'] == eid and data['current_assignment_visible']
    assert data['person']['site_id'] == integrated['sites'][0]['id']
    assert data['sections']['contracts']['resource_counts']['contracts'] == 1
    assert data['sections']['attendance']['count'] == 1
    assert data['sections']['health']['count'] == 1
    assert 'people' in data['sections']['personnel']['resources']
    assert data['sections']['contracts']['resource_filters']['documents'] == {'permission_group': 'contracts'}
    serialized = response.get_data(as_text=True)
    for private in ('0012345678', '09000000000', 'PRIVATE-PERSONNEL-ADDRESS',
                    'PRIVATE-CONTRACT-NOTE', 'CLINICAL-PRIVATE-RESTRICTION'):
        assert private not in serialized
    directory = admin.get('/api/hrm/people-directory?company_id=1').json
    ids = [row['id'] for row in directory['people']]
    assert ids.count(eid) == 1
    assert len(ids) == 2
    with sqlite3.connect(integrated['app'].config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM employees').fetchone()[0] == 2


def test_site_local_directory_and_scope_filters_do_not_expose_peer(integrated):
    local = account(integrated, 'site-dossier-reader', {'personnel': {'read': True}, 'attendance': {'read': True}})
    result = local.get('/api/hrm/people-directory?company_id=1')
    assert result.status_code == 200, result.json
    assert [row['id'] for row in result.json['people']] == [integrated['first']['id']]
    assert local.get('/api/hrm/people-directory?company_id=2').status_code == 403
    assert local.get('/api/hrm/people-directory?company_id=1&site_id=' + str(integrated['sites'][1]['id'])).status_code == 403
    assert local.get('/api/hrm/people-directory?company_id=1&department=Other').json['people'] == []
    unit = account(integrated, 'unit-dossier-reader', {'personnel': {'read': True}},
                   scopes=[{'company_id': 1, 'site_id': integrated['sites'][0]['id'], 'department': 'Production'}])
    assert unit.get('/api/hrm/people-directory?company_id=1&department=Other').status_code == 403
    assert local.get('/api/hrm/people/' + str(integrated['peer']['id']) + '/overview?company_id=1').status_code == 403
    assert local.get('/api/hrm/people-directory?q=not-present&company_id=1').json['people'] == []


def test_history_only_identity_hides_new_location_and_disables_all_writes(integrated):
    old = account(integrated, 'old-attendance-scope', {'attendance': {'read': True, 'create': True, 'edit': True, 'delete': True, 'export': True}})
    transfer(integrated, site_index=2)
    result = old.get('/api/hrm/people-directory?company_id=1')
    assert result.status_code == 200, result.json
    assert len(result.json['people']) == 1
    identity = result.json['people'][0]
    assert identity['id'] == integrated['first']['id']
    assert identity['identity_basis'] == 'authorized_history'
    assert identity['company_id'] is None and identity['site_id'] is None and not identity['job']
    response = overview(old, integrated)
    assert response.status_code == 200, response.json
    assert response.json['sections']['attendance']['count'] == 1
    assert not response.json['current_assignment_visible']
    for section in response.json['sections'].values():
        assert not any(section[operation] for operation in ('create', 'edit', 'delete'))
    assert 'SECRET-CURRENT' not in response.get_data(as_text=True)
    assert 'Dossier C' not in response.get_data(as_text=True)
    new = account(integrated, 'new-attendance-scope', {'attendance': {'read': True}},
                  scopes=[{'company_id': 2, 'site_id': integrated['sites'][2]['id'], 'department': ''}])
    fresh = overview(new, integrated, company=2)
    assert fresh.status_code == 200, fresh.json
    assert fresh.json['current_assignment_visible']
    assert fresh.json['sections']['attendance']['count'] == 0


def test_archived_fact_cannot_authorize_former_employee_identity(integrated):
    old = account(integrated, 'only-old-attendance', {'attendance': {'read': True}})
    transfer(integrated)
    archived = integrated['admin'].delete('/api/hrm/attendance/' + str(integrated['attendance']['id']) + '?company_id=1',
                                         headers=integrated['headers'], json={'version': integrated['attendance']['version']})
    assert archived.status_code == 200, archived.json
    assert old.get('/api/hrm/people-directory?company_id=1').json['people'] == []
    assert overview(old, integrated).status_code == 403


def test_no_clinical_query_or_count_without_actual_health_read(integrated, monkeypatch):
    safety = account(integrated, 'safety-no-health', {'hse': {'read': True}, 'health_environment': {'read': True}})
    statements = []
    connect = sqlite3.connect
    def traced(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection
    monkeypatch.setattr(sqlite3, 'connect', traced)
    response = overview(safety, integrated)
    assert response.status_code == 200, response.json
    assert response.json['sections']['health']['read'] is False
    assert response.json['sections']['health']['count'] is None
    assert 'CLINICAL-PRIVATE' not in response.get_data(as_text=True)
    assert not any('hrm_health_records' in sql.lower() for sql in statements)
    clinical = account(integrated, 'clinical-dossier', {'health': {'read': True}})
    response = overview(clinical, integrated)
    assert response.status_code == 200, response.json
    assert response.json['sections']['health']['count'] == 1
    assert response.json['sections']['contracts']['count'] is None


def test_create_only_reference_has_no_fact_counts_but_can_select_current_person(integrated):
    creator = account(integrated, 'attendance-entry-only', {'attendance': {'create': True}})
    directory = creator.get('/api/hrm/people-directory?company_id=1')
    assert directory.status_code == 200, directory.json
    assert [row['id'] for row in directory.json['people']] == [integrated['first']['id']]
    response = overview(creator, integrated)
    assert response.status_code == 200, response.json
    assert response.json['sections']['attendance']['create']
    assert response.json['sections']['attendance']['count'] is None
    assert not response.json['sections']['attendance']['read']
    assert not any('resource_counts' in section and section['resource_counts'] for section in response.json['sections'].values())
    unassigned = account(integrated, 'settings-not-a-dossier-grant', {'settings': {'read': True}})
    assert unassigned.get('/api/hrm/people-directory?company_id=1').status_code == 403


def test_document_counts_follow_real_permission_group_not_generic_documents(integrated):
    pdf = b'%PDF-1.4\nSynthetic dossier attachment\n%%EOF'
    for group in ('contracts', 'health', 'documents'):
        response = integrated['admin'].post('/api/hrm/documents?company_id=1', headers=integrated['headers'], data={
            'employee_id': str(integrated['first']['id']), 'permission_group': group, 'title': 'PRIVATE-' + group,
            'file': (io.BytesIO(pdf), group + '.pdf')})
        assert response.status_code == 201, response.json
    viewer = account(integrated, 'contracts-dossier-only', {'contracts': {'read': True}})
    response = overview(viewer, integrated)
    assert response.status_code == 200, response.json
    assert response.json['sections']['contracts']['resource_counts'] == {'contracts': 1, 'documents': 1}
    assert response.json['sections']['documents']['count'] is None
    assert response.json['sections']['health']['count'] is None


def test_certificate_only_history_uses_existing_protected_certificate_policy(integrated):
    response = integrated['admin'].post('/api/certificates?company_id=1', headers=integrated['headers'], data={
        'employee_id': str(integrated['first']['id']), 'title': 'Historical qualification',
        'file': (io.BytesIO(b'%PDF-1.4\nOld qualification\n%%EOF'), 'old.pdf')})
    assert response.status_code == 201, response.json
    transfer(integrated, site_index=2)
    old = account(integrated, 'former-company-training', {'training': {'read': True}},
                  scopes=[{'company_id': 1, 'site_id': None, 'department': ''}])
    directory = old.get('/api/hrm/people-directory?company_id=1')
    assert directory.status_code == 200, directory.json
    assert any(row['id'] == integrated['first']['id'] and row['identity_basis'] == 'authorized_history'
               for row in directory.json['people'])
    response = overview(old, integrated)
    assert response.status_code == 200, response.json
    assert response.json['sections']['training']['resource_counts']['training_certificates'] == 1
    assert response.json['person']['company_id'] is None
    site_only = account(integrated, 'legacy-certificate-unknown-site', {'training': {'read': True}})
    assert not any(row['id'] == integrated['first']['id'] for row in site_only.get('/api/hrm/people-directory?company_id=1').json['people'])


def test_reassessment_history_is_linked_without_exposing_new_site_current_level(integrated):
    eid = integrated['first']['id']
    for company in (1,):
        selected = integrated['admin'].post('/api/competency-bank/S001/select?company_id=' + str(company), headers=integrated['headers'])
        assert selected.status_code == 200, selected.json
    old = integrated['post']('training_assessments', {'employee_id': eid, 'competency_code': 'S001',
                              'level': 4, 'assessment_date': today_iso()})
    former = account(integrated, 'assessment-history-reader', {'training': {'read': True}})
    transfer(integrated, site_index=1)
    fresh = integrated['post']('training_assessments', {'employee_id': eid, 'competency_code': 'S001',
                                'level': 0, 'assessment_date': today_iso()}, company=1)
    response = overview(former, integrated)
    assert response.status_code == 200, response.json
    section = response.json['sections']['training']
    assert section['resource_counts'].get('training_assessment_history') == 1
    assert section['resource_counts'].get('training_assessments', 0) == 0
    assert not response.json['current_assignment_visible']
    assert 'SECRET-CURRENT' not in response.get_data(as_text=True)
    directory = former.get('/api/hrm/people-directory?company_id=1').json['people']
    assert any(row['id'] == eid and row['identity_basis'] == 'authorized_history' for row in directory)
    current = account(integrated, 'current-zero-reader', {'training': {'read': True}},
                      scopes=[{'company_id': 1, 'site_id': integrated['sites'][1]['id'], 'department': ''}])
    result = overview(current, integrated, company=1)
    assert result.status_code == 200, result.json
    assert result.json['sections']['training']['resource_counts']['training_assessments'] == 1
    assert result.json['sections']['training']['resource_counts'].get('training_assessment_history', 0) == 0
