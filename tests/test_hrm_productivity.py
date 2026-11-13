"""Exact accords use canonical scores, explicit grants and immutable evidence."""
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import io
import json
import sqlite3

import openpyxl
import pytest

from app import create_app
from app.hrm_common import MAX_MONEY

PASSWORD = 'Accord-test-password-123'


def login(app, name):
    client = app.test_client()
    assert client.post('/api/login', json={'username': name, 'password': PASSWORD}).status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


@pytest.fixture
def accord(tmp_path):
    app = create_app(tmp_path, True)
    admin = app.test_client()
    assert admin.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    admin, h = login(app, 'admin')
    def post(resource, body):
        response = admin.post('/api/hrm/' + resource, headers=h, json=body)
        assert response.status_code == 201, (resource, response.json)
        return response.json
    sites = [post('sites', {'company_id': 1, 'name': 'Accord site ' + str(i), 'kind': 'factory'}) for i in (1, 2)]
    people = [post('people', dict(company_id=1, site_id=sites[i % 2]['id'], name='Accord person ' + str(i),
                 code='ACCORD-' + str(1000 + i), job='Operator', start_date='1405/01/01', effective_date='1405/01/01', department='Production' if i != 2 else 'Support')) for i in range(3)]
    def account(name, permissions, scope=None):
        response = admin.post('/api/hrm/access/users', headers=h, json={
            'username': name, 'password': PASSWORD, 'role': 'executive', 'training_access': False,
            'permissions': permissions, 'scopes': scope or [{'company_id': 1}]})
        assert response.status_code == 201, response.json
        return login(app, name)
    all_flags = dict(read=True, create=True, edit=True, delete=True, export=True)
    manager, mh = account('manager', {'performance': all_flags, 'productivity': all_flags})
    unit, uh = account('unit', {'performance': all_flags, 'productivity': all_flags}, [{'company_id': 1, 'site_id': sites[0]['id'], 'department': 'Production'}])
    financial, fh = account('financial', {'productivity': {'read': True, 'export': True}})
    performer, ph = account('performer', {'performance': all_flags})
    return dict(app=app, admin=admin, h=h, manager=manager, mh=mh, unit=unit, uh=uh,
                financial=financial, fh=fh, performer=performer, ph=ph,
                sites=sites, people=people, post=post, account=account)


def period(e, site=None, title='Monthly accord'):
    return e['post']('evaluation_periods', dict(company_id=1, site_id=site, title=title,
        start_date='1405/01/01', end_date='1405/01/31', status='open', note='PERIOD-COHORT-PRIVATE'))


def criterion(e, term, title='Quality', weight=1, maximum=100):
    return e['post']('performance_criteria', dict(company_id=1, site_id=term['site_id'], period_id=term['id'],
        title=title, weight=weight, max_score=maximum))


def score(e, person, term, key, value):
    return e['post']('performance_scores', dict(employee_id=person['id'], period_id=term['id'], criterion_id=key['id'], score=value))


def policy(e, term, **overrides):
    values = dict(company_id=1, site_id=term['site_id'], period_id=term['id'], title='Accord rule',
        method='individual_base', minimum_score=0, note='POLICY-COHORT-PRIVATE')
    values.update(overrides)
    return e['post']('accord_policies', values)


def entry(e, rule, person=None, **overrides):
    values = dict(policy_id=rule['id'], employee_id=(person or e['people'][0])['id'],
                  scheduled_minutes=480, actual_minutes=480)
    if rule['method'] == 'individual_base':
        values['base_rials'] = 1_000_001
    values.update(overrides)
    return e['post']('accord_entries', values)


def report(e, rule, client='manager'):
    response = e[client].get('/api/hrm/accord_report?policy_id=' + str(rule['id']))
    assert response.status_code == 200, response.json
    return response.json


def close_period(e, term):
    fresh = e['admin'].get('/api/hrm/evaluation_periods/' + str(term['id'])).json
    response = e['admin'].put('/api/hrm/evaluation_periods/' + str(term['id']), headers=e['h'],
        json={'version': fresh['version'], 'status': 'closed'})
    assert response.status_code == 200, response.json


