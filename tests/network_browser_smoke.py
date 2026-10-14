"""Browser workflow over TLS; trust only the generated test server's exact public-key pin."""
import base64,hashlib,secrets,socket,subprocess,sys,tempfile,ssl,urllib.request,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from network.config import initialize
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from playwright.sync_api import sync_playwright

def main():
 with tempfile.TemporaryDirectory() as folder:
  root=Path(folder)/'server';share=Path(folder)/'share';share.mkdir()
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  config=initialize(root,'localhost',port);context=ssl.create_default_context(cafile=config['certificate']);base=f'https://localhost:{port}'
  cert=x509.load_pem_x509_certificate(Path(config['certificate']).read_bytes());public=cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo);pin=base64.b64encode(hashlib.sha256(public).digest()).decode()
  server=subprocess.Popen([sys.executable,'network_server.py','--data-dir',str(root)])
  try:
   opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context));deadline=time.monotonic()+30
   while True:
    try:opener.open(base+'/api/status',timeout=2).close();break
    except OSError:
     if time.monotonic()>deadline:raise
     time.sleep(.2)
   with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--ignore-certificate-errors-spki-list='+pin]);page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base);page.locator('#auth [name=username]').fill('network-admin');page.locator('#auth [name=password]').fill(secrets.token_urlsafe(24));page.locator('#auth button').click();page.locator('.metrics').wait_for();page.locator('[data-page=backup]').click();page.locator('#backup-settings').wait_for()
    page.locator('#backup-settings [name=mirror]').fill(str(share));page.locator('#backup-settings [name=retain]').fill('3');page.locator('#backup-settings button').click();page.locator('#notice').filter(has_text='تأیید شد').wait_for()
    page.locator('#backup-now').wait_for(state='visible');page.wait_for_function("!document.querySelector('#backup-now').disabled");page.locator('#backup-now').click();page.locator('[data-backup-download]').first.wait_for()
    page.wait_for_function("!document.querySelector('#backup-now').disabled");page.locator('[data-backup-test]').first.click();page.locator('#backup-state').filter(has_text='آزمون انتخابی بازیابی: nikan-').wait_for()
    with page.expect_download() as info:page.locator('#recovery-key').click()
    assert Path(info.value.path()).read_bytes()==(root/'keys/recovery.key').read_bytes()
    with page.expect_download() as info:page.locator('[data-backup-download]').first.click()
    assert Path(info.value.path()).read_bytes()[:8]==b'NIKANBK1'
    assert list(share.glob('*.nikbak'));assert not errors,errors
    page.screenshot(path='/tmp/nikan-network-backups.png',full_page=True);browser.close()
  finally:
   subprocess.run([sys.executable,'network_server.py','--data-dir',str(root),'--stop'],check=True)
   server.wait(timeout=40)
 print('Network browser passed: HTTPS admin setup, scheduled backup settings, mirror path, encrypted downloads, recovery test and key export; no JavaScript errors.')
if __name__=='__main__':main()
