"""Browser coverage for admin company management, protected history and empty holdings."""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import expect, sync_playwright


def check(base):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        password = secrets.token_urlsafe(24)
        page.goto(base)
        page.locator('#auth [name=username]').fill('companies-admin')
        page.locator('#auth [name=password]').fill(password)
        page.locator('#auth button').click()
        page.locator('.metrics').wait_for()
        expect(page.locator('.nav-group').filter(has_text='مدیریت سامانه').locator('[data-page=companies]')).to_have_count(1)
        account = page.request.get(base + '/api/me').json()
        headers = {'X-CSRF-Token': account['csrf']}

        def post(path, data):
            response = page.request.post(base + '/api/' + path, headers=headers, data=data)
            assert response.ok, (response.status, response.text())
            return response.json()

        employee = post('data/employees?company_id=1', {'code': 'KEPT', 'name': 'پرسنل محفوظ'})
        for role in ('manager', 'viewer'):
            post('users', {'username': 'companies-' + role, 'password': password, 'role': role, 'company_id': 2})
        page.locator('[data-page=companies]').click()
        expect(page.locator('#companies-table tbody tr')).to_have_count(6)
        writes = []
        page.on('request', lambda request: writes.append((request.method, request.url)) if request.method in ('POST', 'PUT', 'DELETE') else None)
        held = []

        def hold_create(route):
            if route.request.method == 'POST':
                held.append(route)
            else:
                route.continue_()

        page.route('**/api/companies', hold_create)
        page.locator('#company-add').click()
        page.locator('#entry [name=name]').fill('  شرکت   تازه  ')
        with page.expect_request('**/api/companies'):
            page.evaluate("const form=document.querySelector('#entry');form.requestSubmit();form.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true}))")
        expect(page.locator('#entry button').last).to_be_disabled()
        assert len(held) == 1
        page.evaluate("void navigateTo('employees',2)")
        assert page.evaluate('({page,cid})') == {'page': 'companies', 'cid': 1}
        held[0].continue_()
        page.unroute('**/api/companies')
        page.locator('#dialog').wait_for(state='hidden')
        expect(page.locator('#notice')).to_contain_text('شرکت جدید ثبت شد')
        assert sum(method == 'POST' and url == base + '/api/companies' for method, url in writes) == 1
        expect(page.locator('#company option')).to_have_count(7)
        expect(page.locator('#company')).to_have_value('1')
        created = next(company for company in page.request.get(base + '/api/companies').json() if company['name'] == 'شرکت تازه')
        new_id = created['id']

        # A conflict preserves the entered draft instead of closing the modal.
        page.locator('#company-add').click()
        page.locator('#entry [name=name]').fill('شرکت تازه')
        page.locator('#entry button').last.click()
        expect(page.locator('#form-error')).to_contain_text('قبلاً ثبت شده')
        expect(page.locator('#entry [name=name]')).to_have_value('شرکت تازه')
        expect(page.locator('#dialog')).to_be_visible()
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('#close-dialog').click()

        # Renaming is reflected in the selected header and later reference lists.
        renamed = 'شرکت نام تازه <img src=x onerror=window.companyXss=1>'
        page.locator('[data-company-edit="1"]').click()
        page.locator('#entry [name=name]').fill(renamed)
        page.locator('#entry button').last.click()
        page.locator('#dialog').wait_for(state='hidden')
        expect(page.locator('#company-label')).to_have_text(renamed)
        expect(page.locator('[data-select-search-for=company]')).to_have_value(renamed)
        assert page.locator('#companies-table img').count() == 0
        assert page.evaluate('typeof window.companyXss') == 'undefined'
        assert page.request.get(base + '/api/data/employees?company_id=1').json()[0]['id'] == employee['id']
        page.screenshot(path='/tmp/nikan-companies-desktop.png', full_page=True)
        page.locator('#company-search').fill(str(new_id))
        expect(page.locator('#companies-table tbody tr')).to_have_count(1)
        page.locator('#company-search').fill('')
        for company_id, detail in ((1, 'پرسنل'), (2, 'کاربر')):
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator(f'[data-company-delete="{company_id}"]').click()
            expect(page.locator('#company-error')).to_contain_text(detail)
            expect(page.locator(f'[data-company-row="{company_id}"]')).to_have_count(1)

        # Selecting through the searchable header and deleting it chooses a valid replacement.
        page.locator('[data-select-search-for=company]').fill(str(new_id))
        page.locator('[data-select-search-for=company]').press('Enter')
        expect(page.locator('#company')).to_have_value(str(new_id))
        page.locator(f'[data-company-delete="{new_id}"]').wait_for()
        page.evaluate("cache={employees:[{id:999,name:'stale'}]};referenceOptions={jobs:['stale']};referenceOptionsCid=cid")
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator(f'[data-company-delete="{new_id}"]').click()
        expect(page.locator(f'[data-company-row="{new_id}"]')).to_have_count(0)
        expect(page.locator('#company')).to_have_value('1')
        assert page.evaluate('Object.keys(cache).length===0 && Object.keys(referenceOptions).length===0')
        page.locator('[data-page=branding]').click()
        expect(page.locator('#branding-key option[value=company-1]')).to_have_text(renamed)
        expect(page.locator(f'#branding-key option[value=company-{new_id}]')).to_have_count(0)

        for role in ('manager', 'viewer'):
            page.locator('#logout').click()
            page.locator('#auth [name=username]').fill('companies-' + role)
            page.locator('#auth [name=password]').fill(password)
            page.locator('#auth button').click()
            page.locator('.metrics').wait_for()
            expect(page.locator('[data-page=companies]')).to_have_count(0)
            assert page.request.get(base + '/api/companies').status == 403
        assert not errors, errors
        browser.close()


