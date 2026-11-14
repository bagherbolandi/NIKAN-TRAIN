"""Verified pre-upgrade snapshots before the existing application's migrations."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3

VERSION = 'hrm-0.14'


def prepare_upgrade_backup(root):
    root = Path(root)
    database = root / 'nikan.sqlite3'
    if not database.exists() or database.stat().st_size == 0:
        return None
    source = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True, timeout=30)
    try:
        present = source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hrm_schema_migrations'").fetchone()
        if present and source.execute('SELECT 1 FROM hrm_schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return None
        if source.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('پایگاه پیش از ارتقا سالم نیست؛ ارتقا متوقف شد')
        if source.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('ارتباط‌های پایگاه پیش از ارتقا معتبر نیست؛ ارتقا متوقف شد')
        folder = root / 'pre-upgrade-backups'
        folder.mkdir(exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target = folder / ('pre-hrm-0.14-' + stamp + '-' + secrets.token_hex(4) + '.sqlite3')
        destination = sqlite3.connect(target)
        try:
            source.backup(destination, pages=256, sleep=.05)
            if destination.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('پشتیبان پیش از ارتقا تأیید نشد؛ ارتقا متوقف شد')
            if destination.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('پشتیبان پیش از ارتقا ارتباط نامعتبر دارد')
            tables = {row[0]: destination.execute('SELECT count(*) FROM "' + row[0].replace('"', '""') + '"').fetchone()[0]
                      for row in destination.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        except BaseException:
            destination.close()
            target.unlink(missing_ok=True)
            raise
        finally:
            destination.close()
        target.chmod(0o600)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        keyfile = root / 'keys' / 'recovery.key'
        encrypted = False
        if keyfile.exists():
            from network.backup import encrypt, decrypt
            key = keyfile.read_bytes()
            if len(key) != 32:
                raise ValueError('کلید پشتیبان شبکه معتبر نیست؛ ارتقا متوقف شد')
            archive = target.with_suffix('.nikbak')
            encrypt(target, archive, key)
            archive.chmod(0o600)
            verification = target.with_suffix('.verify.sqlite3')
            try:
                header = decrypt(archive, verification, key)
                if header['sha256'] != digest:
                    raise ValueError('پشتیبان رمزگذاری‌شده پیش از ارتقا تأیید نشد')
            finally:
                verification.unlink(missing_ok=True)
            target.unlink()
            target, encrypted = archive, True
        report = dict(version=VERSION, created=stamp, verified=True, encrypted=encrypted,
                      file=target.name, source_sha256=digest, tables=tables)
        meta = target.with_suffix(target.suffix + '.json')
        meta.write_text(json.dumps(report, sort_keys=True, indent=2) + '\n', encoding='utf-8')
        meta.chmod(0o600)
        return report
    finally:
        source.close()


def complete(conn):
    conn.executemany('INSERT OR IGNORE INTO hrm_schema_migrations(version) VALUES (?)', [('hrm-0.12',), ('hrm-0.13',), (VERSION,)])
    conn.commit()
