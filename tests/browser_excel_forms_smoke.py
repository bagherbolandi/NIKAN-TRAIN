"""Real XLSX download/fill/preview/commit workflows in Chromium."""
import io
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

import openpyxl
from PIL import Image
from playwright.sync_api import expect, sync_playwright


def filled(template, rows):
    book = openpyxl.load_workbook(io.BytesIO(template))
    sheet = book['ورود اطلاعات']
    keys = [cell.value for cell in sheet[5]]
    for row_number, values in enumerate(rows, 7):
        for column, key in enumerate(keys, 1):
            sheet.cell(row_number, column, values.get(key))
    output = io.BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def check(base):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        password = secrets.token_urlsafe(24)
        page.goto(base)
        page.locator('#auth [name=username]').fill('excel-admin')
        page.locator('#auth [name=password]').fill(password)
        page.locator('#auth button').click()
        page.locator('.metrics').wait_for()
        csrf = page.request.get(base + '/api/me').json()['csrf']
        headers = {'X-CSRF-Token': csrf}
        logo_output = io.BytesIO()
        Image.new('RGB', (80, 30), '#193e59').save(logo_output, format='PNG')
        logo = logo_output.getvalue()
        response = page.request.post(base + '/api/branding/holding', headers=headers, multipart={'file': {'name': 'holding.png', 'mimeType': 'image/png', 'buffer': logo}})
        assert response.ok, response.text()
        for role in ('manager', 'viewer'):
            response = page.request.post(base + '/api/users', headers=headers, data={'username': 'excel-' + role, 'password': password, 'role': role, 'company_id': 1})
            assert response.ok, response.text()
        page.locator('[data-page=employees]').click()
        page.locator('#open-excel-forms').click()
        expect(page.locator('#excel-kind')).to_have_value('employees')
        expect(page.locator('#excel-commit')).to_be_disabled()
        with page.expect_download() as event:
            page.locator('#excel-download').click()
        template = Path(event.value.path()).read_bytes()
        with zipfile.ZipFile(io.BytesIO(template)) as archive:
            assert any(name.startswith('xl/media/') for name in archive.namelist())
        book = openpyxl.load_workbook(io.BytesIO(template))
        assert book['ورود اطلاعات'].sheet_view.rightToLeft
        assert book['_nikan'].sheet_state != 'visible'
        metadata = dict(book['_nikan'].iter_rows(min_col=1, max_col=2, values_only=True))
        assert metadata['kind'] == 'employees' and str(metadata['company_id']) == '1'
        book.close()
        with page.expect_download() as event:
            page.locator('#excel-download-all').click()
        with zipfile.ZipFile(event.value.path()) as archive:
            names = archive.namelist()
            assert len(names) == page.locator('#excel-kind option').count()
            assert 'Nikan-employees-company-1.xlsx' in names
            assert 'Nikan-companies-company-1.xlsx' in names
            assert all(name.endswith('.xlsx') for name in names)
        rows = [{'code': 'XLSX-001', 'name': 'فرد نخست اکسل'}, {'code': 'XLSX-002'}]

        def upload(payload, name='employees.xlsx'):
            page.locator('#excel-file').set_input_files({'name': name, 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': payload})

        upload(filled(template, rows), 'employees-invalid.xlsx')
        page.locator('#excel-preview').click()
        expect(page.locator('#excel-preview-result')).to_contain_text('فایل نیاز به اصلاح دارد')
        expect(page.locator('#excel-preview-result')).to_contain_text('8')
        expect(page.locator('#excel-commit')).to_be_disabled()
        assert page.request.get(base + '/api/data/employees?company_id=1').json() == []
        rows[1]['name'] = 'فرد دوم اکسل'
        payload = filled(template, rows)
        upload(payload, 'employees-valid.xlsx')
        expect(page.locator('#excel-preview-result')).to_be_empty()
        page.locator('#excel-preview').click()
        expect(page.locator('#excel-preview-result')).to_contain_text('بررسی موفق')
        expect(page.locator('#excel-commit')).to_be_enabled()
        assert page.request.get(base + '/api/data/employees?company_id=1').json() == []
        page.screenshot(path='/tmp/nikan-excel-preview.png', full_page=True)

        # A final import is a single write despite repeated submit events.
        held = []
        page.route('**/api/excel/import/employees?company_id=1', lambda route: held.append(route))
        with page.expect_request('**/api/excel/import/employees?company_id=1'):
            page.evaluate("const button=document.querySelector('#excel-commit');button.dispatchEvent(new MouseEvent('click',{bubbles:true}));button.dispatchEvent(new MouseEvent('click',{bubbles:true}))")
        expect(page.locator('#excel-commit')).to_be_disabled()
        assert len(held) == 1
        page.evaluate("void navigateTo('employees',2)")
        assert page.evaluate('({page,cid})') == {'page': 'import', 'cid': 1}
        held[0].continue_()
        page.unroute('**/api/excel/import/employees?company_id=1')
        expect(page.locator('#excel-preview-result')).to_contain_text('ثبت نهایی انجام شد')
        expect(page.locator('#excel-commit')).to_be_disabled()
        employees = page.request.get(base + '/api/data/employees?company_id=1').json()
        assert len(employees) == 2
        original_ids = {row['code']: row['id'] for row in employees}
        assert page.locator('#excel-file').evaluate('input=>input.files[0].name') == 'employees-valid.xlsx'

        # Update existing records explicitly; replacing the file invalidates approval.
        page.locator('#excel-policy').select_option('upsert')
        expect(page.locator('#excel-preview-result')).to_be_empty()
        rows[0]['name'] = 'نام اصلاح‌شده فرد نخست'
        upload(filled(template, rows))
        page.locator('#excel-preview').click()
        expect(page.locator('#excel-commit')).to_be_enabled()
        page.locator('#excel-commit').click()
        expect(page.locator('#excel-preview-result')).to_contain_text('ثبت نهایی انجام شد')
        employees = page.request.get(base + '/api/data/employees?company_id=1').json()
        assert {row['code']: row['id'] for row in employees} == original_ids
        assert next(row['name'] for row in employees if row['code'] == 'XLSX-001') == rows[0]['name']
        upload(payload)
        page.locator('#excel-preview').click()
        expect(page.locator('#excel-commit')).to_be_enabled()
        upload(filled(template, [{'code': 'XLSX-003', 'name': 'فایل جایگزین'}]))
        expect(page.locator('#excel-commit')).to_be_disabled()
        page.locator('#excel-kind').select_option('instructors')
        assert page.locator('#excel-file').evaluate('input=>input.files.length') == 0
        page.locator('#excel-kind').select_option('employees')
        page.locator('#company').select_option('2')
        page.locator('#excel-file').wait_for()
        upload(payload)
        page.locator('#excel-preview').click()
        expect(page.locator('#excel-error')).to_contain_text('شرکت دیگری')
        expect(page.locator('#excel-commit')).to_be_disabled()
        assert page.request.get(base + '/api/data/employees?company_id=2').json() == []
        assert page.locator('#excel-file').evaluate('input=>input.files.length') == 1
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path='/tmp/nikan-excel-mobile.png', full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.locator('#excel-legacy summary').click()
        page.locator('#import-form').wait_for()
        page.locator('[data-page=requirements]').click()
        page.locator('#open-excel-forms').click()
        expect(page.locator('#excel-kind')).to_have_value('job_profiles')
        page.locator('[data-page=requirements]').click()
        page.locator('#open-excel-requirements').click()
        expect(page.locator('#excel-kind')).to_have_value('requirements')

        for role in ('manager', 'viewer'):
            page.locator('#logout').click()
            page.locator('#auth [name=username]').fill('excel-' + role)
            page.locator('#auth [name=password]').fill(password)
            page.locator('#auth button').click()
            page.locator('.metrics').wait_for()
            page.locator('[data-page=import]').click()
            page.locator('#excel-kind').wait_for()
            assert page.locator('#excel-kind option[value="competency-bank"]').count() == 0
            assert page.locator('#excel-kind option[value=companies]').count() == 0
            with page.expect_download() as event:
                page.locator('#excel-download-all').click()
            with zipfile.ZipFile(event.value.path()) as archive:
                assert not any('-companies-' in name or '-competency-bank-' in name for name in archive.namelist())
            if role == 'viewer':
                assert page.locator('#excel-file,#excel-preview,#excel-commit,#excel-legacy').count() == 0
                with page.expect_download() as event:
                    page.locator('#excel-download').click()
                assert Path(event.value.path()).read_bytes().startswith(b'PK')
        assert not errors, errors
        browser.close()
    print('Excel browser passed: branded real XLSX template, invalid-row atomic preview, single final import, explicit upsert preserving IDs, file/kind/company approval reset, draft retention, contextual/legacy paths, manager scope and viewer download-only, mobile layout.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='nikan-excel-browser-') as directory:
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
            check(base)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
