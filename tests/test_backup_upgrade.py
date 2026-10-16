"""Authenticated pre-upgrade archives remain recoverable without relaxing counts."""
import io,secrets,sqlite3
from pathlib import Path
import pytest
from app import create_app
from network.backup import decrypt,encrypt,inspect_db,recovery_check
from recovery import restore


@pytest.fixture
def legacy_archive(tmp_path):
 root=tmp_path/'old-server';app=create_app(root,True);client=app.test_client()
 client.post('/api/setup',json={'username':'old-admin','password':'Upgrade-password-123'})
 client.post('/api/login',json={'username':'old-admin','password':'Upgrade-password-123'})
 headers={'X-CSRF-Token':client.get('/api/me').json['csrf']}
 sid=client.post('/api/competency-bank/S001/select?company_id=1',headers=headers).json['id']
 eid=client.post('/api/data/employees?company_id=1',headers=headers,json={'code':'OLD-EMP','name':'Archived employee','job':'Engineer'}).json['id']
 assert client.post('/api/data/requirements?company_id=1',headers=headers,json={'skill_id':sid,'job':'Engineer','level':3}).status_code==201
 assert client.post('/api/data/assessments?company_id=1',headers=headers,json={'employee_id':eid,'skill_id':sid,'level':1}).status_code==201
 cid=client.post('/api/data/courses?company_id=1',headers=headers,json={'skill_id':sid,'title':'Archived training','date':'1405/07/23','hours':2}).json['id']
 assert client.post('/api/data/records?company_id=1',headers=headers,json={'employee_id':eid,'course_id':cid,'level':2}).status_code==201
 assert client.put(f'/api/passports/{eid}/document?company_id=1',headers=headers,json={'overrides':{'duties':'Archived personal duties'}}).status_code==200
 assert client.post('/api/certificates?company_id=1',headers=headers,data={'employee_id':str(eid),'title':'Archived certificate','file':(io.BytesIO(b'%PDF-1.4\nArchived certificate\n%%EOF'),'old.pdf')}).status_code==201
 snapshot=tmp_path/'legacy.sqlite3'
 source=sqlite3.connect(app.config['DB']);destination=sqlite3.connect(snapshot)
 try:
  source.backup(destination)
  # Recreate the archived pre-registry schema, including its independent triggers.
  # Current passport triggers reference the counter table and cannot remain in a
  # database that intentionally models a version without that table.
  destination.executescript('''DROP TRIGGER employee_passport_created;
DROP TRIGGER employee_passport_updated;
DROP TRIGGER course_external_code_created;
DROP INDEX courses_external_code;
ALTER TABLE courses DROP COLUMN external_code;
ALTER TABLE employees DROP COLUMN passport_legacy_number;
ALTER TABLE employees DROP COLUMN passport_aliases;
ALTER TABLE employees DROP COLUMN passport_code_numbered;
ALTER TABLE personal_documents DROP COLUMN is_snapshot;
DROP TABLE course_participants;
DROP TABLE holding_catalog_state;
DROP TABLE company_registry;
DROP INDEX courses_calendar_scope;
ALTER TABLE courses DROP COLUMN calendar_scope;''')
 finally:destination.close();source.close()
 key=secrets.token_bytes(32);keyfile=tmp_path/'old-recovery.key';keyfile.write_bytes(key)
 archive=tmp_path/'old-encrypted.nikbak';header=encrypt(snapshot,archive,key)
 assert not {'holding_catalog_state','course_participants','company_registry'}&set(header['tables'])
 return archive,key,keyfile,header,eid


def test_encrypted_pre_upgrade_backup_verifies_restores_and_migrates(legacy_archive,tmp_path):
 archive,key,keyfile,header,eid=legacy_archive
 raw=tmp_path/'authenticated.sqlite3'
 assert decrypt(archive,raw,key)['tables']==header['tables']
 assert inspect_db(raw)==header['tables']
 report=recovery_check(archive,key,tmp_path)
 assert report['verified'] and report['tables']==header['tables']
 target=tmp_path/'restored'
 assert restore(archive,keyfile,target)['restored']
 app=create_app(target,True)
 migrated=inspect_db(app.config['DB'])
 assert {name:migrated[name] for name in header['tables']}==header['tables']
 assert migrated['holding_catalog_state']==1 and migrated['course_participants']==0 and migrated['company_registry']==1
 client=app.test_client()
 assert client.post('/api/login',json={'username':'old-admin','password':'Upgrade-password-123'}).status_code==200
 assert client.get('/api/data/employees?company_id=1').json[0]['name']=='Archived employee'
 assert client.get('/api/data/assessments?company_id=1').json[0]['level']==1
 assert client.get('/api/data/records?company_id=1').json[0]['level']==2
 assert client.get('/api/data/courses?company_id=1').json[0]['calendar_scope']=='holding'
 assert client.get(f'/api/passports/{eid}/document?company_id=1').json['overrides']['duties']=='Archived personal duties'
 certid=client.get('/api/certificates?company_id=1').json[0]['id']
 assert client.get(f'/api/certificates/{certid}?company_id=1').data==b'%PDF-1.4\nArchived certificate\n%%EOF'


@pytest.mark.parametrize('change',['archived_count','unexpected_table','unexpected_marker','nonempty_participants','unexpected_company_registry','empty_company_registry'])
def test_recovery_rejects_unsupported_or_destructive_migration(legacy_archive,tmp_path,monkeypatch,change):
 archive,key,keyfile,header,eid=legacy_archive
 import app as app_module
 real_create=app_module.create_app
 def invalid_migration(folder,testing=False):
  migrated_app=real_create(folder,testing)
  with sqlite3.connect(Path(folder)/'nikan.sqlite3') as conn:
   if change=='archived_count':conn.execute("INSERT INTO employees(company_id,code,name) VALUES (1,'UNEXPECTED','Unexpected employee')")
   elif change=='unexpected_table':conn.execute('CREATE TABLE unexpected_data(note TEXT)')
   elif change=='unexpected_marker':conn.execute("UPDATE holding_catalog_state SET value='unexpected'")
   elif change=='nonempty_participants':conn.execute('INSERT INTO course_participants(course_id,employee_id) SELECT c.id,e.id FROM courses c CROSS JOIN employees e LIMIT 1')
   elif change=='unexpected_company_registry':conn.execute('UPDATE company_registry SET high_watermark=999')
   else:conn.execute('DELETE FROM company_registry')
  return migrated_app
 monkeypatch.setattr(app_module,'create_app',invalid_migration)
 with pytest.raises(ValueError):recovery_check(archive,key,tmp_path)
 target=tmp_path/'rejected'
 with pytest.raises(ValueError):restore(archive,keyfile,target)
 assert not (target/'nikan.sqlite3').exists()


def test_pre_upgrade_archive_authentication_is_still_required(legacy_archive,tmp_path):
 archive,key,keyfile,header,eid=legacy_archive
 data=bytearray(archive.read_bytes());data[-20]^=1
 damaged=tmp_path/'damaged.nikbak';damaged.write_bytes(data)
 for candidate,candidate_key in ((damaged,key),(archive,secrets.token_bytes(32))):
  with pytest.raises(ValueError):recovery_check(candidate,candidate_key,tmp_path)