def finalize(e, rule, preview=None, client='manager', headers='mh'):
    preview = preview or report(e, rule, client)
    return e[client].post('/api/hrm/accord_policies/' + str(rule['id']) + '/finalize', headers=e[headers],
        json={'version': preview['policy']['version'], 'calculation_token': preview['calculation_token']})


def test_weighted_items_are_complete_before_payment_and_use_actual_participation(accord):
    e = accord
    term = period(e)
    one, two = criterion(e, term, maximum=10), criterion(e, term, 'Quantity', 3)
    rule = policy(e, term)
    entry(e, rule, actual_minutes=240)
    score(e, e['people'][0], term, one, 5)
    partial = report(e, rule)
    row = partial['rows'][0]
    assert row['score_percent'] is None and row['accord_rials'] is None and not row['is_complete']
    assert row['completion_percent'] == 25 and row['completion_criteria_percent'] == 50 and row['missing_criteria'] == ['Quantity']
    assert any(item['missing'] and item['score'] is None for item in row['criteria'])
    score(e, e['people'][0], term, two, 80)
    preview = report(e, rule)
    row = preview['rows'][0]
    assert row['score_percent'] == 72.5 and row['participation_percent'] == 50
    assert row['accord_rials'] == 362_500
    assert sum(item['contribution_percent'] for item in row['criteria']) == 72.5
    assert not preview['can_finalize'] and 'دوره' in ' '.join(preview['finalize_blockers'])
    assert finalize(e, rule).status_code == 409
    close_period(e, term)
    final = finalize(e, rule)
    assert final.status_code == 200 and final.json['status'] == 'finalized'
    assert final.json['rows'][0]['accord_rials'] == 362_500


def test_displayed_percent_is_never_reused_for_whole_rial_calculation(accord):
    e = accord
    term = period(e)
    key = criterion(e, term, maximum=3)
    rule = policy(e, term)
    entry(e, rule, base_rials=str(MAX_MONEY), scheduled_minutes=3, actual_minutes=2)
    score(e, e['people'][0], term, key, 1)
    row = report(e, rule)['rows'][0]
    exact = Fraction(MAX_MONEY) * Fraction(1, 3) * Fraction(2, 3)
    expected = (2 * exact.numerator + exact.denominator) // (2 * exact.denominator)
    assert row['score_percent'] == 33.3333
    assert row['accord_rials'] == expected == 2_001_599_834_386_887
    assert report(e, rule)['metrics']['total_accord_rials_exact'] == str(expected)


def test_pool_allocates_every_rial_deterministically_and_incomplete_never_spends_it(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term, method='pool_budget', budget_rials='1000001')
    for person in e['people']:
        entry(e, rule, person)
    for person in e['people'][:2]:
        score(e, person, term, key, 100)
    assert all(row['accord_rials'] is None for row in report(e, rule)['rows'])
    score(e, e['people'][2], term, key, 100)
    preview = report(e, rule)
    assert [r['accord_rials'] for r in preview['rows']] == [333_334, 333_334, 333_333]
    assert preview['metrics']['total_accord_rials'] == 1_000_001
    assert preview['metrics']['unallocated_budget_rials'] == 0
    close_period(e, term)
    assert finalize(e, rule).status_code == 200


def test_all_zero_points_leave_pool_budget_unallocated_instead_of_dividing_or_inventing(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term, method='pool_budget', budget_rials=1001)
    entry(e, rule)
    score(e, e['people'][0], term, key, 0)
    preview = report(e, rule)
    assert preview['rows'][0]['is_complete'] and preview['rows'][0]['accord_rials'] == 0
    assert preview['metrics']['unallocated_budget_rials'] == 1001
    close_period(e, term)
    assert finalize(e, rule).json['metrics']['unallocated_budget_rials'] == 1001


def test_missing_criteria_and_no_cohort_are_explicit_finalize_blockers(accord):
    e = accord
    term = period(e)
    rule = policy(e, term)
    assert report(e, rule)['rows'] == []
    entry(e, rule)
    missing = report(e, rule)['rows'][0]
    assert not missing['is_complete'] and missing['accord_rials'] is None
    close_period(e, term)
    assert finalize(e, rule).status_code == 409
    with sqlite3.connect(e['app'].config['DB']) as c:
        assert c.execute('SELECT count(*) FROM hrm_accord_results').fetchone()[0] == 0


