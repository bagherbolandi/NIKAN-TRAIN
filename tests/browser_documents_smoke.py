"""Optional live Chromium regression test for personal document write recovery.

Run with .venv/bin/python tests/browser_documents_smoke.py. The standalone
runner creates an isolated database; check(base) can also target a fresh server.
"""
import os
from pathlib import Path
import re
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
        page.locator('#auth [name=username]').fill('documents-smoke')
        page.locator('#auth [name=password]').fill(secrets.token_urlsafe(20))
        page.locator('#auth button').click()
        page.locator('.metrics').wait_for()
        headers = {'X-CSRF-Token': page.request.get(base + '/api/me').json()['csrf']}

        def get(path):
            response = page.request.get(base + '/api/' + path)
            assert response.ok, (path, response.status, response.text())
            return response.json()

        def write(method, path, data):
            response = getattr(page.request, method)(base + '/api/' + path, headers=headers, data=data)
            assert response.ok, (path, response.status, response.text())
            return response.json()

        job = 'کارشناس آزمون اسناد'
        master_commitments = 'تعهدات مرجع شغل'
        write('post', 'data/job_profiles?company_id=1', {
            'job': job, 'definition': 'تعریف مرجع شغل',
            'commitments': master_commitments, 'department': 'آموزش',
        })
        for code, title in [('DOC-A', 'مهارت اول آزمون سند'), ('DOC-B', 'مهارت دوم آزمون سند')]:
            write('post', 'data/skills?company_id=1', {'code': code, 'title': title, 'category': 'آزمون اسناد'})
        write('put', 'job-workspace?company_id=1&job=' + urllib.parse.quote(job), {
            'items': [{'code': 'DOC-A', 'level': 3}, {'code': 'DOC-B', 'level': 2}],
        })
        people = [write('post', 'data/employees?company_id=1', {
            'code': f'DOC-{number}', 'name': f'فرد آزمون سند {number}',
            'job': job, 'department': 'آموزش',
        })['id'] for number in (1, 2)]
        owner = people[0]
        document_path = f'passports/{owner}/document?company_id=1'
        requirements_path = f'passports/{owner}/requirements?company_id=1'
        page.locator('[data-page=passports]').click()
        page.locator('#person').select_option(str(owner))
        page.locator('#edit-person-document').wait_for()

        def assert_company_locked():
            # A native change event exercises the same capture guard as the
            # searchable company picker, without clicking behind a modal.
            page.locator('#company').evaluate("""select => {
                select.value = '2'; select.dispatchEvent(new Event('change', {bubbles: true}));
            }""")
            expect(page.locator('#company')).to_have_value('1')
            expect(page.locator('#person')).to_have_value(str(owner))
            expect(page.locator('#notice')).to_contain_text('ذخیره در حال انجام است')

        # First fail the document PUT at the network boundary, then hold the
        # retry so busy controls, duplicate-click suppression and context lock
        # can be checked before allowing the real server write.
        document_requests, held_documents = [], []
        document_failure = {'pending': True}
        document_route = re.compile(rf'/api/passports/{owner}/document\?company_id=1$')

        def intercept_document(route):
            if route.request.method != 'PUT':
                route.continue_()
                return
            document_requests.append(route.request.post_data_json)
            if document_failure['pending']:
                document_failure['pending'] = False
                route.fulfill(status=503, json={'error': 'خطای آزمایشی ذخیره سند'})
            else:
                held_documents.append(route)

        page.route(document_route, intercept_document)
        page.locator('#edit-person-document').click()
        commitments = page.locator('#entry [name=commitments]')
        personal_commitments = 'تعهد اختصاصی فرد اول؛ متن پس از خطا حفظ شود'
        commitments.fill(personal_commitments)
        submit = page.locator('#entry button').last
        submit.click()
        expect(page.locator('#form-error')).to_have_text('خطای آزمایشی ذخیره سند')
        expect(page.locator('#dialog')).to_be_visible()
        expect(commitments).to_have_value(personal_commitments)
        expect(commitments).to_be_enabled()
        expect(submit).to_be_enabled()
        assert get(document_path)['overrides'] == {}
        assert len(document_requests) == 1

        with page.expect_request(document_route):
            submit.evaluate('button => { button.click(); button.click(); }')
        expect(submit).to_be_disabled()
        expect(commitments).to_be_disabled()
        expect(page.locator('#close-dialog')).to_be_disabled()
        assert_company_locked()
        assert len(document_requests) == 2 and len(held_documents) == 1
        assert document_requests[-1]['overrides']['commitments'] == personal_commitments
        held_documents.pop().continue_()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(page.locator('#passport')).to_contain_text(personal_commitments)
        assert get(document_path)['effective']['commitments'] == personal_commitments
        assert get(f'passports/{people[1]}/document?company_id=1')['effective']['commitments'] == master_commitments
        assert get('data/job_profiles?company_id=1')[0]['commitments'] == master_commitments
        assert len(document_requests) == 2
        page.unroute(document_route, intercept_document)

        # An atomic requirements save must retain pending edits after failure,
        # survive a search redraw, and submit only once on a quick retry.
        requirement_requests, held_requirements = [], []
        requirement_failure = {'pending': True}
        requirement_route = re.compile(rf'/api/passports/{owner}/requirements\?company_id=1$')

        def intercept_requirements(route):
            if route.request.method != 'PUT':
                route.continue_()
                return
            requirement_requests.append(route.request.post_data_json)
            if requirement_failure['pending']:
                requirement_failure['pending'] = False
                route.fulfill(status=503, json={'error': 'خطای آزمایشی ذخیره الزامات'})
            else:
                held_requirements.append(route)

        page.route(requirement_route, intercept_requirements)
        page.get_by_role('button', name='الزامات شایستگی اختصاصی این فرد', exact=True).click()
        dialog = page.locator('#personal-requirements-dialog')
        dialog.locator('[data-search]').fill('DOC-')
        dialog.locator('.category-section summary').click()
        level_a = dialog.locator('[data-level="DOC-A"]')
        level_b = dialog.locator('[data-level="DOC-B"]')
        expect(level_a).to_have_value('3')
        expect(level_b).to_have_value('2')
        level_a.select_option('1')
        level_b.select_option('4')
        save = dialog.locator('[data-save]')
        save.click()
        expect(dialog.locator('[data-error]')).to_have_text('خطای آزمایشی ذخیره الزامات')
        expect(level_a).to_have_value('1')
        expect(level_b).to_have_value('4')
        expect(level_a).to_be_enabled()
        expect(level_b).to_be_enabled()
        expect(save).to_be_enabled()
        expect(dialog.locator('[data-close]')).to_be_enabled()
        expect(dialog.locator('[data-search]')).to_be_enabled()
        original = {row['code']: row['level'] for row in get(requirements_path)['matrix']}
        assert original['DOC-A'] == 3 and original['DOC-B'] == 2
        dialog.locator('[data-search]').fill('DOC-A')
        dialog.locator('.category-section summary').click()
        expect(dialog.locator('[data-level="DOC-A"]')).to_have_value('1')
        dialog.locator('[data-search]').fill('DOC-')
        dialog.locator('.category-section summary').click()
        expect(dialog.locator('[data-level="DOC-B"]')).to_have_value('4')

        with page.expect_request(requirement_route):
            save.evaluate('button => { button.click(); button.click(); }')
        expect(save).to_be_disabled()
        expect(dialog.locator('[data-level="DOC-A"]')).to_be_disabled()
        expect(dialog.locator('[data-close]')).to_be_disabled()
        expect(dialog.locator('[data-search]')).to_be_disabled()
        assert_company_locked()
        assert len(requirement_requests) == 2 and len(held_requirements) == 1
        assert sorted(requirement_requests[-1]['items'], key=lambda item: item['code']) == [
            {'code': 'DOC-A', 'level': 1}, {'code': 'DOC-B', 'level': 4},
        ]
        held_requirements.pop().continue_()
        expect(dialog).to_have_count(0)
        expect(page.locator('#notice')).to_contain_text('الزامات اختصاصی همین فرد ذخیره شد')
        stored = {row['code']: row['level'] for row in get(requirements_path)['matrix']}
        other = {row['code']: row['level'] for row in get(f'passports/{people[1]}/requirements?company_id=1')['matrix']}
        assert stored['DOC-A'] == 1 and stored['DOC-B'] == 4
        assert other['DOC-A'] == 3 and other['DOC-B'] == 2
        assert len(requirement_requests) == 2
        assert not errors, errors
        browser.close()
        print('Documents browser smoke passed: failed-write recovery, retained text/levels, retry, per-person isolation, duplicate prevention and company context lock.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='nikan-documents-browser-') as data:
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
