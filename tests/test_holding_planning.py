import sqlite3
from pathlib import Path

import pytest

from app import create_app


PASSWORD = 'Secure-password-123'
PERSONNEL_FIELDS = {'id', 'code', 'name', 'job', 'department', 'company_id', 'company_name'}
COURSE_FIELDS = {'id', 'title', 'date', 'company_id', 'calendar_scope', 'cost', 'status'}


def login(app, username):
    client = app.test_client()
    assert client.post('/api/login', json={'username': username, 'password': PASSWORD}).status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


@pytest.fixture
def planning(tmp_path):
    app = create_app(tmp_path, testing=True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    client, headers = login(app, 'admin')
    employees = {}
    for cid, name in ((1, 'Zahra'), (2, 'Ali'), (3, 'Maryam')):
        response = client.post(
            f'/api/data/employees?company_id={cid}', headers=headers,
            json={'code': f'E{cid}', 'name': name, 'job': 'Engineer', 'department': f'Department {cid}'},
        )
        assert response.status_code == 201
        employees[cid] = response.json['id']
    skill = client.post('/api/data/skills?company_id=1', headers=headers, json={
        'code': 'JOINT', 'title': 'Joint training skill',
    }).json['id']
    courses = {}
    for scope in ('holding', 'internal'):
        response = client.post('/api/data/courses?company_id=1', headers=headers, json={
            'skill_id': skill, 'title': f'{scope} training', 'date': '1405/04/01',
            'hours': 2, 'calendar_scope': scope,
        })
        assert response.status_code == 201
        courses[scope] = response.json['id']
    for role in ('manager', 'viewer'):
        assert client.post('/api/users', headers=headers, json={
            'username': role, 'role': role, 'company_id': 1, 'password': PASSWORD,
        }).status_code == 201
    return app, client, headers, employees, courses


def participant_url(course):
    return f'/api/courses/{course}/participants'


def test_admin_directory_and_persisted_cross_company_participants(planning):
    app, client, headers, employees, courses = planning
    directory = client.get('/api/holding/personnel')
    assert directory.status_code == 200
    assert [row['name'] for row in directory.json] == ['Ali', 'Maryam', 'Zahra']
    assert {row['company_id'] for row in directory.json} == {1, 2, 3}
    assert {row['id'] for row in directory.json} == set(employees.values())
    for row in directory.json:
        assert set(row) == PERSONNEL_FIELDS
        assert row['company_name']
        assert row['job'] == 'Engineer'
    url = participant_url(courses['holding'])
    assert client.get(url).json['participants'] == []
    response = client.put(url, headers=headers, json={'employee_ids': [employees[1], employees[2], employees[3]]})
    assert response.status_code == 200
    assert set(response.json['course']) == COURSE_FIELDS
    assert response.json['participants'] == [dict(row, attended=False) for row in directory.json]
    assert client.get(url).json == response.json
    # Selection plans attendance; it must not claim training or create scores.
    for cid in (1, 2, 3):
        assert client.get(f'/api/data/records?company_id={cid}').json == []
    audit = client.get('/api/audit?company_id=1').json[0]
    assert audit['action'] == 'update_course_participants'
    assert 'Ali' not in audit['detail']


@pytest.mark.parametrize('username', ['manager', 'viewer'])
def test_company_roles_cannot_read_or_modify_holding_rosters(planning, username):
    app, admin, headers, employees, courses = planning
    url = participant_url(courses['holding'])
    assert admin.put(url, headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    client, role_headers = login(app, username)
    for path in ('/api/holding/personnel', url, participant_url(courses['internal']), participant_url(99999)):
        response = client.get(path)
        assert response.status_code == 403
        assert b'Ali' not in response.data
        assert b'Maryam' not in response.data
    assert client.put(url, headers=role_headers, json={'employee_ids': []}).status_code == 403
    assert [row['id'] for row in admin.get(url).json['participants']] == [employees[2]]
    # Existing employee-company access remains unchanged.
    assert client.get('/api/data/employees?company_id=1').status_code == 200
    assert client.get('/api/data/employees?company_id=2').status_code == 403


def test_authentication_csrf_internal_and_missing_courses(planning):
    app, client, headers, employees, courses = planning
    url = participant_url(courses['holding'])
    anonymous = app.test_client()
    assert anonymous.get('/api/holding/personnel').status_code == 401
    assert anonymous.get(url).status_code == 401
    assert anonymous.put(url, json={'employee_ids': [employees[1]]}).status_code == 401
    assert client.put(url, json={'employee_ids': [employees[1]]}).status_code == 403
    assert client.get(participant_url(courses['internal'])).status_code == 400
    assert client.put(
        participant_url(courses['internal']), headers=headers, json={'employee_ids': [employees[2]]},
    ).status_code == 400
    assert client.get(participant_url(99999)).status_code == 404
    assert client.put(participant_url(99999), headers=headers, json={'employee_ids': []}).status_code == 404
    assert client.get(url).json['participants'] == []


def test_validation_is_atomic_and_empty_selection_clears_roster(planning):
    _, client, headers, employees, courses = planning
    url = participant_url(courses['holding'])
    assert client.put(url, headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    original = client.get(url).json
    invalid_bodies = [
        {}, [], None, {'employee_ids': None}, {'employee_ids': 'all'},
        {'employee_ids': [employees[1], employees[1]]},
        {'employee_ids': [employees[1], 999999]},
        {'employee_ids': [True]}, {'employee_ids': ['1']},
        {'employee_ids': [1.0]}, {'employee_ids': [None]},
        {'employee_ids': [[]]}, {'employee_ids': [{}]},
        {'employee_ids': [0]}, {'employee_ids': [-1]}, {'employee_ids': [2 ** 64]},
        {'employee_ids': list(range(1, 5002))},
    ]
    for body in invalid_bodies:
        response = client.put(url, headers=headers, json=body)
        assert response.status_code == 400, (body, response.status_code)
        assert client.get(url).json == original
    assert client.put(url, headers=headers, json={'employee_ids': []}).status_code == 200
    assert client.get(url).json['participants'] == []


def test_scope_change_requires_clearing_joint_participants(planning):
    _, client, headers, employees, courses = planning
    course_id = courses['holding']
    url = participant_url(course_id)
    assert client.put(url, headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    course_url = f'/api/data/courses/{course_id}?company_id=1'
    response = client.put(course_url, headers=headers, json={'calendar_scope': 'internal'})
    assert response.status_code == 400
    assert client.get(url).json['participants'][0]['id'] == employees[2]
    assert client.put(url, headers=headers, json={'employee_ids': []}).status_code == 200
    assert client.put(course_url, headers=headers, json={'calendar_scope': 'internal'}).status_code == 200
    assert client.get(url).status_code == 400


def test_extreme_route_ids_and_malformed_json_are_controlled_and_atomic(planning):
    app, client, headers, employees, courses = planning
    url = participant_url(courses['holding'])
    assert client.put(url, headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    original = client.get(url).json
    for course_id in (2 ** 63, 2 ** 64, '9' * 1000):
        path = participant_url(course_id)
        for response in (client.get(path), client.put(path, headers=headers, json={'employee_ids': []})):
            assert response.status_code == 400
            assert set(response.json) == {'error'}
    for data in ('{', '{"employee_ids":' + '[' * 1500 + '0' + ']' * 1500 + '}',
                 '{"employee_ids":[],"ignored":"\\ud800"}'):
        response = client.put(url, headers=headers, data=data, content_type='application/json')
        assert response.status_code == 400
        assert set(response.json) == {'error'}
    assert client.get(url).json == original
    manager, manager_headers = login(app, 'manager')
    # Authorization still precedes route/body validation and cannot expose data.
    assert manager.get(participant_url(2 ** 64)).status_code == 403
    assert manager.put(url, headers=manager_headers, data='{', content_type='application/json').status_code == 403


def test_roster_survives_restart_and_database_backup_and_course_delete_cascades(planning, tmp_path):
    app, client, headers, employees, courses = planning
    url = participant_url(courses['holding'])
    employee_ids = [employees[1], employees[2], employees[3]]
    assert client.put(url, headers=headers, json={'employee_ids': employee_ids}).status_code == 200
    expected = client.get(url).json
    backup = client.post('/api/backup', headers=headers)
    assert backup.status_code == 200
    restored_dir = tmp_path / 'restored'
    restored_dir.mkdir()
    (restored_dir / 'nikan.sqlite3').write_bytes(backup.data)
    for data_dir in (Path(app.config['DB']).parent, restored_dir):
        restarted = create_app(data_dir, testing=True)
        restarted_client, _ = login(restarted, 'admin')
        assert restarted_client.get(url).json == expected
        connection = sqlite3.connect(data_dir / 'nikan.sqlite3')
        try:
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            assert connection.execute('SELECT count(*) FROM course_participants').fetchone()[0] == 3
        finally:
            connection.close()
    assert client.delete(f"/api/data/courses/{courses['holding']}?company_id=1", headers=headers).status_code == 200
    connection = sqlite3.connect(app.config['DB'])
    try:
        assert connection.execute('SELECT count(*) FROM course_participants').fetchone()[0] == 0
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute('SELECT count(*) FROM employees').fetchone()[0] == 3
    finally:
        connection.close()


def attendance_url(course):
    return f'/api/courses/{course}/attendance'


def allocation_url(course):
    return f'/api/courses/{course}/cost-allocation'


def set_cost(client, headers, course, amount):
    response = client.put(f'/api/data/courses/{course}?company_id=1', headers=headers, json={'cost': amount})
    assert response.status_code == 200, response.json


def test_allocation_uses_actual_attendance_and_preserves_recorded_roster(planning):
    _, client, headers, employees, courses = planning
    course = courses['holding']
    people = list(employees.values())
    set_cost(client, headers, course, 9)
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': people}).status_code == 200
    report = client.get(allocation_url(course)).json
    assert report['allocation_basis'] == 'actual_attendance'
    assert report['source_cost_unit'] == 'million_rials'
    assert report['currency'] == 'IRR'
    assert report['planned_count'] == 3 and report['attendee_count'] == 0
    assert report['total_cost_rials'] == report['unallocated_cost_rials'] == 9_000_000
    assert report['allocated_cost_rials'] == 0
    assert all(row['cost_rials'] == row['attendee_count'] == 0 for row in report['companies'])
    response = client.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[1], employees[2]]})
    assert response.status_code == 200
    assert {row['id'] for row in response.json['participants'] if row['attended']} == {employees[1], employees[2]}
    report = client.get(allocation_url(course)).json
    assert report['planned_count'] == 3 and report['attendee_count'] == 2
    assert report['allocated_cost_rials'] == report['total_cost_rials'] == 9_000_000
    assert report['unallocated_cost_rials'] == 0
    assert [(row['company_id'], row['attendee_count'], row['cost_rials']) for row in report['companies']] == [
        (1, 1, 4_500_000), (2, 1, 4_500_000), (3, 0, 0),
    ]
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': people[::-1]}).json == response.json
    unchanged = client.get(attendance_url(course)).json
    # Editing the plan cannot silently remove presence and change charged shares.
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': [employees[1]]}).status_code == 400
    assert client.get(attendance_url(course)).json == unchanged
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': []}).status_code == 200
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': []}).status_code == 200
    assert client.get(allocation_url(course)).json['unallocated_cost_rials'] == 9_000_000
    # A recorded presence is not a score, certificate, or qualification claim.
    for cid in employees:
        assert client.get(f'/api/data/records?company_id={cid}').json == []


def test_allocation_counts_each_actual_person_and_rounds_exactly_once(planning):
    _, client, headers, employees, courses = planning
    course = courses['holding']
    extra = client.post('/api/data/employees?company_id=1', headers=headers,
                        json={'code': 'E1-extra', 'name': 'Second employee'}).json['id']
    selected = list(employees.values()) + [extra]
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': selected}).status_code == 200
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': selected}).status_code == 200
    set_cost(client, headers, course, 10.000001)
    report = client.get(allocation_url(course)).json
    assert report['attendee_count'] == 4
    assert report['total_cost_rials'] == 10_000_001
    assert [(row['company_id'], row['attendee_count'], row['cost_rials']) for row in report['companies']] == [
        (1, 2, 5_000_001), (2, 1, 2_500_000), (3, 1, 2_500_000),
    ]
    assert sum(row['cost_rials'] for row in report['companies']) == report['allocated_cost_rials']
    # Half a rial is rounded up once before allocation; ties use company ID.
    set_cost(client, headers, course, 0.0000005)
    report = client.get(allocation_url(course)).json
    assert report['total_cost_rials'] == 1
    assert [row['cost_rials'] for row in report['companies']] == [1, 0, 0]
    # Equal fractional shares use a stable company-ID tie break, regardless of
    # the order in which attendance was submitted.
    assert client.put(attendance_url(course), headers=headers,
                      json={'employee_ids': list(employees.values())[::-1]}).status_code == 200
    tied = client.get(allocation_url(course)).json
    assert [row['attendee_count'] for row in tied['companies']] == [1, 1, 1]
    assert [row['cost_rials'] for row in tied['companies']] == [1, 0, 0]
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': selected}).status_code == 200
    # A cost correction is reflected immediately; no stored stale allocation.
    set_cost(client, headers, course, 0)
    report = client.get(allocation_url(course)).json
    assert report['attendee_count'] == 4
    assert report['total_cost_rials'] == report['allocated_cost_rials'] == report['unallocated_cost_rials'] == 0
    assert all(row['cost_rials'] == 0 for row in report['companies'])
    set_cost(client, headers, course, 4)
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[3]]}).status_code == 200
    assert [row['cost_rials'] for row in client.get(allocation_url(course)).json['companies']] == [0, 0, 4_000_000]


