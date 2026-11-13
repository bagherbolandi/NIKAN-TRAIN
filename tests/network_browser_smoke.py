"""Browser workflow over TLS; trust only the generated test server's exact public-key pin."""
import base64,hashlib,secrets,socket,subprocess,sys,tempfile,ssl,urllib.request,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from network.config import initialize
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from playwright.sync_api import sync_playwright,expect

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
    page.goto(base);page.locator('#auth [name=username]').fill('network-admin');page.locator('#auth [name=password]').fill(secrets.token_urlsafe(24));page.locator('#auth button').click();page.locator('.metrics').wait_for()
    writes=[]
    page.on('request',lambda request:writes.append((request.method,request.url)) if request.method in ('POST','PUT') else None)
    def count(method,path):return sum(m==method and url==base+'/api/'+path for m,url in writes)
    def hold_writes(pattern):
     held=[]
     def intercept(route):
      if route.request.method in ('POST','PUT'):held.append(route)
      else:route.continue_()
     page.route(pattern,intercept)
     return held
    def submit_twice(selector):
     page.evaluate("selector=>{const form=document.querySelector(selector);form.requestSubmit();form.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true}))}",selector)
    def click_twice(selector):
     page.evaluate("selector=>{const button=document.querySelector(selector);button.dispatchEvent(new MouseEvent('click',{bubbles:true}));button.dispatchEvent(new MouseEvent('click',{bubbles:true}))}",selector)
    def blocked_navigation(expected):
     page.evaluate("void navigateTo('employees',2)")
     assert page.evaluate('({page,cid})')=={'page':expected,'cid':1}

    # Supplemental logo errors cannot discard a successfully rendered working page.
    page.route('**/api/branding',lambda route:route.fulfill(status=503,json={'error':'آزمون موقت لوگو'}))
    page.locator('[data-page=dashboard]').click();page.locator('.metrics').wait_for()
    assert page.locator('#shell').is_visible() and page.locator('#retry-page').count()==0
    page.unroute('**/api/branding')
    logo=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nO8AAAAASUVORK5CYII=')
    page.locator('[data-page=branding]').click()
    page.locator('#branding-file').set_input_files({'name':'holding.png','mimeType':'image/png','buffer':logo});page.locator('#branding-preview').wait_for(state='visible')
    held=hold_writes('**/api/branding/holding')
    with page.expect_request('**/api/branding/holding'):submit_twice('#branding-form')
    expect(page.locator('#branding-form [type=submit]')).to_be_disabled()
    assert len(held)==1 and count('POST','branding/holding')==1,(len(held),count('POST','branding/holding'))
    blocked_navigation('branding')
    held[0].continue_();page.unroute('**/api/branding/holding');page.locator('#notice').filter(has_text='لوگو ثبت شد').wait_for()
    expect(page.locator('.logo .brand-mark img')).to_have_count(1)
    page.locator('#branding-key').select_option('company-1');page.locator('#branding-file').set_input_files({'name':'company.png','mimeType':'image/png','buffer':logo});page.locator('#branding-preview').wait_for(state='visible')
    page.locator('#branding-form [type=submit]').click();page.locator('#notice').filter(has_text='لوگو ثبت شد').wait_for();expect(page.locator('#company-logo')).to_be_visible()
    # A successful empty asset list removes old logo and favicon references.
    page.route('**/api/branding',lambda route:route.fulfill(json=[],content_type='application/json'))
    page.locator('[data-page=dashboard]').click();page.locator('.metrics').wait_for();expect(page.locator('.logo .brand-mark img')).to_have_count(0)
    expect(page.locator('#company-logo')).to_be_hidden();assert page.locator('#company-logo').get_attribute('src') is None
    assert page.locator('link[rel=icon]').count()==0
    page.unroute('**/api/branding')

    # Polling resumes after a temporary status failure, without reentering the page.
    failures=[]
    def flaky_status(route):
     if route.request.method=='GET' and not failures:
      failures.append(True);route.fulfill(status=503,json={'error':'آزمون قطع موقت وضعیت پشتیبان'})
     else:route.continue_()
    page.route('**/api/network/backups',flaky_status)
    page.locator('[data-page=backup]').click();page.locator('#backup-settings').wait_for();page.locator('#notice').filter(has_text='آزمون قطع موقت').wait_for()
    expect(page.locator('#backup-state')).to_contain_text('آخرین پشتیبان سالم',timeout=10000);page.unroute('**/api/network/backups')
    page.locator('#backup-settings [name=mirror]').fill(str(share));page.locator('#backup-settings [name=retain]').fill('3')
    held=hold_writes('**/api/network/settings')
    with page.expect_request('**/api/network/settings'):submit_twice('#backup-settings')
    expect(page.locator('#backup-settings [type=submit]')).to_be_disabled()
    assert len(held)==1 and count('PUT','network/settings')==1,(len(held),count('PUT','network/settings'));blocked_navigation('backup')
    held[0].continue_();page.unroute('**/api/network/settings');page.locator('#notice').filter(has_text='تأیید شد').wait_for()
    expect(page.locator('#backup-now')).to_be_enabled(timeout=30000)
    held=hold_writes('**/api/network/backups')
    with page.expect_request('**/api/network/backups'):click_twice('#backup-now')
    expect(page.locator('#backup-now')).to_be_disabled()
    assert len(held)==1 and count('POST','network/backups')==1,(len(held),count('POST','network/backups'));blocked_navigation('backup')
    held[0].continue_();page.unroute('**/api/network/backups');page.locator('[data-backup-download]').first.wait_for()
    expect(page.locator('#backup-now')).to_be_enabled(timeout=30000)
    held=hold_writes('**/api/network/verify')
    with page.expect_request('**/api/network/verify'):click_twice('[data-backup-test]')
    expect(page.locator('[data-backup-test]').first).to_be_disabled()
    assert len(held)==1 and count('POST','network/verify')==1,(len(held),count('POST','network/verify'));blocked_navigation('backup')
    held[0].continue_();page.unroute('**/api/network/verify');page.locator('#backup-state').filter(has_text='آزمون انتخابی بازیابی: nikan-').wait_for()
    with page.expect_download() as info:page.locator('#recovery-key').click()
    assert Path(info.value.path()).read_bytes()==(root/'keys/recovery.key').read_bytes()
    with page.expect_download() as info:page.locator('[data-backup-download]').first.click()
    assert Path(info.value.path()).read_bytes()[:8]==b'NIKANBK1'
    assert list(share.glob('*.nikbak'));assert not errors,errors
    page.screenshot(path='/tmp/nikan-network-backups.png',full_page=True);browser.close()
  finally:
   subprocess.run([sys.executable,'network_server.py','--data-dir',str(root),'--stop'],check=True)
   server.wait(timeout=40)
 print('Network browser passed: HTTPS admin setup, recoverable supplemental-logo/poll failures, stale-logo reset, duplicate-write and navigation guards for branding/settings/backup/verify, encrypted mirror/downloads and recovery-key export; no JavaScript errors.')
if __name__=='__main__':main()