def test_suggested_items_are_explicit_editable_and_cannot_overwrite_a_period(accord):
    e = accord
    term = period(e)
    rule = policy(e, term)
    assert e['admin'].get('/api/hrm/performance_criteria').json == []
    path = '/api/hrm/accord_policies/' + str(rule['id']) + '/seed-criteria'
    assert e['financial'].post(path, headers=e['fh'], json={'version': rule['version']}).status_code == 403
    result = e['manager'].post(path, headers=e['mh'], json={'version': rule['version']})
    assert result.status_code == 200 and result.json['criteria_count'] == 8
    items = e['admin'].get('/api/hrm/performance_criteria').json
    assert sorted(row['weight'] for row in items) == [5, 5, 10, 10, 10, 10, 25, 25]
    assert sum(row['weight'] for row in items) == 100 and all(row['max_score'] == 100 for row in items)
    row = items[0]
    assert e['admin'].put('/api/hrm/performance_criteria/' + str(row['id']), headers=e['h'],
        json={'version': row['version'], 'title': 'Approved SMART quality', 'weight': 15,
              'note': 'Approved goal and measurable evidence'}).status_code == 200
    latest = e['admin'].get('/api/hrm/accord_policies/' + str(rule['id'])).json
    assert e['manager'].post(path, headers=e['mh'], json={'version': latest['version']}).status_code == 409


def test_financial_access_is_explicit_and_partial_scope_cannot_preview_or_finalize_whole_policy(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term)
    own = entry(e, rule)
    entry(e, rule, e['people'][1])
    for person in e['people'][:2]:
        score(e, person, term, key, 100)
    assert e['performer'].get('/api/hrm/accord_entries').status_code == 403
    assert e['performer'].get('/api/hrm/accord_policies').status_code == 403
    assert {r['id'] for r in e['unit'].get('/api/hrm/accord_entries').json} == {own['id']}
    assert e['unit'].get('/api/hrm/accord_report?policy_id=' + str(rule['id'])).status_code == 403
    assert e['financial'].get('/api/hrm/accord_report?policy_id=' + str(rule['id'])).status_code == 403
    assert e['unit'].post('/api/hrm/accord_policies', headers=e['uh'], json={
        'company_id': 1, 'site_id': None, 'period_id': term['id'], 'title': 'Whole company', 'method': 'individual_base'}).status_code == 403
    close_period(e, term)
    preview = report(e, rule)
    assert finalize(e, rule, preview, 'unit', 'uh').status_code == 403
    assert finalize(e, rule, preview).status_code == 200
    own_final = report(e, rule, 'unit')
    assert len(own_final['rows']) == 1 and own_final['policy_metrics'] is None
    assert len(report(e, rule, 'financial')['rows']) == 2


def test_partial_result_evidence_redacts_shared_budget_and_cohort_notes_without_altering_stored_snapshot(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term, method='pool_budget', budget_rials=1_000_003)
    for person in e['people'][:2]:
        entry(e, rule, person)
        score(e, person, term, key, 100)
    close_period(e, term)
    assert finalize(e, rule).status_code == 200
    listing = e['unit'].get('/api/hrm/accord_results').json
    assert len(listing) == 1
    rid = listing[0]['id']
    responses = [listing, e['unit'].get('/api/hrm/accord_results/' + str(rid)).json, report(e, rule, 'unit')]
    for response in responses:
        text = json.dumps(response)
        assert 'POLICY-COHORT-PRIVATE' not in text and 'PERIOD-COHORT-PRIVATE' not in text
        assert '1000003' not in text
    with sqlite3.connect(e['app'].config['DB']) as c:
        snapshot = json.loads(c.execute('SELECT snapshot_json FROM hrm_accord_results WHERE id=?', (rid,)).fetchone()[0])
        assert snapshot['policy']['budget_rials'] == 1_000_003
        assert snapshot['policy']['note'] == 'POLICY-COHORT-PRIVATE'
        assert snapshot['period']['note'] == 'PERIOD-COHORT-PRIVATE'


