"""Actionable summaries preserve actual dates, identity, scope and privacy."""
from datetime import date, timedelta
import sqlite3

import pytest

from app import create_app
from app.hrm_common import today_iso

PASSWORD = 'Synthetic-management-overview-123'


@pytest.fixture
def managed(tmp_path):
    app = create_app(tmp_path, testing=True)
    admin = app.test_client()
    assert admin.post('/api/setup', json={'username': 'overview-admin', 'password': PASSWORD}).status_code == 200
    assert admin.post('/api/login', json={'username': 'overview-admin', 'password': PASSWORD}).status_code == 200
    headers = {'X-CSRF-Token': admin.get('/api/me').json['csrf']}

    def post(resource, body, company=1):
        result = admin.post('/api/hrm/' + resource + '?company_id=' + str(company), json=body, headers=headers)
        assert result.status_code == 201, (resource, result.json)
        return result.json

    sites = [post('sites', {'name': name, 'kind': 'office'}, company)
             for name, company in [('Overview A', 1), ('Overview B', 1), ('Overview C', 2)]]
    first = post('people', {'company_id': 1, 'site_id': sites[0]['id'], 'department': 'Production',
                            'job': 'Operator', 'code': 'OV-001', 'name': 'Overview first',
                            'address': 'PRIVATE-ADDRESS-OVERVIEW'})
    peer = post('people', {'company_id': 1, 'site_id': sites[1]['id'], 'department': 'Secret',
                           'job': 'Operator', 'code': 'OV-002', 'name': 'Other site secret'})
    foreign = post('people', {'company_id': 2, 'site_id': sites[2]['id'], 'department': 'Foreign',
                              'job': 'Operator', 'code': 'OV-003', 'name': 'Foreign secret'}, 2)
    return dict(app=app, admin=admin, headers=headers, post=post,
                sites=sites, first=first, peer=peer, foreign=foreign)


def account(fixture, name, permissions, scopes=None):
    response = fixture['admin'].post('/api/hrm/access/users', headers=fixture['headers'], json={
        'username': name, 'password': PASSWORD, 'role': 'holding_manager',
        'scopes': scopes if scopes is not None else [dict(company_id=1, site_id=fixture['sites'][0]['id'], department='')],
        'permissions': permissions, 'training_access': False})
    assert response.status_code == 201, response.json
    client = fixture['app'].test_client()
    assert client.post('/api/login', json={'username': name, 'password': PASSWORD}).status_code == 200
    return client


def day(offset=0):
    return (date.fromisoformat(today_iso()) + timedelta(days=offset)).isoformat()


def overview(client, **filters):
    response = client.get('/api/hrm/management-overview', query_string=filters)
    assert response.status_code == 200, response.json
    return response.json


def category(response, key):
    return next(item for item in response['categories'] if item['key'] == key)


def contract(fixture, person=None, company=1, **overrides):
    body = dict(employee_id=(person or fixture['first'])['id'], number='OV-CON-' + str(overrides.get('number', 'default')),
                contract_type='Fixed', start_date=day(-60), end_date=day(12), status='active', notes='PRIVATE-CONTRACT-NOTES')
    body.update(overrides)
    return fixture['post']('contracts', body, company)


def test_contract_dates_states_and_exact_counts_exceeding_bounded_examples(managed):
    for index in range(9):
        contract(managed, number='past-' + str(index), end_date=day(-index - 1))
    for index, offset in enumerate((0, 1, 30, 31)):
        contract(managed, number='boundary-' + str(index), end_date=day(offset))
    for status in ('ended', 'terminated', 'renewed', 'draft'):
        contract(managed, number=status, end_date=day(-2), status=status)
    contract(managed, number='future-start', start_date=day(1), end_date=day(4))
    contract(managed, number='open-ended', end_date='')
    data = overview(managed['admin'], company_id=1)
    expired, upcoming = category(data, 'contracts_expired'), category(data, 'contracts_due')
    assert expired['count'] == 9 and len(expired['items']) == 6 and expired['truncated']
    assert upcoming['count'] == 3 and not upcoming['truncated']
    assert data['as_of'] == today_iso() and data['horizon_days'] == 30 and data['due_before'] == day(30)
    text = str(data)
    assert 'PRIVATE-CONTRACT-NOTES' not in text and 'PRIVATE-ADDRESS' not in text


def test_current_staff_incomplete_is_once_per_person_and_excludes_ended_staff(managed):
    missing = managed['post']('people', {'code': 'OV-MISS', 'name': 'Missing placement'})
    managed['post']('people', {'code': 'OV-END', 'name': 'Ended placement', 'status': 'terminated'})
    data = overview(managed['admin'], company_id=1, section='personnel')
    incomplete = category(data, 'personnel_incomplete')
    assert incomplete['count'] == 1 and incomplete['items'][0]['employee_id'] == missing['id']
    assert data['metrics']['current_personnel'] == 3
    assert incomplete['items'][0]['target']['resource'] == 'people'
    assert all(item['module'] in ('personnel', 'contracts', 'performance') for item in data['categories'])


