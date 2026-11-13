"""Contract forms must be scoped, literal, effective and free of assessments."""
import io
import json
import re
import shutil
import subprocess
from pathlib import Path
import sqlite3
from urllib.parse import quote
import xml.etree.ElementTree as ET
import zipfile

from docx import Document
from PIL import Image
import pytest

from app import create_app
from app.commitments import COMMITMENT_KEYS, PROFILE_FIELDS, TITLE, ANNEX_LIMITS, SUMMARY_NOTE, summary_excerpt


NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
PASSWORD = 'Commitment-test-password-123'


@pytest.fixture
def ready(tmp_path):
    app = create_app(tmp_path, True)
    client = app.test_client()
    assert client.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    assert client.post('/api/login', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    headers = {'X-CSRF-Token': client.get('/api/me').json['csrf']}

    def write(method, path, data):
        response = getattr(client, method)('/api/' + path, headers=headers, json=data)
        assert response.status_code in (200, 201), response.json
        return response.json

    master = {key: 'MASTER-' + key.upper() for key, _ in PROFILE_FIELDS}
    master.update(job='کارشناس پیوست قرارداد', department='واحد آزمون',
                  effective_date='1405/01/01', review_date='1406/01/01')
    job_id = write('post', 'data/job_profiles?company_id=1', master)['id']
    people = [write('post', 'data/employees?company_id=1', {
        'code': 'CONTRACT-' + str(number), 'name': 'فرد قرارداد ' + str(number),
        'job': master['job'], 'department': master['department'],
    })['id'] for number in (1, 2)]
    foreign = write('post', 'data/employees?company_id=2', {'code': 'FOREIGN', 'name': 'FOREIGN-SECRET'})['id']
    skills = {}
    for code in ('REQ-REMOVED', 'REQ-CHANGED', 'REQ-PERSONAL', 'ASSESSMENT-ONLY'):
        skills[code] = write('post', 'data/skills?company_id=1', {
            'code': code, 'title': 'TITLE-' + code, 'category': 'گروه قرارداد',
        })['id']
    write('put', 'job-workspace?company_id=1&job=' + quote(master['job']), {
        'items': [{'code': 'REQ-REMOVED', 'level': 3}, {'code': 'REQ-CHANGED', 'level': 2}],
    })
    write('put', f'passports/{people[0]}/requirements?company_id=1', {
        'items': [{'code': 'REQ-REMOVED', 'level': 0}, {'code': 'REQ-CHANGED', 'level': 1},
                  {'code': 'REQ-PERSONAL', 'level': 4}],
    })
    write('post', 'data/assessments?company_id=1', {
        'employee_id': people[0], 'skill_id': skills['ASSESSMENT-ONLY'], 'level': 4,
    })
    personal = {'commitments': 'PERSONAL-COMMITMENTS\nتعهد کامل فردی',
                'duties': 'PERSONAL-DUTIES <script>window.bad=1</script> & متن کامل'}
    write('put', f'passports/{people[0]}/document?company_id=1', {'overrides': personal})
    client.post('/api/certificates?company_id=1', headers=headers, data={
        'employee_id': str(people[0]), 'title': 'CONFIDENTIAL-CERTIFICATE',
        'file': (io.BytesIO(b'%PDF-1.4\nprivate\n%%EOF'), 'certificate.pdf'),
    })
    with sqlite3.connect(app.config['DB']) as connection:
        connection.execute('INSERT INTO employee_meta(employee_id,company_id,assessor,evidence) VALUES (?,?,?,?)',
                           (people[0], 1, 'CONFIDENTIAL-ASSESSOR', 'CONFIDENTIAL-EVIDENCE'))
    return dict(app=app, client=client, headers=headers, people=people, foreign=foreign,
                job_id=job_id, master=master, personal=personal, skills=skills, directory=tmp_path)


def word_xml(response):
    assert response.status_code == 200, response.json
    assert response.mimetype == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    assert response.data.startswith(b'PK')
    with zipfile.ZipFile(io.BytesIO(response.data)) as package:
        assert package.testzip() is None
        assert '[Content_Types].xml' in package.namelist()
        for name in package.namelist():
            if name.endswith('.xml'):
                ET.fromstring(package.read(name))
        return ET.fromstring(package.read('word/document.xml'))


def body_text(xml):
    return '\n'.join(node.text or '' for node in xml.findall('.//w:t', NS))


def test_compact_commitment_keeps_selected_short_values_in_a_real_word_package(ready):
    c, eid = ready['client'], ready['people'][0]
    response = c.get(f'/api/passports/{eid}/commitment.docx?company_id=1')
    xml = word_xml(response)
    content = body_text(xml)
    assert TITLE in content
    assert 'PERSONAL-COMMITMENTS' in content and 'تعهد کامل فردی' in content
    assert ready['personal']['duties'] in content
    assert 'MASTER-COMMITMENTS' not in content and 'MASTER-DUTIES' not in content
    assert 'MASTER-DEFINITION' not in content and 'MASTER-AUTHORITY' in content
    assert 'MASTER-EDUCATION' not in content and 'MASTER-EXPERIENCE' not in content
    assert 'شماره قرارداد کار' in content and 'تاریخ قرارداد کار' in content
    assert 'نماینده مجاز شرکت' in content and 'منابع انسانی' in content
    assert 'فرم بدون امضا' in content and 'حدود قرارداد کار' in content
    assert 'شناسنامه واقعی فرد' in content
    assert 'TITLE-REQ-CHANGED' not in content and 'TITLE-REQ-PERSONAL' not in content
    assert 'TITLE-REQ-REMOVED' not in content
    for secret in ('ASSESSMENT-ONLY', 'CONFIDENTIAL-CERTIFICATE', 'CONFIDENTIAL-ASSESSOR',
                   'CONFIDENTIAL-EVIDENCE', 'FOREIGN-SECRET'):
        assert secret not in content
    assert xml.findall('.//w:bidi', NS)
    assert xml.findall('.//w:rtl', NS)
    assert xml.findall('.//w:bidiVisual', NS)
    assert xml.findall('.//w:rFonts[@w:cs="Tahoma"]', NS)
    assert xml.findall('.//w:jc[@w:val="start"]', NS)
    assert xml.findall('.//w:bCs[@w:val="1"]', NS)
    assert xml.findall('.//w:cantSplit', NS)
    assert not xml.findall('.//w:br[@w:type="page"]', NS)
    headings=[node.text for node in xml.findall('.//w:t', NS)
              if node.text and re.match(r'^[۰-۹]+\. ', node.text)]
    labels=dict(PROFILE_FIELDS)
    assert headings==[str(index).translate(str.maketrans('0123456789','۰۱۲۳۴۵۶۷۸۹'))+'. '+labels[key]
                      for index,key in enumerate(COMMITMENT_KEYS,1)]
    assert 'attachment;' in response.headers['Content-Disposition']
    assert response.headers['Content-Disposition'].endswith(f'1-{eid}.docx"')
    # A consumer using the public Word API can reopen the generated document.
    assert Document(io.BytesIO(response.data)).tables


def test_html_preview_escapes_stored_text_and_uses_external_print_controls(ready):
    c, eid = ready['client'], ready['people'][0]
    malicious = '<img src=x onerror="window.pwned=1">'
    assert c.put(f'/api/data/employees/{eid}?company_id=1', headers=ready['headers'],
                 json={'name': malicious}).status_code == 200
    response = c.get(f'/api/passports/{eid}/commitment?company_id=1')
    assert response.status_code == 200
    content = response.get_data(as_text=True)
    assert '<html lang="fa" dir="rtl">' in content
    assert TITLE in content
    assert malicious not in content and '&lt;img src=x onerror=' in content
    assert '<script>window.bad=1</script>' not in content
    assert '&lt;script&gt;window.bad=1&lt;/script&gt;' in content
    assert '/static/commitment.css' in content and '/static/commitment.js' in content
    assert 'id="print-commitment"' in content and 'Ctrl+P' in content
    assert f'/api/passports/{eid}/commitment.docx?company_id=1' in content
    assert 'onclick=' not in content and '<style>' not in content
    assert 'script-src \'self\'' in response.headers['Content-Security-Policy']
    assert 'CONFIDENTIAL-' not in content and 'ASSESSMENT-ONLY' not in content


@pytest.mark.parametrize('suffix', ('commitment', 'commitment.docx', 'profile.docx'))
def test_personal_exports_authentication_and_company_scope_including_viewer(ready, suffix):
    c, eid = ready['client'], ready['people'][0]
    assert c.get(f'/api/passports/{eid}/{suffix}?company_id=2').status_code == 404
    assert c.get(f'/api/passports/{ready["foreign"]}/{suffix}?company_id=1').status_code == 404
    assert c.get(f'/api/passports/{eid}/{suffix}?company_id=invalid').status_code == 400
    assert c.post('/api/users', headers=ready['headers'], json={
        'username': 'own-viewer', 'password': PASSWORD, 'role': 'viewer', 'company_id': 1,
    }).status_code == 201
    assert c.post('/api/logout', headers=ready['headers']).status_code == 200
    assert c.post('/api/login', json={'username': 'own-viewer', 'password': PASSWORD}).status_code == 200
    assert c.get(f'/api/passports/{eid}/{suffix}?company_id=1').status_code == 200
    assert c.get(f'/api/passports/{eid}/{suffix}?company_id=2').status_code == 403
    assert ready['app'].test_client().get(f'/api/passports/{eid}/{suffix}?company_id=1').status_code == 401


def test_full_master_and_actual_word_outputs_share_all_eighteen_sections_and_preserve_values(ready):
    c, eid, jid = ready['client'], ready['people'][0], ready['job_id']
    actual = word_xml(c.get(f'/api/passports/{eid}/profile.docx?company_id=1'))
    ideal = word_xml(c.get(f'/api/job-profiles/{jid}/profile.docx?company_id=1'))
    texts = [body_text(item) for item in (actual, ideal)]
    for content in texts:
        assert 'شناسنامه شغلی — قالب کامل' in content
        assert 'شماره قرارداد کار' not in content and 'اعلام آگاهی و تعهد شغلی' not in content
        assert 'CONFIDENTIAL-' not in content
    headings = []
    for xml in (actual, ideal):
        headings.append([node.text for node in xml.findall('.//w:body/w:p/w:r/w:t', NS)
                         if node.text and re.match(r'^[۰-۹]+\. ', node.text)])
    assert headings[0] == headings[1]
    assert len(headings[0]) == len(PROFILE_FIELDS) == 18
    for key, label in PROFILE_FIELDS:
        assert label in texts[0] and label in texts[1]
        if key in ready['personal']:
            for line in ready['personal'][key].splitlines():
                assert line in texts[0]
            assert ready['master'][key] not in texts[0]
        else:
            assert ready['master'][key] in texts[0]
        assert ready['master'][key] in texts[1]
    assert 'شناسنامه واقعی فرد' in texts[0] and 'الگوی مطلوب شغل' in texts[1]
    # Other people and the ideal template remain separate read models.
    assert c.get(f'/api/passports/{ready["people"][1]}/document?company_id=1').json['effective']['commitments'] == ready['master']['commitments']
    assert c.get(f'/api/job-profiles/{jid}/profile.docx?company_id=2').status_code == 404


def test_exports_are_read_only_and_snapshot_status_is_explicit(ready):
    c, eid = ready['client'], ready['people'][0]
    before_document = c.get(f'/api/passports/{eid}/document?company_id=1').json
    before_requirements = c.get(f'/api/passports/{eid}/requirements?company_id=1').json
    with sqlite3.connect(ready['app'].config['DB']) as connection:
        before_audit = connection.execute('SELECT count(*) FROM audit').fetchone()[0]
    before_files = {path.relative_to(ready['directory']) for path in ready['directory'].rglob('*')}
    for suffix in ('commitment', 'commitment.docx', 'profile.docx'):
        assert c.get(f'/api/passports/{eid}/{suffix}?company_id=1').status_code == 200
    assert c.get(f'/api/job-profiles/{ready["job_id"]}/profile.docx?company_id=1').status_code == 200
    assert c.get(f'/api/passports/{eid}/document?company_id=1').json == before_document
    assert c.get(f'/api/passports/{eid}/requirements?company_id=1').json == before_requirements
    with sqlite3.connect(ready['app'].config['DB']) as connection:
        assert connection.execute('SELECT count(*) FROM audit').fetchone()[0] == before_audit
    assert {path.relative_to(ready['directory']) for path in ready['directory'].rglob('*')} == before_files
    untouched = ready['people'][1]
    response = c.get(f'/api/passports/{untouched}/commitment?company_id=1')
    assert 'نیازمند بازبینی و ثبت شناسنامه واقعی فرد' in response.get_data(as_text=True)
    profile = body_text(word_xml(c.get(f'/api/passports/{untouched}/profile.docx?company_id=1')))
    assert 'نیازمند بازبینی' in profile


@pytest.mark.parametrize('valid', (True, False))
def test_holding_logo_is_embedded_or_falls_back_without_export_failure(ready, valid):
    if valid:
        output = io.BytesIO()
        Image.new('RGB', (80, 30), '#193e59').save(output, format='PNG')
        logo = output.getvalue()
    else:
        logo = b'\x89PNG\r\n\x1a\ninvalid-signature-only-logo'
    with sqlite3.connect(ready['app'].config['DB']) as connection:
        connection.execute('INSERT INTO branding(key,mime,content) VALUES (?,?,?)', ('holding', 'image/png', logo))
    eid = ready['people'][0]
    preview = ready['client'].get(f'/api/passports/{eid}/commitment?company_id=1')
    assert preview.status_code == 200
    assert ('data:image/png;base64,' in preview.get_data(as_text=True)) == valid
    response = ready['client'].get(f'/api/passports/{eid}/commitment.docx?company_id=1')
    word_xml(response)
    with zipfile.ZipFile(io.BytesIO(response.data)) as package:
        assert any(name.startswith('word/media/') for name in package.namelist()) == valid
        if not valid:
            header = package.read('word/header1.xml').decode('utf-8')
            assert 'NIKAN' in header


def test_long_source_and_full_profile_stay_literal_while_annex_is_an_explicit_excerpt(ready):
    eid, c = ready['people'][0], ready['client']
    value = '\n'.join(f'خط کامل وظایف شماره {number}؛ حفظ همه جزئیات سازمانی و شغلی.' for number in range(110))
    value += '\n  فاصله آغازین و متن پایانی حفظ شود  \n'
    assert c.put(f'/api/passports/{eid}/document?company_id=1', headers=ready['headers'],
                 json={'overrides': {'duties': value, 'commitments': 'متن معتبر\x00\x01پایان'}}).status_code == 200
    full = c.get(f'/api/passports/{eid}/profile.docx?company_id=1')
    word_xml(full)
    paragraphs = [paragraph.text for paragraph in Document(io.BytesIO(full.data)).paragraphs]
    assert value in paragraphs and 'متن معتبرپایان' in paragraphs
    summary = body_text(word_xml(c.get(f'/api/passports/{eid}/commitment.docx?company_id=1')))
    excerpt = summary_excerpt(value, ANNEX_LIMITS['duties'], conservative=True)
    assert excerpt in summary and excerpt.endswith('…')
    assert 'متن پایانی حفظ شود' not in summary and SUMMARY_NOTE in summary
    assert value not in summary and 'متن معتبرپایان' in summary
    preview = c.get(f'/api/passports/{eid}/commitment?company_id=1').get_data(as_text=True)
    assert excerpt in preview and SUMMARY_NOTE in preview
    assert c.get(f'/api/passports/{eid}/document?company_id=1').json['effective']['duties'] == value


def test_excerpt_folds_only_display_whitespace_and_does_not_invent_text():
    assert summary_excerpt('  حفظ\nمتن\tاصلی  ', 100) == 'حفظ متن اصلی'
    assert summary_excerpt('متن کوتاه', 100) == 'متن کوتاه'
    value = 'انجام امور محوله و ارائه گزارش به مسئول مستقیم ' * 100
    result = summary_excerpt(value, 90)
    assert result.endswith('…') and ' '.join(value.split()).startswith(result[:-1])
    assert len(result) <= 90
    assert len(summary_excerpt('W' * 1000, 120)) <= 61
    assert len(summary_excerpt('界' * 1000, 120)) <= 61
    assert summary_excerpt('', 100) == ''


@pytest.mark.parametrize('case', ('long-persian-logo', 'long-wide-fallback', 'long-unbroken-arabic', 'long-unbroken-latin', 'empty-profile'))
def test_actual_office_renderer_keeps_annex_on_one_a4_page(ready, tmp_path, case):
    office = shutil.which('soffice') or '/opt/codex/runtimes/codex-primary-runtime/dependencies/bin/override/soffice'
    if not Path(office).exists():
        pytest.skip('A real LibreOffice renderer is needed for the one-page layout regression')
    c, eid = ready['client'], ready['people'][0]
    if case != 'empty-profile':
        glyphs = {'long-persian-logo': 'وظیفه جاری فرد در سازمان و پیگیری اجرای امور محوله؛ ',
                  'long-wide-fallback': 'WW界😀', 'long-unbroken-arabic': 'ض',
                  'long-unbroken-latin': 'Q'}[case]
        values = {key: (glyphs * 3000)[:10000] for key in COMMITMENT_KEYS}
        assert c.put(f'/api/passports/{eid}/document?company_id=1', headers=ready['headers'],
                     json={'overrides': values}).status_code == 200
        identity = {key: ('نام بسیار طولانی سازمان و سمت کارکنان ' * 30) for key in ('name', 'job', 'department')}
        assert c.put(f'/api/data/employees/{eid}?company_id=1', headers=ready['headers'], json=identity).status_code == 200
        with sqlite3.connect(ready['app'].config['DB']) as connection:
            connection.execute('UPDATE companies SET name=? WHERE id=1', ('نام کامل و طولانی شرکت وابسته به هلدینگ ' * 30,))
        if 'logo' in case:
            image = io.BytesIO()
            Image.new('RGB', (30, 800), '#193e59').save(image, format='PNG')
            with sqlite3.connect(ready['app'].config['DB']) as connection:
                connection.execute('INSERT INTO branding(key,mime,content) VALUES (?,?,?)', ('holding', 'image/png', image.getvalue()))
    else:
        assert c.put(f'/api/data/employees/{eid}?company_id=1', headers=ready['headers'],
                     json={'job': 'بدون الگو'}).status_code == 200
        assert c.delete(f'/api/passports/{eid}/document?company_id=1', headers=ready['headers']).status_code == 200
    before = c.get(f'/api/passports/{eid}/document?company_id=1').json
    path = tmp_path / (case + '.docx')
    response = c.get(f'/api/passports/{eid}/commitment.docx?company_id=1')
    word_xml(response)
    path.write_bytes(response.data)
    profile = tmp_path / 'office-profile'
    converted = subprocess.run([office, '-env:UserInstallation=' + profile.as_uri(), '--headless',
                                '--convert-to', 'pdf', '--outdir', str(tmp_path), str(path)],
                               capture_output=True, timeout=45)
    assert converted.returncode == 0, converted.stderr.decode(errors='replace')
    pdf = path.with_suffix('.pdf').read_bytes()
    assert len(re.findall(rb'/Type\s*/Page(?!s)\b', pdf)) == 1
    # A4 dimensions in the emitted PDF, not just the editable document metadata.
    media = re.search(rb'/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]', pdf)
    assert media and abs(float(media[1]) - 595.3) < 1 and abs(float(media[2]) - 841.9) < 1
    assert c.get(f'/api/passports/{eid}/document?company_id=1').json == before
