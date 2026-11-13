"""Upgrade actual published 0.10/0.11.2 databases, rather than edited new schemas.

The historical source is read from Git's immutable 0.10 commit and the uploaded
0.11.2 source archive. CI fetches full history. Offline source-only test runs can
set NIKAN_OLD_010_SOURCE and NIKAN_OLD_0112_SOURCE to extracted original sources.
"""
import hashlib
from contextlib import closing
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import zipfile

import pytest

from app import create_app
from app.documents import FIELDS
from network.backup import RECOVERY_ADDITIONS, decrypt, encrypt, recovery_check
from recovery import restore

ROOT = Path(__file__).resolve().parents[1]
OLD_010 = '094dfff47196af17b8c0d0866d3c2abb82d622fa'
PASSWORD = 'Synthetic-upgrade-password-123'


SEED = r'''
import base64,io,json,sqlite3,sys
from pathlib import Path
source,data,result=map(Path,sys.argv[1:4]);sys.path.insert(0,str(source))
from app import create_app
from app.documents import FIELDS
app=create_app(data,testing=True);client=app.test_client()
password='Synthetic-upgrade-password-123'
assert client.post('/api/setup',json={'username':'old-admin','password':password}).status_code==200
assert client.post('/api/login',json={'username':'old-admin','password':password}).status_code==200
h={'X-CSRF-Token':client.get('/api/me').json['csrf']}
def write(method,path,body):
 response=getattr(client,method)('/api/'+path,headers=h,json=body)
 assert response.status_code in (200,201),(path,response.status_code,response.json)
 return response.json
ids={};profiles={};actual={}
for cid in (1,2):
 skill=client.post('/api/competency-bank/S001/select?company_id='+str(cid),headers=h)
 assert skill.status_code==200,skill.json
 sid=skill.json['id'];job='Synthetic legacy job '+str(cid)
 ideal={key:'متن مطلوب قدیمی '+key for key in FIELDS}
 ideal.update(effective_date='1405/01/01',review_date='1406/01/01',revision='نسخه قدیمی')
 jid=write('post','data/job_profiles?company_id='+str(cid),dict(ideal,job=job,department='Legacy department'))['id']
 eid=write('post','data/employees?company_id='+str(cid),{'code':'SYNTH-'+str(cid)+'1234','name':'Synthetic archived person '+str(cid),'job':job,'department':'Legacy department'})['id']
 write('post','data/requirements?company_id='+str(cid),{'job':job,'skill_id':sid,'level':3})
 write('post','data/assessments?company_id='+str(cid),{'employee_id':eid,'skill_id':sid,'level':1})
 course=write('post','data/courses?company_id='+str(cid),{'skill_id':sid,'title':'Synthetic archived course '+str(cid),'date':'1405/08/20','hours':4,'cost':1.000001,'status':'completed'})['id']
 write('post','data/records?company_id='+str(cid),{'employee_id':eid,'course_id':course,'level':2,'score':79,'reaction':4,'behavior':3,'result':65})
 values={key:'متن واقعی قدیمی '+key+'؛ '+('شرح وظایف جاری سازمان، ' * 40 if key=='duties' else '')+' END-'+key for key in FIELDS}
 values.update(effective_date='1405/02/01',review_date='1406/02/01',revision='بازنگری واقعی')
 if cid==2:values['authority']=''
 write('put',f'passports/{eid}/approval?company_id={cid}',{'job':job})
 doc=write('put',f'passports/{eid}/document?company_id={cid}',{'overrides':values})
 write('put',f'passports/{eid}/requirements?company_id={cid}',{'items':[{'code':'S001','level':4}]})
 certificate=b'%PDF-1.4\nSynthetic archived certificate '+str(cid).encode()+b'\n%%EOF'
 r=client.post('/api/certificates?company_id='+str(cid),headers=h,data={'employee_id':str(eid),'title':'Synthetic original certificate','file':(io.BytesIO(certificate),'legacy.pdf')})
 assert r.status_code==201,r.json
 write('post','data/instructors?company_id='+str(cid),{'name':'Synthetic legacy teacher '+str(cid),'specialty':'Training','rate':2})
 need=write('post','data/needs?company_id='+str(cid),{'skill_id':sid,'department':'Legacy department','reason':'Synthetic old need','priority':2,'year':1405})['id']
 if 'holding_needs' in app.view_functions:
  current=next(row for row in client.get('/api/data/needs?company_id='+str(cid)).json if row['id']==need)
  write('post',f'holding/needs/{need}/decision',{'status':'approved','decision':'Synthetic approved old decision','version':current['version']})
  write('put',f'development-plans/{need}?company_id={cid}',{'objective':'Synthetic old objective','method':'Classroom','owner':'Synthetic owner','due_date':'1405/12/25','success_criterion':'Pass','baseline':1,'target':3,'actual':2,'evidence':'Synthetic evidence','review_date':'1405/08/20','course_id':course,'version':0})
 ids[str(cid)]={'employee':eid,'skill':sid,'course':course,'profile':jid,'certificate':r.json['id']}
 actual[str(eid)]=values;profiles[str(eid)]=doc
write('post','users',{'username':'old-company-manager','password':password,'role':'manager','company_id':2})
first=ids['1']['course'];members=[ids['1']['employee'],ids['2']['employee']]
write('put',f'courses/{first}/participants',{'employee_ids':members})
write('put',f'courses/{first}/attendance',{'employee_ids':members})
logo=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')
r=client.post('/api/branding/holding',headers=h,data={'file':(io.BytesIO(logo),'synthetic.png')});assert r.status_code==200,r.json
client.post('/api/login',json={'username':'synthetic-bad-login','password':'invalid'})
if (source/'app'/'recovery.py').exists():
 from app.recovery import issue
 with sqlite3.connect(app.config['DB']) as conn:issue(conn,'old-company-manager')
result.write_text(json.dumps({'source_file_sha256':__import__('hashlib').sha256((source/'app'/'__init__.py').read_bytes()).hexdigest(),'ids':ids,'actual':actual,'documents':profiles},ensure_ascii=False),encoding='utf-8')
'''


