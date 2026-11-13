"""Private server configuration and initial TLS identity."""
import ipaddress,socket,re,json,secrets
from pathlib import Path
from datetime import datetime,timedelta,timezone
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from .storage import atomic_write,read_json,write_json

def default_names():
 names={'localhost','127.0.0.1','::1',socket.gethostname().lower(),socket.getfqdn().lower()}
 try:
  for r in socket.getaddrinfo(socket.gethostname(),None):names.add(r[4][0])
 except OSError:pass
 return sorted(names)

def initialize(root,hostname=None,port=8766,mirror='',upgrade_legacy_http=False):
 root=Path(root)
 if __import__('os').name=='nt':
  import ctypes
  if str(root).startswith('\\\\') or ctypes.windll.kernel32.GetDriveTypeW(str(root.resolve().anchor))==4:raise ValueError('پایگاه مرکزی باید روی دیسک محلی سرور باشد')
 root.mkdir(parents=True,exist_ok=True);path=root/'server.json'
 legacy=None
 if path.exists():
  try:return load(root)
  except (ValueError,KeyError):
   if not upgrade_legacy_http:raise
   old=json.loads(path.read_text(encoding='utf-8-sig'))
   if (not isinstance(old,dict) or set(old)-{'host','port','https'} or
       old.get('https') is not False or old.get('host') not in ('0.0.0.0','127.0.0.1','::1') or
       type(old.get('port')) is not int or not 1024<=old['port']<=65535):
    raise ValueError('تنظیمات موجود قابل ارتقای خودکار از HTTP نیست؛ تنظیمات و هویت TLS موجود تغییر نکرد')
   if any((root/'tls'/name).exists() for name in ('server.key','server.pem','server.cer')):
    raise ValueError('هویت TLS موجود نیازمند بررسی است؛ فایل‌ها بازنویسی نشدند')
   legacy=old;port=old['port']
 hostname=(hostname or socket.gethostname()).strip().lower()
 if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',hostname):raise ValueError('نام میزبان شبکه معتبر نیست')
 if type(port) is not int or not 1024<=port<=65535:raise ValueError('پورت باید بین ۱۰۲۴ و ۶۵۵۳۵ باشد')
 if legacy is not None:
  backup=root/('server-http-before-0.12-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'-'+secrets.token_hex(4)+'.json')
  atomic_write(backup,path.read_bytes())
 from .backup import BackupManager
 manager=BackupManager(root)
 if mirror:manager.configure(dict(enabled=True,time='02:00',retain=30,mirror=mirror))
 names=sorted(set(default_names()+[hostname]));tls=root/'tls';tls.mkdir(exist_ok=True)
 key=rsa.generate_private_key(public_exponent=65537,key_size=3072)
 name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,hostname)])
 san=[]
 for v in names:
  try:san.append(x509.IPAddress(ipaddress.ip_address(v)))
  except ValueError:san.append(x509.DNSName(v))
 now=datetime.now(timezone.utc)
 cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365)).add_extension(x509.SubjectAlternativeName(san),critical=False).add_extension(x509.BasicConstraints(ca=False,path_length=None),critical=True).add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),critical=False).sign(key,hashes.SHA256())
 atomic_write(tls/'server.key',key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
 atomic_write(tls/'server.pem',cert.public_bytes(serialization.Encoding.PEM));atomic_write(tls/'server.cer',cert.public_bytes(serialization.Encoding.DER))
 config=dict(host='0.0.0.0',port=port,hostname=hostname,allowed_hosts=names,certificate=str(tls/'server.pem'),private_key=str(tls/'server.key'),threads=16)
 if legacy is not None and legacy['host'] in ('127.0.0.1','::1'):config['host']='127.0.0.1'
 write_json(path,config)
 return config

def load(root):
 c=read_json(Path(root)/'server.json')
 if not isinstance(c,dict) or c.get('host') not in ('0.0.0.0','127.0.0.1') or type(c.get('port')) is not int or not 1024<=c['port']<=65535 or not isinstance(c.get('allowed_hosts'),list) or not c['allowed_hosts']:raise ValueError('تنظیمات سرور شبکه معتبر نیست')
 if not all(isinstance(c.get(key),str) and c[key] for key in ('hostname','certificate','private_key')):
  raise ValueError('هویت HTTPS سرور کامل نیست')
 return c
