"""Offline recovery. Refuses a running server and makes a verified safety backup before replacement."""
import argparse,os,secrets,sys,tempfile
from pathlib import Path
BASE=Path(__file__).resolve().parent
if str(BASE) not in sys.path:sys.path.insert(0,str(BASE))
from network.storage import FileLock,atomic_write
from network.backup import BackupManager,decrypt,recovery_check

def restore(archive,key_file,target,replace=False):
 target=Path(target);target.mkdir(parents=True,exist_ok=True);key=Path(key_file).read_bytes()
 if len(key)!=32:raise ValueError('کلید بازیابی نامعتبر است')
 with FileLock(target/'server.lock'):
  database=target/'nikan.sqlite3'
  if database.exists() and not replace:raise ValueError('پایگاه مقصد موجود است؛ برای جایگزینی کنترل‌شده --replace لازم است')
  # Authenticate, check every table and boot the restored app before touching the live database.
  report=recovery_check(archive,key,target)
  safety=None
  with tempfile.TemporaryDirectory(prefix='restore-',dir=target) as temp:
   candidate=Path(temp)/'nikan.sqlite3';decrypt(archive,candidate,key)
   if database.exists():safety=BackupManager(target).run('before-restore')
   with FileLock(target/'backup.lock'):
    for suffix in ('-wal','-shm'):(target/('nikan.sqlite3'+suffix)).unlink(missing_ok=True)
    os.replace(candidate,database)
    # Invalidate old browser sessions after rollback.
    atomic_write(target/'session.key',secrets.token_hex(32).encode())
    if not (target/'keys'/'recovery.key').exists():atomic_write(target/'keys'/'recovery.key',key)
  return dict(restored=True,safety_backup=safety,verification=report)

def import_database(source,target,replace=False):
 """Migrate a standalone SQLite backup through the same authenticated recovery path."""
 import sqlite3
 from network.backup import encrypt
 from app import create_app
 source=Path(source)
 if not source.is_file():raise ValueError('فایل پایگاه مستقل یافت نشد')
 if source.resolve()==(Path(target)/'nikan.sqlite3').resolve():raise ValueError('مبدأ و مقصد یکسان هستند')
 with tempfile.TemporaryDirectory(prefix='nikan-migration-') as folder:
  folder=Path(folder);snapshot=folder/'nikan.sqlite3'
  conn=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True);out=sqlite3.connect(snapshot)
  try:conn.backup(out)
  finally:out.close();conn.close()
  create_app(folder,True)
  key=secrets.token_bytes(32);keyfile=folder/'migration.key';atomic_write(keyfile,key)
  archive=folder/'migration.nikbak';encrypt(snapshot,archive,key)
  return restore(archive,keyfile,target,replace)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('archive',type=Path);parser.add_argument('--key',type=Path);parser.add_argument('--import-sqlite',action='store_true');parser.add_argument('--target',type=Path,required=True);parser.add_argument('--replace',action='store_true');args=parser.parse_args()
 import json
 try:
  if args.import_sqlite:result=import_database(args.archive,args.target,args.replace)
  else:
   if not args.key:parser.error('--key is required for encrypted backups')
   result=restore(args.archive,args.key,args.target,args.replace)
  print(json.dumps(result,ensure_ascii=True,indent=2))
 except Exception as exc:print(str(exc),file=sys.stderr);sys.exit(1)
