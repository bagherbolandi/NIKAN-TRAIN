import json
import sqlite3

import pytest

from app import create_app


PASSWORD = 'Secure-password-123'


@pytest.fixture
def ready(tmp_path):
    app = create_app(tmp_path, testing=True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    assert client.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    headers = {'X-CSRF-Token': client.get('/api/me').json['csrf']}
    skill = client.post('/api/data/skills?company_id=1', headers=headers, json={'code': 'VAL', 'title': 'Validation skill'}).json['id']
    employee = client.post('/api/data/employees?company_id=1', headers=headers, json={'code': 'E1', 'name': 'Employee'}).json['id']
    course = client.post('/api/data/courses?company_id=1', headers=headers, json={
        'skill_id': skill, 'title': 'Course', 'date': '1405/01/01', 'hours': 1,
    }).json['id']
    return app, client, headers, {'skill': skill, 'employee': employee, 'course': course}


def assert_bad(response):
    assert response.status_code == 400
    assert set(response.json) == {'error'}


def test_non_object_and_malformed_json_is_a_controlled_error(ready):
    _, client, headers, ids = ready
    for method, url in (
        ('post', '/api/login'), ('post', '/api/users'),
        ('put', '/api/users/1'), ('post', '/api/data/employees?company_id=1'),
        ('put', f"/api/data/employees/{ids['employee']}?company_id=1"),
        ('put', f"/api/data/courses/{ids['course']}?company_id=1"),
    ):
        for body in ([], [1], True, 7, None, 'text'):
            assert_bad(getattr(client, method)(url, headers=headers, data=json.dumps(body), content_type='application/json'))
        assert_bad(getattr(client, method)(url, headers=headers, data='{', content_type='application/json'))
    assert len(client.get('/api/data/employees?company_id=1').json) == 1


def test_setup_and_credentials_reject_non_text_and_non_object_inputs(tmp_path, ready):
    fresh = create_app(tmp_path / 'fresh', testing=True).test_client()
    for body in ([], True, None, {'username': [], 'password': PASSWORD},
                 {'username': 'admin', 'password': 123456789012}, {'username': '   ', 'password': PASSWORD}):
        assert_bad(fresh.post('/api/setup', data=json.dumps(body), content_type='application/json'))
    assert fresh.get('/api/status').json['setup_required']
    _, client, headers, _ = ready
    for body in ({'username': {}, 'password': PASSWORD}, {'username': 'admin', 'password': None}):
        assert_bad(client.post('/api/login', json=body))
        assert_bad(client.post('/api/users', headers=headers, json={**body, 'role': 'manager', 'company_id': 1}))


def test_reference_ids_reject_boolean_fraction_nonfinite_and_overflow_atomically(ready):
    _, client, headers, ids = ready
    base = {'employee_id': ids['employee'], 'skill_id': ids['skill'], 'level': 2}
    for field in ('employee_id', 'skill_id'):
        for invalid in (True, False, 1.5, 1.0, '1.5', 'NaN', float('nan'), float('inf'), None, [], {}, 0, -1, 2 ** 64, '9' * 100):
            assert_bad(client.post('/api/data/assessments?company_id=1', headers=headers, json={**base, field: invalid}))
    assert client.get('/api/data/assessments?company_id=1').json == []
    assert client.post('/api/data/assessments?company_id=1', headers=headers, json={
        **base, 'employee_id': str(ids['employee']), 'skill_id': str(ids['skill']), 'level': '2',
    }).status_code == 201
    for invalid in (True, 1.5, None, [], 2 ** 64):
        assert_bad(client.post('/api/users', headers=headers, json={
            'username': 'manager', 'password': PASSWORD, 'role': 'manager', 'company_id': invalid,
        }))
    for invalid in ('1.5', '0', '-1', '9' * 100):
        assert_bad(client.get('/api/data/employees', query_string={'company_id': invalid}))
    assert_bad(client.put('/api/users/' + str(2 ** 64), headers=headers, json={'password': PASSWORD}))
    assert_bad(client.delete('/api/data/employees/' + str(2 ** 64) + '?company_id=1', headers=headers))


def test_numeric_bounds_types_and_text_fields_fail_without_saving(ready):
    _, client, headers, ids = ready
    course = {'skill_id': ids['skill'], 'title': 'Invalid course', 'date': '1405/01/01', 'hours': 1}
    for patch in ({'hours': True}, {'hours': 0}, {'hours': -1}, {'hours': 'NaN'},
                  {'hours': float('inf')}, {'hours': []}, {'hours': '1e999'},
                  {'cost': -1}, {'cost': None}, {'status': 'unknown'},
                  {'title': []}, {'title': 17}, {'title': '  '}, {'instructor': None}, {'date': 14050101}):
        assert_bad(client.post('/api/data/courses?company_id=1', headers=headers, json={**course, **patch}))
    assert len(client.get('/api/data/courses?company_id=1').json) == 1
    for level in (True, 1.5, -1, 5, 'NaN', None):
        assert_bad(client.post('/api/data/assessments?company_id=1', headers=headers, json={
            'employee_id': ids['employee'], 'skill_id': ids['skill'], 'level': level,
        }))
    assert_bad(client.post('/api/data/requirements?company_id=1', headers=headers, json={
        'job': 'Engineer', 'skill_id': ids['skill'], 'level': 0,
    }))
    record = {'employee_id': ids['employee'], 'course_id': ids['course'], 'level': 2}
    for patch in ({'score': True}, {'score': -1}, {'score': 101}, {'reaction': 0},
                  {'behavior': 6}, {'result': 101}, {'result': 'Infinity'}):
        assert_bad(client.post('/api/data/records?company_id=1', headers=headers, json={**record, **patch}))
    assert client.get('/api/data/records?company_id=1').json == []
    assert client.post('/api/data/records?company_id=1', headers=headers, json={
        **record, 'score': None, 'reaction': None, 'behavior': None, 'result': None,
    }).status_code == 201


def test_failed_updates_leave_password_activation_and_course_unchanged(ready):
    app, client, headers, ids = ready
    assert client.post('/api/users', headers=headers, json={
        'username': 'manager', 'password': PASSWORD, 'role': 'manager', 'company_id': 1,
    }).status_code == 201
    uid = next(row['id'] for row in client.get('/api/users').json if row['username'] == 'manager')
    for body in ({'active': 0, 'password': []}, {'active': 0, 'password': 'short'},
                 {'active': True, 'password': 'Valid-new-password-123'}):
        assert_bad(client.put(f'/api/users/{uid}', headers=headers, json=body))
    manager = app.test_client()
    assert manager.post('/api/login', json={'username': 'manager', 'password': PASSWORD}).status_code == 200
    before = client.get('/api/data/courses?company_id=1').json
    assert_bad(client.put(f"/api/data/courses/{ids['course']}?company_id=1", headers=headers, json={
        'title': 'Must not save', 'hours': 'NaN',
    }))
    assert client.get('/api/data/courses?company_id=1').json == before
    connection = sqlite3.connect(app.config['DB'])
    try:
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        connection.close()


def test_jalali_leap_days_month_lengths_and_digit_normalization(ready):
    _, client, headers, ids = ready
    base = {'skill_id': ids['skill'], 'title': 'Jalali date', 'hours': 1}
    for date, normalized in (
        ('1403/12/30', '1403/12/30'), ('1399/12/30', '1399/12/30'),
        ('1404/12/29', '1404/12/29'), ('1300/12/30', '1300/12/30'),
        ('1498/12/30', '1498/12/30'), ('1499/12/29', '1499/12/29'),
        ('۱۴۰۳/۱۲/۳۰', '1403/12/30'), ('١٤٠٣/١٢/٣٠', '1403/12/30'),
        ('1405/06/31', '1405/06/31'), ('1405/11/30', '1405/11/30'),
    ):
        response = client.post('/api/data/courses?company_id=1', headers=headers, json={**base, 'date': date})
        assert response.status_code == 201, (date, response.json)
        saved = client.get('/api/data/courses?company_id=1').json
        assert next(row for row in saved if row['id'] == response.json['id'])['date'] == normalized
    before = client.get('/api/data/courses?company_id=1').json
    for date in (
        '1404/12/30', '1405/12/30', '1499/12/30', '1403/12/31',
        *(f'1405/{month:02}/31' for month in range(7, 12)),
        '1299/12/29', '1500/01/01', '1405/00/01', '1405/13/01',
        '1405/01/00', '1405/01/32', '1405/1/1', '1405-01-01',
        '1405/01/01\n', '1405/01/01 extra', '9' * 10000,
        None, [], {}, 14050101,
    ):
        assert_bad(client.post('/api/data/courses?company_id=1', headers=headers, json={**base, 'date': date}))
        assert_bad(client.put(
            f"/api/data/courses/{ids['course']}?company_id=1", headers=headers,
            json={'title': 'Must not save', 'date': date},
        ))
    assert client.get('/api/data/courses?company_id=1').json == before


def test_extreme_numbers_and_unicode_are_controlled_and_atomic(ready):
    _, client, headers, ids = ready
    base = {'skill_id': ids['skill'], 'title': 'Extreme value', 'date': '1405/01/01', 'hours': 1}
    for hours in ('1e999999999999999999999999', '1e-999999999', '9' * 10000,
                  '0.' + '0' * 10000 + '1', 10 ** 1000):
        assert_bad(client.post('/api/data/courses?company_id=1', headers=headers, json={**base, 'hours': hours}))
    for body in ({'username': 'bad\ud800', 'password': PASSWORD},
                 {'username': 'admin', 'password': 'bad\udfff'},
                 {'username': 'admin', 'password': PASSWORD, '\ud800': 'ignored key'}):
        assert_bad(client.post('/api/login', json=body))
    assert_bad(client.put(
        f"/api/data/courses/{ids['course']}?company_id=1", headers=headers,
        json={'title': 'invalid\ud800', 'hours': 2},
    ))
    # A deeply nested JSON body can raise RecursionError inside Python's parser.
    assert_bad(client.put(
        f"/api/data/courses/{ids['course']}?company_id=1", headers=headers,
        data='{"nested":' + '[' * 1500 + '0' + ']' * 1500 + '}', content_type='application/json',
    ))
    courses = client.get('/api/data/courses?company_id=1').json
    assert len(courses) == 1
    assert courses[0]['title'] == 'Course'
    assert courses[0]['hours'] == 1
