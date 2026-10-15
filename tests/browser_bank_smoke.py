"""Optional Chromium smoke for holding-bank ownership and company selection."""
import os,secrets,socket,subprocess,sys,tempfile,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright,expect


def check(base):
 with sync_playwright() as p:
  browser=p.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
  page=browser.new_page(viewport={'width':1440,'height':1000})
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  passwords={role:secrets.token_urlsafe(20) for role in ('admin','manager','viewer')}
  page.goto(base)
  page.locator('#auth-title').filter(has_text='ساخت حساب').wait_for()
  def login(role):
   page.locator('#auth [name=username]').fill('bank-'+role)
   page.locator('#auth [name=password]').fill(passwords[role])
   page.locator('#auth button').click()
   page.locator('.metrics').wait_for()
  def headers():return {'X-CSRF-Token':page.request.get(base+'/api/me').json()['csrf']}
  def open_bank(code=None):
   page.locator('[data-page=bank]').click()
   page.locator('#bank-search').wait_for()
   if code:page.locator('#bank-search').fill(code)
  def new_competency(code,title):
   page.locator('#bank-new').click()
   page.locator('#entry [name=code]').fill(code)
   page.locator('#entry [name=title]').fill(title)
   page.locator('#entry [name=category]').select_option(index=1)
   page.locator('#entry button').last.click()
   page.locator('#notice').filter(has_text='تعریف شد').wait_for()
  login('admin')
  for role in ('manager','viewer'):
   response=page.request.post(base+'/api/users',headers=headers(),data={'username':'bank-'+role,'password':passwords[role],'role':role,'company_id':1})
   assert response.status==201,response.text()
  open_bank()
  new_competency('BROWSER-FREE','شایستگی قابل حذف')
  page.locator('#bank-search').fill('BROWSER-FREE')
  page.locator('[data-bank-edit="BROWSER-FREE"]').click()
  expect(page.locator('#entry [name=code]')).to_have_attribute('readonly','')
  page.locator('#entry [name=title]').fill('شایستگی اصلاح‌شده')
  page.locator('#entry button').last.click()
  page.locator('#notice').filter(has_text='به‌روزرسانی').wait_for()
  page.locator('#bank-search').fill('BROWSER-FREE')
  page.locator('[data-bank-select="BROWSER-FREE"]').click()
  expect(page.locator('[data-bank-select="BROWSER-FREE"]')).to_be_disabled()
  page.once('dialog',lambda dialog:dialog.accept())
  page.locator('[data-bank-delete="BROWSER-FREE"]').click()
  page.locator('#notice').filter(has_text='حذف شد').wait_for()
  page.locator('#bank-search').fill('BROWSER-FREE')
  expect(page.locator('#bank-table tbody tr')).to_have_count(0)
  new_competency('BROWSER-USED','شایستگی مورد استفاده شرکت')
  page.locator('#logout').click();login('manager');open_bank('BROWSER-USED')
  expect(page.locator('#bank-new')).to_have_count(0)
  expect(page.locator('[data-bank-edit],[data-bank-delete]')).to_have_count(0)
  page.locator('[data-bank-select="BROWSER-USED"]').click()
  page.locator('#notice').filter(has_text='انتخاب شد').wait_for()
  expect(page.locator('[data-bank-select="BROWSER-USED"]')).to_be_disabled()
  skills=page.request.get(base+'/api/data/skills?company_id=1').json()
  sid=next(r['id'] for r in skills if r['code']=='BROWSER-USED')
  response=page.request.post(base+'/api/data/requirements?company_id=1',headers=headers(),data={'job':'Browser job','skill_id':sid,'level':3})
  assert response.status==201,response.text()
  page.screenshot(path='/tmp/nikan-bank-company-selection.png',full_page=True)
  page.locator('#logout').click();login('viewer');open_bank('BROWSER-USED')
  expect(page.locator('#bank-new,#adopt-bank,[data-bank-edit],[data-bank-delete],[data-bank-select]')).to_have_count(0)
  expect(page.locator('#bank-table')).to_contain_text('شایستگی مورد استفاده شرکت')
  page.locator('#logout').click();login('admin');open_bank('BROWSER-USED')
  page.once('dialog',lambda dialog:dialog.accept())
  page.locator('[data-bank-delete="BROWSER-USED"]').click()
  page.locator('#notice').filter(has_text='قابل حذف نیست').wait_for()
  expect(page.locator('[data-bank-edit="BROWSER-USED"]')).to_be_visible()
  assert page.request.get(base+'/api/data/requirements?company_id=1').json()[0]['skill_id']==sid
  page.screenshot(path='/tmp/nikan-bank-admin-preserve-history.png',full_page=True)
  assert not errors,errors
  browser.close()
 print('Bank browser passed: admin add/edit/delete, immutable code, company selection, viewer read-only, in-use deletion rejection; no JavaScript errors.')


if __name__=='__main__':
 with tempfile.TemporaryDirectory(prefix='nikan-bank-browser-') as folder:
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  base=f'http://127.0.0.1:{port}'
  root=Path(__file__).resolve().parents[1]
  server=subprocess.Popen([sys.executable,'run.py','--no-browser','--port',str(port)],cwd=root,env={**os.environ,'NIKAN_DATA_DIR':folder},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  try:
   for attempt in range(100):
    try:urllib.request.urlopen(base+'/api/status',timeout=1).close();break
    except OSError:
     if server.poll() is not None:raise RuntimeError('Server exited')
     time.sleep(.1)
   else:raise RuntimeError('Server did not become ready')
   check(base)
  finally:
   server.terminate()
   try:server.wait(timeout=5)
   except subprocess.TimeoutExpired:server.kill();server.wait()
