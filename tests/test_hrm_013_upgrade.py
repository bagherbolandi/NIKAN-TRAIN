"""Upgrade a genuine 0.12.1 HRM database with financial/personnel history intact."""
from contextlib import closing
import io
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import tarfile

from app import create_app
from network.backup import encrypt, recovery_check

ROOT = Path(__file__).resolve().parents[1]
SEED = r'''
import io,json,sys,sqlite3
from pathlib import Path
source,data,result=map(Path,sys.argv[1:]);sys.path.insert(0,str(source))
from app import create_app
app=create_app(data,testing=True);c=app.test_client()
pw='Synthetic-013-upgrade-password-123'
assert c.post('/api/setup',json={'username':'old-admin','password':pw}).status_code==200
assert c.post('/api/login',json={'username':'old-admin','password':pw}).status_code==200
h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
def post(resource,body):
 if resource in ('evaluation_periods','performance_criteria'):body.setdefault('company_id',1)
 if resource=='evaluation_periods':body.setdefault('status','open')
 r=c.post('/api/hrm/'+resource+'?company_id=1',json=body,headers=h)
 assert r.status_code==201,(resource,r.json)
 return r.json
site=post('sites',{'name':'Historical site','kind':'factory'})['id']
person=post('people',{'code':'HIST-0042','name':'Historical person','site_id':site,'department':'Manufacturing','job':'Operator','start_date':'2026-01-01'})['id']
contract=post('contracts',{'employee_id':person,'number':'OLD-CONTRACT-42','contract_type':'Fixed','start_date':'2026-01-01','end_date':'2027-01-01','status':'active'})['id']
pdf=b'%PDF-1.4\nSynthetic historical contract\n%%EOF'
r=c.post('/api/hrm/documents?company_id=1',headers=h,data={'employee_id':str(person),'contract_id':str(contract),'permission_group':'contracts','title':'Private old contract','file':(io.BytesIO(pdf),'old.pdf')})
assert r.status_code==201,r.json
document=r.json['id']
post('attendance',{'employee_id':person,'work_date':'2026-11-01','kind':'work','minutes':480})
period=post('evaluation_periods',{'title':'Historical performance','site_id':site,'start_date':'2026-11-01','end_date':'2026-11-30'})['id']
criterion=post('performance_criteria',{'period_id':period,'site_id':site,'title':'Quality','weight':100,'max_score':100})['id']
post('performance_scores',{'employee_id':person,'period_id':period,'criterion_id':criterion,'score':82})
loan=post('loans',{'employee_id':person,'title':'Historical loan','loan_kind':'internal','amount_rials':1000001,'issued_date':'2026-11-01','status':'approved'})['id']
result.write_text(json.dumps(dict(site=site,person=person,contract=contract,document=document,period=period,loan=loan,pdf=pdf.hex())),encoding='utf-8')
'''


def projection(database, columns=None):
    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        if columns is None:
            columns = {name: [row[1] for row in conn.execute('PRAGMA table_info("'+name+'")')]
                       for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        result = {}
        for name, fields in columns.items():
            result[name] = conn.execute('SELECT '+','.join('"'+field+'"' for field in fields)+' FROM "'+name+'" ORDER BY rowid').fetchall()
    return columns, result


def test_upgrade_0121_preserves_existing_hrm_facts_and_migration_timestamps(tmp_path):
    old = tmp_path / 'source-0121';old.mkdir()
    payload = subprocess.check_output(['git','archive','v0.12.1','app','network'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        for member in archive:
            if member.isfile():
                assert '..' not in Path(member.name).parts
                target = old / member.name;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.extractfile(member).read())
    (old/'resources').mkdir()
    (old/'resources/workbook.xlsx').write_bytes(next(ROOT.glob('*.xlsx')).read_bytes())
    data = tmp_path/'data';result = tmp_path/'seed.json'
    process = subprocess.run([sys.executable,'-I','-c',SEED,str(old),str(data),str(result)],capture_output=True,text=True,timeout=90)
    assert process.returncode == 0, process.stdout+process.stderr
    ids = json.loads(result.read_text());database = data/'nikan.sqlite3'
    fields,before = projection(database)
    assert {row[0] for row in before['hrm_schema_migrations']} == {'hrm-1','hrm-0.12'}
    key = secrets.token_bytes(32);archive = tmp_path/'historical.nikbak';encrypt(database,archive,key)
    app = create_app(data,testing=True)
    assert app.extensions['hrm_upgrade_backup']['verified']
    _,after = projection(database,fields)
    for name,rows in before.items():
        if name=='hrm_schema_migrations':
            assert all(row in after[name] for row in rows)
        else:
            assert rows==after[name],name
    with closing(sqlite3.connect(database)) as conn:
        assert {r[0] for r in conn.execute('SELECT version FROM hrm_schema_migrations')} == {'hrm-1','hrm-0.12','hrm-0.13'}
        for name in ('hrm_accord_policies','hrm_accord_entries','hrm_accord_results'):
            assert conn.execute('SELECT count(*) FROM '+name).fetchone()[0] == 0
    client=app.test_client()
    assert client.post('/api/login',json={'username':'old-admin','password':'Synthetic-013-upgrade-password-123'}).status_code==200
    person=client.get('/api/hrm/people?company_id=1').json[0]
    assert person['id']==ids['person'] and person['site_id']==ids['site']
    assert client.get(f"/api/hrm/documents/{ids['document']}/file").data==bytes.fromhex(ids['pdf'])
    assert create_app(data,testing=True).extensions['hrm_upgrade_backup'] is None
    assert recovery_check(archive,key,tmp_path)['verified']