def test_attendance_validation_is_atomic_and_requires_planned_people(planning):
    _, client, headers, employees, courses = planning
    course = courses['holding']
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': [employees[1], employees[2]]}).status_code == 200
    url = attendance_url(course)
    assert client.put(url, headers=headers, json={'employee_ids': [employees[1]]}).status_code == 200
    original = client.get(url).json
    invalid = [None, [], {}, {'employee_ids': 'all'}, {'employee_ids': None},
               {'employee_ids': [employees[1], employees[1]]},
               {'employee_ids': [employees[1], employees[3]]},
               {'employee_ids': [employees[1], 999999]},
               {'employee_ids': [], 'attended': True},
               {'employee_ids': [True]}, {'employee_ids': ['1']},
               {'employee_ids': [1.0]}, {'employee_ids': [[]]}, {'employee_ids': [{}]},
               {'employee_ids': [None]}, {'employee_ids': [0]}, {'employee_ids': [-1]},
               {'employee_ids': [2 ** 64]}, {'employee_ids': list(range(1, 5002))}]
    for body in invalid:
        response = client.put(url, headers=headers, json=body)
        assert response.status_code == 400, (body, response.json)
        assert set(response.json) == {'error'}
        assert client.get(url).json == original
    for data in ('{', '{"employee_ids":' + '[' * 1500 + '0' + ']' * 1500 + '}',
                 '{"employee_ids":[],"ignored":"\\ud800"}'):
        assert client.put(url, headers=headers, data=data, content_type='application/json').status_code == 400
        assert client.get(url).json == original


