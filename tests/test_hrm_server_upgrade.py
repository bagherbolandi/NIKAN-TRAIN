"""Convert the uploaded0.11 HTTP setup without replacing its business database."""
import hashlib
import json
import sqlite3

import pytest

from network.config import initialize


def test_existing_https_identity_and_settings_are_unchanged(tmp_path):
    config=initialize(tmp_path,'localhost',8766)
    files={name:(tmp_path/name).read_bytes() for name in ('server.json','tls/server.key','tls/server.pem','keys/recovery.key')}
    assert initialize(tmp_path,'different-host',9999,upgrade_legacy_http=True)==config
    assert {name:(tmp_path/name).read_bytes() for name in files}==files


def test_guided_http_upgrade_preserves_database_port_and_original_configuration(tmp_path):
    old={'host':'0.0.0.0','port':8877,'https':False}
    original=json.dumps(old).encode('utf-8-sig')
    (tmp_path/'server.json').write_bytes(original)
    with sqlite3.connect(tmp_path/'nikan.sqlite3') as conn:
        conn.execute('CREATE TABLE business_data(value TEXT)')
        conn.execute("INSERT INTO business_data VALUES ('Existing real data must not be replaced')")
    before=hashlib.sha256((tmp_path/'nikan.sqlite3').read_bytes()).digest()
    config=initialize(tmp_path,'localhost',8766,upgrade_legacy_http=True)
    assert config['port']==8877 and config['certificate']
    assert next(tmp_path.glob('server-http-before-0.12-*.json')).read_bytes()==original
    assert hashlib.sha256((tmp_path/'nikan.sqlite3').read_bytes()).digest()==before
    assert initialize(tmp_path,'localhost',8766,upgrade_legacy_http=True)==config


def test_unsupported_existing_proxy_or_malformed_settings_are_not_overwritten(tmp_path):
    original=json.dumps({'host':'127.0.0.1','port':8766,'https':True}).encode()
    (tmp_path/'server.json').write_bytes(original)
    with pytest.raises(ValueError):initialize(tmp_path,'localhost',upgrade_legacy_http=True)
    assert (tmp_path/'server.json').read_bytes()==original
    assert not (tmp_path/'tls/server.key').exists()
