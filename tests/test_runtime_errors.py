"""Operational failures remain diagnosable and never masquerade as success."""
import re
import sqlite3

import pytest

from app import create_app


@pytest.fixture
def runtime(tmp_path):
    app = create_app(tmp_path, testing=True)

    @app.get('/api/synthetic-failure/<kind>')
    def fail(kind):
        if kind == 'busy':
            raise sqlite3.OperationalError('database is locked')
        if kind == 'disk':
            raise sqlite3.OperationalError('disk I/O error: private/database/path')
        if kind == 'validation':
            raise ValueError('نام لازم است')
        if kind == 'permission':
            raise PermissionError()
        raise RuntimeError('private internal diagnostic')

    return app.test_client()


def test_server_generated_trace_id_and_success_contract(runtime):
    first = runtime.get('/api/status', headers={'X-Request-ID': 'caller-injected'})
    second = runtime.get('/api/status')
    assert re.fullmatch('[a-f0-9]{16}', first.headers['X-Request-ID'])
    assert first.headers['X-Request-ID'] != second.headers['X-Request-ID']
    assert first.json == {'setup_required': True}
    assert first.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('kind,status', [('busy', 503), ('disk', 500), ('unexpected', 500)])
def test_failure_is_json_with_correlated_trace_and_no_private_details(runtime, kind, status):
    response = runtime.get('/api/synthetic-failure/' + kind)
    assert response.status_code == status
    assert response.json['request_id'] == response.headers['X-Request-ID']
    assert 'private' not in response.text and 'OperationalError' not in response.text
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers.get('Retry-After') == ('2' if status == 503 else None)


def test_standard_api_errors_are_json_and_keep_allowed_methods(runtime):
    missing = runtime.get('/api/does-not-exist')
    assert missing.status_code == 404 and missing.is_json
    wrong = runtime.post('/api/status')
    assert wrong.status_code == 405 and wrong.is_json
    assert 'GET' in wrong.headers['Allow']
    assert wrong.json['request_id'] == wrong.headers['X-Request-ID']
    assert runtime.get('/missing-page').status_code == 404


def test_domain_validation_and_permission_handlers_remain_specific(runtime):
    invalid = runtime.get('/api/synthetic-failure/validation')
    assert invalid.status_code == 400 and invalid.json['error'] == 'نام لازم است'
    assert runtime.get('/api/synthetic-failure/permission').status_code == 403
    assert runtime.get('/api/me').status_code == 401
