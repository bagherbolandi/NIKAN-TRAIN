"""Real Chromium integration of the single personnel directory and protected dossier.

Run with .venv/bin/python tests/browser_person_workspace_smoke.py. All personnel,
accounts and attachments are synthetic; the server/data are isolated and cleaned.
NIKAN_CHROMIUM selects Chromium; NIKAN_BROWSER_ARTIFACTS selects output location.
This verifies the source web UI, not native Windows installer execution.
"""
from pathlib import Path
import json,os,socket,subprocess,sys,tempfile,time,urllib.request
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];ARTIFACTS=Path(os.environ.get('NIKAN_BROWSER_ARTIFACTS',tempfile.gettempdir()));ARTIFACTS.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(ROOT))
from app.hrm_common import today_iso
PASSWORD='Unified-browser-synthetic-only-123'
checks=[]
with tempfile.TemporaryDirectory(prefix='nikan-unified-browser-') as data:
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}'
 with open(str(ARTIFACTS / 'nikan-unified-server.log'),'w') as log:
  process=subprocess.Popen([sys.executable,'run.py','--no-browser','--port',str(port)],cwd=ROOT,env={**os.environ,'NIKAN_DATA_DIR':data},stdout=log,stderr=log)
  try:
   for _ in range(120):
    try:urllib.request.urlopen(base+'/api/status',timeout=1).close();break
    except OSError:
     if process.poll() is not None:raise RuntimeError(Path(str(ARTIFACTS / 'nikan-unified-server.log')).read_text())
     time.sleep(.1)
   with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM','/usr/bin/chromium'),headless=True,args=['--no-sandbox']);context=browser.new_context(viewport={'width':1440,'height':1000});page=context.new_page();errors=[];page.on('pageerror',lambda err:errors.append(str(err)))
    assert context.request.post(base+'/api/setup',data={'username':'unified-admin','password':PASSWORD}).status==200
    assert context.request.post(base+'/api/login',data={'username':'unified-admin','password':PASSWORD}).status==200
    token=context.request.get(base+'/api/me').json()['csrf']
    def api(method,path,body=None,status=200,client=None,csrf=None):
     response=(client or context.request).fetch(base+'/api/'+path,method=method,data=body,headers={'X-CSRF-Token':csrf or token});assert response.status==status,(path,response.status,response.text());return response.json()
    site=api('POST','hrm/sites?company_id=1',{'name':'Unified site','kind':'office'},201)['id']
    other=api('POST','hrm/sites?company_id=2',{'name':'SECRET-CURRENT-SITE','kind':'office'},201)['id']
    people=[]
    for code,name in [('UN-A','فرد یکپارچه الف'),('UN-B','فرد دیگر ب')]:people.append(api('POST','hrm/people',{'company_id':1,'site_id':site,'code':code,'name':name,'department':'عملیات','job':'اپراتور','start_date':today_iso()},201))
    a,b=people
    for person in people:api('POST','hrm/contracts?company_id=1',{'employee_id':person['id'],'number':'CONTRACT-'+person['code'],'contract_type':'موقت','start_date':today_iso()},201)
    certificate=b'%PDF-1.4\nSynthetic unified certificate\n%%EOF'
    uploaded=context.request.post(base+'/api/certificates?company_id=1',headers={'X-CSRF-Token':token},multipart={'employee_id':str(a['id']),'title':'گواهینامه یکپارچه','file':{'name':'unified-proof.pdf','mimeType':'application/pdf','buffer':certificate}});assert uploaded.status==201,uploaded.text();certificate_id=uploaded.json()['id']
    api('POST','hrm/health_records?company_id=1',{'employee_id':a['id'],'exam_date':today_iso(),'exam_type':'معاینه دوره‌ای','fitness':'fit','restrictions':'MEDICAL-SECRET-UNIFIED'},201)
    api('POST','hrm/training_requirements?company_id=1',{'company_id':1,'site_id':site,'job':'اپراتور','competency_code':'S001','required_level':3,'active':1},201)
    api('POST','hrm/training_assessments?company_id=1',{'employee_id':a['id'],'competency_code':'S001','level':0,'assessment_date':today_iso()},201)
    page.goto(base);page.locator('#shell').wait_for(state='visible');page.locator('[data-page=hrm_personnel]').click();page.locator(f'#hrm-table [data-person-workspace="{a["id"]}"]').click();expect(page.locator('.hrm-person-header')).to_contain_text(a['name'])
    expect(page.locator('#nav [data-page=employees],#nav [data-page=users],#nav [data-page=dashboard],#nav [data-page=records],#nav [data-page=assessments]')).to_have_count(0)
    page.locator('[data-person-section=contracts]').click();expect(page.locator('#hrm-person-resource')).to_contain_text('CONTRACT-UN-A');expect(page.locator('#hrm-person-resource')).not_to_contain_text('CONTRACT-UN-B');checks.append('one directory/dossier entry and person-filtered contracts')
    page.locator('#hrm-person-resource #hrm-add').click();expect(page.locator('#hrm-form [name=employee_id]')).to_have_value(str(a['id']));assert page.locator('#hrm-form [name=employee_id] option').evaluate_all('(nodes)=>nodes.filter(n=>n.value).map(n=>n.value)')==[str(a['id'])]
    page.locator('#hrm-form [name=number]').fill('CONTRACT-PINNED-A');page.locator('#hrm-form [name=contract_type]').fill('موقت');page.locator('#hrm-form [name=start_date]').fill(page.evaluate('currentJalaliDate()'));page.locator('#hrm-save').click();page.locator('#hrm-editor').wait_for(state='hidden');expect(page.locator('#hrm-person-resource')).to_contain_text('CONTRACT-PINNED-A');assert len(api('GET',f'hrm/contracts?company_id=1&employee_id={a["id"]}'))==2;checks.append('existing contract form pins employee identity and reloads same dossier')
    with page.expect_response(lambda response:'/api/hrm/attendance?' in response.url):
     page.evaluate("()=>{document.querySelector('#hrm-person-resource #hrm-add').click();document.querySelector('[data-person-section=attendance]').click();}")
    expect(page.locator('#hrm-editor')).to_be_hidden();checks.append('section changes discard stale editor preparation without changing pinned identity')
    page.locator('[data-person-section=training]').click();page.locator('[data-matrix-cell]').first.wait_for();expect(page.locator('.hrm-matrix-state-gap').first).to_contain_text('0');page.locator('[data-matrix-cell]').first.click();expect(page.locator('#hrm-matrix-cell-detail')).to_contain_text('سطح صفر ثبت‌شده');expect(page.locator('#hrm-matrix-cell-detail')).to_contain_text('ثبت نشده');page.evaluate('window.scrollTo(0,0)');page.screenshot(path=str(ARTIFACTS / 'nikan-unified-person-matrix.png'),full_page=True);checks.append('canonical per-person matrix distinguishes explicit zero and unknown audit fields')
    with page.expect_download() as download:page.locator('#hrm-matrix-export').click()
    assert download.value.suggested_filename.endswith('.xlsx');checks.append('matrix Excel export uses same scoped source')
    page.locator('[data-person-resource-tab=training_certificates]').click();page.locator(f'[data-person-certificate="{certificate_id}"]').wait_for()
    with page.expect_download() as proof:page.locator(f'[data-person-certificate="{certificate_id}"]').click()
    assert Path(proof.value.path()).read_bytes()==certificate;assert proof.value.suggested_filename.endswith('.pdf');checks.append('training certificate metadata and protected file download reuse existing attachment bytes')
    page.evaluate("()=>navigateTo('records')");expect(page.locator('[data-hrm-resource=training_records]')).to_have_attribute('aria-selected','true');page.evaluate("()=>navigateTo('assessments')");expect(page.locator('[data-hrm-resource=skill_matrix]')).to_have_attribute('aria-selected','true');page.evaluate("()=>navigateTo('users')");page.locator('#hrm-user-add').wait_for();checks.append('legacy employee/user/record/assessment navigation resolves canonical workspaces')
    api('POST','hrm/access/users',{'username':'unified-local','password':PASSWORD,'role':'company_hr','scopes':[{'company_id':1,'site_id':site,'department':''}],'permissions':{'contracts':{'read':True},'training':{'read':True}},'training_access':False},201)
    child=browser.new_context(viewport={'width':390,'height':844});cp=child.new_page();cp.on('pageerror',lambda err:errors.append(str(err)));assert child.request.post(base+'/api/login',data={'username':'unified-local','password':PASSWORD}).status==200;cp.goto(base);cp.locator('#shell').wait_for(state='visible');cp.locator('[data-page=hrm_personnel]').click();cp.locator(f'[data-person-workspace="{a["id"]}"]').first.click();expect(cp.locator('[data-person-section=health]')).to_have_count(0);assert 'MEDICAL-SECRET-UNIFIED' not in cp.content();checks.append('non-personnel domain reader gets minimal dossier without clinical tabs/data')
    current=api('GET',f'hrm/people/{a["id"]}?company_id=1');api('PUT',f'hrm/people/{a["id"]}?company_id=1',{'company_id':2,'site_id':other,'version':current['version'],'effective_date':today_iso()})
    cp.locator('#hrm-person-directory').click();cp.locator(f'[data-person-workspace="{a["id"]}"]').first.click();expect(cp.locator('.hrm-person-header')).to_contain_text('سوابق مجاز');expect(cp.locator('#company')).to_have_value('1');assert 'SECRET-CURRENT-SITE' not in cp.content();cp.locator('[data-person-section=contracts]').click();expect(cp.locator('#hrm-person-resource #hrm-add')).to_have_count(0);expect(cp.locator('#hrm-person-resource [data-hrm-edit]')).to_have_count(0);assert cp.evaluate('document.documentElement.scrollWidth<=innerWidth');cp.evaluate('window.scrollTo(0,0)');cp.screenshot(path=str(ARTIFACTS / 'nikan-unified-person-history-mobile.png'),full_page=True);checks.append('historical-only dossier keeps company context and disables writes without leaking current site')
    assert not errors,errors;browser.close();print(json.dumps({'passed':True,'checks':checks,'javascript_errors':errors,'native_windows_execution':False},ensure_ascii=False,indent=2));Path(str(ARTIFACTS / 'nikan-unified-ui-result.json')).write_text(json.dumps({'passed':True,'checks':checks,'javascript_errors':errors,'native_windows_execution':False},ensure_ascii=False,indent=2))
  finally:
   process.terminate();process.wait(timeout=10)
