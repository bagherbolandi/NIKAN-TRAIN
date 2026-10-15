"""Optional real-browser smoke test; requires Playwright and a local Chromium."""
from playwright.sync_api import sync_playwright, expect, Error as PlaywrightError
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
  assert page.locator('#global-search,#global-search-results').count()==0
  expect(page.locator('.nav-group')).to_have_count(3)
  # A temporary API failure preserves authentication and offers a recoverable page.
  page.route('**/api/dashboard?*',lambda route:route.fulfill(status=503,json={'error':'آزمون قطع موقت سرویس'}))
  page.locator('[data-page=dashboard]').click();page.locator('#retry-page').wait_for()
  assert page.locator('#shell').is_visible() and not page.locator('#login').is_visible()
  page.unroute('**/api/dashboard?*');page.locator('#retry-page').click();page.locator('.metrics').wait_for()
  # A failed session-state check must also keep the signed-in shell intact.
  page.route('**/api/me',lambda route:route.fulfill(status=500,json={'error':'آزمون پاسخ موقت ناموفق'}))
  page.evaluate('load()');page.locator('#retry-page').wait_for();assert page.locator('#shell').is_visible()
  page.unroute('**/api/me');page.locator('#retry-page').click();page.locator('.metrics').wait_for()
  page.locator('[data-page=bank]').click();page.locator('#adopt-bank').click();page.locator('#notice').filter(has_text='فعال شد').wait_for()
  page.locator('[data-page=employees]').click();page.locator('#add').click();page.locator('#entry [name=code]').fill('E001');page.locator('#entry [name=name]').fill('کاربر آزمایشی');page.locator('#entry [name=job]').select_option(label='مدیر کارخانه')
  writes=[];page.on('request',lambda request:writes.append(request.url) if request.method=='POST' and '/api/data/employees?' in request.url else None)
  page.evaluate("document.querySelector('#entry').requestSubmit();document.querySelector('#entry').requestSubmit()")
  page.locator('#table').get_by_text('کاربر آزمایشی').wait_for();assert len(writes)==1,writes
  expect(page.locator('#table tbody tr')).to_have_count(1)
  # Complete an earlier company request after navigating elsewhere: it cannot refill the cache.
  old_rows=page.request.get(base+'/api/data/employees?company_id=1').json();held=[]
  page.route('**/api/data/employees?company_id=1',lambda route:held.append(route))
  with page.expect_request('**/api/data/employees?company_id=1'):
   page.evaluate("void navigateTo('employees',1)")
  page.evaluate("void navigateTo('employees',2)")
  page.locator('#table .empty').wait_for();assert held
  for route in held:
   try:route.fulfill(json=old_rows)
   except PlaywrightError:pass # Chromium may have already discarded the aborted request.
  page.unroute('**/api/data/employees?company_id=1')
  assert page.evaluate('Number(cid)===2 && cache.employees.length===0')
  assert page.locator('#table').get_by_text('کاربر آزمایشی').count()==0
  page.locator('#company').select_option('1');page.locator('#table').get_by_text('کاربر آزمایشی').wait_for()
  page.locator('[data-page=bank]').click();page.locator('#adopt-bank').click();page.locator('#notice').filter(has_text='فعال شد').wait_for()
  page.locator('[data-page=passports]').click();page.locator('#person').select_option(index=1);page.locator('[data-pane-button="1"]').click();page.locator('#approved-job').wait_for();page.locator('#approved-job').select_option(label='مدیر کارخانه');page.locator('#approve-job').click();page.locator('.passport-heading .pill').filter(has_text='تأییدشده').wait_for();page.locator('[data-pane-button="2"]').click();assert page.locator('#passport .category-section').count()>1;assert page.locator('#passport table tbody tr').count()==0;page.locator('#passport .category-section summary').first.click();page.locator('#passport table tbody tr').first.wait_for();assert page.locator('#passport table tbody tr').count()<100
  page.locator('#edit-person-document').click();page.locator('#entry [name=commitments]').fill('تعهد اختصاصی آزمون');page.locator('#entry button').last.click();page.locator('#notice').filter(has_text='همین فرد').wait_for();page.locator('[data-pane-button="0"]').click();page.locator('#passport').get_by_text('تعهد اختصاصی آزمون',exact=True).wait_for()
  page.screenshot(path=os.environ.get('NIKAN_PASSPORT_SCREENSHOT','/tmp/nikan-passport.png'),full_page=True)
  page.locator('[data-page=requirements]').click();page.locator('#job-search').fill('مدیر کارخانه');page.locator('[data-job="مدیر کارخانه"]').click();page.locator('#job-matrix .category-section').first.wait_for();assert page.locator('#job-matrix table').count()==0;page.locator('#job-matrix summary').first.click();page.locator('#job-matrix select[data-level]').first.select_option('4');page.locator('#save-job').click();page.locator('#notice').filter(has_text='تراکنش').wait_for();page.screenshot(path=os.environ.get('NIKAN_JOB_SCREENSHOT','/tmp/nikan-job-workspace.png'),full_page=True)
  page.locator('[data-page=assessments]').click();page.locator('#person-search').fill('E001');assert page.locator('#people-list .master-item').count()==1;page.locator('[data-employee]').click();page.locator('#assessment-categories .category-section').first.wait_for();assert page.locator('#assessment-categories table').count()==0;page.locator('#assessment-categories summary').first.click();page.locator('#assessment-categories select[data-level]').first.select_option('2');page.locator('#save-assessment').click();page.locator('#notice').filter(has_text='یکجا').wait_for();page.locator('#assessment-categories summary').first.click();page.locator('#assessment-categories select[data-level]').first.wait_for();page.screenshot(path=os.environ.get('NIKAN_MATRIX_SCREENSHOT','/tmp/nikan-person-workspace.png'),full_page=True)
  page.locator('[data-page=gaps]').click();page.locator('#needs-table tbody tr').first.wait_for();assert page.locator('#needs-table tbody tr').count()<=20;assert page.locator('#needs-table').get_by_text('کاربر آزمایشی').count()==0;page.locator('[data-need]').first.click();page.locator('#need-people-dialog').get_by_text('کاربر آزمایشی').wait_for();page.locator('#need-people-dialog [data-query]').fill('E001');assert page.locator('#need-people-dialog tbody tr').count()==1;page.locator('#need-people-dialog [data-close]').click()
  page.locator('[data-page=assessments]').click();page.locator('#person-search').fill('E001');page.locator('[data-employee]').click();page.locator('#person-matrix .passport-heading').get_by_text('کاربر آزمایشی').wait_for()
  page.locator('[data-page=bank]').click();page.locator('#bank-categories').wait_for();page.screenshot(path=os.environ.get('NIKAN_BANK_SCREENSHOT','/tmp/nikan-shared-bank.png'),full_page=True);assert page.locator('#bank-table table').count()==0;page.locator('#bank-search').fill('S001');page.locator('[data-bank-edit="S001"]').click();page.locator('#entry [name=title]').fill('شایستگی مشترک آزمون');page.locator('#entry button').last.click();page.locator('#notice').filter(has_text='به‌روزرسانی').wait_for();page.locator('#company').select_option('2');page.locator('#bank-search').fill('S001');page.locator('#bank-table').get_by_text('شایستگی مشترک آزمون').wait_for();page.locator('#company').select_option('1')
  page.locator('[data-page=certificates]').click();page.locator('#certificate-form [name=title]').fill('گواهینامه ایمنی');page.locator('#certificate-form [name=file]').set_input_files({'name':'certificate.pdf','mimeType':'application/pdf','buffer':b'%PDF-1.4\nTest\n%%EOF'});page.locator('#certificate-form button:not([type=button])').click();page.locator('#notice').filter(has_text='بایگانی شد').wait_for()
  with page.expect_download() as info:page.locator('[data-cert]').click()
  assert Path(info.value.path()).read_bytes().startswith(b'%PDF-')
  page.locator('[data-page=employees]').click()
  page.locator('#company').select_option('2');page.locator('.empty').wait_for();assert page.locator('#table').get_by_text('کاربر آزمایشی').count()==0
  page.locator('#company').select_option('1');page.locator('[data-page=dashboard]').click();page.locator('.metrics').wait_for();page.screenshot(path=os.environ.get('NIKAN_SCREENSHOT','/tmp/nikan-dashboard.png'),full_page=True)
  page.locator('[data-page=courses]').click();page.locator('.month-card').first.wait_for();assert page.locator('.month-card').count()==12
  import base64
  logo=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nO8AAAAASUVORK5CYII=')
  page.locator('[data-page=branding]').click();page.locator('#branding-key').select_option('holding');page.locator('#branding-file').set_input_files({'name':'logo.png','mimeType':'image/png','buffer':logo});page.locator('#branding-preview').wait_for(state='visible');page.locator('#branding-form button:not([type=button])').click();page.locator('#notice').filter(has_text='لوگو ثبت شد').wait_for();assert page.locator('.logo .brand-mark img').count()==1
  page.locator('#branding-key').select_option('company-1');page.locator('#branding-file').set_input_files({'name':'company.png','mimeType':'image/png','buffer':logo});page.locator('#branding-preview').wait_for(state='visible');page.locator('#branding-form button:not([type=button])').click();page.locator('#notice').filter(has_text='لوگو ثبت شد').wait_for();page.locator('#company-logo').wait_for(state='visible')
  page.locator('[data-page=users]').click();page.locator('#add').click();page.locator('#entry [name=username]').fill('company-manager');page.locator('#entry [name=password]').fill(secrets.token_urlsafe(20));page.locator('#entry button').last.click();page.locator('.company-row').filter(has_text='company-manager').get_by_text('تغییر رمز').click();newpass=secrets.token_urlsafe(20);page.locator('#entry [name=password]').fill(newpass);page.locator('#entry button').last.click();page.locator('#dialog').wait_for(state='hidden');page.locator('#logout').click();page.locator('#auth [name=username]').fill('company-manager');page.locator('#auth [name=password]').fill(newpass);page.locator('#auth button').click();page.locator('.metrics').wait_for();assert page.locator('#company option').count()==1;assert page.locator('[data-page=users]').count()==0
  # A server-revoked session removes every cached company/person and returns to login.
  account=page.request.get(base+'/api/me').json()
  assert page.request.post(base+'/api/logout',headers={'X-CSRF-Token':account['csrf']}).ok
  page.locator('[data-page=employees]').click();page.locator('#login').wait_for(state='visible')
  assert page.evaluate('!user && cid===null && Object.keys(cache).length===0 && Object.keys(referenceOptions).length===0')
  assert page.locator('#content').inner_text()==''
  assert page.locator('#user-label').inner_text()==''
  assert not errors,errors
  page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
  print('Browser smoke passed: setup/login, no header search, grouped menus, transient failure/retry, one write on double submit, stale-company reads, personnel workflows, company isolation, revoked-session reset, mobile layout; no JavaScript errors.')
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