@pytest.mark.parametrize('username', ['manager', 'viewer'])
def test_company_roles_cannot_access_actual_attendance_or_costs(planning, username):
    app, admin, headers, employees, courses = planning
    course = courses['holding']
    assert admin.put(participant_url(course), headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    assert admin.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    client, role_headers = login(app, username)
    for cid in (course, courses['internal'], 99999, 2 ** 64):
        for path in (attendance_url(cid), allocation_url(cid)):
            response = client.get(path)
            assert response.status_code == 403
            assert set(response.json) == {'error'}
            assert 'Ali' not in response.get_data(as_text=True)
        assert client.put(attendance_url(cid), headers=role_headers, json={'employee_ids': []}).status_code == 403
    assert admin.get(allocation_url(course)).json['attendee_count'] == 1


def test_attendance_and_allocation_authentication_csrf_scope_and_route_ids(planning):
    app, client, headers, employees, courses = planning
    anonymous = app.test_client()
    for route in (attendance_url, allocation_url):
        assert anonymous.get(route(courses['holding'])).status_code == 401
        assert client.get(route(courses['internal'])).status_code == 400
        assert client.get(route(99999)).status_code == 404
        for cid in (2 ** 63, 2 ** 64, '9' * 1000):
            response = client.get(route(cid))
            assert response.status_code == 400
            assert set(response.json) == {'error'}
    url = attendance_url(courses['holding'])
    assert anonymous.put(url, json={'employee_ids': []}).status_code == 401
    assert client.put(url, json={'employee_ids': []}).status_code == 403
    assert client.put(attendance_url(courses['internal']), headers=headers, json={'employee_ids': []}).status_code == 400
    assert client.put(attendance_url(99999), headers=headers, json={'employee_ids': []}).status_code == 404
    for cid in (2 ** 63, 2 ** 64, '9' * 1000):
        assert client.put(attendance_url(cid), headers=headers, json={'employee_ids': []}).status_code == 400


def test_cancelled_course_preserves_actual_cost_history_but_cannot_add_attendance(planning):
    _, client, headers, employees, courses = planning
    course = courses['holding']
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': list(employees.values())}).status_code == 200
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[1]]}).status_code == 200
    set_cost(client, headers, course, 1)
    assert client.put(f'/api/data/courses/{course}?company_id=1', headers=headers, json={'status': 'cancelled'}).status_code == 200
    report = client.get(allocation_url(course)).json
    assert report['course']['status'] == 'cancelled'
    assert report['attendee_count'] == 1 and report['allocated_cost_rials'] == 1_000_000
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[1]]}).status_code == 200
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[1], employees[2]]}).status_code == 400
    assert client.get(allocation_url(course)).json == report
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': []}).status_code == 200
    assert client.get(allocation_url(course)).json['unallocated_cost_rials'] == 1_000_000