def test_review_token_rejects_changed_score_basis_and_final_snapshots_never_recalculate(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term)
    entry(e, rule)
    observed = score(e, e['people'][0], term, key, 80)
    close_period(e, term)
    old = report(e, rule)
    fresh_period = e['admin'].get('/api/hrm/evaluation_periods/' + str(term['id'])).json
    assert e['admin'].put('/api/hrm/evaluation_periods/' + str(term['id']), headers=e['h'], json={'version': fresh_period['version'], 'status': 'open'}).status_code == 200
    assert e['admin'].put('/api/hrm/performance_scores/' + str(observed['id']), headers=e['h'], json={'version': observed['version'], 'score': 90}).status_code == 200
    close_period(e, term)
    assert finalize(e, rule, old).status_code == 409
    final = finalize(e, rule)
    assert final.status_code == 200
    before = final.json['rows']
    with sqlite3.connect(e['app'].config['DB']) as c:
        c.execute('UPDATE hrm_performance_scores SET score=1 WHERE id=?', (observed['id'],))
        c.execute('UPDATE hrm_performance_criteria SET weight=9,title=? WHERE id=?', ('Changed later', key['id']))
        c.execute('UPDATE employees SET name=? WHERE id=?', ('Changed later employee', e['people'][0]['id']))
    assert report(e, rule)['rows'] == before
    assert finalize(e, rule).status_code == 409


def test_transfer_does_not_move_entry_results_or_prevent_historical_scope_finalization(accord):
    e = accord
    term = period(e, e['sites'][0]['id'])
    key = criterion(e, term)
    rule = policy(e, term)
    saved = entry(e, rule)
    score(e, e['people'][0], term, key, 100)
    person = e['admin'].get('/api/hrm/people/' + str(e['people'][0]['id'])).json
    moved = e['admin'].put('/api/hrm/people/' + str(person['id']), headers=e['h'],
        json={'version': person['version'], 'site_id': e['sites'][1]['id'], 'effective_date': '1405/02/01'})
    assert moved.status_code == 200, moved.json
    close_period(e, term)
    old_site, sh = e['account']('old-site', {'performance': {'read': True}, 'productivity': {'read': True, 'edit': True}}, [{'company_id': 1, 'site_id': e['sites'][0]['id']}])
    preview = old_site.get('/api/hrm/accord_report?policy_id=' + str(rule['id'])).json
    response = old_site.post('/api/hrm/accord_policies/' + str(rule['id']) + '/finalize', headers=sh,
        json={'version': preview['policy']['version'], 'calculation_token': preview['calculation_token']})
    assert response.status_code == 200, response.json
    row = response.json['rows'][0]
    assert row['site_id'] == saved['site_id'] == e['sites'][0]['id']
    assert row['department'] == saved['department']
    new_site, _ = e['account']('new-site', {'productivity': {'read': True}}, [{'company_id': 1, 'site_id': e['sites'][1]['id']}])
    assert new_site.get('/api/hrm/accord_results').json == []


def test_archives_duplicates_versions_and_frozen_records(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term)
    saved = entry(e, rule)
    assert e['admin'].post('/api/hrm/accord_entries', headers=e['h'], json=dict(policy_id=rule['id'], employee_id=saved['employee_id'], base_rials=1, scheduled_minutes=1, actual_minutes=1)).status_code == 409
    assert e['admin'].post('/api/hrm/accord_policies', headers=e['h'], json=dict(company_id=1, period_id=term['id'], title='Duplicate period', method='individual_base')).status_code == 409
    path = '/api/hrm/accord_entries/' + str(saved['id'])
    assert e['manager'].delete(path, headers=e['mh'], json={'version': 99}).status_code == 409
    assert e['manager'].delete(path, headers=e['mh'], json={'version': saved['version']}).status_code == 200
    assert e['manager'].get('/api/hrm/accord_entries').json == []
    assert e['manager'].get('/api/hrm/accord_entries?include_deleted=1').json[0]['is_deleted'] == 1
    assert e['manager'].post(path + '/restore', headers=e['mh'], json={'version': 2}).status_code == 200
    score(e, e['people'][0], term, key, 100)
    close_period(e, term)
    assert finalize(e, rule).status_code == 200
    row = e['manager'].get(path).json
    assert row['locked'] and row['policy_status'] == 'finalized'
    assert e['manager'].put(path, headers=e['mh'], json={'version': row['version'], 'base_rials': 2000}).status_code == 409
    assert e['manager'].delete(path, headers=e['mh'], json={'version': row['version']}).status_code == 409
    assert e['manager'].post('/api/hrm/accord_results', headers=e['mh'], json={}).status_code == 404


