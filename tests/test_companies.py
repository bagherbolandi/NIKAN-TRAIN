import io
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import COMPANIES, create_app


PASSWORD = 'Secure-password-123'
PNG = b'\x89PNG\r\n\x1a\nsynthetic-company-logo'


def login(app, username='admin'):
    client = app.test_client()
    assert client.post('/api/login', json={'username': username, 'password': PASSWORD}).status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


@pytest.fixture
def ready(tmp_path):
    app = create_app(tmp_path, testing=True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    client, headers = login(app)
    return app, client, headers


def create_company(client, headers, name):
    response = client.post('/api/companies', headers=headers, json={'name': name})
    assert response.status_code == 201, response.json
    return response.json['id']


def test_admin_company_crud_without_company_selection_and_audit(ready):
    _, client, headers = ready
    response = client.get('/api/companies')
    assert response.status_code == 200
    assert response.json == [{'id': cid, 'name': name} for cid, name in enumerate(COMPANIES, 1)]
    cid = create_company(client, headers, '  New\n company   ')
    assert client.get('/api/companies').json[-1] == {'id': cid, 'name': 'New company'}
    response = client.put(f'/api/companies/{cid}', headers=headers, json={'name': ' Renamed company '})
    assert response.status_code == 200
    assert response.json == {'id': cid, 'name': 'Renamed company'}
    assert client.get('/api/me').json['companies'][-1] == response.json
    assert client.delete(f'/api/companies/{cid}', headers=headers).json == {'ok': True}
    assert cid not in {row['id'] for row in client.get('/api/companies').json}
    # Audit history survives removal and does not turn every company permanent.
    app = ready[0]
    with sqlite3.connect(app.config['DB']) as connection:
        actions = connection.execute('SELECT action FROM audit WHERE company_id=? ORDER BY id', (cid,)).fetchall()
        assert actions == [('create_company',), ('update_company',), ('delete_company',)]
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('role', ['manager', 'viewer'])
def test_company_management_requires_admin_and_csrf(ready, role):
    app, admin, headers = ready
    assert admin.post('/api/users', headers=headers, json={
        'username': role, 'password': PASSWORD, 'role': role, 'company_id': 1,
    }).status_code == 201
    client, scoped_headers = login(app, role)
    assert client.get('/api/me').json['companies'] == [{'id': 1, 'name': COMPANIES[0]}]
    assert client.get('/api/companies').status_code == 403
    assert client.post('/api/companies', headers=scoped_headers, json={'name': 'Forbidden'}).status_code == 403
    assert client.put('/api/companies/1', headers=scoped_headers, json={'name': 'Forbidden'}).status_code == 403
    assert client.delete('/api/companies/1', headers=scoped_headers).status_code == 403
    assert client.put('/api/companies/' + str(2 ** 64), headers=scoped_headers, json=[]).status_code == 403
    anonymous = app.test_client()
    assert anonymous.get('/api/companies').status_code == 401
    for method, path, body in (('post', '/api/companies', {'name': 'No CSRF'}),
                               ('put', '/api/companies/2', {'name': 'No CSRF'}),
                               ('delete', '/api/companies/2', None)):
        assert getattr(admin, method)(path, json=body).status_code == 403
        assert getattr(anonymous, method)(path, json=body).status_code == 401
    assert len(admin.get('/api/companies').json) == len(COMPANIES)


def test_names_malformed_inputs_duplicates_and_missing_ids_are_atomic(ready):
    _, client, headers = ready
    cid = create_company(client, headers, 'Example Company')
    second = create_company(client, headers, 'Another company')
    before = client.get('/api/companies').json
    for body in ([], None, True, 4, {}, {'name': None}, {'name': []}, {'name': {}},
                 {'name': True}, {'name': 1}, {'name': ''}, {'name': '\n\t  '},
                 {'name': 'x' * 201}, {'name': '\x00bad'}, {'name': 'invalid\ud800'},
                 {'name': 'Wrong fields', 'id': 1}):
        for path, method in (('/api/companies', 'post'), (f'/api/companies/{cid}', 'put')):
            response = getattr(client, method)(path, headers=headers, data=json.dumps(body), content_type='application/json')
            assert response.status_code == 400, body
            assert set(response.json) == {'error'}
    for body in ('{', '{"name":' + '[' * 1500 + '0' + ']' * 1500 + '}'):
        assert client.post('/api/companies', headers=headers, data=body, content_type='application/json').status_code == 400
    assert client.post('/api/companies', headers=headers, json={'name': ' example   COMPANY '}).status_code == 409
    assert client.put(f'/api/companies/{second}', headers=headers, json={'name': 'example company'}).status_code == 409
    for value in (0, 2 ** 63, 2 ** 64, '9' * 1000):
        assert client.put(f'/api/companies/{value}', headers=headers, json={'name': 'Invalid'}).status_code == 400
        assert client.delete(f'/api/companies/{value}', headers=headers).status_code == 400
    assert client.put('/api/companies/999999', headers=headers, json={'name': 'Missing'}).status_code == 404
    assert client.delete('/api/companies/999999', headers=headers).status_code == 404
    assert client.get('/api/companies').json == before
    # Renaming to one's own normalized spelling is permitted.
    assert client.put(f'/api/companies/{cid}', headers=headers, json={'name': ' Example   Company '}).status_code == 200


@pytest.mark.parametrize('table,payload', [
    ('employees', {'code': 'E1', 'name': 'Existing employee'}),
    ('skills', {'code': 'COMPANY-SKILL', 'title': 'Selected competency'}),
    ('job_profiles', {'job': 'Existing job'}),
    ('instructors', {'name': 'Existing instructor', 'rate': 0}),
])
def test_company_with_business_data_is_never_cascade_deleted(ready, table, payload):
    app, client, headers = ready
    cid = create_company(client, headers, 'Business company')
    created = client.post(f'/api/data/{table}?company_id={cid}', headers=headers, json=payload)
    assert created.status_code == 201, created.json
    before = client.get(f'/api/data/{table}?company_id={cid}').json
    response = client.delete(f'/api/companies/{cid}', headers=headers)
    assert response.status_code == 409
    assert response.json['error']
    assert client.get(f'/api/data/{table}?company_id={cid}').json == before
    with sqlite3.connect(app.config['DB']) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('active', [0, 1])
def test_company_with_any_assigned_user_cannot_be_deleted(ready, active):
    _, client, headers = ready
    cid = create_company(client, headers, 'Assigned company')
    assert client.post('/api/users', headers=headers, json={
        'username': 'company-user', 'password': PASSWORD, 'role': 'manager', 'company_id': cid,
    }).status_code == 201
    uid = next(row['id'] for row in client.get('/api/users').json if row['username'] == 'company-user')
    if not active:
        assert client.put(f'/api/users/{uid}', headers=headers, json={'active': 0}).status_code == 200
    assert client.delete(f'/api/companies/{cid}', headers=headers).status_code == 409
    assert next(row['active'] for row in client.get('/api/users').json if row['id'] == uid) == active


def test_dynamic_business_reference_checks_cover_future_company_tables(ready):
    app, client, headers = ready
    cid = create_company(client, headers, 'Future table company')
    with sqlite3.connect(app.config['DB']) as connection:
        connection.execute('CREATE TABLE future_business(id INTEGER PRIMARY KEY, company_id INTEGER, note TEXT)')
        connection.execute('INSERT INTO future_business(company_id,note) VALUES (?,?)', (cid, 'Must survive'))
    assert client.delete(f'/api/companies/{cid}', headers=headers).status_code == 409
    with sqlite3.connect(app.config['DB']) as connection:
        assert connection.execute('SELECT note FROM future_business').fetchone() == ('Must survive',)


def test_rename_preserves_links_document_numbers_logo_and_company_user_scope(ready, tmp_path):
    app, client, headers = ready
    employee = client.post('/api/data/employees?company_id=1', headers=headers, json={
        'code': 'E1', 'name': 'Existing person',
    }).json['id']
    assert client.post('/api/branding/company-1', headers=headers,
                       data={'file': (io.BytesIO(PNG), 'company.png')}).status_code == 200
    assert client.post('/api/users', headers=headers, json={
        'username': 'company-manager', 'password': PASSWORD, 'role': 'manager', 'company_id': 1,
    }).status_code == 201
    manager, _ = login(app, 'company-manager')
    before = client.get('/api/data/employees?company_id=1').json
    assert before[0]['id'] == employee
    assert client.put('/api/companies/1', headers=headers, json={'name': 'Renamed default company'}).status_code == 200
    assert manager.get('/api/me').json['companies'] == [{'id': 1, 'name': 'Renamed default company'}]
    assert manager.get('/api/data/employees?company_id=1').json == before
    assert manager.get('/api/data/employees?company_id=2').status_code == 403
    assert client.get('/api/branding/logo/company-1').data == PNG
    restarted, _ = login(create_app(tmp_path, testing=True))
    assert restarted.get('/api/data/employees?company_id=1').json == before
    names = [row['name'] for row in restarted.get('/api/companies').json]
    assert 'Renamed default company' in names
    assert COMPANIES[0] not in names
    assert len(names) == len(COMPANIES)


def test_empty_companies_stay_deleted_after_restart_and_backup_ids_never_reused(ready, tmp_path):
    app, client, headers = ready
    original = client.get('/api/companies').json
    highest = max(row['id'] for row in original)
    assert client.post(f'/api/branding/company-{highest}', headers=headers,
                       data={'file': (io.BytesIO(PNG), 'company.png')}).status_code == 200
    with sqlite3.connect(app.config['DB']) as connection:
        for key in (f'company-000{highest}', f'company-+{highest}', 'holding'):
            connection.execute('INSERT INTO branding(key,mime,content) VALUES (?,?,?)', (key, 'image/png', PNG))
    for row in original:
        assert client.delete(f"/api/companies/{row['id']}", headers=headers).status_code == 200
    assert client.get('/api/companies').json == []
    assert client.get('/api/me').json['companies'] == []
    assert client.get(f'/api/branding/logo/company-{highest}').status_code == 404
    assert client.get(f'/api/branding/logo/company-000{highest}').status_code == 404
    assert client.get(f'/api/branding/logo/company-+{highest}').status_code == 404
    assert client.get('/api/branding/logo/holding').data == PNG
    backup = client.post('/api/backup', headers=headers)
    restored_dir = tmp_path / 'restored'
    restored_dir.mkdir()
    (restored_dir / 'nikan.sqlite3').write_bytes(backup.data)
    for directory in (tmp_path, restored_dir):
        restarted = create_app(directory, testing=True)
        admin, token = login(restarted)
        assert admin.get('/api/companies').json == []
        assert admin.get('/api/me').json['companies'] == []
        new_id = create_company(admin, token, 'After deletion')
        assert new_id > highest
        assert admin.delete(f'/api/companies/{new_id}', headers=token).status_code == 200
        newer_id = create_company(admin, token, 'Next company')
        assert newer_id > new_id


def test_existing_legacy_company_table_is_not_reseeded_or_rewritten(tmp_path):
    database = tmp_path / 'nikan.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL)')
        connection.execute("INSERT INTO companies VALUES (42,'Existing custom company')")
    app = create_app(tmp_path, testing=True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    client, headers = login(app)
    assert client.get('/api/companies').json == [{'id': 42, 'name': 'Existing custom company'}]
    assert create_company(client, headers, 'Next company') == 43
    empty_dir = tmp_path / 'empty-legacy'
    empty_dir.mkdir()
    with sqlite3.connect(empty_dir / 'nikan.sqlite3') as connection:
        connection.execute('CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL)')
    empty = create_app(empty_dir, testing=True)
    with sqlite3.connect(empty.config['DB']) as connection:
        assert connection.execute('SELECT count(*) FROM companies').fetchone()[0] == 0
        assert connection.execute('SELECT id,high_watermark FROM company_registry').fetchall() == [(1, 0)]


def test_concurrent_normalized_creates_allow_only_one_company(ready):
    app, admin, _ = ready
    clients = [login(app), login(app)]
    barrier = threading.Barrier(2)

    def create(index):
        client, headers = clients[index]
        barrier.wait(timeout=5)
        return client.post('/api/companies', headers=headers, json={
            'name': 'Concurrent company' if index == 0 else ' concurrent   COMPANY ',
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create, (0, 1))) == [201, 409]
    assert len([row for row in admin.get('/api/companies').json if row['name'].casefold() == 'concurrent company']) == 1


def test_concurrent_company_delete_and_employee_insert_never_orphan_data(ready):
    app, admin, headers = ready
    cid = create_company(admin, headers, 'Concurrent delete company')
    clients = [login(app), login(app)]
    barrier = threading.Barrier(2)

    def operate(index):
        client, token = clients[index]
        barrier.wait(timeout=5)
        if index == 0:
            return client.delete(f'/api/companies/{cid}', headers=token).status_code
        return client.post(f'/api/data/employees?company_id={cid}', headers=token,
                           json={'code': 'E1', 'name': 'Concurrent employee'}).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_status, insert_status = list(pool.map(operate, (0, 1)))
    assert (delete_status, insert_status) in ((200, 400), (409, 201))
    with sqlite3.connect(app.config['DB']) as connection:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        company_exists = connection.execute('SELECT count(*) FROM companies WHERE id=?', (cid,)).fetchone()[0]
        employees = connection.execute('SELECT count(*) FROM employees WHERE company_id=?', (cid,)).fetchone()[0]
        assert (company_exists, employees) == ((0, 0) if delete_status == 200 else (1, 1))


def test_company_delete_detects_target_ownership_and_future_foreign_keys(ready):
    app, client, headers = ready
    cid = create_company(client, headers, 'Destination company with history')
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute('CREATE TABLE future_company_destinations(id INTEGER PRIMARY KEY,target_company_id INTEGER)')
        conn.execute('INSERT INTO future_company_destinations(target_company_id) VALUES (?)', (cid,))
    assert client.delete(f'/api/companies/{cid}', headers=headers).status_code == 409
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute('DELETE FROM future_company_destinations')
        conn.execute('CREATE TABLE future_declared_owners(id INTEGER PRIMARY KEY,owner INTEGER REFERENCES companies(id))')
        conn.execute('INSERT INTO future_declared_owners(owner) VALUES (?)', (cid,))
    assert client.delete(f'/api/companies/{cid}', headers=headers).status_code == 409
    assert any(row['id'] == cid for row in client.get('/api/companies').json)
