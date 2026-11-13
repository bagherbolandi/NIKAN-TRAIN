"""Chromium regression for full ideal/actual documents and read-only annex exports."""
import io
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.commitments import PROFILE_FIELDS, COMMITMENT_KEYS, SUMMARY_NOTE


NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


def word_content(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        assert package.testzip() is None
        root = ET.fromstring(package.read('word/document.xml'))
        content = '\n'.join(node.text or '' for node in root.findall('.//w:t', NS))
        headings = [node.text for node in root.findall('.//w:t', NS)
                    if node.text and re.match(r'^[۰-۹]+\. ', node.text)]
        assert root.findall('.//w:bidi', NS) and root.findall('.//w:rtl', NS)
        assert any(name.startswith('word/media/') for name in package.namelist())
        return content, headings


def check(base):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'),
            headless=True, args=['--no-sandbox'],
        )
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        password = secrets.token_urlsafe(24)
        page.goto(base)
        page.locator('#auth [name=username]').fill('commitment-admin')
        page.locator('#auth [name=password]').fill(password)
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

        logo_image = Image.new('RGB', (160, 60), '#193e59')
        ImageDraw.Draw(logo_image).text((18, 20), 'NIKAN', fill='white')
        logo = io.BytesIO()
        logo_image.save(logo, format='PNG')
        response = page.request.post(base + '/api/branding/holding', headers=headers,
                                     multipart={'file': {'name': 'holding.png', 'mimeType': 'image/png', 'buffer': logo.getvalue()}})
        assert response.ok, response.text()
        job = 'کارشناس برنامه‌ریزی آموزش'
        ideal = {key: 'متن کامل الگوی مطلوب برای ' + label for key, label in PROFILE_FIELDS}
        ideal.update(job=job, department='سرمایه انسانی', effective_date='1405/01/01', review_date='1406/01/01')
        jid = write('post', 'data/job_profiles?company_id=1', ideal)['id']
        people = [write('post', 'data/employees?company_id=1', {
            'code': 'ANNEX-' + str(number), 'name': 'شاغل آزمون تعهدنامه ' + str(number),
            'job': job, 'department': ideal['department'],
        })['id'] for number in (1, 2)]
        eid = people[0]
        skill = write('post', 'data/skills?company_id=1', {
            'code': 'ANNEX-REQ', 'title': 'تنظیم و پیگیری تقویم آموزشی', 'category': 'برنامه‌ریزی آموزش',
        })['id']
        write('put', 'job-workspace?company_id=1&job=' + urllib.parse.quote(job),
              {'items': [{'code': 'ANNEX-REQ', 'level': 3}]})
        write('post', 'data/assessments?company_id=1', {'employee_id': eid, 'skill_id': skill, 'level': 1})
        write('post', 'users', {'username': 'annex-viewer', 'password': password, 'role': 'viewer', 'company_id': 1})
        foreign = write('post', 'data/employees?company_id=2', {'code': 'FOREIGN', 'name': 'نام محرمانه شرکت دیگر'})['id']

        # The ideal and actual forms expose exactly the same eighteen writing fields.
        page.locator('[data-page=requirements]').click()
        page.locator('[data-job]').click()
        page.locator('#edit-job-description').click()
        ideal_fields = page.locator('#fields [name]').evaluate_all('nodes=>nodes.map(node=>node.name).filter(name=>!["job","department"].includes(name))')
        assert ideal_fields == [key for key, _ in PROFILE_FIELDS]
        page.locator('#close-dialog').click()
        with page.expect_download() as event:
            page.locator('#download-job-profile').click()
        ideal_text, ideal_headings = word_content(Path(event.value.path()).read_bytes())
        assert len(ideal_headings) == 18
        assert ideal['duties'] in ideal_text and ideal['commitments'] in ideal_text

        page.locator('[data-page=passports]').click()
        page.locator('#person').select_option(str(eid))
        page.locator('#edit-person-document').wait_for()
        number = page.locator('#passport-number').input_value()
        assert re.fullmatch(r'NP-01-\d{4}(?:-[1-9]\d*)?', number), number
        expect(page.locator('#personal-document-state')).to_contain_text('پیش‌نویس')
        page.locator('#edit-person-document').click()
        assert page.locator('#fields [name]').evaluate_all('nodes=>nodes.map(node=>node.name)') == ideal_fields
        duties = '\n'.join([
            'گردآوری نیازهای آموزشی واحدها و هماهنگی جلسات بررسی در شرایط فعلی سازمان.',
            'پیگیری اجرای دوره‌های مصوب، ثبت سوابق آموزشی و ارائه گزارش به مدیر مستقیم.',
            'محدودیت جاری: هماهنگی آموزش‌ها با یک نفر نیرو و امکانات موجود انجام می‌شود.',
        ] * 3) + '\n<script>window.bad=1</script>'
        commitments = 'تعهد اختصاصی شاغل: انجام امور محوله در حدود شرح وظایف و قرارداد کار.'
        page.locator('#fields [name=duties]').fill(duties)
        page.locator('#fields [name=commitments]').fill(commitments)
        # An intentionally blank actual field must stay blank after ideal-template edits.
        page.locator('#fields [name=authority]').fill('')
        page.locator('#entry > button').click()
        expect(page.locator('#dialog')).not_to_be_visible()
        expect(page.locator('#personal-document-state')).to_contain_text('وضعیت واقعی این فرد ثبت شده')
        actual = get(f'passports/{eid}/document?company_id=1')
        assert actual['is_snapshot'] and set(actual['overrides']) == set(ideal_fields)
        assert actual['effective']['authority'] == ''
        write('put', f'data/job_profiles/{jid}?company_id=1', {
            'duties': 'وظایف مطلوب جدید — مستقل از وضعیت فعلی فرد',
            'authority': 'اختیارات جدید الگوی مطلوب', 'commitments': 'تعهدات جدید الگوی مطلوب',
        })
        page.locator('#person').select_option(str(people[1]))
        expect(page.locator('#personal-document-state')).to_contain_text('پیش‌نویس')
        page.locator('#person').select_option(str(eid))
        expect(page.locator('#personal-document-state')).to_contain_text('وضعیت واقعی این فرد ثبت شده')
        assert get(f'passports/{eid}/document?company_id=1')['effective']['authority'] == ''

        before = {path: get(path) for path in [
            f'passports/{eid}/document?company_id=1', f'passports/{people[1]}/document?company_id=1',
            f'passports/{eid}/requirements?company_id=1', 'data/job_profiles?company_id=1', 'audit?company_id=1',
        ]}
        for selector in ('#download-person-profile', '#download-commitment'):
            with page.expect_download() as event:
                page.locator(selector).click()
            content, headings = word_content(Path(event.value.path()).read_bytes())
            assert commitments in content
            assert 'اختیارات جدید الگوی مطلوب' not in content
            assert 'نام محرمانه شرکت دیگر' not in content
            if selector == '#download-person-profile':
                assert '<script>window.bad=1</script>' in content
                assert headings == ideal_headings
                assert 'شماره قرارداد کار' not in content and 'اعلام آگاهی و تعهد شغلی' not in content
            else:
                assert len(headings) == 8
                assert SUMMARY_NOTE in content and '…' in content
                assert '<script>window.bad=1</script>' not in content
                assert 'اعلام آگاهی و تعهد شغلی' in content and 'نماینده مجاز شرکت' in content
                assert 'تنظیم و پیگیری تقویم آموزشی' not in content
                assert ideal['education'] not in content and ideal['definition'] not in content

        page.set_viewport_size({'width': 390, 'height': 844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path='/tmp/nikan-passport-export-mobile.png', full_page=True)
        page.set_viewport_size({'width': 1440, 'height': 1000})

        with page.expect_popup() as event:
            page.locator('#preview-commitment').click()
        preview = event.value
        preview.on('pageerror', lambda error: errors.append(str(error)))
        preview.wait_for_load_state()
        expect(preview.locator('.document-number')).to_contain_text(number)
        expect(preview.locator('.clause')).to_have_count(8)
        assert preview.locator('.competency-appendix').count() == 0
        expect(preview.locator('.commitment-sheet')).to_contain_text(commitments)
        assert not preview.evaluate('Boolean(window.bad)')
        assert preview.locator('.holding-logo').evaluate('image=>image.complete&&image.naturalWidth>0')
        assert preview.evaluate('getComputedStyle(document.documentElement).direction') == 'rtl'
        assert preview.evaluate('document.documentElement.scrollWidth <= innerWidth')
        preview.screenshot(path='/tmp/nikan-commitment-desktop.png', full_page=True)
        # Exercise the external CSP-compatible print handler without opening a dialog.
        preview.evaluate('() => { window.printCalls=0; window.print=()=>{window.printCalls++}; }')
        preview.locator('#print-commitment').click()
        assert preview.evaluate('window.printCalls') == 1
        preview.emulate_media(media='print')
        expect(preview.locator('.preview-tools')).not_to_be_visible()
        assert preview.locator('.signatures').evaluate('element=>getComputedStyle(element).breakInside') == 'avoid'
        pdf = preview.pdf(path='/tmp/nikan-commitment-print.pdf', print_background=True, prefer_css_page_size=True)
        assert pdf.startswith(b'%PDF') and len(pdf) > 10000
        assert len(re.findall(rb'/Type\s*/Page(?!s)\b', pdf)) == 1
        preview.emulate_media(media='screen')
        preview.set_viewport_size({'width': 390, 'height': 844})
        assert preview.evaluate('document.documentElement.scrollWidth <= innerWidth')
        preview.screenshot(path='/tmp/nikan-commitment-mobile.png', full_page=True)
        with preview.expect_download() as event:
            preview.locator('.preview-actions a').click()
        assert word_content(Path(event.value.path()).read_bytes())[0].find(commitments) >= 0
        preview.close()
        assert {path: get(path) for path in before} == before
        assert page.request.get(base + f'/api/passports/{foreign}/commitment?company_id=1').status == 404

        # Every selected field may contain thousands of literal characters.
        # The separate excerpt stays on one page; nothing is hidden by overflow.
        stress_id = write('post', 'data/employees?company_id=1', {
            'code': 'ANNEX-9999', 'name': 'نام و نام خانوادگی طولانی شاغل ' * 30,
            'job': job, 'department': 'واحد سازمانی طولانی ' * 30,
        })['id']
        long_value = ('گردآوری نیازهای آموزشی و پیگیری اجرای امور محوله در شرایط جاری سازمان؛ ' * 200)[:10000]
        write('put', f'passports/{stress_id}/document?company_id=1',
              {'overrides': {key: long_value for key in COMMITMENT_KEYS}})
        with page.expect_popup() as event:
            page.evaluate('(url) => window.open(url)', base + f'/api/passports/{stress_id}/commitment?company_id=1')
        stress = event.value
        stress.wait_for_load_state()
        expect(stress.locator('.clause')).to_have_count(8)
        expect(stress.locator('.unsigned').last).to_contain_text('یک‌صفحه‌ای')
        assert stress.locator('.clause p').evaluate_all('nodes => nodes.every(node => node.textContent.endsWith("…"))')
        stress.emulate_media(media='print')
        stress_pdf = stress.pdf(path='/tmp/nikan-commitment-long-print.pdf', print_background=True, prefer_css_page_size=True)
        assert len(re.findall(rb'/Type\s*/Page(?!s)\b', stress_pdf)) == 1
        # There is no clipping or font-shrinking workaround for the source text.
        assert stress.locator('.commitment-sheet').evaluate('node => !["hidden","clip"].includes(getComputedStyle(node).overflow)')
        assert get(f'passports/{stress_id}/document?company_id=1')['effective']['duties'] == long_value
        for label, glyph in (('latin', 'Q'), ('arabic', 'ض')):
            write('put', f'passports/{stress_id}/document?company_id=1',
                  {'overrides': {key: glyph * 10000 for key in COMMITMENT_KEYS}})
            stress.reload()
            edge_pdf = stress.pdf(path=f'/tmp/nikan-commitment-{label}-print.pdf',
                                  print_background=True, prefer_css_page_size=True)
            assert len(re.findall(rb'/Type\s*/Page(?!s)\b', edge_pdf)) == 1
            assert get(f'passports/{stress_id}/document?company_id=1')['effective']['duties'] == glyph * 10000
        stress.close()

        # Own-company read-only users can export, with no writing controls or cross-company access.
        page.locator('#logout').click()
        page.locator('#auth [name=username]').fill('annex-viewer')
        page.locator('#auth [name=password]').fill(password)
        page.locator('#auth button').click()
        page.locator('.metrics').wait_for()
        page.locator('[data-page=passports]').click()
        page.locator('#person').select_option(str(eid))
        page.locator('#download-commitment').wait_for()
        assert page.locator('#edit-person-document,#reset-person-document').count() == 0
        with page.expect_download() as event:
            page.locator('#download-commitment').click()
        assert commitments in word_content(Path(event.value.path()).read_bytes())[0]
        assert page.request.get(base + f'/api/passports/{foreign}/commitment?company_id=2').status == 403
        assert not errors, errors
        browser.close()
    print('Commitment browser passed: identical complete18field forms/Word layouts, separate oneA4page8section excerpt including10000characterfields, independent actual snapshot including blank, short reference, branded DOCX and escaped HTML preview, print button and A4PDF, desktop/mobile, export read-only and company/viewer scope.')


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='nikan-commitment-browser-') as directory:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        server = subprocess.Popen([sys.executable, 'run.py', '--no-browser', '--port', str(port)],
                                  cwd=Path(__file__).resolve().parents[1],
                                  env={**os.environ, 'NIKAN_DATA_DIR': directory}, stdout=subprocess.DEVNULL)
        try:
            for _ in range(80):
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
