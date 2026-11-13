"""Real-account authorization, scope isolation and backup exfiltration checks."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

import pytest
from flask import g

from app import create_app
from app.hrm_access import actor_summary, employee, legacy_guard, require, scope_sql, record_scope, record_scope_sql

PASSWORD = 'Test-HRM-password-123'


def login(app, name):
    client = app.test_client()
    response = client.post('/api/login', json={'username': name, 'password': PASSWORD})
    assert response.status_code == 200, response.json
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


@pytest.fixture
def access(tmp_path):
    app = create_app(tmp_path, testing=True)
    first = app.test_client()
    assert first.post('/api/setup', json={'username': 'access-admin', 'password': PASSWORD}).status_code == 200
    admin, headers = login(app, 'access-admin')
    people = {}
    for cid, code, dept in ((1, 'HRM-001', 'واحد اول'), (1, 'HRM-002', 'واحد دوم'),
                            (2, 'HRM-003', 'واحد اول'), (3, 'HRM-004', 'واحد اول')):
        result = admin.post(f'/api/data/employees?company_id={cid}', headers=headers,
                            json={'code': code, 'name': code, 'department': dept, 'job': 'کارشناس'})
        assert result.status_code == 201, result.json
        people[code] = result.json['id']
    return app, admin, headers, people


def create_user(admin, headers, name='hr-user', role='holding_hr', scopes=None, permissions=None, **extra):
    body = {'username': name, 'password': PASSWORD, 'role': role,
            'scopes': scopes if scopes is not None else [{'company_id': 1, 'department': ''}],
            'permissions': permissions or {}, 'training_access': False, **extra}
    response = admin.post('/api/hrm/access/users', headers=headers, json=body)
    assert response.status_code == 201, response.json
    return response.json


@contextmanager
def as_actor(app, user_id, path='/api/hrm/context'):
    with app.test_request_context(path):
        connection = sqlite3.connect(app.config['DB'])
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        g.user = connection.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        try:
            yield connection
        finally:
            connection.close()


def counts(app):
    with sqlite3.connect(app.config['DB']) as conn:
        return tuple(conn.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                     for table in ('users', 'hrm_user_access', 'hrm_user_scopes', 'hrm_user_permissions', 'audit'))


def test_primary_holding_admin_has_all_operations_and_all_companies(access):
    app, admin, headers, people = access
    context = admin.get('/api/hrm/context')
    assert context.status_code == 200, context.json
    data = context.json
    assert data['role'] == 'system_admin' and not data['configured']
    assert data['can_manage_access'] and data['training_access']
    assert data['is_primary_admin'] and data['all_companies']
    assert 'health' in data['modules'] and 'health_environment' in data['modules']
    assert all(data['permissions']['settings'].values())
    for module in ('personnel', 'contracts', 'documents', 'loans', 'discipline', 'health', 'backup.full_export'):
        assert all(data['permissions'][module].values())
    with as_actor(app, 1) as conn:
        for module in ('contracts', 'loans', 'health'):
            assert employee(conn, people['HRM-001'], module)['id'] == people['HRM-001']
        assert 'hrm-1' in {row[0] for row in conn.execute('SELECT version FROM hrm_schema_migrations')}
        assert conn.execute('SELECT count(*) FROM hrm_user_access').fetchone()[0] == 0
    # The upgrade preserves existing training administration.
    assert admin.get('/api/data/employees?company_id=1').status_code == 200
    assert admin.get('/api/hrm/access/options').status_code == 200


@pytest.mark.parametrize('change', [
    {'role': 'employee'}, {'role': 'unknown'},
    {'permissions': {'requests': {'read': True}}},
    {'permissions': {'contracts': {'read': False, 'create': 'yes'}}},
    {'permissions': {'health': {'read': 1}}},
    {'role': 'unit_manager', 'scopes': [{'company_id': 1, 'department': ''}]},
    {'role': 'company_hr', 'scopes': [{'company_id': 1}, {'company_id': 2}]},
    {'scopes': [{'company_id': 999999}]},
    {'scopes': [{'company_id': 1}, {'company_id': 1}]},
    {'role': 'company_hr', 'all_companies': True},
])
def test_invalid_access_configuration_is_atomic_and_employee_login_not_supported(access, change):
    app, admin, headers, _ = access
    body = {'username': 'invalid-user', 'password': PASSWORD, 'role': 'holding_hr',
            'scopes': [{'company_id': 1}], 'permissions': {}, 'training_access': False, **change}
    before = counts(app)
    response = admin.post('/api/hrm/access/users', headers=headers, json=body)
    assert response.status_code == 400, response.json
    assert counts(app) == before


def test_holding_hr_company_scope_and_legacy_endpoint_bypass_are_enforced(access):
    app, admin, headers, people = access
    user = create_user(admin, headers, scopes=[{'company_id': 1}, {'company_id': 2}],
                       permissions={'personnel': {'read': True, 'write': True}, 'contracts': {'read': True}})
    staff, staff_headers = login(app, 'hr-user')
    assert staff.get('/api/hrm/context').json['modules'] == ['personnel', 'contracts']
    assert staff.get('/api/data/employees?company_id=1').status_code == 403
    assert staff.get('/api/excel/templates/employees?company_id=1').status_code == 403
    assert staff.get('/api/audit?company_id=1').status_code == 403
    assert staff.get('/api/hrm/access/options').status_code == 403
    assert staff.post('/api/hrm/access/users', headers=staff_headers, json={}).status_code == 403
    with as_actor(app, user['id']) as conn:
        for code in ('HRM-001', 'HRM-002', 'HRM-003'):
            assert employee(conn, people[code], 'personnel')['id'] == people[code]
        with pytest.raises(PermissionError):
            employee(conn, people['HRM-004'], 'personnel')
        with pytest.raises(PermissionError):
            employee(conn, people['HRM-001'], 'contracts', write=True)
        with pytest.raises(PermissionError):
            employee(conn, people['HRM-001'], 'health')


def test_unit_scope_uses_effective_company_without_rewriting_training_ownership(access):
    app, admin, headers, people = access
    unit = create_user(admin, headers, name='unit-lead', role='unit_manager',
                       scopes=[{'company_id': 1, 'department': 'واحد اول'}],
                       permissions={'personnel': {'read': True}})
    receiving = create_user(admin, headers, name='receiving-hr', role='company_hr',
                            scopes=[{'company_id': 2}], permissions={'personnel': {'read': True}})
    with as_actor(app, unit['id']) as conn:
        predicate, args = scope_sql(conn, 'personnel')
        assert [row['id'] for row in conn.execute('SELECT e.id FROM employees e WHERE ' + predicate, args)] == [people['HRM-001']]
        with pytest.raises(PermissionError):
            employee(conn, people['HRM-002'], 'personnel')
        with pytest.raises(ValueError):
            scope_sql(conn, 'personnel', 'e; DROP TABLE users')
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute('''INSERT INTO hrm_employee_details(employee_id,company_id,department,job)
                        VALUES (?,?,?,?)''', (people['HRM-001'], 2, 'واحد اول', 'سمت فعلی'))
    with as_actor(app, unit['id']) as conn:
        with pytest.raises(PermissionError):
            employee(conn, people['HRM-001'], 'personnel')
    with as_actor(app, receiving['id']) as conn:
        actual = employee(conn, people['HRM-001'], 'personnel')
        assert actual['company_id'] == 2 and actual['training_company_id'] == 1
        assert actual['job'] == 'سمت فعلی'
        assert conn.execute('SELECT company_id,job FROM employees WHERE id=?', (people['HRM-001'],)).fetchone()[:] == (1, 'کارشناس')


def test_explicit_confidential_grant_and_revocation_invalidates_existing_session(access):
    app, admin, headers, people = access
    user = create_user(admin, headers, permissions={'contracts': {'read': True}})
    staff, _ = login(app, 'hr-user')
    with as_actor(app, user['id']) as conn:
        assert employee(conn, people['HRM-001'], 'contracts')['id'] == people['HRM-001']
    changed = admin.put(f'/api/hrm/access/users/{user["id"]}', headers=headers,
                        json={'permissions': {}, 'version': user['version']})
    assert changed.status_code == 200, changed.json
    assert changed.json['version'] == user['version'] + 1
    assert staff.get('/api/hrm/context').status_code == 401
    staff, _ = login(app, 'hr-user')
    with as_actor(app, user['id']) as conn:
        with pytest.raises(PermissionError):
            require(conn, 'contracts')
    before = counts(app)
    stale = admin.put(f'/api/hrm/access/users/{user["id"]}', headers=headers,
                      json={'permissions': {'contracts': {'read': True}}, 'version': user['version']})
    assert stale.status_code == 409
    assert counts(app) == before


def test_account_creation_requires_csrf_and_password_is_hashed(access):
    app, admin, headers, _ = access
    assert admin.post('/api/hrm/access/users', json={}).status_code == 403
    user = create_user(admin, headers, name='new-hse', role='hse_officer', permissions={'hse': {'read': True}})
    with sqlite3.connect(app.config['DB']) as conn:
        password_hash = conn.execute('SELECT password FROM users WHERE id=?', (user['id'],)).fetchone()[0]
        assert password_hash != PASSWORD and password_hash.startswith('scrypt:')
    hse, _ = login(app, 'new-hse')
    assert hse.get('/api/hrm/context').json['permissions']['health']['read'] is False


def test_explicit_training_compatibility_does_not_turn_read_into_write(access):
    app, admin, headers, _ = access
    user = create_user(admin, headers, name='training-reader', role='training_officer', legacy_role='manager',
                       permissions={'training': {'read': True}}, training_access=True)
    staff, sh = login(app, 'training-reader')
    assert staff.get('/api/data/employees?company_id=1').status_code == 200
    assert staff.get('/api/data/employees?company_id=2').status_code == 403
    assert staff.post('/api/data/employees?company_id=1', headers=sh,
                      json={'code': 'UNAUTHORIZED', 'name': 'Forbidden training write'}).status_code == 403
    before = counts(app)
    invalid = admin.put(f'/api/hrm/access/users/{user["id"]}', headers=headers, json={
        'scopes': [{'company_id': 1, 'department': 'واحد اول'}], 'training_access': True})
    assert invalid.status_code == 400
    assert counts(app) == before


def test_primary_admin_backup_retained_and_delegates_cannot_export_entire_database(access):
    app, admin, headers, people = access
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute('INSERT INTO hrm_employee_details(employee_id,company_id,department,job) VALUES (?,?,?,?)',
                     (people['HRM-001'], 1, 'واحد اول', 'کارشناس'))
    exported = admin.post('/api/backup', headers=headers)
    assert exported.status_code == 200 and exported.data.startswith(b'SQLite format 3')
    create_user(admin, headers, name='holding-training', role='holding_manager', scopes=[], all_companies=True,
                training_access=True, permissions={'training': {'read': True, 'create': True, 'edit': True},
                                                  'backup.full_export': {'read': True}})
    delegated, dh = login(app, 'holding-training')
    assert delegated.get('/api/me').json['role'] == 'admin'  # Training adapter only.
    assert not delegated.get('/api/hrm/context').json['is_primary_admin']
    assert delegated.post('/api/backup', headers=dh).status_code == 403
    with as_actor(app, delegated.get('/api/hrm/context').json['user_id'], '/api/network/recovery-key') as conn:
        with pytest.raises(PermissionError):
            legacy_guard(conn)
    assert delegated.get('/api/hrm/access/users').status_code == 403
    assert delegated.get('/api/companies').status_code == 403
    assert delegated.get('/api/hrm/health_records').status_code == 403


def test_all_company_training_delegation_stays_dynamic_and_does_not_grant_hrm_admin(access):
    app, admin, headers, _ = access
    create_user(admin, headers, name='global-training', role='holding_manager', all_companies=True, scopes=[],
                training_access=True, permissions={'training': {'read': True}})
    delegated, dh = login(app, 'global-training')
    assert delegated.get('/api/calendar').status_code == 200
    assert delegated.get('/api/holding/personnel').status_code == 200
    assert delegated.get('/api/holding/needs?year=1405').status_code == 200
    assert delegated.get('/api/hrm/context').json['permissions']['health']['read'] is False
    assert delegated.post('/api/competency-bank', headers=dh,
                          json={'code': 'DELEGATE-BANK', 'title': 'Forbidden'}).status_code == 403
    assert delegated.post('/api/data/employees?company_id=1', headers=dh,
                          json={'code': 'READONLY', 'name': 'Forbidden write'}).status_code == 403
    assert delegated.get('/api/export.xlsx?company_id=1').status_code == 403
    assert delegated.get('/api/hrm/access/options').status_code == 403
    created = admin.post('/api/companies', headers=headers, json={'name': 'شرکت تازه هلدینگ'})
    assert created.status_code == 201
    summary = delegated.get('/api/hrm/context').json
    assert created.json['id'] in summary['company_ids'] and summary['training_access']


def test_five_operation_grants_are_independent_including_create_and_export_only(access):
    app, admin, headers, people = access
    creator = create_user(admin, headers, name='create-only', permissions={'loans': {'create': True}})
    export_user = create_user(admin, headers, name='export-only', permissions={'loans': {'export': True}})
    read_user = create_user(admin, headers, name='read-only', permissions={'loans': {'read': True}})
    with as_actor(app, creator['id']) as conn:
        assert employee(conn, people['HRM-001'], 'loans', operation='create')['id'] == people['HRM-001']
        for operation in ('read', 'edit', 'delete', 'export'):
            with pytest.raises(PermissionError):
                require(conn, 'loans', operation=operation)
    creator_client, _ = login(app, 'create-only')
    assert creator_client.get('/api/hrm/loans').status_code == 403
    exporter, _ = login(app, 'export-only')
    response = exporter.get('/api/hrm/reports/loans.xlsx')
    assert response.status_code == 200, response.json
    assert response.data.startswith(b'PK')
    assert exporter.get('/api/hrm/loans').status_code == 403
    reader, _ = login(app, 'read-only')
    assert reader.get('/api/hrm/loans').status_code == 200
    assert reader.get('/api/hrm/reports/loans.xlsx').status_code == 403


def test_multi_site_scope_and_record_snapshots_prevent_transfer_history_leak(access):
    app, admin, headers, people = access
    with sqlite3.connect(app.config['DB']) as conn:
        first = conn.execute("INSERT INTO hrm_sites(company_id,name) VALUES (1,'سایت یک')").lastrowid
        second = conn.execute("INSERT INTO hrm_sites(company_id,name) VALUES (1,'سایت دو')").lastrowid
        foreign = conn.execute("INSERT INTO hrm_sites(company_id,name) VALUES (2,'سایت شرکت دوم')").lastrowid
        conn.execute('INSERT INTO hrm_employee_details(employee_id,company_id,site_id,department,job) VALUES (?,?,?,?,?)',
                     (people['HRM-001'], 1, first, 'واحد اول', 'کارشناس'))
    viewer = create_user(admin, headers, name='site-manager', role='holding_manager',
                         scopes=[{'company_id': 1, 'site_id': second}],
                         permissions={'personnel': {'read': True}, 'loans': {'read': True}})
    with as_actor(app, viewer['id']) as conn:
        with pytest.raises(PermissionError):
            employee(conn, people['HRM-001'], 'personnel')
        with pytest.raises(PermissionError):
            record_scope(conn, {'company_id': 1, 'site_id': first, 'department': 'واحد اول'}, 'loans')
        with pytest.raises(PermissionError):
            record_scope(conn, {'company_id': 1, 'site_id': None}, 'loans')
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute('UPDATE hrm_employee_details SET site_id=? WHERE employee_id=?', (second, people['HRM-001']))
    with as_actor(app, viewer['id']) as conn:
        assert employee(conn, people['HRM-001'], 'personnel')['site_id'] == second
        # Current permission never discloses an earlier site's confidential record.
        with pytest.raises(PermissionError):
            record_scope(conn, {'company_id': 1, 'site_id': first, 'department': 'واحد اول'}, 'loans')
    before = counts(app)
    invalid = admin.put(f'/api/hrm/access/users/{viewer["id"]}', headers=headers,
                        json={'scopes': [{'company_id': 1, 'site_id': foreign}]})
    assert invalid.status_code == 400 and counts(app) == before
    incompatible = admin.put(f'/api/hrm/access/users/{viewer["id"]}', headers=headers, json={
        'training_access': True, 'permissions': {'training': {'read': True}}})
    assert incompatible.status_code == 400


def test_delegated_training_writes_do_not_grant_personnel_identity_mutations(access):
    app, admin, headers, people = access
    create_user(admin, headers, name='training-writer', role='holding_manager', scopes=[],
                all_companies=True, training_access=True,
                permissions={'training': {'read': True, 'create': True, 'edit': True, 'delete': True}})
    staff, sh = login(app, 'training-writer')
    assert staff.get('/api/data/employees?company_id=1').status_code == 200
    assert staff.post('/api/data/employees?company_id=1', headers=sh,
                      json={'code': 'FORBIDDEN-ID', 'name': 'Forbidden'}).status_code == 403
    assert staff.put(f'/api/data/employees/{people["HRM-001"]}?company_id=1', headers=sh,
                     json={'name': 'Forbidden identity edit'}).status_code == 403
    assert staff.delete(f'/api/data/employees/{people["HRM-001"]}?company_id=1', headers=sh).status_code == 403
    assert staff.post('/api/excel/import/employees?company_id=1', headers=sh,
                      data={'policy': 'upsert', 'mode': 'commit'}).status_code == 403
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT name FROM employees WHERE id=?', (people['HRM-001'],)).fetchone()[0] == 'HRM-001'


def test_early_hrm_access_schema_upgrades_without_losing_grants_or_scopes(access):
    app, admin, headers, _ = access
    user = create_user(admin, headers, name='prototype-hr', role='holding_hr',
                       scopes=[{'company_id': 1}, {'company_id': 2}],
                       permissions={'contracts': {'read': True, 'write': False}})
    with sqlite3.connect(app.config['DB']) as conn:
        conn.execute('PRAGMA foreign_keys=OFF')
        conn.executescript('''DROP TABLE hrm_user_permissions;
            DROP TABLE hrm_user_scopes;
            DROP TABLE hrm_user_access;
            CREATE TABLE hrm_user_access(user_id INTEGER PRIMARY KEY REFERENCES users(id),
                role TEXT NOT NULL CHECK(role IN ('system_admin','holding_hr','company_hr','unit_manager','training_officer','hse_officer','executive')),
                employee_id INTEGER UNIQUE REFERENCES employees(id),training_access INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE hrm_user_scopes(user_id INTEGER NOT NULL REFERENCES hrm_user_access(user_id) ON DELETE CASCADE,
                company_id INTEGER NOT NULL REFERENCES companies(id),department TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(user_id,company_id,department));
            CREATE TABLE hrm_user_permissions(user_id INTEGER NOT NULL REFERENCES hrm_user_access(user_id) ON DELETE CASCADE,
                module TEXT NOT NULL,can_read INTEGER NOT NULL,can_write INTEGER NOT NULL CHECK(can_write<=can_read),
                PRIMARY KEY(user_id,module));''')
        conn.execute('INSERT INTO hrm_user_access(user_id,role) VALUES (?,?)', (user['id'], 'holding_hr'))
        conn.executemany('INSERT INTO hrm_user_scopes(user_id,company_id) VALUES (?,?)', [(user['id'], 1), (user['id'], 2)])
        conn.execute("INSERT INTO hrm_user_permissions VALUES (?,'contracts',1,0)", (user['id'],))
    restarted = create_app(str(Path(app.config['DB']).parent), testing=True)
    staff, _ = login(restarted, 'prototype-hr')
    context = staff.get('/api/hrm/context').json
    assert context['role'] == 'holding_hr' and context['company_ids'] == [1, 2]
    assert context['permissions']['contracts']['read'] and context['permissions']['contracts']['export']
    assert not context['permissions']['contracts']['create']
    with sqlite3.connect(restarted.config['DB']) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('SELECT count(*) FROM hrm_user_access').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM hrm_user_scopes').fetchone()[0] == 2
        assert conn.execute('SELECT count(*) FROM hrm_user_permissions').fetchone()[0] == 1


def test_additional_hrm_admin_binding_is_effective_and_can_be_revoked(access):
    app, admin, headers, _ = access
    user = create_user(admin, headers, name='additional-admin', role='system_admin', scopes=[])
    assert user['legacy_role'] == 'viewer' and user['is_primary_admin']
    second, sh = login(app, 'additional-admin')
    assert second.get('/api/companies').status_code == 200
    assert second.get('/api/data/employees?company_id=2').status_code == 200
    assert second.get('/api/hrm/access/users').status_code == 200
    changed = admin.put(f'/api/hrm/access/users/{user["id"]}', headers=headers, json={
        'role': 'holding_manager', 'all_companies': False, 'scopes': [{'company_id': 1}],
        'permissions': {'personnel': {'read': True}}, 'training_access': False})
    assert changed.status_code == 200, changed.json
    assert not changed.json['is_primary_admin']
    assert second.get('/api/hrm/access/users').status_code == 401
    second, _ = login(app, 'additional-admin')
    assert second.get('/api/hrm/access/users').status_code == 403
    assert second.get('/api/companies').status_code == 403
    # The original main administrator is never silently demoted by a shadow role.
    attempted = admin.put('/api/hrm/access/users/1', headers=headers, json={
        'role': 'holding_manager', 'scopes': [{'company_id': 1}], 'permissions': {},
        'all_companies': False, 'training_access': False})
    assert attempted.status_code == 400


def test_real_legacy_admin_demotion_is_atomic_and_invalidates_sessions(access):
    app, admin, headers, _ = access
    user = create_user(admin, headers, name='legacy-secondary-admin', role='system_admin',
                       legacy_role='admin', scopes=[])
    secondary, _ = login(app, 'legacy-secondary-admin')
    assert secondary.get('/api/companies').status_code == 200
    changed = admin.put(f'/api/hrm/access/users/{user["id"]}', headers=headers, json={
        'role': 'holding_manager', 'all_companies': False,
        'scopes': [{'company_id': 1}], 'permissions': {'personnel': {'read': True}}, 'training_access': False})
    assert changed.status_code == 200, changed.json
    assert changed.json['legacy_role'] == 'viewer' and not changed.json['is_primary_admin']
    with sqlite3.connect(app.config['DB']) as conn:
        assert conn.execute('SELECT role FROM users WHERE id=?', (user['id'],)).fetchone()[0] == 'viewer'
    assert secondary.get('/api/hrm/access/users').status_code == 401
    secondary, _ = login(app, 'legacy-secondary-admin')
    assert secondary.get('/api/companies').status_code == 403
    assert secondary.get('/api/hrm/access/users').status_code == 403