def git_bytes(*args):
    try:
        return subprocess.run(['git', *args], cwd=ROOT, check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError('Historical upgrade fixtures require full Git history (including v0.10.0), or set NIKAN_OLD_010_SOURCE/NIKAN_OLD_0112_SOURCE.') from exc


@pytest.fixture(scope='session')
def old_sources(tmp_path_factory):
    folder = tmp_path_factory.mktemp('actual-legacy-source')
    result = {}
    explicit = os.environ.get('NIKAN_OLD_010_SOURCE')
    if explicit:
        result['0.10'] = Path(explicit)
    else:
        old = folder / '0.10';old.mkdir()
        payload = git_bytes('archive', '--format=tar', OLD_010, 'app', 'network')
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            for entry in archive:
                if entry.isfile():
                    target = old / entry.name
                    assert '..' not in target.relative_to(old).parts
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(entry).read())
        names = git_bytes('ls-tree', '-r', '--name-only', '-z', OLD_010).decode().split('\0')
        workbook = next(name for name in names if name.endswith('.xlsx') and '/' not in name)
        (old / 'resources').mkdir()
        (old / 'resources' / 'workbook.xlsx').write_bytes(git_bytes('show', OLD_010 + ':' + workbook))
        result['0.10'] = old
    explicit = os.environ.get('NIKAN_OLD_0112_SOURCE')
    if explicit:
        result['0.11.2'] = Path(explicit)
    else:
        old = folder / '0.11.2';old.mkdir()
        source = ROOT / 'NikanTraining-0.11.2-Source.zip'
        payload = source.read_bytes() if source.exists() else git_bytes('show', '6a98f062a2d5a18d06eb5515ceb68a24e97c916f:NikanTraining-0.11.2-Source.zip')
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in archive.namelist():
                if name.endswith('/'):
                    continue
                target = old / name
                assert '..' not in target.relative_to(old).parts
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
        candidates = list(old.rglob('app/__init__.py'))
        assert len(candidates) == 1
        result['0.11.2'] = candidates[0].parent.parent
    for version, source in result.items():
        assert (source / 'app' / '__init__.py').is_file(), version
    return result