@pytest.mark.parametrize('cost', [9_007_199_254.740992, 1e308])
def test_oversized_legacy_cost_is_a_controlled_error_without_mutating_source(planning, cost):
    _, client, headers, employees, courses = planning
    course = courses['holding']
    set_cost(client, headers, course, cost)
    response = client.get(allocation_url(course))
    assert response.status_code == 400
    assert set(response.json) == {'error'}
    assert client.get(attendance_url(course)).json['course']['cost'] == cost
    # Largest supported practical boundary remains an exact JSON integer.
    set_cost(client, headers, course, 9_007_199_254)
    response = client.get(allocation_url(course))
    assert response.status_code == 200
    assert response.json['total_cost_rials'] == 9_007_199_254_000_000


def test_actual_attendance_survives_backup_restart_and_old_plans_migrate_unconfirmed(planning, tmp_path):
    app, client, headers, employees, courses = planning
    course = courses['holding']
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': list(employees.values())}).status_code == 200
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    set_cost(client, headers, course, 0.000001)
    expected = client.get(allocation_url(course)).json
    restored = tmp_path / 'restored-attendance'
    restored.mkdir()
    (restored / 'nikan.sqlite3').write_bytes(client.post('/api/backup', headers=headers).data)
    for folder in (Path(app.config['DB']).parent, restored):
        restarted = create_app(folder, testing=True)
        restarted_client, _ = login(restarted, 'admin')
        assert restarted_client.get(allocation_url(course)).json == expected
        assert [row['id'] for row in restarted_client.get(attendance_url(course)).json['participants'] if row['attended']] == [employees[2]]
    # Reconstruct the 0.9 two-column planned roster. Migration cannot infer
    # attendance from planned selections or their existing training records.
    with sqlite3.connect(restored / 'nikan.sqlite3') as connection:
        connection.execute('ALTER TABLE course_participants DROP COLUMN attended')
    upgraded = create_app(restored, testing=True)
    upgraded_client, _ = login(upgraded, 'admin')
    report = upgraded_client.get(allocation_url(course)).json
    assert report['planned_count'] == 3 and report['attendee_count'] == 0
    assert report['unallocated_cost_rials'] == 1
    with sqlite3.connect(restored / 'nikan.sqlite3') as connection:
        assert connection.execute('SELECT count(*),sum(attended) FROM course_participants').fetchone() == (3, 0)
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