def test_company_site_department_and_person_filters_preserve_record_scope(managed):
    first = contract(managed, number='first')
    contract(managed, person=managed['peer'], number='other-site')
    contract(managed, person=managed['foreign'], company=2, number='foreign')
    local = account(managed, 'overview-site', {'contracts': {'read': True}})
    data = overview(local, company_id=1)
    assert category(data, 'contracts_due')['count'] == 1
    assert category(data, 'contracts_due')['items'][0]['id'] == first['id']
    assert {item['module'] for item in data['categories']} == {'contracts'}
    assert category(overview(local, employee_id=managed['peer']['id']), 'contracts_due')['count'] == 0
    assert local.get('/api/hrm/management-overview?company_id=2').status_code == 403
    assert local.get('/api/hrm/management-overview?site_id=' + str(managed['sites'][1]['id'])).status_code == 403
    unit = account(managed, 'overview-unit', {'contracts': {'read': True}},
                   [dict(company_id=1, site_id=managed['sites'][0]['id'], department='Production')])
    assert category(overview(unit), 'contracts_due')['count'] == 1
    assert unit.get('/api/hrm/management-overview?department=Secret').status_code == 403


def test_transfer_keeps_historical_contract_owner_without_current_assignment_leak(managed):
    contract(managed, number='historical')
    local = account(managed, 'overview-history', {'contracts': {'read': True}, 'personnel': {'read': True}})
    response = managed['admin'].put('/api/hrm/people/' + str(managed['first']['id']) + '?company_id=1',
        headers=managed['headers'], json={'company_id': 2, 'site_id': managed['sites'][2]['id'],
            'department': 'SECRET-NEW-UNIT', 'job': 'SECRET-NEW-JOB',
            'version': managed['first']['version'], 'effective_date': today_iso()})
    assert response.status_code == 200, response.json
    data = overview(local, company_id=1)
    assert data['metrics']['current_personnel'] == 0
    item = category(data, 'contracts_due')['items'][0]
    assert item['company_id'] == 1 and item['site_id'] == managed['sites'][0]['id']
    assert 'SECRET-NEW' not in str(data) and 'Overview C' not in str(data)


def test_matrix_is_shared_zero_unknown_ideal_and_assessment_trace(managed):
    post = managed['post']
    unknown = post('people', {'code': 'OV-UNK', 'name': 'Unknown evidence', 'job': 'Operator',
                              'site_id': managed['sites'][0]['id'], 'department': 'Production'})
    post('training_requirements', {'company_id': 1, 'site_id': managed['sites'][0]['id'],
                                   'job': 'Operator', 'competency_code': 'S001', 'required_level': 3})
    post('training_assessments', {'employee_id': managed['first']['id'], 'competency_code': 'S001',
                                 'level': 0, 'assessment_date': day()})
    local = account(managed, 'overview-training', {'training': {'read': True}})
    data = overview(local, company_id=1)
    assert category(data, 'training_gaps')['count'] == 1
    unassessed = category(data, 'training_unassessed')
    assert unassessed['count'] == 1 and unassessed['items'][0]['employee_id'] == unknown['id']
    assert category(data, 'training_evidence_reviews')['count'] == 1
    assert category(data, 'training_renewal_due')['count'] == 0
    assert {row['module'] for row in data['categories']} == {'training'}
    single = overview(local, employee_id=managed['first']['id'])
    assert category(single, 'training_unassessed')['count'] == 0


def test_unconfigured_training_and_large_scope_are_not_fake_green(managed, monkeypatch):
    data = overview(managed['admin'], company_id=1, section='training')
    assert category(data, 'training_unassessed')['status'] == 'not_configured'
    from app import hrm_matrix
    def oversized(*args, **kwargs):
        raise ValueError('ماتریس بسیار بزرگ است؛ محدوده شرکت، سایت یا فرد را کوچک‌تر کنید')
    monkeypatch.setattr(hrm_matrix, 'matrix_report', oversized)
    data = overview(managed['admin'], section='training')
    assert len(data['categories']) == 4
    assert all(row['count'] is None and row['status'] == 'scope_too_large' for row in data['categories'])


def test_clinical_tables_not_even_queried_without_health_read(managed, monkeypatch):
    managed['post']('health_records', {'employee_id': managed['first']['id'], 'exam_type': 'Occupational',
                                      'exam_date': day(-90), 'next_review_date': day(12),
                                      'fitness': 'fit', 'restrictions': 'MEDICAL-SECRET-OVERVIEW'})
    safety = account(managed, 'overview-safety', {'hse': {'read': True}, 'health_environment': {'read': True}})
    original = sqlite3.connect;statements = []
    def traced(*args, **kwargs):
        conn = original(*args, **kwargs);conn.set_trace_callback(statements.append);return conn
    monkeypatch.setattr(sqlite3, 'connect', traced)
    data = overview(safety)
    assert 'medical_follow_up' not in data['metrics']
    assert not any('hrm_health_records' in sql.lower() or 'hrm_medical_requirements' in sql.lower() for sql in statements)
    assert 'MEDICAL-SECRET' not in str(data)


