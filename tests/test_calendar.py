import sqlite3

import pytest

from app import create_app


PASSWORD = 'Secure-password-123'
PUBLIC_FIELDS = {
    'id', 'company_id', 'company_name', 'skill_id', 'skill_title', 'title',
    'date', 'instructor', 'hours', 'status', 'calendar_scope',
}


def login(app, username, base_url='http://localhost'):
    client = app.test_client()
    response = client.post('/api/login', base_url=base_url, json={'username': username, 'password': PASSWORD})
    assert response.status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me', base_url=base_url).json['csrf']}


@pytest.fixture
def calendars(tmp_path):
    app = create_app(tmp_path, testing=True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    client, headers = login(app, 'admin')
    courses = {}
    for cid in (1, 2):
        skill = client.post(
            f'/api/data/skills?company_id={cid}', headers=headers,
            json={'code': f'CAL-{cid}', 'title': f'Skill {cid}'},
        )
        assert skill.status_code == 201, skill.json
        for scope in ('holding', 'internal'):
            response = client.post(
                f'/api/data/courses?company_id={cid}', headers=headers,
                json={
                    'skill_id': skill.json['id'], 'title': f'{scope} {cid}',
                    'date': f'1405/01/0{cid}', 'hours': 2,
                    'instructor': f'Instructor {cid}', 'cost': 10000 * cid,
                    'calendar_scope': scope,
                },
            )
            assert response.status_code == 201, response.json
            courses[cid, scope] = response.json['id']
    employee = client.post(
        '/api/data/employees?company_id=2', headers=headers,
        json={'code': 'PRIVATE', 'name': 'Private employee'},
    ).json['id']
    attendance = client.post(
        '/api/data/records?company_id=2', headers=headers,
        json={
            'employee_id': employee, 'course_id': courses[2, 'holding'],
            'level': 3, 'score': 95,
        },
    )
    assert attendance.status_code == 201, attendance.json
    for role in ('manager', 'viewer'):
        response = client.post('/api/users', headers=headers, json={
            'username': role, 'password': PASSWORD, 'role': role, 'company_id': 1,
        })
        assert response.status_code == 201
    return app, client, headers, courses


def test_holding_calendar_consolidates_schedule_for_admin(calendars):
    app, _, _, courses = calendars
    client, _ = login(app, 'admin')
    response = client.get('/api/calendar')
    assert response.status_code == 200
    assert [row['id'] for row in response.json] == [courses[1, 'holding'], courses[2, 'holding']]
    assert {row['company_id'] for row in response.json} == {1, 2}
    for row in response.json:
        assert set(row) == PUBLIC_FIELDS
        assert row['company_name']
        assert row['skill_title'] == f"Skill {row['company_id']}"
        assert row['calendar_scope'] == 'holding'
    assert b'Private employee' not in response.data
    # A previously selected company must not silently narrow the main calendar.
    assert client.get('/api/calendar?scope=holding&company_id=1').json == response.json


@pytest.mark.parametrize('username', ['manager', 'viewer'])
def test_holding_calendar_is_unavailable_to_company_users(calendars, username):
    app, _, _, _ = calendars
    client, _ = login(app, username)
    for path in ('/api/calendar', '/api/calendar?scope=holding', '/api/calendar?scope=holding&company_id=1'):
        response = client.get(path)
        assert response.status_code == 403
        assert b'holding 2' not in response.data


@pytest.mark.parametrize('username', ['manager', 'viewer'])
def test_internal_calendar_isolated_for_company_users(calendars, username):
    app, _, _, courses = calendars
    client, _ = login(app, username)
    response = client.get('/api/calendar?scope=internal&company_id=1')
    assert response.status_code == 200
    assert [row['id'] for row in response.json] == [courses[1, 'internal']]
    assert client.get('/api/calendar?scope=internal&company_id=2').status_code == 403
    assert client.get('/api/calendar?scope=internal').status_code == 400
    assert client.get('/api/calendar?scope=internal&company_id=999').status_code == 400


def test_admin_selects_internal_company_and_requires_valid_scope(calendars):
    _, client, _, courses = calendars
    for cid in (1, 2):
        response = client.get(f'/api/calendar?scope=internal&company_id={cid}')
        assert response.status_code == 200
        assert [row['id'] for row in response.json] == [courses[cid, 'internal']]
    for scope in ('', 'all', 'HOLDING', 'private'):
        assert client.get('/api/calendar', query_string={'scope': scope}).status_code == 400
    assert client.get('/api/calendar?scope=internal').status_code == 400


def test_calendar_authentication_and_no_write_endpoint(calendars):
    app, client, headers, _ = calendars
    anonymous = app.test_client()
    assert anonymous.get('/api/calendar').status_code == 401
    assert anonymous.get('/api/calendar?scope=internal&company_id=1').status_code == 401
    assert client.post('/api/calendar', headers=headers, json={}).status_code == 405


def test_network_https_calendar_uses_company_authorization(tmp_path):
    from network.config import initialize

    root = tmp_path / 'server'
    config = initialize(root, 'nikan.test')
    app = create_app(root, testing=True)
    app.extensions['register_network'](root, config)
    url = 'https://localhost'
    client = app.test_client()
    assert client.post(
        '/api/setup', base_url=url, json={'username': 'admin', 'password': PASSWORD},
    ).status_code == 200
    admin, headers = login(app, 'admin', url)
    for cid in (1, 2):
        skill = admin.post(
            f'/api/data/skills?company_id={cid}', base_url=url, headers=headers,
            json={'code': f'NET-{cid}', 'title': f'Network skill {cid}'},
        ).json['id']
        for scope in ('holding', 'internal'):
            response = admin.post(
                f'/api/data/courses?company_id={cid}', base_url=url, headers=headers,
                json={
                    'skill_id': skill, 'title': f'{scope} {cid}',
                    'date': '1405/03/01', 'hours': 1, 'calendar_scope': scope,
                },
            )
            assert response.status_code == 201
    user = admin.post('/api/users', base_url=url, headers=headers, json={
        'username': 'network-manager', 'password': PASSWORD,
        'role': 'manager', 'company_id': 1,
    })
    assert user.status_code == 201
    manager, manager_headers = login(app, 'network-manager', url)
    assert manager.get('/api/calendar').status_code == 403
    assert manager.get('/api/calendar', base_url=url).status_code == 403
    assert manager.get('/api/calendar?scope=holding', base_url=url).status_code == 403
    response = admin.get('/api/calendar', base_url=url)
    assert response.status_code == 200
    assert {row['company_id'] for row in response.json} == {1, 2}
    assert all(row['calendar_scope'] == 'holding' for row in response.json)
    assert len(manager.get('/api/calendar?scope=internal&company_id=1', base_url=url).json) == 1
    assert manager.get('/api/calendar?scope=internal&company_id=2', base_url=url).status_code == 403
    assert manager.get('/api/data/records?company_id=2', base_url=url).status_code == 403
    assert manager.post('/api/competency-bank', base_url=url, headers=manager_headers, json={
        'code': 'FORBIDDEN', 'title': 'Cannot edit shared bank',
    }).status_code == 403


def test_company_courses_and_records_remain_company_scoped(calendars):
    app, _, _, courses = calendars
    client, headers = login(app, 'manager')
    assert client.get('/api/calendar').status_code == 403
    for table in ('courses', 'employees', 'records'):
        assert client.get(f'/api/data/{table}?company_id=2').status_code == 403
    for method, payload in (('put', {'title': 'Changed'}), ('delete', None)):
        response = getattr(client, method)(
            f"/api/data/courses/{courses[2, 'holding']}?company_id=2",
            headers=headers, json=payload,
        )
        assert response.status_code == 403
    assert client.put(
        f"/api/data/courses/{courses[2, 'holding']}?company_id=1",
        headers=headers, json={'title': 'Changed'},
    ).status_code == 404
    assert client.get('/api/data/records?company_id=1').json == []


def test_company_manager_creates_and_manages_only_internal_courses(calendars):
    app, admin, admin_headers, courses = calendars
    skill_id = admin.get('/api/data/skills?company_id=1').json[0]['id']
    manager, headers = login(app, 'manager')
    course = {'skill_id': skill_id, 'title': 'Company internal training', 'date': '1405/02/01', 'hours': 1}
    response = manager.post('/api/data/courses?company_id=1', headers=headers, json=course)
    assert response.status_code == 201
    new_id = response.json['id']
    own_courses = manager.get('/api/data/courses?company_id=1').json
    assert next(row for row in own_courses if row['id'] == new_id)['calendar_scope'] == 'internal'
    # Historical holding references remain readable for company attendance.
    assert any(row['id'] == courses[1, 'holding'] for row in own_courses)
    assert new_id in {row['id'] for row in manager.get('/api/calendar?scope=internal&company_id=1').json}
    assert manager.post(
        '/api/data/courses?company_id=1', headers=headers, json={**course, 'calendar_scope': 'holding'},
    ).status_code == 403
    assert manager.put(
        f'/api/data/courses/{new_id}?company_id=1', headers=headers,
        json={'calendar_scope': 'holding', 'title': 'Disallowed promotion'},
    ).status_code == 403
    assert manager.put(
        f'/api/data/courses/{new_id}?company_id=1', headers=headers, json={'title': 'Updated internal training'},
    ).status_code == 200
    holding_id = courses[1, 'holding']
    for patch in ({'title': 'Disallowed edit'}, {'status': 'completed'}, {'calendar_scope': 'internal'}):
        assert manager.put(
            f'/api/data/courses/{holding_id}?company_id=1', headers=headers, json=patch,
        ).status_code == 403
    assert manager.delete(f'/api/data/courses/{holding_id}?company_id=1', headers=headers).status_code == 403
    # Only the admin can promote a company course, after which the company
    # manager loses write access without changing the course's owner or ID.
    assert admin.put(
        f'/api/data/courses/{new_id}?company_id=1', headers=admin_headers, json={'calendar_scope': 'holding'},
    ).status_code == 200
    assert manager.delete(f'/api/data/courses/{new_id}?company_id=1', headers=headers).status_code == 403
    assert admin.put(
        f'/api/data/courses/{new_id}?company_id=1', headers=admin_headers, json={'calendar_scope': 'internal'},
    ).status_code == 200
    assert manager.delete(f'/api/data/courses/{new_id}?company_id=1', headers=headers).status_code == 200
    own_courses = admin.get('/api/data/courses?company_id=1').json
    assert next(row for row in own_courses if row['id'] == holding_id)['title'] == 'holding 1'


def test_course_scope_defaults_validation_and_viewer_read_only(calendars):
    app, admin, headers, courses = calendars
    skill_id = admin.get('/api/data/skills?company_id=1').json[0]['id']
    course = {'skill_id': skill_id, 'title': 'Default scope', 'date': '1405/02/01', 'hours': 1}
    response = admin.post('/api/data/courses?company_id=1', headers=headers, json=course)
    assert response.status_code == 201
    new_id = response.json['id']
    assert any(row['id'] == new_id and row['calendar_scope'] == 'holding' for row in admin.get('/api/calendar').json)
    for invalid in ('all', '', None, ['holding']):
        assert admin.post(
            '/api/data/courses?company_id=1', headers=headers,
            json={**course, 'calendar_scope': invalid},
        ).status_code == 400
        assert admin.put(
            f'/api/data/courses/{new_id}?company_id=1', headers=headers,
            json={'calendar_scope': invalid},
        ).status_code == 400
    assert admin.put(
        f'/api/data/courses/{new_id}?company_id=1', headers=headers,
        json={'calendar_scope': 'internal'},
    ).status_code == 200
    assert new_id not in {row['id'] for row in admin.get('/api/calendar').json}
    assert new_id in {row['id'] for row in admin.get('/api/calendar?scope=internal&company_id=1').json}
    viewer, viewer_headers = login(app, 'viewer')
    assert viewer.post('/api/data/courses?company_id=1', headers=viewer_headers, json=course).status_code == 403
    assert viewer.put(
        f"/api/data/courses/{courses[1, 'holding']}?company_id=1",
        headers=viewer_headers, json={'calendar_scope': 'internal'},
    ).status_code == 403
    assert viewer.delete(
        f"/api/data/courses/{courses[1, 'internal']}?company_id=1", headers=viewer_headers,
    ).status_code == 403


def test_legacy_migration_preserves_course_and_records_and_restarts(tmp_path):
    # Use the pre-calendar schema, including a live attendance reference, to
    # verify upgrades rather than only creating a fresh database with the field.
    connection = sqlite3.connect(tmp_path / 'nikan.sqlite3')
    try:
        connection.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL);
            CREATE TABLE employees(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),code TEXT NOT NULL,name TEXT NOT NULL,department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',UNIQUE(company_id,code));
            CREATE TABLE skills(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),code TEXT NOT NULL,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',importance INTEGER NOT NULL DEFAULT 1 CHECK(importance BETWEEN 1 AND 3),UNIQUE(company_id,code));
            CREATE TABLE courses(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),skill_id INTEGER NOT NULL REFERENCES skills(id),title TEXT NOT NULL,date TEXT NOT NULL,instructor TEXT NOT NULL DEFAULT '',hours REAL NOT NULL CHECK(hours>0),cost REAL NOT NULL DEFAULT 0 CHECK(cost>=0),status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','approved','completed','cancelled')));
            CREATE TABLE records(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),employee_id INTEGER NOT NULL REFERENCES employees(id),course_id INTEGER NOT NULL REFERENCES courses(id),level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 4),score REAL CHECK(score BETWEEN 0 AND 100),reaction REAL CHECK(reaction BETWEEN 1 AND 5),behavior REAL CHECK(behavior BETWEEN 1 AND 5),result REAL CHECK(result BETWEEN 0 AND 100),UNIQUE(company_id,employee_id,course_id));
            INSERT INTO companies VALUES (1,'Legacy company');
            INSERT INTO skills(id,company_id,code,title) VALUES (41,1,'LEGACY-CALENDAR','Legacy skill');
            INSERT INTO employees(id,company_id,code,name) VALUES (42,1,'E-42','Legacy employee');
            INSERT INTO courses(id,company_id,skill_id,title,date,hours,status) VALUES (43,1,41,'Legacy course','1404/12/20',4,'completed');
            INSERT INTO records(id,company_id,employee_id,course_id,level,score) VALUES (44,1,42,43,3,87);
        ''')
        connection.commit()
    finally:
        connection.close()
    for run in range(2):
        app = create_app(tmp_path, testing=True)
        client = app.test_client()
        if not run:
            assert client.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
        client, headers = login(app, 'admin')
        calendar = client.get('/api/calendar').json
        assert len(calendar) == 1
        assert calendar[0]['id'] == 43
        assert calendar[0]['calendar_scope'] == 'holding'
        records = client.get('/api/data/records?company_id=1').json
        assert len(records) == 1
        assert (records[0]['id'], records[0]['employee_id'], records[0]['course_id'], records[0]['score']) == (44, 42, 43, 87)
        assert client.get('/api/calendar?scope=internal&company_id=1').json == []
        connection = sqlite3.connect(tmp_path / 'nikan.sqlite3')
        try:
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute("UPDATE courses SET calendar_scope='invalid' WHERE id=43")
            connection.rollback()
        finally:
            connection.close()
