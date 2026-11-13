import io,base64,sqlite3,struct,zlib
import pytest
from app import create_app
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nO8AAAAASUVORK5CYII=')
def icon_png():
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',256,256,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(256*4))*256))+chunk(b'IEND',b'')

def test_logo_upload_cannot_recreate_deleted_company_branding(tmp_path,monkeypatch):
 from werkzeug.datastructures import FileStorage
 app=create_app(tmp_path,True);client=app.test_client()
 client.post('/api/setup',json={'username':'admin','password':'Secure-password-123'})
 client.post('/api/login',json={'username':'admin','password':'Secure-password-123'})
 headers={'X-CSRF-Token':client.get('/api/me').json['csrf']}
 company=client.post('/api/companies',headers=headers,json={'name':'Company deleted during upload'}).json['id']
 def delete_during_read(storage,*args,**kwargs):
  if storage.filename=='race.png':
   with sqlite3.connect(app.config['DB']) as conn:
    conn.execute('PRAGMA foreign_keys=ON')
    conn.execute('DELETE FROM companies WHERE id=?',(company,))
  return storage.stream.read(*args,**kwargs)
 monkeypatch.setattr(FileStorage,'read',delete_during_read,raising=False)
 response=client.post(f'/api/branding/company-{company}',headers=headers,data={'file':(io.BytesIO(PNG),'race.png')})
 assert response.status_code==400 and response.is_json
 assert client.get(f'/api/branding/logo/company-{company}').status_code==404
 assert client.post('/api/branding/company-9223372036854775808',headers=headers,data={'file':(io.BytesIO(PNG),'large-id.png')}).status_code==400

def test_branding_preserves_original_backup_and_permissions(tmp_path):
 app=create_app(tmp_path,True);c=app.test_client();c.post('/api/setup',json={'username':'admin','password':'Secure-password-123'});c.post('/api/login',json={'username':'admin','password':'Secure-password-123'});h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
 assert c.post('/api/branding/holding',headers=h,data={'file':(io.BytesIO(b'<svg>'),'logo.svg')}).status_code==400
 r=c.post('/api/branding/holding',headers=h,data={'file':(io.BytesIO(PNG),'logo.png'),'icon':(io.BytesIO(icon_png()),'icon.png')});assert r.status_code==200,r.json
 assert (tmp_path/'holding.ico').read_bytes()[:6]==b'\0\0\1\0\1\0'
 assert c.get('/api/branding/logo/holding').data==PNG
 assert c.post('/api/branding/company-1',headers=h,data={'file':(io.BytesIO(PNG),'company.png')}).status_code==200
 assert c.post('/api/branding/company-100',headers=h,data={'file':(io.BytesIO(PNG),'company.png')}).status_code==400
 backup=tmp_path/'backup.sqlite3';backup.write_bytes(c.post('/api/backup',headers=h).data)
 d=sqlite3.connect(backup)
 try:assert d.execute("SELECT content FROM branding WHERE key='holding'").fetchone()[0]==PNG
 finally:d.close()
 c.post('/api/users',headers=h,json={'username':'viewer','password':'Secure-password-123','role':'viewer','company_id':1});c.post('/api/logout',headers=h);c.post('/api/login',json={'username':'viewer','password':'Secure-password-123'});h={'X-CSRF-Token':c.get('/api/me').json['csrf']}
 assert c.post('/api/branding/holding',headers=h,data={'file':(io.BytesIO(PNG),'logo.png')}).status_code==403