def check_empty(base):
    """All-empty-company removal, zero-company login and recovery with a new company."""
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        password = secrets.token_urlsafe(24)
        page.locator('#auth [name=username]').fill('empty-admin')
        page.locator('#auth [name=password]').fill(password)
        page.locator('#auth button').click()
        page.locator('.metrics').wait_for()
        page.locator('[data-page=companies]').click()
        for company_id in range(1, 7):
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator(f'[data-company-delete="{company_id}"]').click()
            expect(page.locator(f'[data-company-row="{company_id}"]')).to_have_count(0)
        expect(page.locator('#companies-table')).to_contain_text('هنوز شرکتی ثبت نشده است')
        expect(page.locator('#company')).to_be_disabled()
        expect(page.locator('[data-page=employees]')).to_be_disabled()
        page.locator('[data-page=users]').click()
        expect(page.locator('#table')).to_contain_text('empty-admin')
        expect(page.locator('#add')).to_have_count(0)
        page.locator('#logout').click()
        page.locator('#auth [name=username]').fill('empty-admin')
        page.locator('#auth [name=password]').fill(password)
        page.locator('#auth button').click()
        page.locator('#company-add').wait_for()
        assert page.evaluate("page==='companies' && cid===null && user.companies.length===0")
        page.locator('#company-add').click()
        page.locator('#entry [name=name]').fill('شرکت پس از شروع مجدد')
        page.locator('#entry button').last.click()
        page.locator('#dialog').wait_for(state='hidden')
        expect(page.locator('#company-label')).to_have_text('شرکت پس از شروع مجدد')
        expect(page.locator('[data-page=employees]')).to_be_enabled()
        page.screenshot(path='/tmp/nikan-companies-mobile.png', full_page=True)
        page.locator('[data-page=dashboard]').click()
        page.locator('.metrics').wait_for()
        assert page.evaluate('cid>6 && user.companies.length===1')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()


def run(checker):
    with tempfile.TemporaryDirectory(prefix='nikan-companies-browser-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = subprocess.Popen([sys.executable, 'run.py', '--no-browser', '--port', str(port)], cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'NIKAN_DATA_DIR': directory}, stdout=subprocess.DEVNULL)
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + '/api/status', timeout=1).close()
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError('Server exited')
                    time.sleep(.1)
            else:
                raise RuntimeError('Server did not become ready')
            checker(base)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == '__main__':
    run(check)
    run(check_empty)
    print('Companies browser passed: admin add/rename, duplicate draft retention, single-write locking, header/reference refresh, escaped names, protected personnel/users, selected deletion/cache reset, manager/viewer exclusion, empty-holding login and first-company recovery, mobile layout.')