def test_authorized_health_follows_actual_next_review_without_clinical_fields(managed):
    managed['post']('health_records', {'employee_id': managed['first']['id'], 'exam_type': 'Occupational',
                                      'exam_date': day(-90), 'next_review_date': day(12),
                                      'fitness': 'fit', 'restrictions': 'MEDICAL-SECRET-OVERVIEW'})
    clinical = account(managed, 'overview-health', {'health': {'read': True}})
    data = overview(clinical)
    health = category(data, 'medical_follow_up')
    assert health['count'] == 1 and health['items'][0]['employee_id'] == managed['first']['id']
    assert health['items'][0]['due_date'] == day(12)
    assert 'MEDICAL-SECRET' not in str(data) and 'fitness' not in str(data)
    assert {row['module'] for row in data['categories']} == {'health'}


def test_ppe_follow_up_describes_delivered_records_and_ignores_returned_or_archived(managed):
    gear = managed['post']('ppe_catalog', {'name': 'Synthetic helmet'})
    for status, due in [('delivered', -1), ('delivered', 30), ('delivered', 31), ('returned', -1)]:
        managed['post']('ppe_issues', {'employee_id': managed['first']['id'], 'ppe_id': gear['id'],
                                      'issue_date': day(-60), 'quantity': 1, 'status': status,
                                      'next_due_date': day(due)})
    safety = account(managed, 'overview-ppe', {'hse': {'read': True}})
    data = overview(safety)
    ppe = category(data, 'ppe_follow_up')
    assert ppe['count'] == 2 and {row['due_date'] for row in ppe['items']} == {day(-1), day(30)}
    assert 'کمبود قطعی' in ppe['description']


def test_performance_grant_does_not_query_or_disclose_finance_and_periods_need_follow_up(managed, monkeypatch):
    managed['post']('evaluation_periods', {'company_id': 1, 'site_id': managed['sites'][0]['id'],
                                          'title': 'Closing evaluation', 'start_date': day(-40),
                                          'end_date': day(-1), 'status': 'open'})
    performance = account(managed, 'overview-performance', {'performance': {'read': True}})
    original = sqlite3.connect;statements = []
    def traced(*args, **kwargs):
        conn = original(*args, **kwargs);conn.set_trace_callback(statements.append);return conn
    monkeypatch.setattr(sqlite3, 'connect', traced)
    data = overview(performance)
    assert category(data, 'performance_periods_due')['count'] == 1
    assert not any('from hrm_accord_' in sql.lower() or 'from hrm_loans' in sql.lower() for sql in statements)
    assert 'budget_rials' not in str(data) and {item['module'] for item in data['categories']} == {'performance'}


def test_point_in_time_read_is_nonmutating_and_rejects_unavailable_sections_dates(managed):
    local = account(managed, 'overview-readonly', {'contracts': {'read': True}})
    creator = account(managed, 'overview-writeonly', {'personnel': {'create': True}})
    with sqlite3.connect(managed['app'].config['DB']) as conn:
        before = conn.execute('SELECT count(*) FROM audit').fetchone()[0]
    data = overview(local, from_date=day(-100), to_date=day(-50))
    assert data['date_filters_applied'] is False
    with sqlite3.connect(managed['app'].config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM audit').fetchone()[0] == before
    assert local.get('/api/hrm/management-overview?section=training').status_code == 403
    assert local.get('/api/hrm/management-overview?as_of=' + day(-1)).status_code == 400
    assert local.get('/api/hrm/management-overview?section=invalid').status_code == 400
    assert creator.get('/api/hrm/management-overview').status_code == 403
    assert managed['app'].test_client().get('/api/hrm/management-overview').status_code == 401


@pytest.mark.parametrize('stale_filters', [
    {'q': 'ZZZZ_NOMATCH_STALE_SEARCH'},
    {'from_date': '2099-01-01', 'to_date': '2000-01-01'},
])
def test_point_in_time_staff_and_medical_ignore_stale_search_and_period_filters(managed, stale_filters):
    managed['post']('people', {'code': 'OV-SNAPSHOT', 'name': 'Incomplete current placement'})
    managed['post']('health_records', {'employee_id': managed['first']['id'], 'exam_type': 'Occupational',
                                      'exam_date': day(-90), 'next_review_date': day(12), 'fitness': 'fit'})
    expected = overview(managed['admin'], company_id=1)
    assert expected['metrics']['current_personnel'] == 3
    assert category(expected, 'personnel_incomplete')['count'] == 1
    assert category(expected, 'medical_follow_up')['count'] == 1
    actual = overview(managed['admin'], company_id=1, **stale_filters)
    assert actual['date_filters_applied'] is False
    assert actual['filters'] == {'company_id': 1}
    assert actual == expected
