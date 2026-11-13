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
  # A failed bank POST must leave the populated form open for a retry.
  page.locator('#bank-new').click()
  page.locator('#entry [name=code]').fill('BROWSER-DRAFT')
  page.locator('#entry [name=title]').fill('حفظ فرم هنگام خطای شبکه')
  page.route('**/api/competency-bank',lambda route:route.fulfill(status=503,json={'error':'اختلال آزمایشی؛ فرم حفظ شود'}))
  page.locator('#entry button:not([type=button])').click()
  expect(page.locator('#form-error')).to_contain_text('اختلال آزمایشی')
  expect(page.locator('#dialog')).to_be_visible()
  expect(page.locator('#entry [name=title]')).to_have_value('حفظ فرم هنگام خطای شبکه')
  page.unroute('**/api/competency-bank')
  page.locator('#entry button:not([type=button])').click()
  page.locator('#notice').filter(has_text='تعریف شد').wait_for()
  new_competency('BROWSER-FREE','شایستگی قابل حذف')
  page.locator('#bank-search').fill('BROWSER-FREE')
  page.locator('[data-bank-edit="BROWSER-FREE"]').click()
  expect(page.locator('#entry [name=code]')).to_have_attribute('readonly','')
  page.locator('#entry [name=title]').fill('شایستگی اصلاح‌شده')
  page.locator('#entry button').last.click()
  page.locator('#notice').filter(has_text='به‌روزرسانی').wait_for()
  page.locator('#bank-search').fill('BROWSER-FREE')
  pending=[]
  page.route('**/api/competency-bank/BROWSER-FREE/select?company_id=1',lambda route:pending.append(route))
  choose=page.locator('[data-bank-select="BROWSER-FREE"]')
  choose.click()
  expect(choose).to_be_disabled()
  choose.dispatch_event('click')
  page.locator('[data-page=employees]').click()
  assert page.evaluate('page')=='bank'
  page.locator('#company').select_option('2')
  expect(page.locator('#company')).to_have_value('1')
  assert len(pending)==1,'A repeated selection generated another mutation'
  pending[0].continue_()
  page.unroute('**/api/competency-bank/BROWSER-FREE/select?company_id=1')
  expect(page.locator('[data-bank-select="BROWSER-FREE"]')).to_be_disabled()
  page.locator('#notice').filter(has_text='انتخاب شد').wait_for()
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
  page.locator('[data-page=requirements]').click()
  page.locator('[data-job="Browser job"]').click()
  page.locator('#job-matrix .category-section summary').first.click()
  level=page.locator('#job-matrix [data-level="BROWSER-USED"]')
  level.select_option('2')
  page.once('dialog',lambda dialog:dialog.dismiss())
  page.locator('#new-job').click()
  expect(page.locator('#dialog')).not_to_be_visible()
  expect(level).to_have_value('2')
  assert page.evaluate('pendingWorkspace')==1
  page.once('dialog',lambda dialog:dialog.accept())
  page.locator('#new-job').click()
  expect(page.locator('#dialog')).to_be_visible()
  page.locator('#close-dialog').click()
  expect(page.locator('#dialog')).not_to_be_visible()
  expect(level).to_have_value('2')
  assert page.evaluate('pendingWorkspace')==1,'Cancelling a new-job form discarded the old job draft'
  page.route('**/api/job-workspace?**',lambda route:route.fulfill(status=503,json={'error':'اختلال آزمایشی؛ تغییرات حفظ شوند'}) if route.request.method=='PUT' else route.continue_())
  page.locator('#save-job').click()
  page.locator('#notice').filter(has_text='اختلال آزمایشی').wait_for()
  expect(level).to_have_value('2')
  expect(page.locator('#save-job')).to_be_enabled()
  assert page.evaluate('pendingWorkspace')==1
  assert page.request.get(base+'/api/data/requirements?company_id=1').json()[0]['level']==3
  page.unroute('**/api/job-workspace?**')
  pending=[]
  page.route('**/api/job-workspace?**',lambda route:pending.append(route) if route.request.method=='PUT' else route.continue_())
  page.locator('#save-job').click()
  expect(level).to_be_disabled()
  page.locator('#save-job').dispatch_event('click')
  page.locator('#logout').click()
  assert page.evaluate('page')=='requirements'
  assert page.evaluate('!!user')
  assert len(pending)==1,'A repeated save generated another mutation'
  pending[0].continue_()
  page.unroute('**/api/job-workspace?**')
  page.locator('#notice').filter(has_text='تراکنش ذخیره شد').wait_for()
  expect(page.locator('#save-job')).to_be_disabled()
  assert page.evaluate('pendingWorkspace')==0
  assert page.request.get(base+'/api/data/requirements?company_id=1').json()[0]['level']==2
  response=page.request.post(base+'/api/data/employees?company_id=1',headers=headers(),data={'code':'BROWSER-CERT','name':'دارنده گواهینامه','job':'Browser job'})
  assert response.status==201,response.text()
  page.locator('[data-page=certificates]').click()
  page.locator('#certificate-form [name=title]').fill('مدرک اولیه برای آزمون حذف')
  pdf={'name':'first.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4\nTest\n%%EOF'}
  page.locator('#certificate-form [name=file]').set_input_files(pdf)
  page.locator('#certificate-form button:not([type=button])').click()
  page.locator('#notice').filter(has_text='بایگانی شد').wait_for()
  page.locator('#certificate-form [name=title]').fill('پیش‌نویس مدرک جدید حفظ شود')
  page.locator('#certificate-form [name=file]').set_input_files({**pdf,'name':'keep-draft.pdf'})
  page.once('dialog',lambda dialog:dialog.accept())
  page.locator('[data-remove]').click()
  page.locator('#notice').filter(has_text='مدرک حذف شد').wait_for()
  expect(page.locator('#certificate-form [name=title]')).to_have_value('پیش‌نویس مدرک جدید حفظ شود')
  assert page.locator('#certificate-form [name=file]').evaluate('(input)=>input.files[0].name')=='keep-draft.pdf'
  assert page.evaluate('pendingWorkspace')==1
  assert page.request.get(base+'/api/certificates?company_id=1').json()==[]
  page.once('dialog',lambda dialog:dialog.accept())
  open_bank('BROWSER-USED')
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
