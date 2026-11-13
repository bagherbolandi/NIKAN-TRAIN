"""Real Chromium regression for organization administration and productivity accord.

Uses synthetic people, isolated temporary data and the actual management UI.
Run: .venv/bin/python tests/browser_org_accord_smoke.py
"""
from datetime import date
from io import BytesIO
from decimal import Decimal
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from openpyxl import load_workbook
from playwright.sync_api import expect, sync_playwright


PASSWORD = 'Org-accord-' + secrets.token_urlsafe(20)


def check(base):
    checks = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'),
            headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        contexts = []
        try:
            page.goto(base)
            page.locator('#auth [name=username]').fill('org-accord-admin')
            page.locator('#auth [name=password]').fill(PASSWORD)
            page.locator('#auth button').click()
            page.locator('#shell').wait_for(state='visible')
            csrf = page.request.get(base + '/api/me').json()['csrf']

            def api(method, path, data=None, status=200, client=None, token=None):
                response = (client or page.request).fetch(
                    base + '/api/' + path, method=method, data=data,
                    headers={'X-CSRF-Token': token or csrf})
                assert response.status == status, (method, path, response.status, response.text())
                return response.json()

            def save_hrm():
                page.locator('#hrm-save').click()
                page.locator('#hrm-editor').wait_for(state='hidden')

            def company_form(name):
                page.locator('#entry [name=name]').fill(name)
                page.locator('#entry button').last.click()
                page.locator('#dialog').wait_for(state='hidden')

            def add_site(name, kind='کارخانه'):
                page.locator('#hrm-site-add').click()
                page.locator('#hrm-form [name=name]').fill(name)
                page.locator('#hrm-form [name=kind]').fill(kind)
                save_hrm()
                return next(row for row in api('GET', f'hrm/sites?company_id={company_id}') if row['name'] == name)

            # The real company and site forms support add, edit and safe deletion.
            page.locator('[data-page=companies]').click()
            page.locator('#company-add').click()
            company_form('شرکت موقت مرورگر')
            disposable = next(row for row in api('GET', 'companies') if row['name'] == 'شرکت موقت مرورگر')
            page.locator(f'[data-company-edit="{disposable["id"]}"]').click()
            company_form('شرکت موقت اصلاح‌شده')
            page.locator(f'[data-company-row="{disposable["id"]}"]').get_by_text('شرکت موقت اصلاح‌شده', exact=True).wait_for()
            page.locator(f'[data-company-sites="{disposable["id"]}"]').click()
            expect(page.locator('#company')).to_have_value(str(disposable['id']))
            expect(page.locator('[data-select-search-for=company]')).to_have_value('شرکت موقت اصلاح‌شده')
            page.locator('#hrm-site-add').wait_for()
            company_id = disposable['id']
            empty_site = add_site('سایت خالی مرورگر', 'دفتر')
            page.locator(f'[data-structure-edit="{empty_site["id"]}"]').click()
            page.locator('#hrm-form [name=name]').fill('سایت خالی اصلاح‌شده')
            save_hrm()
            expect(page.locator(f'[data-structure-site="{empty_site["id"]}"]')).to_contain_text('سایت خالی اصلاح‌شده')
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator(f'[data-structure-delete="{empty_site["id"]}"]').click()
            expect(page.locator(f'[data-structure-site="{empty_site["id"]}"]')).to_have_count(0)
            page.locator('[data-page=companies]').click()
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator(f'[data-company-delete="{disposable["id"]}"]').click()
            expect(page.locator(f'[data-company-row="{disposable["id"]}"]')).to_have_count(0)
            assert all(row['id'] != company_id for row in api('GET', 'companies'))
            checks.append('company/site create, rename and empty-only delete through real forms')

            company_id = api('POST', 'companies', {'name': 'شرکت بهره‌وری مرورگر'}, 201)['id']
            page.reload()
            page.locator('#shell').wait_for(state='visible')
            page.locator('[data-page=companies]').click()
            page.locator(f'[data-company-sites="{company_id}"]').click()
            expect(page.locator('#company')).to_have_value(str(company_id))
            expect(page.locator('[data-select-search-for=company]')).to_have_value('شرکت بهره‌وری مرورگر')
            site = add_site('کارخانه اول مرورگر')
            other_site = add_site('کارخانه دوم مرورگر', 'دفتر')
            page.locator('#hrm-roster-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=code]').fill('ORG-1001')
            form.locator('[name=name]').fill('اپراتور اول مرورگر')
            form.locator('[name=company_id]').select_option(str(company_id))
            form.locator('[name=site_id]').select_option(str(site['id']))
            form.locator('[name=department]').fill('تولید')
            form.locator('[name=job]').fill('اپراتور')
            form.locator('[name=status]').select_option('active')
            save_hrm()
            person = next(row for row in api('GET', f'hrm/people?company_id={company_id}') if row['code'] == 'ORG-1001')
            visitor = api('POST', f'hrm/people?company_id={company_id}', {
                'company_id': company_id, 'site_id': other_site['id'], 'code': 'ORG-1002',
                'name': 'همکار موقت مرورگر', 'department': 'تعمیرات', 'job': 'تکنسین', 'status': 'active'}, 201)
            unassigned = api('POST', f'hrm/people?company_id={company_id}', {
                'company_id': company_id, 'code': 'ORG-1003', 'name': 'همکار بدون سایت مرورگر',
                'department': 'اداری', 'job': 'کارشناس', 'status': 'active'}, 201)
            today = date.today().isoformat()
            api('POST', f'hrm/temporary_assignments?company_id={company_id}', {
                'employee_id': visitor['id'], 'target_site_id': site['id'],
                'start_date': today, 'end_date': today, 'activity_location': 'سالن اول', 'status': 'active'}, 201)
            page.locator('[data-page=hrm_structure]').click()
            page.locator(f'[data-structure-people="{site["id"]}"]').click()
            expect(page.locator('#hrm-roster-current')).to_contain_text(person['name'])
            expect(page.locator('#hrm-roster-current')).not_to_contain_text(visitor['name'])
            expect(page.locator('#hrm-roster-temporary')).to_contain_text(visitor['name'])
            page.locator('#hrm-roster-site').select_option('unassigned')
            expect(page.locator('#hrm-roster-current')).to_contain_text(unassigned['name'])
            expect(page.locator('#hrm-roster-current')).not_to_contain_text(person['name'])
            page.locator('#hrm-roster-site').select_option(str(site['id']))
            page.locator('#hrm-roster-search').fill('ORG-1001')
            expect(page.locator('#hrm-roster-current tbody tr')).to_have_count(1)
            page.locator('#hrm-roster-search').fill('')
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator(f'[data-structure-delete="{site["id"]}"]').click()
            expect(page.locator('#hrm-structure-error')).not_to_be_empty()
            expect(page.locator(f'[data-structure-site="{site["id"]}"]')).to_have_count(1)
            assert api('GET', f'hrm/people/{person["id"]}?company_id={company_id}')['site_id'] == site['id']
            page.screenshot(path='/tmp/nikan-013-structure.png', full_page=True)
            checks.append('current site roster separates temporary visitors and unassigned people; linked delete preserves staff')

            def create_account(username, permissions, site_id=site['id']):
                api('POST', 'hrm/access/users', {
                    'username': username, 'password': PASSWORD, 'role': 'company_hr',
                    'scopes': [{'company_id': company_id, 'site_id': site_id, 'department': ''}],
                    'permissions': permissions, 'training_access': False}, 201)
                context = browser.new_context(viewport={'width': 1440, 'height': 1000})
                contexts.append(context)
                child = context.new_page()
                child.on('pageerror', lambda error: errors.append(str(error)))
                child.goto(base)
                child.locator('#auth [name=username]').fill(username)
                child.locator('#auth [name=password]').fill(PASSWORD)
                child.locator('#auth button').click()
                child.locator('#shell').wait_for(state='visible')
                token = context.request.get(base + '/api/me').json()['csrf']
                return context, child, token

            local, local_page, local_token = create_account('roster-viewer', {'personnel': {'read': True}})
            local_page.locator('[data-page=hrm_structure]').click()
            expect(local_page.locator('#hrm-roster-current')).to_contain_text(person['name'])
            expect(local_page.locator('#hrm-roster-current')).not_to_contain_text(visitor['name'])
            expect(local_page.locator('#hrm-site-add,[data-structure-edit],[data-structure-delete]')).to_have_count(0)
            api('POST', f'hrm/sites?company_id={company_id}', {'name': 'غیرمجاز', 'kind': 'office'}, 403, local.request, local_token)
            api('DELETE', f'hrm/sites/{site["id"]}?company_id={company_id}', {'version': site['version']}, 403, local.request, local_token)
            api('GET', f'hrm/site-roster?company_id={company_id}&site_id={other_site["id"]}', status=403, client=local.request, token=local_token)
            checks.append('local personnel reader sees only assigned site and cannot change organization')

            # Productivity fixtures and actual calculation UI follow below.
            _check_accord(page, api, create_account, company_id, site, person, visitor, checks)

            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('[data-page=hrm_structure]').click()
            page.locator('#hrm-roster-current').wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile horizontal overflow'
            page.screenshot(path='/tmp/nikan-013-structure-mobile.png', full_page=True)
            assert not errors, errors
            report = {'passed': True, 'native_windows_execution': False, 'checks': checks,
                      'javascript_errors': errors, 'mobile_horizontal_overflow': False}
            Path('/tmp/nikan-013-browser-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return report
        except BaseException:
            if not page.is_closed():
                page.screenshot(path='/tmp/nikan-013-browser-failure.png', full_page=True)
                Path('/tmp/nikan-013-browser-failure.html').write_text(page.content())
            raise
        finally:
            for context in contexts:
                context.close()
            browser.close()


def _check_accord(page, api, create_account, company_id, site, person, visitor, checks):
    term = api('POST', f'hrm/evaluation_periods?company_id={company_id}', {
        'company_id': company_id, 'site_id': site['id'], 'title': 'ارزیابی آکورد مرورگر',
        'start_date': '1405/01/01', 'end_date': '1405/01/31', 'status': 'open'}, 201)
    criteria = []
    scores = []
    for title, weight, score in [('کیفیت محصول', 60, 80), ('تحویل به‌موقع', 40, 50)]:
        criterion = api('POST', f'hrm/performance_criteria?company_id={company_id}', {
            'company_id': company_id, 'site_id': site['id'], 'period_id': term['id'],
            'title': title, 'weight': weight, 'max_score': 100}, 201)
        criteria.append(criterion)
        scores.append(api('POST', f'hrm/performance_scores?company_id={company_id}', {
            'employee_id': person['id'], 'period_id': term['id'],
            'criterion_id': criterion['id'], 'score': score}, 201))

    page.locator('[data-page=hrm_productivity]').click()
    page.locator('[data-productivity-tab=accord_policies]').click()
    page.locator('#hrm-add').click()
    form = page.locator('#hrm-form')
    form.locator('[name=company_id]').select_option(str(company_id))
    form.locator('[name=site_id]').select_option(str(site['id']))
    form.locator('[name=period_id]').select_option(str(term['id']))
    form.locator('[name=title]').fill('آکورد فردی مرورگر')
    form.locator('[name=method]').select_option('individual_base')
    form.locator('[name=minimum_score]').fill('0')
    page.locator('#hrm-save').click()
    page.locator('#hrm-editor').wait_for(state='hidden')
    policy = next(row for row in api('GET', f'hrm/accord_policies?company_id={company_id}')
                  if row['title'] == 'آکورد فردی مرورگر')
    page.locator('[data-productivity-tab=accord_entries]').click()
    page.locator('#hrm-add').click()
    form = page.locator('#hrm-form')
    form.locator('[name=policy_id]').select_option(str(policy['id']))
    form.locator('[name=employee_id]').select_option(str(person['id']))
    form.locator('[name=base_rials]').fill('1000001')
    form.locator('[name=scheduled_minutes]').fill('480')
    form.locator('[name=actual_minutes]').fill('240')
    form.locator('[name=note]').fill('مشارکت تأییدشده برای آزمون محاسبه دقیق ریالی')
    page.locator('#hrm-save').click()
    page.locator('#hrm-editor').wait_for(state='hidden')
    entry = next(row for row in api('GET', f'hrm/accord_entries?company_id={company_id}')
                 if row['policy_id'] == policy['id'])
    page.locator('[data-productivity-tab=calculation]').click()
    page.locator('#hrm-accord-policy').select_option(str(policy['id']))
    page.locator('#hrm-accord-review').click()
    page.locator(f'[data-accord-person="{person["id"]}"]').wait_for()
    report = api('GET', f'hrm/accord_report?company_id={company_id}&policy_id={policy["id"]}')
    row = report['rows'][0]
    assert Decimal(str(row['score_percent'])) == Decimal('68'), row
    assert Decimal(str(row['participation_percent'])) == Decimal('50'), row
    assert row['accord_rials'] == 340000, row
    assert report['metrics']['total_accord_rials'] == 340000
    assert report['can_finalize'] is False, 'An open evaluation period was finalized'
    expect(page.locator('#hrm-accord-finalize')).to_be_disabled()
    checks.append('real accord forms calculate 60/40 weighted score, participation and exact whole-rial award')

    latest_term = next(row for row in api('GET', f'hrm/evaluation_periods?company_id={company_id}')
                       if row['id'] == term['id'])
    api('PUT', f'hrm/evaluation_periods/{term["id"]}?company_id={company_id}',
        {'version': latest_term['version'], 'status': 'closed'})
    page.locator('#hrm-accord-review').click()
    expect(page.locator('#hrm-accord-finalize')).to_be_enabled()
    page.once('dialog', lambda dialog: dialog.accept())
    with page.expect_response(lambda response: response.request.method == 'POST' and
                              response.url.split('?')[0].endswith(f'/accord_policies/{policy["id"]}/finalize')) as saved:
        page.locator('#hrm-accord-finalize').click()
    assert saved.value.status == 200, saved.value.text()
    finalized = api('GET', f'hrm/accord_report?company_id={company_id}&policy_id={policy["id"]}')
    assert finalized['status'] == 'finalized'
    assert finalized['rows'][0]['accord_rials'] == 340000
    expect(page.locator('#hrm-accord-finalize')).to_have_count(0)
    with page.expect_download() as downloaded:
        page.locator('#hrm-accord-export').click()
    workbook = load_workbook(BytesIO(Path(downloaded.value.path()).read_bytes()), read_only=True)
    cells = [cell for sheet in workbook for row in sheet.values for cell in row]
    workbook.close()
    assert person['name'] in cells and 340000 in cells, cells
    page.screenshot(path='/tmp/nikan-013-accord.png', full_page=True)
    checks.append('closed-period review finalizes immutable result and exports the actual financial result to Excel')

    # Reopening an evaluation and correcting a source grade cannot rewrite an
    # already finalized financial snapshot. Direct writes to the award are denied.
    latest_term = next(row for row in api('GET', f'hrm/evaluation_periods?company_id={company_id}')
                       if row['id'] == term['id'])
    api('PUT', f'hrm/evaluation_periods/{term["id"]}?company_id={company_id}',
        {'version': latest_term['version'], 'status': 'open'})
    api('PUT', f'hrm/performance_scores/{scores[0]["id"]}?company_id={company_id}',
        {'version': scores[0]['version'], 'score': 0})
    frozen = api('GET', f'hrm/accord_report?company_id={company_id}&policy_id={policy["id"]}')
    assert frozen['rows'] == finalized['rows']
    api('PUT', f'hrm/accord_entries/{entry["id"]}?company_id={company_id}',
        {'version': entry['version'], 'base_rials': '2000001'}, 409)
    checks.append('finalized accord remains stable after later source-score correction and rejects monetary edits')

    # A recorded grade is never taken as a complete evaluation when another
    # configured criterion has no score; no zero or financial award is invented.
    missing_term = api('POST', f'hrm/evaluation_periods?company_id={company_id}', {
        'company_id': company_id, 'site_id': site['id'], 'title': 'ارزیابی ناقص مرورگر',
        'start_date': '1405/02/01', 'end_date': '1405/02/31', 'status': 'open'}, 201)
    missing_criteria = []
    for title in ['کیفیت ثبت‌شده', 'ایمنی ثبت‌نشده']:
        missing_criteria.append(api('POST', f'hrm/performance_criteria?company_id={company_id}', {
            'company_id': company_id, 'site_id': site['id'], 'period_id': missing_term['id'],
            'title': title, 'weight': 1, 'max_score': 100}, 201))
    api('POST', f'hrm/performance_scores?company_id={company_id}', {
        'employee_id': person['id'], 'period_id': missing_term['id'],
        'criterion_id': missing_criteria[0]['id'], 'score': 90}, 201)
    missing_policy = api('POST', f'hrm/accord_policies?company_id={company_id}', {
        'company_id': company_id, 'site_id': site['id'], 'period_id': missing_term['id'],
        'title': 'آکورد ناقص مرورگر', 'method': 'individual_base', 'minimum_score': 0}, 201)
    api('POST', f'hrm/accord_entries?company_id={company_id}', {
        'policy_id': missing_policy['id'], 'employee_id': person['id'], 'base_rials': '1000000',
        'scheduled_minutes': 480, 'actual_minutes': 480}, 201)
    api('PUT', f'hrm/evaluation_periods/{missing_term["id"]}?company_id={company_id}',
        {'version': missing_term['version'], 'status': 'closed'})
    page.locator('[data-page=hrm_productivity]').click()
    page.locator('[data-productivity-tab=calculation]').click()
    page.locator('#hrm-accord-policy').select_option(str(missing_policy['id']))
    page.locator('#hrm-accord-review').click()
    expect(page.locator('#hrm-accord-finalize')).to_be_disabled()
    incomplete = api('GET', f'hrm/accord_report?company_id={company_id}&policy_id={missing_policy["id"]}')
    assert incomplete['rows'][0]['is_complete'] is False
    assert incomplete['rows'][0]['accord_rials'] is None
    assert 'ایمنی ثبت‌نشده' in incomplete['rows'][0]['missing_criteria']
    api('POST', f'hrm/accord_policies/{missing_policy["id"]}/finalize?company_id={company_id}',
        {'version': incomplete['policy']['version'], 'calculation_token': incomplete['calculation_token']}, 409)
    checks.append('incomplete criteria stay explicit and block finalization without inventing a zero award')

    performance, performance_page, performance_token = create_account(
        'performance-without-money', {'performance': {'read': True}})
    expect(performance_page.locator('[data-page=hrm_productivity]')).to_have_count(0)
    api('GET', f'hrm/accord_results?company_id={company_id}', status=403,
        client=performance.request, token=performance_token)
    api('GET', f'hrm/accord_report?company_id={company_id}&policy_id={policy["id"]}', status=403,
        client=performance.request, token=performance_token)
    financial, financial_page, financial_token = create_account(
        'accord-read-only', {'productivity': {'read': True, 'export': True}})
    financial_page.locator('[data-page=hrm_productivity]').click()
    expect(financial_page.locator('#hrm-add')).to_have_count(0)
    financial_page.locator('[data-productivity-tab=calculation]').click()
    financial_page.locator('#hrm-accord-policy').select_option(str(policy['id']))
    financial_page.locator('#hrm-accord-review').click()
    expect(financial_page.locator(f'[data-accord-person="{person["id"]}"]')).to_contain_text(person['name'])
    api('POST', f'hrm/accord_policies/{policy["id"]}/finalize?company_id={company_id}',
        {'version': finalized['policy']['version'], 'calculation_token': 'not-authorized'}, 403,
        financial.request, financial_token)
    api('PUT', f'hrm/accord_entries/{entry["id"]}?company_id={company_id}',
        {'version': entry['version'], 'base_rials': '3000000'}, 403,
        financial.request, financial_token)
    checks.append('performance permission does not grant financial access; accord reader can view frozen result but cannot change it')
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#hrm-accord-policy').select_option(str(policy['id']))
    page.locator('#hrm-accord-review').click()
    page.locator(f'[data-accord-person="{person["id"]}"]').wait_for()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'accord mobile horizontal overflow'
    page.screenshot(path='/tmp/nikan-013-accord-mobile.png', full_page=True)
    page.set_viewport_size({'width': 1440, 'height': 1000})


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='nikan-org-accord-browser-') as data:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        process = subprocess.Popen([sys.executable, 'run.py', '--no-browser', '--port', str(port)],
                                   cwd=root, env={**os.environ, 'NIKAN_DATA_DIR': data},
                                   stdout=subprocess.DEVNULL)
        try:
            for _ in range(150):
                try:
                    urllib.request.urlopen(base + '/api/status', timeout=1).close(); break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError('Server exited')
                    time.sleep(.1)
            else:
                raise RuntimeError('Server did not become ready')
            check(base)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