def snapshot(path, projection=None):
    """Exact scalar values, SHA256 of every original BLOB, stable original order."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        result = {}
        for name in (projection or tables):
            columns = projection[name]['columns'] if projection else [row[1] for row in conn.execute('PRAGMA table_info("' + name + '")')]
            sql = 'SELECT ' + ','.join('"' + col + '"' for col in columns) + ' FROM "' + name + '" ORDER BY rowid'
            rows = [[{'blob_sha256': hashlib.sha256(value).hexdigest(), 'length': len(value)} if isinstance(value, bytes) else value
                     for value in row] for row in conn.execute(sql)]
            result[name] = {'columns': columns, 'rows': rows}
        return result


def seeded_database(source, directory):
    root = directory / 'original';root.mkdir()
    baseline = directory / 'baseline.json'
    process = subprocess.run([sys.executable, '-I', '-c', SEED, str(source), str(root), str(baseline)],
                             cwd=directory, capture_output=True, text=True, timeout=90)
    assert process.returncode == 0, process.stdout + process.stderr
    # SQLite backup, rather than a possibly live WAL file copy.
    canonical = directory / 'original.sqlite3'
    with sqlite3.connect(root / 'nikan.sqlite3') as src, sqlite3.connect(canonical) as dst:
        src.backup(dst)
    return canonical, json.loads(baseline.read_text(encoding='utf-8'))


def projection_report(before, after):
    result = {}
    for name, original in before.items():
        assert original == after[name], 'An original column or BLOB changed in ' + name
        digest = hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result[name] = {'rows': len(original['rows']), 'original_columns': original['columns'],
                        'original_projection_sha256': digest, 'verified_projection_sha256': digest}
    return result


@pytest.mark.parametrize('version', ['0.10', '0.11.2'])
def test_actual_old_source_upgrade_preserves_all_original_columns_and_verified_backups(old_sources, tmp_path, version):
    original, baseline = seeded_database(old_sources[version], tmp_path)
    before = snapshot(original)
    assert len(before['employees']['rows']) == 2 and len(before['course_participants']['rows']) == 2
    assert len(before['personal_documents']['rows']) == 2
    table_evidence = {}
    key = secrets.token_bytes(32)
    old_archive = tmp_path / 'original-encrypted.nikbak'
    authenticated_header = encrypt(original, old_archive, key)
    for encrypted in (False, True):
        folder = tmp_path / ('network' if encrypted else 'desktop');folder.mkdir()
        shutil.copy2(original, folder / 'nikan.sqlite3')
        if encrypted:
            (folder / 'keys').mkdir()
            (folder / 'keys' / 'recovery.key').write_bytes(key)
        application = create_app(folder, testing=True)
        database = Path(application.config['DB'])
        table_evidence = projection_report(before, snapshot(database, before))
        with closing(sqlite3.connect(database)) as conn:
            actual_tables = {row[0]: conn.execute('SELECT count(*) FROM "' + row[0] + '"').fetchone()[0]
                             for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            for added in set(actual_tables) - set(before):
                assert added in RECOVERY_ADDITIONS and actual_tables[added] == RECOVERY_ADDITIONS[added], added
        verified = application.extensions['hrm_upgrade_backup']
        assert verified and verified['verified'] and verified['encrypted'] is encrypted
        saved = folder / 'pre-upgrade-backups' / verified['file']
        assert saved.is_file()
        if encrypted:
            assert saved.suffix == '.nikbak' and not list(saved.parent.glob('*.sqlite3'))
            checked = tmp_path / 'decrypted-pre-upgrade.sqlite3'
            decrypt(saved, checked, key)
            projection_report(before, snapshot(checked, before))
        else:
            assert hashlib.sha256(saved.read_bytes()).hexdigest() == verified['source_sha256']
            projection_report(before, snapshot(saved, before))
        saved_names = {path.name for path in saved.parent.iterdir()}
        restarted = create_app(folder, testing=True)
        assert restarted.extensions['hrm_upgrade_backup'] is None
        assert {path.name for path in saved.parent.iterdir()} == saved_names
        projection_report(before, snapshot(database, before))
        client = restarted.test_client()
        assert client.post('/api/login', json={'username': 'old-admin', 'password': PASSWORD}).status_code == 200
        headers = {'X-CSRF-Token': client.get('/api/me').json['csrf']}
        for values in baseline['ids'].values():
            eid = values['employee']
            response = client.get(f'/api/passports/{eid}/document?company_id=' + ('1' if eid == baseline['ids']['1']['employee'] else '2'))
            assert response.status_code == 200
            document = response.json
            assert document['overrides'] == baseline['actual'][str(eid)]
            assert {field: document['effective'][field] for field in FIELDS} == baseline['actual'][str(eid)]
            assert document['number'] == baseline['documents'][str(eid)]['number']
            assert document['aliases'] == baseline['documents'][str(eid)]['aliases']
            assert document['is_snapshot']
        eid = baseline['ids']['1']['employee']
        full_word = client.get(f'/api/passports/{eid}/profile.docx?company_id=1')
        assert full_word.status_code == 200
        with zipfile.ZipFile(io.BytesIO(full_word.data)) as archive:
            text = archive.read('word/document.xml').decode()
            for field in FIELDS:
                assert baseline['actual'][str(eid)][field] in text
        course = baseline['ids']['1']['course']
        allocation = client.get(f'/api/courses/{course}/cost-allocation').json
        assert allocation['attendee_count'] == 2
        assert allocation['allocated_cost_rials'] == 1_000_001
        assert sum(row['cost_rials'] for row in allocation['companies']) == 1_000_001
        manager = restarted.test_client()
        assert manager.post('/api/login', json={'username': 'old-company-manager', 'password': PASSWORD}).status_code == 200
        assert manager.get('/api/data/employees?company_id=2').status_code == 200
        assert manager.get('/api/data/employees?company_id=1').status_code == 403
        assert manager.get('/api/hrm/health_records').status_code == 403
        # Existing legacy users and issued references remain valid after backup.
        backup = client.post('/api/backup', headers=headers)
        assert backup.status_code == 200 and backup.data.startswith(b'SQLite format 3')
        restored = tmp_path / ('network-raw-restored' if encrypted else 'desktop-restored');restored.mkdir()
        (restored / 'nikan.sqlite3').write_bytes(backup.data)
        restored_app = create_app(restored, testing=True)
        with closing(sqlite3.connect(restored_app.config['DB'])) as conn:
            assert conn.execute('SELECT count(*) FROM employees').fetchone()[0] == 2
            assert conn.execute('SELECT sum(attended) FROM course_participants').fetchone()[0] == 2
    assert recovery_check(old_archive, key, tmp_path)['verified']
    keyfile = tmp_path / 'test-recovery.key';keyfile.write_bytes(key)
    restored = tmp_path / 'encrypted-original-restored'
    assert restore(old_archive, keyfile, restored)['restored']
    restored_app = create_app(restored, testing=True)
    projection_report(before, snapshot(restored_app.config['DB'], before))
    evidence = {'version': version, 'passed': True, 'source_app_sha256': baseline['source_file_sha256'],
                'original_tables': table_evidence, 'original_encrypted_tables': authenticated_header['tables'],
                'raw_and_encrypted_pre_upgrade_snapshots_verified': True,
                'all_18_actual_fields_and_original_reference_aliases_preserved': True,
                'actual_attendance_and_exact_cost_allocation_preserved': True,
                'legacy_user_passwords_and_company_isolation_preserved': True,
                'restart_raw_backup_and_authenticated_old_archive_recovery_passed': True}
    destination = os.environ.get('NIKAN_UPGRADE_EVIDENCE')
    if destination:
        output = Path(destination);output.mkdir(parents=True, exist_ok=True)
        (output / ('upgrade-' + version + '-to-hrm.json')).write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
