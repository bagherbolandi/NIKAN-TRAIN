"""Real Chromium regression checks for reference pickers and form integrity.

Run with .venv/bin/python tests/browser_select_smoke.py.
Uses a temporary database and never changes an existing installation.
"""
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
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'),
            headless=True, args=['--no-sandbox'],
        )
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(base)
        page.locator('#auth [name=username]').fill('select-smoke')
        page.locator('#auth [name=password]').fill(secrets.token_urlsafe(20))
        page.locator('#auth button').click()
        page.locator('.metrics').wait_for()
        csrf = {'X-CSRF-Token': page.request.get(base + '/api/me').json()['csrf']}

        def get(path):
            response = page.request.get(base + '/api/' + path)
            assert response.ok, response.text()
            return response.json()

        def picker(selector):
            native = page.locator(selector)
            expect(native).to_have_class('select-search-native')
            return native.locator('..').locator('.select-search-input')

        def choose(selector, query, label):
            field = picker(selector)
            field.fill(query)
            page.locator(selector).locator('..').get_by_role('option', name=label, exact=True).click()
            expect(field).to_have_value(label)

        response = page.request.post(base + '/api/competency-bank/adopt?company_id=1', headers=csrf)
        assert response.ok, response.text()
        references = get('reference-options?company_id=1')
        job = next(value for value in references['jobs'] if value == 'مدیر کارخانه')
        department = references['departments'][0]
        skills = get('data/skills?company_id=1')
        skill = next(value for value in skills if value['title'])

        # Real reference controls in the employee editor; Arabic and Persian
        # variants must match the same stored job title.
        page.locator('[data-page=employees]').click()
        page.locator('#add').click()
        page.locator('#entry [name=code]').fill('SELECT-001')
        page.locator('#entry [name=name]').fill('کیان یوسفی')
        choose('#entry [name=job]', 'مدير كارخانه', job)
        choose('#entry [name=department]', department[:5], department)
        expect(picker('#entry [name=job]')).to_have_attribute('aria-label', 'سمت شغلی')
        page.locator('#entry button').last.click()
        page.locator('#table').get_by_text('کیان یوسفی').wait_for()
        employee = next(row for row in get('data/employees?company_id=1') if row['code'] == 'SELECT-001')
        assert employee['job'] == job and employee['department'] == department

        # Company navigation still uses the selected database ID, and the
        # source native control remains usable for existing select_option code.
        company_two = page.locator('#company option[value="2"]').inner_text()
        choose('#company', company_two, company_two)
        expect(page.locator('#company')).to_have_value('2')
        expect(page.locator('#table').get_by_text('کیان یوسفی')).to_have_count(0)
        page.locator('#company').select_option('1')
        page.locator('#table').get_by_text('کیان یوسفی').wait_for()
        expect(picker('#company')).to_have_value(page.locator('#company option[value="1"]').inner_text())

        page.locator('[data-page=courses]').click()
        page.locator('#add').click()
        choose('#entry [name=skill_id]', skill['title'][:8], skill['title'])
        expect(page.locator('#entry [name=skill_id]')).to_have_value(str(skill['id']))
        page.locator('#entry [name=title]').fill('دوره آزمون انتخاب امن')
        page.locator('#entry [name=hours]').fill('2')
        # Typing an unknown title must clear the old ID, block submission, and
        # leave the editor open; Escape restores the last deliberate selection.
        old_count = len(get('data/courses?company_id=1'))
        field = picker('#entry [name=skill_id]')
        field.fill('موردی که در بانک وجود ندارد 9XYZ')
        expect(page.locator('#entry [name=skill_id]')).to_have_value('')
        expect(page.locator('#entry').locator('.select-search-status').filter(has_text='موردی یافت نشد')).to_be_visible()
        page.locator('#entry button').last.click()
        expect(page.locator('#dialog')).to_be_visible()
        expect(field).to_have_attribute('aria-invalid', 'true')
        assert len(get('data/courses?company_id=1')) == old_count
        field.press('Escape')
        expect(field).to_have_value(skill['title'])
        expect(page.locator('#dialog')).to_be_visible()
        page.locator('#entry button').last.click()
        expect(page.locator('#dialog')).not_to_be_visible()
        course = next(row for row in get('data/courses?company_id=1') if row['title'] == 'دوره آزمون انتخاب امن')
        assert course['skill_id'] == skill['id']

        # Person and course pickers on another real form use keyboard selection.
        page.locator('[data-page=certificates]').click()
        field = picker('#certificate-form [name=employee_id]')
        field.fill('كيان يوسفي')
        field.press('Enter')
        expect(page.locator('#certificate-form [name=employee_id]')).to_have_value(str(employee['id']))
        choose('#certificate-form [name=course_id]', 'دوره آزمون', course['title'])

        # Focused edge regressions use DOM-created forms, real browser validity,
        # pointer hit testing, and native mutation events rather than mocks.
        page.evaluate("""() => {
          const form = document.createElement('form'); form.id = 'picker-fixture';
          form.innerHTML = '<fieldset><label for="fixture-select">فهرست آزمون</label>' +
            '<select id="fixture-select" name="skill_id" required>' +
            '<option value="">انتخاب کنید</option><option value="old">گزینه قدیمی</option>' +
            '<option value="disabled" disabled>گزینه غیرفعال</option>' +
            '<option value="safe">كاربري ۱۲۳</option></select></fieldset>' +
            '<button type="submit">ارسال آزمون</button><button type="reset">بازنشانی آزمون</button>';
          form.onsubmit = event => { event.preventDefault(); window.fixtureSubmissions = (window.fixtureSubmissions || 0) + 1; };
          document.querySelector('#content').append(form);
        }""")
        fixture = picker('#fixture-select')
        page.locator('#picker-fixture button[type=submit]').click()
        expect(fixture).to_have_attribute('aria-invalid', 'true')
        assert page.evaluate('window.fixtureSubmissions || 0') == 0
        fixture.fill('کاربری 123')
        fixture.press('Enter')
        expect(page.locator('#fixture-select')).to_have_value('safe')
        fixture.fill('گزینه')
        fixture.press('ArrowDown')
        fixture.press('Enter')
        expect(page.locator('#fixture-select')).to_have_value('old')

        fixture.fill('جدید')
        page.locator('#fixture-select').evaluate("select => select.add(new Option('گزینه جدید', 'new'))")
        expect(fixture).to_have_value('جدید')
        page.locator('#fixture-select').locator('..').get_by_role('option', name='گزینه جدید', exact=True).click()
        expect(page.locator('#fixture-select')).to_have_value('new')
        page.locator('#fixture-select').evaluate("select => { select.value = 'safe'; }")
        expect(fixture).to_have_value('كاربري ۱۲۳')
        page.locator('#picker-fixture fieldset').evaluate('node => { node.disabled = true; }')
        expect(fixture).to_be_disabled()
        page.locator('#picker-fixture fieldset').evaluate('node => { node.disabled = false; }')
        expect(fixture).to_be_enabled()
        page.locator('#fixture-select').evaluate("node => node.setAttribute('readonly', '')")
        expect(fixture).to_have_attribute('readonly', '')
        fixture.click()
        expect(fixture).to_have_attribute('aria-expanded', 'false')
        page.locator('#fixture-select').evaluate("node => node.removeAttribute('readonly')")
        page.locator('#picker-fixture button[type=reset]').click()
        expect(fixture).to_have_value('انتخاب کنید')
        expect(page.locator('#fixture-select')).to_have_value('')

        # Option text is rendered literally even if a reference contains markup.
        payload = '<img src=x onerror="window.pickerXss=true">'
        page.locator('#fixture-select').evaluate('(select, text) => select.add(new Option(text, "html"))', payload)
        fixture.fill('<img')
        expect(page.locator('#fixture-select').locator('..').get_by_role('option', name=payload, exact=True)).to_be_visible()
        assert page.locator('.select-search-option img').count() == 0
        assert page.evaluate('window.pickerXss === undefined')

        # Dropdown options must be clickable above the native modal, including
        # when a long form scrolls and on a narrow mobile viewport.
        # Employee/course choices above intentionally left the certificate form
        # unsaved; explicitly discard it when the navigation guard asks.
        page.once('dialog', lambda dialog: dialog.accept())
        page.locator('[data-page=employees]').click()
        page.locator('#add').click()
        for width, height in [(1440, 1000), (390, 844)]:
            page.set_viewport_size({'width': width, 'height': height})
            field = picker('#entry [name=job]')
            field.fill('مدير كارخانه')
            option = page.locator('#entry [name=job]').locator('..').get_by_role('option', name=job, exact=True)
            expect(option).to_be_visible()
            assert option.evaluate("node => { const r=node.getBoundingClientRect(); return document.elementFromPoint(r.left+r.width/2,r.top+r.height/2) === node; }")
            option.click()
            expect(page.locator('#entry [name=job]')).to_have_value(job)
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.locator('#close-dialog').click()
        expect(page.locator('.select-search-popup:visible')).to_have_count(0)
        assert not errors, errors
        browser.close()
        print('Select browser smoke passed: reference forms, Persian search, native compatibility, valid IDs, keyboard, dynamic options, disabled/readonly/reset, literal text, modal and mobile.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='nikan-select-browser-') as data:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        process = subprocess.Popen(
            [sys.executable, 'run.py', '--no-browser', '--port', str(port)],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'NIKAN_DATA_DIR': data}, stdout=subprocess.DEVNULL,
        )
        try:
            for _ in range(80):
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
