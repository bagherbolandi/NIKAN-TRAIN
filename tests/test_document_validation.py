from test_catalog import ready
import sqlite3


def test_invalid_document_ids_are_controlled(ready):
    _, client, headers = ready
    for suffix in ('document', 'requirements'):
        for employee in (0, 9223372036854775808):
            path = f'/api/passports/{employee}/{suffix}?company_id=1'
            for method in ('GET', 'PUT', 'DELETE'):
                response = client.open(path, method=method, headers=headers, json={})
                assert response.status_code == 400
                assert response.is_json


def test_malformed_personal_documents_preserve_existing_draft_and_revision(ready):
    _, client, headers = ready
    employee = client.post('/api/data/employees?company_id=1', headers=headers,
                           json={'code': 'DOC-VALID', 'name': 'Document person'}).json['id']
    path = f'/api/passports/{employee}/document?company_id=1'
    assert client.put(path, headers=headers,
                      json={'overrides': {'duties': 'Existing duties'}}).status_code == 200
    before = client.get(path).json
    for payload in [[], None, 'text', {}, {'overrides': []}, {'overrides': {'duties': None}},
                    {'overrides': {'duties': 'changed', 'unknown': 'invalid'}},
                    {'overrides': {'duties': 'changed'}, 'company_id': 2}]:
        response = client.put(path, headers=headers, json=payload)
        assert response.status_code == 400, (payload, response.status_code)
        assert response.is_json
        assert client.get(path).json == before


def test_invalid_personal_requirements_do_not_attach_or_partially_update(ready):
    _, client, headers = ready
    employee = client.post('/api/data/employees?company_id=1', headers=headers,
                           json={'code': 'REQ-VALID', 'name': 'Requirements person'}).json['id']
    path = f'/api/passports/{employee}/requirements?company_id=1'
    assert client.put(path, headers=headers,
                      json={'items': [{'code': 'S001', 'level': 2}]}).status_code == 200
    before = client.get(path).json
    skills = client.get('/api/data/skills?company_id=1').json
    for payload in [[], {}, {'items': None}, {'items': [None]},
                    {'items': [{'code': 'S002', 'level': 4}, {'code': 'S001', 'level': True}]},
                    {'items': [{'code': 'S002', 'level': 1.5}]},
                    {'items': [{'code': 'S001', 'level': 1}, {'code': 'S001', 'level': 3}]},
                    {'items': [{'code': 'S002', 'level': 3}, {'code': 'MISSING', 'level': 1}]},
                    {'items': [], 'employee_id': employee + 1}]:
        response = client.put(path, headers=headers, json=payload)
        assert response.status_code == 400, (payload, response.status_code)
        assert client.get(path).json == before
        assert client.get('/api/data/skills?company_id=1').json == skills


def test_exhausted_personal_reference_counter_rejects_insert_atomically(ready):
    app, client, headers = ready
    with sqlite3.connect(app.config['DB']) as connection:
        connection.execute('UPDATE company_registry SET passport_high_watermark=9223372036854775807')
        before = connection.execute('SELECT count(*) FROM employees').fetchone()[0]
    response = client.post('/api/data/employees?company_id=1', headers=headers,
                           json={'code': 'EXHAUSTED', 'name': 'Must not be created'})
    assert response.status_code == 400
    assert response.is_json
    with sqlite3.connect(app.config['DB']) as connection:
        assert connection.execute('SELECT count(*) FROM employees').fetchone()[0] == before
        assert connection.execute('SELECT passport_high_watermark FROM company_registry').fetchone()[0] == 9223372036854775807
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
