"""Stdlib-only check/snapshot run by an installed Python before replacing files."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3


def snapshot(root):
    root=Path(root);database=root/'nikan.sqlite3'
    if not database.exists() or not database.stat().st_size:
        return dict(existing_database=False,verified=True)
    source=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True,timeout=30)
    target=None
    try:
        if source.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
            raise ValueError('Existing database failed integrity check. Installation stopped.')
        if source.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('Existing database has invalid references. Installation stopped.')
        folder=root/'pre-upgrade-backups';folder.mkdir(exist_ok=True,mode=0o700)
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        target=folder/('before-files-0.14-'+stamp+'-'+secrets.token_hex(4)+'.sqlite3')
        target.touch(exist_ok=False,mode=0o600)
        destination=sqlite3.connect(target)
        try:
            source.backup(destination)
            if destination.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise ValueError('Pre-upgrade snapshot verification failed. Installation stopped.')
            if destination.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('Pre-upgrade snapshot references failed. Installation stopped.')
        finally:destination.close()
        report=dict(existing_database=True,verified=True,file=target.name,
                    sha256=hashlib.sha256(target.read_bytes()).hexdigest())
        target.with_suffix('.sqlite3.json').write_text(json.dumps(report,indent=2)+'\n')
        return report
    except BaseException:
        if target:target.unlink(missing_ok=True)
        raise
    finally:source.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(snapshot(args.data_dir)))
