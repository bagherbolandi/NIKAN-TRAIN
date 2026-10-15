"""Real-browser coverage for the holding calendar and company-owned course controls."""
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def check(base):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'),
            headless=True, args=['--no-sandbox'],
        )
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator('#auth-title').filter(has_text='ساخت حساب').wait_for()
        password = secrets.token_urlsafe(20)
        page.locator('#auth [name=username]').fill('calendar-admin')
        page.locator('#auth [name=password]').fill(password)
        page.locator('#auth button').click()
        page.locator('.metrics').wait_for()
        csrf = page.request.get(base + '/api/me').json()['csrf']

        def post(path, body):
            response = page.request.post(base + '/api/' + path, data=body, headers={'X-CSRF-Token': csrf})
            assert response.ok, (path, response.status, response.text())
            return response.json()

        skills = {}
        for company_id in (1, 2):
            post('competency-bank/adopt?company_id=' + str(company_id), {})
            skills[company_id] = page.request.get(base + '/api/data/skills?company_id=' + str(company_id)).json()[0]['id']
        employee1 = post('data/employees?company_id=1', {'code': 'EPLAN1', 'name': 'فرد شرکت اول', 'job': 'کارشناس', 'department': 'آموزش'})['id']
        employee2 = post('data/employees?company_id=2', {'code': 'EPLAN2', 'name': 'فرد شرکت دوم <img src=x onerror=window.personnelXss=1>', 'job': 'کارشناس', 'department': 'آموزش'})['id']

        def course(company_id, title, scope='holding', year=1405):
            return post('data/courses?company_id=' + str(company_id), {
                'skill_id': skills[company_id], 'title': title, 'calendar_scope': scope,
                'date': f'{year}/03/25', 'instructor': 'مدرس تقویم', 'hours': 2,
                'cost': 998877, 'status': 'planned',
            })

        course(1, 'دوره تجمیعی شرکت اول')
        unsafe_title = 'دوره تجمیعی شرکت دوم <img src=x onerror=window.calendarXss=1>'
        holding2 = course(2, unsafe_title)['id']
        course(1, 'دوره داخلی شرکت اول', 'internal')
        course(2, 'دوره داخلی شرکت دوم', 'internal')
        course(1, 'دوره سال آینده شرکت اول', year=1406)
        viewer_password = secrets.token_urlsafe(20)
        post('users', {'username': 'calendar-viewer', 'password': viewer_password, 'role': 'viewer', 'company_id': 1})
        manager_password = secrets.token_urlsafe(20)
        post('users', {'username': 'calendar-manager', 'password': manager_password, 'role': 'manager', 'company_id': 1})

        page.locator('[data-page=courses]').click()
        expect(page.locator('#calendar-scope')).to_have_value('holding')
        current_year = page.evaluate("Number(new Intl.DateTimeFormat('en-US-u-ca-persian-nu-latn',{year:'numeric',timeZone:'Asia/Tehran'}).format(new Date()).match(/\\d+/)[0])")
        expect(page.locator('#calendar-year')).to_have_value(str(current_year))
        page.locator('#calendar-year').select_option('1405')
        expect(page.locator('.month-card')).to_have_count(12)
        expect(page.locator('.calendar-event')).to_have_count(2)
        expect(page.locator('#calendar-grid')).to_contain_text('دوره تجمیعی شرکت اول')
        expect(page.locator('#calendar-grid')).to_contain_text(unsafe_title)
        assert page.locator('#calendar-grid img').count() == 0
        assert page.evaluate('typeof window.calendarXss') == 'undefined'
        assert '998877' not in page.locator('#calendar-grid').inner_text()
        expect(page.locator('#table tbody tr')).to_have_count(1)
        assert page.locator('#table').get_by_text(unsafe_title).count() == 0
        page.locator('#calendar-search').fill('شرکت دوم')
        expect(page.locator('.calendar-event')).to_have_count(1)
        page.locator('#calendar-search').fill('')

        # The holding administrator can plan a company 2 event while company 1 is selected.
        page.locator(f'[data-plan-course="{holding2}"]').click()
        expect(page.locator('#participant-table tbody tr')).to_have_count(2)
        assert page.locator('#participant-table img').count() == 0
        assert page.evaluate('typeof window.personnelXss') == 'undefined'
        page.locator('#participant-search').fill('EPLAN1')
        expect(page.locator('#participant-table tbody tr')).to_have_count(1)
        page.locator(f'[data-participant="{employee1}"]').check()
        page.locator('#participant-company').select_option('2')
        expect(page.locator('#participant-table')).to_contain_text('پرسنلی با این جستجو')
        page.locator('#participant-search').fill('')
        page.locator(f'[data-participant="{employee2}"]').check()
        expect(page.locator('#participant-selected')).to_contain_text('۲ نفر از ۲ شرکت')
        page.locator('#participant-company').select_option('1')
        expect(page.locator(f'[data-participant="{employee1}"]')).to_be_checked()
        page.screenshot(path='/tmp/nikan-holding-personnel-plan.png', full_page=True)
        # Hold the real roster write to verify that a second submission and
        # context changes cannot race with a shared-course save.
        planner_route = re.compile(rf'/api/courses/{holding2}/participants$')
        planner_requests, held_planner_writes = [], []

        def hold_planner_write(route):
            if route.request.method == 'PUT':
                planner_requests.append(route.request.post_data_json)
                held_planner_writes.append(route)
            else:
                route.continue_()

        page.route(planner_route, hold_planner_write)
        with page.expect_request(planner_route):
            page.locator('#save-course-participants').evaluate('button => { button.click(); button.click(); }')
        expect(page.locator('#save-course-participants')).to_be_disabled()
        expect(page.locator('#participant-search')).to_be_disabled()
        expect(page.locator('#participant-company')).to_be_disabled()
        expect(page.locator('#participant-company').locator('..').locator('.select-search-input')).to_be_disabled()
        expect(page.locator(f'[data-participant="{employee1}"]')).to_be_disabled()
        for button in page.locator('#course-participant-dialog [data-plan-close]').all():
            expect(button).to_be_disabled()
        page.locator('#company').evaluate("""select => {
            select.value = '2'; select.dispatchEvent(new Event('change', {bubbles:true}));
        }""")
        expect(page.locator('#company')).to_have_value('1')
        page.locator('[data-page=employees]').evaluate('button => button.click()')
        expect(page.locator('[data-page=courses]')).to_have_class('active')
        expect(page.locator('#course-participant-dialog')).to_be_visible()
        expect(page.locator('#notice')).to_contain_text('ذخیره در حال انجام است')
        assert len(planner_requests) == len(held_planner_writes) == 1
        assert set(planner_requests[0]['employee_ids']) == {employee1, employee2}
        held_planner_writes.pop().continue_()
        expect(page.locator('#course-participant-dialog')).to_have_count(0)
        expect(page.locator('#notice')).to_contain_text('برنامه‌ریزی افراد دوره مشترک ذخیره شد')
        assert len(planner_requests) == 1
        page.unroute(planner_route, hold_planner_write)
        roster = page.request.get(base + f'/api/courses/{holding2}/participants').json()
        assert {person['id'] for person in roster['participants']} == {employee1, employee2}
        page.locator(f'[data-plan-course="{holding2}"]').click()
        expect(page.locator(f'[data-participant="{employee1}"]')).to_be_checked()
        expect(page.locator(f'[data-participant="{employee2}"]')).to_be_checked()
        page.locator(f'[data-participant="{employee1}"]').uncheck()
        page.locator('#course-participant-dialog [data-plan-close]').last.click()
        roster = page.request.get(base + f'/api/courses/{holding2}/participants').json()
        assert {person['id'] for person in roster['participants']} == {employee1, employee2}

        page.locator('#company').select_option('2')
        expect(page.locator('#course-management-title')).to_contain_text('نور ایستا خراسان')
        expect(page.locator('.calendar-event')).to_have_count(2)
        expect(page.locator('#table')).to_contain_text(unsafe_title)
        assert page.locator('#table').get_by_text('دوره تجمیعی شرکت اول', exact=True).count() == 0
        page.locator('#calendar-scope').select_option('internal')
        expect(page.locator('#calendar-grid')).to_contain_text('دوره داخلی شرکت دوم')
        expect(page.locator('.calendar-event')).to_have_count(1)
        page.locator('#company').select_option('1')
        expect(page.locator('#calendar-grid')).to_contain_text('دوره داخلی شرکت اول')
        assert 'دوره داخلی شرکت دوم' not in page.locator('#calendar-grid').inner_text()
        expect(page.locator('#table')).to_contain_text('دوره داخلی شرکت اول')

        page.locator('#calendar-year').select_option('1406')
        expect(page.locator('.calendar-event')).to_have_count(0)
        expect(page.locator('#calendar-state')).to_contain_text('دوره‌ای')
        page.locator('#calendar-scope').select_option('holding')
        expect(page.locator('#calendar-grid')).to_contain_text('دوره سال آینده شرکت اول')
        expect(page.locator('.calendar-event')).to_have_count(1)
        page.locator('#calendar-year').select_option('1405')
        page.locator('#table [data-edit]').click()
        expect(page.locator('#entry [name=calendar_scope]')).to_have_value('holding')
        page.locator('#entry [name=calendar_scope]').select_option('internal')
        page.locator('#entry button').last.click()
        expect(page.locator('#notice')).to_contain_text('ذخیره شد')
        expect(page.locator('.calendar-event')).to_have_count(1)
        page.locator('#calendar-scope').select_option('internal')
        expect(page.locator('.calendar-event')).to_have_count(2)
        page.locator('#add').click()
        expect(page.locator('#entry [name=calendar_scope]')).to_have_value('internal')
        page.locator('#entry [name=title]').fill('دوره داخلی تازه')
        page.locator('#entry [name=date]').fill('1405/04/01')
        page.locator('#entry [name=hours]').fill('3')
        page.locator('#entry button').last.click()
        expect(page.locator('#notice')).to_contain_text('ذخیره شد')
        expect(page.locator('#calendar-grid')).to_contain_text('دوره داخلی تازه')
        page.screenshot(path=os.environ.get('NIKAN_CALENDAR_SCREENSHOT', '/tmp/nikan-training-calendar.png'), full_page=True)

        def login(name, secret):
            page.locator('#logout').click()
            page.locator('#auth [name=username]').fill(name)
            page.locator('#auth [name=password]').fill(secret)
            page.locator('#auth button').click()
            page.locator('.metrics').wait_for()
            page.locator('[data-page=courses]').click()
            page.locator('#calendar-year').select_option('1405')
            expect(page.locator('#calendar-scope')).to_have_count(0)
            expect(page.locator('[data-page=courses]')).to_contain_text('آموزش‌های داخلی شرکت')
            assert page.locator('#nav').get_by_text('تقویم تجمیعی', exact=False).count() == 0

        login('calendar-manager', manager_password)
        expect(page.locator('#calendar-grid')).to_contain_text('دوره داخلی تازه')
        assert unsafe_title not in page.locator('#calendar-grid').inner_text()
        assert page.locator('[data-plan-course]').count() == 0
        assert page.request.get(base + '/api/calendar?scope=holding').status == 403
        assert page.request.get(base + '/api/holding/personnel').status == 403
        assert page.request.get(base + f'/api/courses/{holding2}/participants').status == 403
        expect(page.locator('#table [data-edit]')).to_have_count(3)
        page.locator('#add').click()
        expect(page.locator('#entry [name=calendar_scope]')).to_have_value('internal')
        expect(page.locator('#entry [name=calendar_scope] option')).to_have_count(1)
        assert page.locator('#entry [name=calendar_scope] option[value=holding]').count() == 0
        page.locator('#entry [name=title]').fill('دوره ساخته‌شده توسط شرکت')
        page.locator('#entry [name=date]').fill('1405/05/01')
        page.locator('#entry [name=hours]').fill('2')
        page.locator('#entry button').last.click()
        expect(page.locator('#notice')).to_contain_text('ذخیره شد')
        expect(page.locator('#calendar-grid')).to_contain_text('دوره ساخته‌شده توسط شرکت')
        created = next(row for row in page.request.get(base + '/api/data/courses?company_id=1').json()
                       if row['title'] == 'دوره ساخته‌شده توسط شرکت')
        assert created['calendar_scope'] == 'internal'

        login('calendar-viewer', viewer_password)
        expect(page.locator('#calendar-grid')).to_contain_text('دوره داخلی تازه')
        assert unsafe_title not in page.locator('#calendar-grid').inner_text()
        assert page.locator('[data-plan-course]').count() == 0
        assert page.request.get(base + '/api/calendar?scope=holding').status == 403
        assert page.request.get(base + '/api/holding/personnel').status == 403
        assert page.request.get(base + f'/api/courses/{holding2}/participants').status == 403
        assert page.locator('#add,[data-edit],[data-delete]').count() == 0
        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        api_error = '<img src=x onerror=window.calendarXss=2> calendar unavailable'
        page.route('**/api/calendar?*', lambda route: route.fulfill(status=503, json={'error': api_error}))
        page.locator('[data-page=courses]').click()
        expect(page.locator('#calendar-state')).to_contain_text(api_error)
        assert page.locator('#calendar-state img').count() == 0
        expect(page.locator('#retry-calendar')).to_be_visible()
        page.locator('#calendar-year').select_option('1406')
        expect(page.locator('#calendar-state')).to_contain_text(api_error)
        page.unroute('**/api/calendar?*')
        page.locator('#retry-calendar').click()
        expect(page.locator('#calendar-state')).not_to_contain_text(api_error)
        expect(page.locator('#retry-calendar')).to_be_hidden()
        page.locator('#calendar-year').select_option('1405')
        expect(page.locator('#calendar-grid')).to_contain_text('دوره ساخته‌شده توسط شرکت')
        assert not errors, errors
        browser.close()
    print('Calendar browser passed: holding/internal views, year/search, safe text, company-owned mutations, holding personnel planning with retained selections, cancellation, locked writes and duplicate prevention, admin-only consolidated access, company internal creation, viewer read-only, escaped API errors and retry.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='nikan-calendar-browser-') as data:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        root = Path(__file__).resolve().parents[1]
        process = subprocess.Popen(
            [sys.executable, 'run.py', '--no-browser', '--port', str(port)], cwd=root,
            env={**os.environ, 'NIKAN_DATA_DIR': data}, stdout=subprocess.DEVNULL,
        )
        try:
            for _ in range(60):
                try:
                    urllib.request.urlopen(base + '/api/status', timeout=1).close()
                    break
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
                process.kill()
                process.wait()