def test_export_uses_explicit_financial_grant_and_keeps_scopes(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term)
    for person in e['people'][:2]:
        entry(e, rule, person)
        score(e, person, term, key, 100)
    close_period(e, term)
    assert finalize(e, rule).status_code == 200
    exporter, _ = e['account']('export-only', {'productivity': {'export': True}}, [{'company_id': 1, 'site_id': e['sites'][0]['id']}])
    assert exporter.get('/api/hrm/accord_results').status_code == 403
    response = exporter.get('/api/hrm/reports/accord_results.xlsx')
    assert response.status_code == 200, response.json
    workbook = openpyxl.load_workbook(io.BytesIO(response.data), read_only=True)
    cells = ' '.join(str(v) for row in workbook.active.values for v in row)
    assert e['people'][0]['name'] in cells and e['people'][1]['name'] not in cells
    reader, _ = e['account']('read-only', {'productivity': {'read': True}})
    assert reader.get('/api/hrm/reports/accord_results.xlsx').status_code == 403


@pytest.mark.parametrize('patch', [{'base_rials': True}, {'base_rials': -1}, {'base_rials': '1.5'},
    {'scheduled_minutes': 0}, {'actual_minutes': 481}, {'actual_minutes': -1},
    {'accord_rials': 999999}, {'employee_id': False}])
def test_invalid_financial_input_never_writes(accord, patch):
    e = accord
    rule = policy(e, period(e))
    body = dict(policy_id=rule['id'], employee_id=e['people'][0]['id'], base_rials=1,
                scheduled_minutes=480, actual_minutes=480)
    body.update(patch)
    result = e['admin'].post('/api/hrm/accord_entries', headers=e['h'], json=body)
    assert result.status_code == 400, result.json
    assert e['admin'].get('/api/hrm/accord_entries').json == []


def test_concurrent_finalization_has_one_immutable_winner(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term)
    entry(e, rule)
    score(e, e['people'][0], term, key, 100)
    close_period(e, term)
    preview = report(e, rule)
    def worker(_):
        client, headers = login(e['app'], 'manager')
        result = client.post('/api/hrm/accord_policies/' + str(rule['id']) + '/finalize', headers=headers,
            json={'version': preview['policy']['version'], 'calculation_token': preview['calculation_token']})
        return result.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(worker, range(2))) == [200, 409]
    with sqlite3.connect(e['app'].config['DB']) as c:
        assert c.execute('SELECT count(*) FROM hrm_accord_results').fetchone()[0] == 1
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []


def test_date_filters_use_period_overlap_and_frozen_period_snapshot(accord):
    e = accord
    term = period(e)
    key = criterion(e, term)
    rule = policy(e, term)
    entry(e, rule)
    score(e, e['people'][0], term, key, 100)
    for resource in ('accord_policies', 'accord_entries'):
        assert len(e['manager'].get('/api/hrm/' + resource + '?from_date=1405/01/15&to_date=1405/02/01').json) == 1
        assert e['manager'].get('/api/hrm/' + resource + '?from_date=1405/02/01').json == []
        assert e['manager'].get('/api/hrm/' + resource + '?from_date=1405/02/01&to_date=1405/01/01').status_code == 400
        assert e['manager'].get('/api/hrm/' + resource + '?from_date=1405/01/01&from=1405/01/02').status_code == 400
    close_period(e, term)
    assert finalize(e, rule).status_code == 200
    with sqlite3.connect(e['app'].config['DB']) as c:
        c.execute("UPDATE hrm_evaluation_periods SET start_date='2020-01-01',end_date='2020-01-31' WHERE id=?", (term['id'],))
    for resource in ('accord_policies', 'accord_entries', 'accord_results'):
        result = e['manager'].get('/api/hrm/' + resource + '?from_date=1405/01/15&to_date=1405/02/01')
        assert result.status_code == 200 and len(result.json) == 1, result.json
        assert result.json[0]['period_start_date'] != '2020-01-01'
        assert e['manager'].get('/api/hrm/' + resource + '?to_date=2020-12-31').json == []
    assert report(e, rule)['rows'][0]['period_start_date'] != '2020-01-01'
