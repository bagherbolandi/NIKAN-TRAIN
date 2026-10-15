import sqlite3
from pathlib import Path

import pytest

from app import create_app


PASSWORD = 'Secure-password-123'
PERSONNEL_FIELDS = {'id', 'code', 'name', 'job', 'department', 'company_id', 'company_name'}
COURSE_FIELDS = {'id', 'title', 'date', 'company_id', 'calendar_scope'}


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
    assert response.json['participants'] == directory.json
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
