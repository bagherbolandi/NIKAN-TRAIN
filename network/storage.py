"""Atomic files and process locks, including on Windows."""
import json,os,secrets
from pathlib import Path

def atomic_write(path,data):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
 tmp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
 try:
  with tmp.open('xb') as out:
   out.write(data);out.flush();os.fsync(out.fileno())
  tmp.chmod(0o600);os.replace(tmp,path)
 finally:tmp.unlink(missing_ok=True)

def write_json(path,data):atomic_write(path,json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))

def read_json(path,default=None):
 try:return json.loads(Path(path).read_text(encoding='utf-8'))
 except FileNotFoundError:return default

class FileLock:
 def __init__(self,path):self.path=Path(path);self.file=None
 def acquire(self):
  self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b')
  if self.file.tell()==0:self.file.write(b'0');self.file.flush()
  self.file.seek(0)
  try:
   if os.name=='nt':
    import msvcrt
    msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
   else:
    import fcntl
    fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except (OSError,BlockingIOError):self.file.close();self.file=None;raise RuntimeError('سرور یا عملیات دیگری فعال است؛ ابتدا آن را متوقف کنید')
  return self
 def close(self):
  if self.file:
   if os.name=='nt':
    import msvcrt
    self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
   self.file.close();self.file=None
 def __enter__(self):return self.acquire()
 def __exit__(self,*_):self.close()
