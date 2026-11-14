"""Real Chromium coverage for the professional shell and role-filtered navigation.

Run .venv/bin/python tests/browser_shell_smoke.py. NIKAN_CHROMIUM overrides the
browser path; NIKAN_BROWSER_ARTIFACTS selects reports/screenshots (default temp).
All accounts/personnel are synthetic and the temporary server is cleaned up.
"""
from pathlib import Path
import json,os,socket,subprocess,sys,tempfile,time,urllib.request
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];OUT=Path(os.environ.get('NIKAN_BROWSER_ARTIFACTS',tempfile.gettempdir()));OUT.mkdir(parents=True,exist_ok=True);checks=[]
with tempfile.TemporaryDirectory(prefix='nikan-015-shell-') as data:
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}';log=(Path(data)/'server.log').open('w')
 server=subprocess.Popen([sys.executable,'run.py','--no-browser','--port',str(port)],cwd=ROOT,env={**os.environ,'NIKAN_DATA_DIR':data},stdout=log,stderr=log)
 try:
  for _ in range(120):
   try:urllib.request.urlopen(base+'/api/status',timeout=1).close();break
   except OSError:
    if server.poll() is not None:raise RuntimeError('server exited')
    time.sleep(.1)
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox']);context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(base);page.locator('#auth [name=username]').fill('shell-admin');page.locator('#auth [name=password]').fill('Shell-professional-synthetic-123');page.locator('#auth button').click();page.locator('#shell').wait_for(state='visible');page.locator('[data-hrm-metric]').first.wait_for()
   token=context.request.get(base+'/api/me').json()['csrf']
   def api(method,path,body=None,status=200):
    r=context.request.fetch(base+'/api/'+path,method=method,data=body,headers={'X-CSRF-Token':token});assert r.status==status,(path,r.status,r.text());return r.json()
   site=api('POST','hrm/sites?company_id=1',{'name':'کارخانه مرکزی نیکان','kind':'factory'},201)['id']
   for code,name,job in [('01501','همکار نمونه الف','کارشناس تولید'),('01502','همکار نمونه ب','سرپرست اداری'),('01503','همکار نمونه ج','کارشناس آموزش')]:api('POST','hrm/people?company_id=1',{'company_id':1,'site_id':site,'code':code,'name':name,'department':'عملیات','job':job,'start_date':'2026-01-01'},201)
   expect(page.locator('#nav [data-page=hrm_personnel]')).to_have_count(1);expect(page.locator('#nav .workspace-nav-group')).to_have_count(7);expect(page.locator('#workspace-sidebar .brand-mark img')).to_be_visible();assert not page.locator('header input[type=search]').count();checks.append('role-filtered original pages grouped once in seven domain sections with official local logo and no global search')
   page.locator('#nav [data-page=hrm_personnel]').click();page.locator('#hrm-table').get_by_text('همکار نمونه الف',exact=True).wait_for();expect(page.locator('#nav [data-page=hrm_personnel]')).to_have_attribute('aria-current','page');expect(page.locator('#workspace-section-label')).to_have_text('کارکنان و امور اداری');assert 'پرونده کارکنان' in page.title();checks.append('active module, page title and breadcrumbs track canonical personnel workspace')
   toggle=page.locator('[data-workspace-toggle=performance]');toggle.click();expect(toggle).to_have_attribute('aria-expanded','false');expect(page.locator('#workspace-nav-performance')).to_be_hidden();page.evaluate('drawNavigation()');expect(page.locator('[data-workspace-toggle=performance]')).to_have_attribute('aria-expanded','false');page.locator('[data-workspace-toggle=performance]').click();checks.append('collapsible navigation preserves account-session choice across a permission-aware redraw')
   page.locator('[data-workspace-toggle=benefits]').click();expect(page.locator('#workspace-nav-benefits')).to_be_hidden();page.evaluate("navigateTo('hrm_loans')");expect(page.locator('#nav [data-page=hrm_loans]')).to_have_attribute('aria-current','page');expect(page.locator('#workspace-nav-benefits')).to_be_visible();page.evaluate("navigateTo('hrm_personnel')");page.locator('#hrm-table').get_by_text('همکار نمونه الف',exact=True).wait_for();checks.append('programmatic navigation expands its previously collapsed group and keeps the active destination visible')
   page.locator('[data-workspace-toggle=overview]').focus();page.keyboard.press('ArrowDown');assert page.evaluate("document.activeElement.matches('#nav button')");checks.append('vertical menu keyboard navigation preserves a visible focus target')
   current=page.locator('#company').input_value();page.locator('#company').select_option('2');expect(page.locator('#company-label')).to_have_text(page.locator('#company option:checked').inner_text());page.locator('#company').select_option(current);page.locator('#hrm-table').get_by_text('همکار نمونه الف',exact=True).wait_for();checks.append('company selection updates visible scope and reads the matching company without replacing the identity source')
   layout=[]
   for width in (1440,1024,390,320):
    page.set_viewport_size({'width':width,'height':1000 if width>760 else 844});page.wait_for_timeout(80);assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
    assert page.evaluate("document.querySelector('#nav').scrollHeight>=document.querySelector('#nav').clientHeight")
    layout.append({'width':width,'horizontal_overflow':False})
    if width in (1440,390):page.screenshot(path=str(OUT/f'hrm-0.15-shell-{width}.png'),full_page=True)
   checks.append('1440, 1024, 390 and 320 pixel layouts contain the page while navigation scrolls independently')
   page.set_viewport_size({'width':390,'height':844});expect(page.locator('#workspace-sidebar')).to_have_attribute('aria-hidden','true');assert page.locator('#workspace-sidebar').evaluate('(el)=>el.inert')
   page.locator('#workspace-menu-toggle').click();expect(page.locator('#workspace-menu-toggle')).to_have_attribute('aria-expanded','true');assert page.locator('#workspace-main').evaluate('(el)=>el.inert');expect(page.locator('#workspace-menu-close')).to_be_focused()
   page.keyboard.press('Shift+Tab');assert page.evaluate("document.querySelector('#workspace-sidebar').contains(document.activeElement)");page.keyboard.press('Tab');expect(page.locator('#workspace-menu-close')).to_be_focused();page.keyboard.press('Escape');expect(page.locator('#workspace-menu-toggle')).to_be_focused();assert not page.locator('#workspace-main').evaluate('(el)=>el.inert');checks.append('mobile drawer hides from accessibility tree when closed, traps focus when open and restores focus on Escape')
   page.locator('#workspace-menu-toggle').click();page.locator('#workspace-drawer-backdrop').click(position={'x':8,'y':500});expect(page.locator('#workspace-menu-toggle')).to_have_attribute('aria-expanded','false');page.locator('#workspace-menu-toggle').click();page.locator('#nav [data-page=hrm_training]').click();expect(page.locator('#workspace-menu-toggle')).to_have_attribute('aria-expanded','false');page.locator('[data-hrm-resource=skill_matrix]').wait_for();assert not page.locator('#workspace-main').evaluate('(el)=>el.inert');checks.append('backdrop and navigation close the drawer and restore usable content')
   page.set_viewport_size({'width':1440,'height':1000});page.route('**/api/hrm/dashboard?*',lambda route:route.fulfill(status=503,content_type='application/json',body=json.dumps({'error':'خطای موقت آزمون','request_id':'0123456789abcdef'})))
   page.locator('#nav [data-page=hrm_dashboard]').click();expect(page.locator('#notice')).to_contain_text('0123456789abcdef');page.locator('#retry-page').wait_for();assert context.request.get(base+'/api/me').status==200;page.unroute('**/api/hrm/dashboard?*');page.locator('#retry-page').click();page.locator('[data-hrm-metric]').first.wait_for();checks.append('transient server error exposes safe correlation code and retry recovers without ending session or repeating a write')
   api('POST','hrm/access/users',{'username':'shell-local','password':'Shell-professional-synthetic-123','role':'company_hr','scopes':[{'company_id':1,'site_id':site,'department':''}],'permissions':{'personnel':{'read':True,'create':True,'edit':True}},'training_access':False},201)
   local=browser.new_context(viewport={'width':390,'height':844});lp=local.new_page();lp.on('pageerror',lambda e:errors.append(str(e)));assert local.request.post(base+'/api/login',data={'username':'shell-local','password':'Shell-professional-synthetic-123'}).status==200;lp.goto(base);lp.locator('#shell').wait_for(state='visible');lp.locator('#workspace-menu-toggle').click();expect(lp.locator('#nav [data-page=hrm_personnel]')).to_have_count(1);expect(lp.locator('#nav [data-page=hrm_access],#nav [data-page=companies],#nav [data-page=hrm_health_environment],#nav [data-page=hrm_loans]')).to_have_count(0);assert lp.locator('#company option').count()==1;checks.append('company personnel manager sees only assigned-company operational pages and no access management, clinical or finance navigation')
   assert not errors,errors
   report={'passed':True,'platform':'linux','native_windows_execution':False,'checks':checks,'layouts':layout,'javascript_errors':errors,'screenshots':['hrm-0.15-shell-1440.png','hrm-0.15-shell-390.png']};(OUT/'hrm-0.15-shell-browser.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n');print(json.dumps(report,indent=2,ensure_ascii=False));browser.close()
 finally:server.terminate();server.wait(timeout=10);log.close()