def test_course_deletion_requires_explicitly_clearing_actual_attendance(planning):
    app, client, headers, employees, courses = planning
    course = courses['holding']
    selected = list(employees.values())
    assert client.put(participant_url(course), headers=headers, json={'employee_ids': selected}).status_code == 200
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': [employees[2]]}).status_code == 200
    set_cost(client, headers, course, 5)
    roster_before = client.get(attendance_url(course)).json
    allocation_before = client.get(allocation_url(course)).json
    audit_before = client.get('/api/audit?company_id=1').json
    url = f'/api/data/courses/{course}?company_id=1'
    response = client.delete(url, headers=headers)
    assert response.status_code == 409
    assert set(response.json) == {'error'}
    assert client.get(attendance_url(course)).json == roster_before
    assert client.get(allocation_url(course)).json == allocation_before
    assert client.get('/api/audit?company_id=1').json == audit_before
    # Clearing presence is a distinct, audited action before destructive delete.
    assert client.put(attendance_url(course), headers=headers, json={'employee_ids': []}).status_code == 200
    assert client.delete(url, headers=headers).status_code == 200
    assert client.get(attendance_url(course)).status_code == 404
    assert client.get(allocation_url(course)).status_code == 404
    with sqlite3.connect(app.config['DB']) as connection:
        assert connection.execute('SELECT count(*) FROM course_participants WHERE course_id=?', (course,)).fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM employees').fetchone()[0] == 3
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    actions = client.get('/api/audit?company_id=1').json
    assert actions[0]['action'] == 'DELETE_courses'
    assert actions[1]['action'] == 'update_course_attendance'
