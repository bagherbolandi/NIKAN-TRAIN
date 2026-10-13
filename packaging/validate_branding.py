"""Native installer test: branding updates existing Windows shortcuts."""
from pathlib import Path
import sys,struct,zlib,io
sys.path.insert(0,str(Path(__file__).resolve().parent))
from app import create_app

def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',256,256,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(256*4))*256))+chunk(b'IEND',b'')
app=create_app(testing=True);c=app.test_client()
assert c.post('/api/setup',json={'username':'branding-validation','password':'Temporary-test-password-123'}).status_code==200
assert c.post('/api/login',json={'username':'branding-validation','password':'Temporary-test-password-123'}).status_code==200
h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
r=c.post('/api/branding/holding',headers=h,data={'file':(io.BytesIO(png),'logo.png'),'icon':(io.BytesIO(png),'icon.png')})
assert r.status_code==200,r.json;assert r.json['icon_updated'],r.json
assert c.get('/api/branding/logo/holding').data==png
print('Original branding and Windows shortcut icon update passed.')
