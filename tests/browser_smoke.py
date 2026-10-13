"""Optional real-browser smoke test; requires Playwright and a local Chromium."""
from playwright.sync_api import sync_playwright
import secrets, os, socket, subprocess, tempfile, time, urllib.request, sys
from pathlib import Path

def check(base):
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(base);page.locator('#auth-title').filter(has_text='ساخت حساب').wait_for()
  page.locator('#auth [name=username]').fill('smoke-admin');page.locator('#auth [name=password]').fill(secrets.token_urlsafe(20));page.locator('#auth button').click();page.locator('#shell').wait_for(state='visible');page.locator('.metrics').wait_for()
  assert not page.locator('#login').is_visible()
  assert page.locator('#company option').count()==6
  page.locator('[data-page=employees]').click();page.locator('#add').click();page.locator('#entry [name=code]').fill('E001');page.locator('#entry [name=name]').fill('کاربر آزمایشی');page.locator('#entry [name=job]').fill('کارشناس آموزش');page.locator('#entry button').last.click();page.locator('#table').get_by_text('کاربر آزمایشی').wait_for()
  page.locator('#company').select_option('2');page.locator('.empty').wait_for();assert page.locator('#table').get_by_text('کاربر آزمایشی').count()==0
  page.locator('#company').select_option('1');page.locator('[data-page=dashboard]').click();page.locator('.metrics').wait_for();page.screenshot(path=os.environ.get('NIKAN_SCREENSHOT','/tmp/nikan-dashboard.png'),full_page=True)
  page.locator('[data-page=courses]').click();page.locator('.month-card').first.wait_for();assert page.locator('.month-card').count()==12
  page.locator('[data-page=users]').click();page.locator('#add').click();page.locator('#entry [name=username]').fill('company-manager');page.locator('#entry [name=password]').fill(secrets.token_urlsafe(20));page.locator('#entry button').last.click();page.locator('.company-row').filter(has_text='company-manager').get_by_text('تغییر رمز').click();newpass=secrets.token_urlsafe(20);page.locator('#entry [name=password]').fill(newpass);page.locator('#entry button').last.click();page.locator('#dialog').wait_for(state='hidden');page.locator('#logout').click();page.locator('#auth [name=username]').fill('company-manager');page.locator('#auth [name=password]').fill(newpass);page.locator('#auth button').click();page.locator('.metrics').wait_for();assert page.locator('#company option').count()==1;assert page.locator('[data-page=users]').count()==0
  assert not errors,errors
  page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
  print('Browser smoke passed: initial setup, login, six companies, employee creation, company isolation, calendar, mobile layout; no JavaScript errors.')
  browser.close()

if __name__ == '__main__':
 with tempfile.TemporaryDirectory(prefix='nikan-browser-') as data:
  with socket.socket() as sock:
   sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  base=f'http://127.0.0.1:{port}'
  env={**os.environ,'NIKAN_DATA_DIR':data}
  root=Path(__file__).resolve().parents[1]
  process=subprocess.Popen([sys.executable,'run.py','--no-browser','--port',str(port)],cwd=root,env=env,stdout=subprocess.DEVNULL)
  try:
   for attempt in range(60):
    try:
     urllib.request.urlopen(base+'/api/status',timeout=1).close();break
    except OSError:
     if process.poll() is not None:raise RuntimeError('Server exited')
     time.sleep(.1)
   else:raise RuntimeError('Server did not become ready')
   check(base)
  finally:
   process.terminate()
   try:process.wait(timeout=5)
   except subprocess.TimeoutExpired:process.kill();process.wait()
