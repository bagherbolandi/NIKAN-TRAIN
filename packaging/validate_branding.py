"""Native test-profile branding; never select the original desktop database."""
import argparse
import io
import os
from pathlib import Path
import struct
import sys
import zlib

parser = argparse.ArgumentParser()
parser.add_argument('--install', type=Path, required=True)
args = parser.parse_args()
install = args.install.resolve()
sys.path.insert(0, str(install))
data = Path(os.environ['LOCALAPPDATA']) / 'NikanHRM-Test'
os.environ['NIKAN_DATA_DIR'] = str(data)
os.environ['NIKAN_DESKTOP_PROFILE'] = 'NikanHRM-Test'
from app import create_app


def chunk(kind, value):
    return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff)


png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 256, 8, 6, 0, 0, 0))
       + chunk(b'IDAT', zlib.compress((b'\0' + bytes(256 * 4)) * 256)) + chunk(b'IEND', b''))
app = create_app(data, testing=True)
client = app.test_client()
if client.get('/api/status').json['setup_required']:
    assert client.post('/api/setup', json={'username': 'branding-validation', 'password': 'Temporary-test-password-123'}).status_code == 200
assert client.post('/api/login', json={'username': 'branding-validation', 'password': 'Temporary-test-password-123'}).status_code == 200
headers = {'X-CSRF-Token': client.get('/api/me').json['csrf']}
response = client.post('/api/branding/holding', headers=headers,
                       data={'file': (io.BytesIO(png), 'logo.png'), 'icon': (io.BytesIO(png), 'icon.png')})
assert response.status_code == 200, response.json
assert response.json['icon_updated'], response.json
assert client.get('/api/branding/logo/holding').data == png
assert (data / 'holding.ico').is_file()
print('Test-profile branding and original image round trip passed; data is isolated in NikanHRM-Test.')
