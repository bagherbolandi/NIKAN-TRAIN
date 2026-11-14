"""Actual Chromium checks of scoped follow-up and consistent management lists.

All accounts, people and financial values are synthetic in an isolated server.
Run with .venv/bin/python tests/browser_management_workspace_smoke.py.
"""
from datetime import date, timedelta
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.hrm_common import today_iso

PASSWORD = 'Workspace-browser-synthetic-only-015'
ARTIFACTS = Path(os.environ.get('NIKAN_BROWSER_ARTIFACTS', tempfile.gettempdir()))
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def check(base):
    checks = []
    errors = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        assert context.request.post(base+'/api/setup', data={'username': 'workspace-admin', 'password': PASSWORD}).status == 200
        assert context.request.post(base+'/api/login', data={'username': 'workspace-admin', 'password': PASSWORD}).status == 200
        csrf = context.request.get(base+'/api/me').json()['csrf']

        def api(method, path, body=None, expected=200):
            response = context.request.fetch(base+'/api/'+path, method=method, data=body, headers={'X-CSRF-Token': csrf})
            assert response.status == expected, (path, response.status, response.text())
            return response.json()

        today = today_iso()
        due = (date.fromisoformat(today)+timedelta(days=7)).isoformat()
        site = api('POST', 'hrm/sites?company_id=1', {'name': 'سایت فهرست حرفه‌ای', 'kind': 'office'}, 201)['id']
        people = []
        contracts = []
        for index in range(32):
            person = api('POST', 'hrm/people?company_id=1', {'code': f'P-{index:04}', 'name': f'کارمند شماره {index:02}', 'company_id': 1, 'site_id': site, 'job': 'کارشناس', 'department': 'عملیات', 'start_date': today}, 201)
            people.append(person)
            contracts.append(api('POST', 'hrm/contracts?company_id=1', {'employee_id': person['id'], 'number': f'CON-{index:03}', 'contract_type': 'موقت', 'start_date': today, 'end_date': due, 'status': 'active'}, 201))
        foreign = api('POST', 'hrm/people?company_id=2', {'code': 'FOREIGN-015', 'name': 'کارمند شرکت دیگر', 'company_id': 2, 'job': 'کارشناس'}, 201)
        api('POST', 'hrm/contracts?company_id=2', {'employee_id': foreign['id'], 'number': 'FOREIGN-CONTRACT-015', 'contract_type': 'موقت', 'start_date': today, 'end_date': due, 'status': 'active'}, 201)
        small = api('POST', 'hrm/loans?company_id=1', {'employee_id': people[0]['id'], 'title': 'مبلغ دقیق کمتر', 'loan_kind': 'internal', 'amount_rials': '9007199254740990', 'status': 'approved'}, 201)
        large = api('POST', 'hrm/loans?company_id=1', {'employee_id': people[0]['id'], 'title': 'مبلغ دقیق بیشتر', 'loan_kind': 'internal', 'amount_rials': '9007199254740991', 'status': 'approved'}, 201)
        page.goto(base)
        page.locator('#shell').wait_for(state='visible')

        def nav(key):
            button = page.locator(f'#nav [data-page={key}]')
            if page.locator('#workspace-menu-toggle').is_visible():
                page.locator('#workspace-menu-toggle').click()
            if not button.is_visible():
                button.locator('xpath=ancestor::section[1]').locator('[data-workspace-toggle]').click()
            button.click()

        nav('hrm_personnel')
        expect(page.locator('#hrm-table tbody tr')).to_have_count(25)
        expect(page.locator('#hrm-row-status')).to_contain_text('۳۲')
        page.locator('[data-list-size]').select_option('10')
        expect(page.locator('#hrm-table tbody tr')).to_have_count(10)
        page.locator('[data-hrm-sort=code]').click()
        expect(page.locator('#hrm-table tbody tr').first).to_contain_text('P-0000')
        page.locator('[data-next]').click()
        expect(page.locator('#hrm-table tbody tr').first).to_contain_text('P-0010')
        checks.append('32 authorized personnel: exact total, page size and stable natural code sorting')
        page.locator('#hrm-search').fill('P-٠٠٠٧')
        expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
        expect(page.locator('#hrm-table tbody tr')).to_contain_text('P-0007')
        page.locator('#hrm-search').fill('كارمند\u200c  شماره   07')
        expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
        expect(page.locator('#hrm-table tbody tr')).to_contain_text('کارمند شماره 07')
        page.locator('[data-list-reset]').click()
        expect(page.locator('#hrm-search')).to_be_focused()
        expect(page.locator('#hrm-table tbody tr')).to_have_count(10)
        checks.append('Arabic/Persian letters and digits, zero-width/repeated spaces normalize in local search')

        nav('hrm_contracts')
        page.locator('[data-list-size]').select_option('10')
        page.locator('[data-hrm-sort=number]').focus()
        page.keyboard.press('Enter')
        expect(page.locator('th:has([data-hrm-sort=number])')).to_have_attribute('aria-sort', 'ascending')
        expect(page.locator('[data-hrm-sort=number]')).to_be_focused()
        page.locator('[data-next]').click()
        expect(page.locator('#hrm-table tbody tr').first).to_contain_text('CON-010')
        page.locator('#hrm-search').fill('CON-029')
        expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
        page.locator(f'[data-hrm-edit="{contracts[29]["id"]}"]').click()
        expect(page.locator('#hrm-form [name=number]')).to_have_value('CON-029')
        page.locator('#hrm-form [name=notes]').fill('اصلاح همان شناسه پس از جستجو و مرتب‌سازی')
        page.locator('#hrm-save').click()
        page.locator('#hrm-editor').wait_for(state='hidden')
        expect(page.locator('#hrm-search')).to_have_value('CON-029')
        expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
        saved = api('GET', 'hrm/contracts?company_id=1')
        assert len(saved) == 32
        assert next(row for row in saved if row['id'] == contracts[29]['id'])['notes'].startswith('اصلاح همان شناسه')
        assert next(row for row in saved if row['id'] == contracts[28]['id'])['notes'] == ''
        page.locator('[data-list-reset]').click()
        page.locator('[data-list-size]').select_option('50')
        expect(page.locator('#hrm-table tbody tr')).to_have_count(32)
        checks.append('keyboard-sort, paging and filtered edit preserve record ID/version and list state')

        nav('hrm_loans')
        page.locator('[data-hrm-resource=loans]').click()
        page.locator('[data-hrm-sort=amount_rials]').click()
        assert page.locator('#hrm-table tbody tr').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.hrmRow))') == [small['id'], large['id']]
        page.locator(f'[data-hrm-edit="{large["id"]}"]').click()
        expect(page.locator('#hrm-form [name=amount_rials]')).to_have_value('9007199254740991')
        page.locator('[data-hrm-close]').first.click()
        checks.append('exact whole-rial sorting and editor retain the full supported amount')

        page.route('**/api/hrm/management-overview*', lambda route: route.fulfill(status=503, content_type='application/json', body=json.dumps({'error': 'خطای آزمایشی دریافت پیگیری'})))
        nav('hrm_dashboard')
        page.locator('[data-management-retry]').wait_for()
        page.unroute('**/api/hrm/management-overview*')
        page.locator('[data-management-retry]').click()
        page.locator('[data-management-category=contracts_due]').wait_for()
        expect(page.locator('#hrm-management-overview')).to_contain_text('مستقل از بازه تاریخی')
        expect(page.locator('[data-management-category=contracts_due] .hrm-attention-title strong')).to_have_text('۳۳')
        assert page.locator('[data-management-category=contracts_due] .hrm-attention-items li').count() == 6
        page.screenshot(path=str(ARTIFACTS/'nikan-015-management-dashboard.png'), full_page=True)
        page.locator('[data-management-category=medical_follow_up] .hrm-attention-open').click()
        expect(page.locator('#page-title')).to_contain_text('بهداشت و محیط زیست')
        expect(page.locator('[data-hrm-resource=health_records]')).to_have_attribute('aria-selected', 'true')
        nav('hrm_dashboard')
        page.locator('[data-management-category=contracts_due] [data-management-item-index]').first.click()
        expect(page.locator('.hrm-person-header')).to_contain_text(people[0]['name'])
        expect(page.locator('#hrm-person-resource')).to_contain_text('CON-000')
        checks.append('live scoped action center: bounded samples, full counts, error retry, combined health workspace and original person/resource link')

        api('POST', 'hrm/access/users', {'username': 'workspace-company', 'password': PASSWORD, 'role': 'company_hr', 'scopes': [{'company_id': 1, 'site_id': site, 'department': ''}], 'permissions': {'contracts': {'read': True}}, 'training_access': False}, 201)
        local = browser.new_context(viewport={'width': 390, 'height': 844})
        lp = local.new_page()
        lp.on('pageerror', lambda error: errors.append(str(error)))
        assert local.request.post(base+'/api/login', data={'username': 'workspace-company', 'password': PASSWORD}).status == 200
        lp.goto(base)
        lp.locator('#shell').wait_for(state='visible')
        lp.locator('[data-management-category=contracts_due]').wait_for()
        expect(lp.locator('[data-management-category=contracts_due] .hrm-attention-title strong')).to_have_text('۳۲')
        expect(lp.locator('[data-management-category=medical_follow_up]')).to_have_count(0)
        expect(lp.locator('#nav [data-page=hrm_loans]')).to_have_count(0)
        assert 'FOREIGN-CONTRACT-015' not in lp.content()
        assert lp.evaluate('document.documentElement.scrollWidth <= innerWidth')
        lp.screenshot(path=str(ARTIFACTS/'nikan-015-management-mobile.png'), full_page=True)
        expect(lp.locator('.hrm-filter-disclosure')).not_to_have_attribute('open', '')
        lp.locator('.hrm-filter-disclosure > summary').click()
        lp.locator('#hrm-filters [name=employee_id]').select_option(str(people[0]['id']))
        lp.locator('#hrm-filters button[type=submit]').click()
        expect(lp.locator('[data-management-category=contracts_due] .hrm-attention-title strong')).to_have_text('۱')
        expect(lp.locator('.hrm-filter-disclosure > summary')).to_contain_text('۱ فیلتر فعال')
        lp.locator('.hrm-filter-disclosure > summary').click()
        lp.locator('#hrm-clear-filters').click()
        expect(lp.locator('[data-management-category=contracts_due] .hrm-attention-title strong')).to_have_text('۳۲')
        checks.append('mobile scope filters start folded, open accessibly and apply/reset the same scoped API filters')
        lp.set_viewport_size({'width': 320, 'height': 800})
        assert lp.evaluate('document.documentElement.scrollWidth <= innerWidth')
        checks.append('company/site-only reader: no foreign or medical/financial cards; 390/320 mobile without page overflow')
        assert not errors, errors
        browser.close()
    report = {'passed': True, 'platform': sys.platform, 'native_windows_execution': False, 'checks': checks, 'javascript_errors': errors}
    (ARTIFACTS/'nikan-015-management-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='nikan-management-workspace-browser-') as data:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        with (ARTIFACTS/'nikan-015-management-server.log').open('w') as log:
            process = subprocess.Popen([sys.executable, 'run.py', '--no-browser', '--port', str(port)], cwd=ROOT, env={**os.environ, 'NIKAN_DATA_DIR': data}, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    try:
                        urllib.request.urlopen(base+'/api/status', timeout=1).close(); break
                    except OSError:
                        if process.poll() is not None:
                            raise RuntimeError('Synthetic server exited; inspect local log')
                        time.sleep(.1)
                else:
                    raise RuntimeError('Synthetic server was not ready')
                check(base)
            finally:
                process.terminate()
                process.wait(timeout=10)
