"""Read-only employment annexes built from a person's effective job document."""
import base64
from datetime import datetime, timezone
import io
import re
import warnings

from flask import Response, render_template, url_for

from .documents import document_view
from .validation import positive_id


TITLE = 'خلاصه تعهدات و وظایف — پیوست قرارداد کار'
DECLARATION = (
    'اینجانب با مشخصات پرسنلی درج‌شده در این فرم، پس از مطالعه شرح شغل، وظایف، '
    'حدود اختیارات و مسئولیت‌های مندرج در این پیوست، آگاهی خود را از موارد فوق '
    'اعلام می‌کنم و متعهد می‌شوم وظایف محوله را در حدود قرارداد کار، قوانین و مقررات '
    'لازم‌الاجرا و دستورالعمل‌های ابلاغ‌شده مرتبط با شغل انجام دهم. تغییرات شرح وظایف '
    'و شرایط اجرای آن باید از مسیر مجاز سازمان و با رعایت قرارداد و قوانین مربوط ابلاغ شود.'
)
PROFILE_FIELDS = (
    ('purpose', 'هدف شغل'),
    ('definition', 'تعریف شغل'),
    ('supervisor', 'مافوق مستقیم'),
    ('duties', 'وظایف و مسئولیت‌ها'),
    ('commitments', 'تعهدات کاری و مسئولیت‌پذیری'),
    ('authority', 'حدود اختیارات'),
    ('education', 'حداقل تحصیلات'),
    ('experience', 'سابقه لازم'),
    ('legal', 'گواهینامه‌ها و الزامات قانونی'),
    ('hse', 'ریسک‌های HSE'),
    ('equipment', 'ابزار و تجهیزات'),
    ('conditions', 'شرایط و محیط کار'),
    ('performance', 'معیارهای ارزیابی عملکرد'),
    ('contract_notes', 'الزامات و ملاحظات قرارداد'),
    ('effective_date', 'تاریخ اجرای سند'),
    ('review_date', 'تاریخ بازنگری بعدی'),
    ('revision', 'مرجع سند / بازنگری'),
    ('approved_by', 'تأییدکننده'),
)
COMMITMENT_KEYS = ('duties', 'commitments', 'authority', 'conditions', 'hse',
                   'legal', 'performance', 'contract_notes')
# The annex is an explicitly shortened reading copy, never a write to either
# full document. A conservative excerpt budget keeps editable Word output on A4
# without shrinking type or hiding overflowing text.
SUMMARY_NOTE = ('گزیده یک‌صفحه‌ای؛ موارد دارای «…» کوتاه شده‌اند. متن کامل و ملاک شرح وظایف، '
                'شناسنامه فردی با شناسه زیر است؛ اصل شناسنامه‌ها تغییری نمی‌کند.')
ANNEX_LIMITS = {'duties': 180, 'commitments': 150, 'authority': 120,
                'conditions': 120, 'hse': 120, 'legal': 120,
                'performance': 140, 'contract_notes': 140}
FONT = 'Tahoma'
XML_INVALID = re.compile('[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')


def text(value):
    """Keep stored text literal while removing characters invalid in Office XML."""
    return XML_INVALID.sub('', str(value if value is not None else ''))


def summary_excerpt(value, limit, *, conservative=False):
    """Literal leading excerpt, with bounded whitespace and an honest ellipsis.

    Count broad Latin capitals and East Asian/emoji glyphs conservatively so a
    long unbroken string cannot expand the one-page form unexpectedly. This
    presentation copy has no effect on saved text or full Word exports.
    """
    import unicodedata

    value = re.sub(r'\s+', ' ', text(value)).strip()
    units, end = 0, 0
    for index, character in enumerate(value):
        weight = 0 if unicodedata.combining(character) else (2 if (
            unicodedata.east_asian_width(character) in ('W', 'F') or
            character in 'WM@%&' or unicodedata.category(character) == 'So') else 1)
        if conservative and weight:
            # A full em is possible for an isolated Persian letter or a broad
            # Latin glyph. Two units per glyph bounds even unbroken input.
            weight = max(2, weight)
        if units + weight > limit:
            break
        units += weight
        end = index + 1
    if end == len(value):
        return value
    prefix = value[:max(0, end - 1)].rstrip()
    # Prefer a word boundary while retaining a useful excerpt of unbroken codes.
    if ' ' in prefix and prefix.rfind(' ') >= len(prefix) * .75:
        prefix = prefix[:prefix.rfind(' ')]
    return prefix.rstrip(' ،؛') + '…'


def fa_number(value):
    return str(value).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))


def holding_logo(conn):
    """Return a validated, bounded copy of the supplied logo, entirely in memory."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    row = conn.execute("SELECT mime,content FROM branding WHERE key='holding'").fetchone()
    if not row or row['mime'] not in ('image/png', 'image/jpeg'):
        return None
    source = bytes(row['content'])
    if len(source) > 2 * 1024 * 1024:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source)) as image:
                if image.format not in ('PNG', 'JPEG') or image.width * image.height > 25_000_000:
                    return None
                image.verify()
            with Image.open(io.BytesIO(source)) as original:
                image = ImageOps.exif_transpose(original).convert('RGBA')
                image.thumbnail((1200, 800))
                output = io.BytesIO()
                image.save(output, format='PNG')
                width, height = image.size
                return dict(content=output.getvalue(), width=width, height=height)
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        # Branding uploads from older versions may contain a signature-only file.
        return None


def commitment_data(conn, cid, eid):
    """Read the full profile and a separate selection for the contract summary."""
    person = conn.execute('SELECT * FROM employees WHERE id=? AND company_id=?', (eid, cid)).fetchone()
    if person is None:
        return None
    organization = conn.execute('SELECT name FROM companies WHERE id=?', (cid,)).fetchone()
    master = conn.execute('SELECT * FROM job_profiles WHERE company_id=? AND job=?', (cid, person['job'])).fetchone()
    document = document_view(conn, cid, eid, dict(master) if master else None)
    effective = document['effective'] or {}
    identity = {key: text(person[key]) for key in ('code', 'name', 'job', 'department')}
    identity['company'] = text(organization['name'])
    # Full fields stay literal. Only the separate annex receives bounded
    # presentation excerpts. Assessments stay separate from both exports.
    fields = [dict(key=key, label=label, value=text(effective.get(key)))
              for key, label in PROFILE_FIELDS]
    by_key = {field['key']: field for field in fields}
    annex_identity = {key: summary_excerpt(value, {
        'company': 90, 'name': 65, 'code': 35, 'job': 75, 'department': 65,
    }[key]) for key, value in identity.items()}
    commitment_fields = [dict(by_key[key], value=summary_excerpt(by_key[key]['value'], ANNEX_LIMITS[key], conservative=True))
                         for key in COMMITMENT_KEYS]
    logo = holding_logo(conn)
    is_snapshot = bool(document.get('is_snapshot'))
    return dict(
        title=TITLE, declaration=DECLARATION, identity=identity,
        number=text(document['number']), legacy_number=text(document.get('legacy_number')),
        master_number=text(document.get('master_number')),
        personal_revision=fa_number(document['revision']), profile_revision=text(effective.get('revision')),
        profile_approved_by=text(effective.get('approved_by')),
        effective_date=text(effective.get('effective_date')), review_date=text(effective.get('review_date')),
        fields=fields, commitment_fields=commitment_fields, annex_identity=annex_identity,
        summary_note=SUMMARY_NOTE, annex_number=summary_excerpt(document['number'], 60),
        annex_master_number=summary_excerpt(document.get('master_number'), 45),
        annex_legacy_number=summary_excerpt(document.get('legacy_number'), 40),
        annex_effective_date=summary_excerpt(effective.get('effective_date'), 22),
        annex_review_date=summary_excerpt(effective.get('review_date'), 22),
        missing_profile=not effective, logo=logo,
        logo_uri=('data:image/png;base64,' + base64.b64encode(logo['content']).decode('ascii')) if logo else None,
        is_snapshot=is_snapshot, requires_review=bool(document.get('requires_review', not is_snapshot)),
        document_kind='واقعی فرد' if is_snapshot else 'پیشنهادی برای فرد',
        status_label=('شناسنامه واقعی فرد — نسخه اختصاصی ثبت‌شده' if is_snapshot else
                      'پیشنهاد مبتنی بر الگوی شغل — نیازمند بازبینی و ثبت شناسنامه واقعی فرد'),
        cid=cid, eid=eid,
    )


def _element(name, **attributes):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    node = OxmlElement('w:' + name)
    for key, value in attributes.items():
        node.set(qn('w:' + key), str(value))
    return node


def _paragraph_format(paragraph, *, keep_next=False, after=5):
    from docx.oxml.ns import qn
    from docx.shared import Pt
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.line_spacing = 1.45
    paragraph.paragraph_format.keep_with_next = keep_next
    properties = paragraph._p.get_or_add_pPr()
    # Use the logical leading edge. Physical "right" is mirrored by Writer
    # for bidi paragraphs, placing Persian text against the left margin.
    justification = properties.find(qn('w:jc'))
    if justification is None:
        justification = _element('jc')
        properties.append(justification)
    justification.set(qn('w:val'), 'start')
    if properties.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}bidi') is None:
        properties.append(_element('bidi', val='1'))
    paragraph.paragraph_format.widow_control = True
    return paragraph


def _run(paragraph, value, *, bold=False, size=10, latin=False):
    from docx.oxml.ns import qn
    from docx.shared import Pt
    run = paragraph.add_run(text(value))
    run.bold = bold
    run.font.name = FONT
    run.font.size = Pt(size)
    properties = run._r.get_or_add_rPr()
    fonts = properties.find(qn('w:rFonts'))
    if fonts is None:
        fonts = _element('rFonts')
        properties.insert(0, fonts)
    for key in ('ascii', 'hAnsi', 'eastAsia', 'cs'):
        fonts.set(qn('w:' + key), FONT)
    properties.append(_element('rtl', val='0' if latin else '1'))
    properties.append(_element('bCs', val='1' if bold else '0'))
    properties.append(_element('szCs', val=str(int(size * 2))))
    properties.append(_element('lang', val='en-US' if latin else 'fa-IR', bidi='fa-IR'))
    return run


def _paragraph(container, value='', *, bold=False, size=10, keep_next=False, after=5):
    paragraph = _paragraph_format(container.add_paragraph(), keep_next=keep_next, after=after)
    if value:
        _run(paragraph, value, bold=bold, size=size)
    return paragraph


def _table_format(table, widths, *, border=True):
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_TABLE_DIRECTION, WD_CELL_VERTICAL_ALIGNMENT
    from docx.shared import Cm
    table.alignment = WD_TABLE_ALIGNMENT.RIGHT
    table.table_direction = WD_TABLE_DIRECTION.RTL
    table.autofit = False
    if border:
        table.style = 'Table Grid'
    for column, width in zip(table.columns, widths):
        column.width = Cm(width)
    for row in table.rows:
        row._tr.get_or_add_trPr().append(_element('cantSplit'))
        for cell, width in zip(row.cells, widths):
            cell.width = Cm(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            properties = cell._tc.get_or_add_tcPr()
            margins = _element('tcMar')
            for side in ('top', 'left', 'bottom', 'right'):
                margins.append(_element(side, w='90', type='dxa'))
            properties.append(margins)
            _paragraph_format(cell.paragraphs[0], after=3)


def _cell(cell, value, *, bold=False, size=9, latin=False):
    paragraph = cell.paragraphs[0]
    paragraph.clear()
    _paragraph_format(paragraph, after=3)
    _run(paragraph, value, bold=bold, size=size, latin=latin)


def _create_document(data, title, subject, *, compact=False):
    """Apply one readable RTL page, letterhead and footer to Word exports."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    document = Document()
    document.core_properties.title = title
    document.core_properties.subject = subject
    document.core_properties.author = 'NIKAN'
    document.core_properties.created = datetime.now(timezone.utc)
    document.core_properties.modified = datetime.now(timezone.utc)
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin, section.bottom_margin = (Cm(2.15), Cm(1.2)) if compact else (Cm(2.6), Cm(1.8))
    section.left_margin = section.right_margin = Cm(1.5 if compact else 1.8)
    section.header_distance, section.footer_distance = (Cm(.4), Cm(.45)) if compact else (Cm(.65), Cm(.7))
    section._sectPr.append(_element('bidi', val='1'))
    normal = document.styles['Normal']
    normal.font.name, normal.font.size = FONT, Pt(10)
    normal._element.get_or_add_rPr().append(_element('rFonts', ascii=FONT, hAnsi=FONT, cs=FONT, eastAsia=FONT))
    normal._element.get_or_add_pPr().append(_element('bidi', val='1'))
    for name in ('Title', 'Heading 1', 'Heading 2'):
        style = document.styles[name]
        style.font.name, style.font.color.rgb = FONT, RGBColor.from_string('173B55')
        style._element.get_or_add_pPr().append(_element('bidi', val='1'))

    # A restrained repeated letterhead uses only the holding's stored logo.
    header = section.header.add_table(rows=1, cols=2, width=Cm(18 if compact else 17.4))
    _table_format(header, [2.4, 15.6 if compact else 15], border=False)
    if data['logo']:
        logo = data['logo']
        scale = min(2.1 / logo['width'], 1.1 / logo['height'])
        header.cell(0, 0).paragraphs[0].add_run().add_picture(
            io.BytesIO(logo['content']), width=Cm(logo['width'] * scale), height=Cm(logo['height'] * scale),
        )
    else:
        _cell(header.cell(0, 0), 'NIKAN', bold=True, size=13, latin=True)
    header_identity = data['annex_identity'] if compact else data['identity']
    _cell(header.cell(0, 1), header_identity['company'] + '\nمدیریت آموزش و شناسنامه‌های شغلی', bold=True, size=9)
    if compact:
        for cell in header.rows[0].cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = Pt(11)
    # Keep a terminal paragraph after the header table. It also avoids the
    # leading empty line that can push the letterhead into the document body.
    terminal = section.header.paragraphs[0]
    terminal._p.getparent().append(terminal._p)
    terminal.paragraph_format.space_before = Pt(0)
    terminal.paragraph_format.space_after = Pt(0)
    terminal.paragraph_format.line_spacing = Pt(1)
    footer = _paragraph_format(section.footer.paragraphs[0], after=0)
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _run(footer, 'نیکان  |  شناسه سند: ', size=8)
    _run(footer, data['annex_number'] if compact else data['number'], size=8, latin=True)
    _run(footer, '  |  صفحه ', size=8)
    footer._p.append(_element('fldSimple', instr='PAGE'))
    _run(footer, ' از ', size=8)
    footer._p.append(_element('fldSimple', instr='NUMPAGES'))
    return document


def render_docx(data):
    """A readable, one-page A4 excerpt, independently from both full profiles."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    document = _create_document(data, TITLE, 'گزیده یک‌صفحه‌ای مستقل — اصل شناسنامه بدون تغییر', compact=True)

    def paragraph(container, value='', *, bold=False, size=10, after=2, keep_next=False):
        result = _paragraph(container, value, bold=bold, size=size, after=after, keep_next=keep_next)
        result.paragraph_format.line_spacing = Pt(13)
        return result

    heading = paragraph(document, TITLE, bold=True, size=12, keep_next=True, after=3)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph(document, 'فرم بدون امضا — برای تکمیل و امضای طرفین قرارداد', size=9, after=1)
    paragraph(document, SUMMARY_NOTE, size=9, after=2)
    paragraph(document, data['status_label'], size=9, bold=data['requires_review'], after=1)
    paragraph(document, 'شماره شناسنامه فردی: ' + data['annex_number'], bold=True, after=4)
    identity = data['annex_identity']
    rows = [
        ('شرکت', identity['company'], 'نام و نام خانوادگی', identity['name']),
        ('کد پرسنلی', identity['code'], 'سمت شغلی', identity['job'] or 'تکمیل شود'),
        ('واحد سازمانی', identity['department'] or 'تکمیل شود', 'شماره قرارداد کار', '........................'),
        ('تاریخ قرارداد کار', '...... / ...... / ......', 'اجرا / بازنگری بعدی',
         (data['annex_effective_date'] or '............') + ' / ' + (data['annex_review_date'] or '............')),
    ]
    details = document.add_table(rows=len(rows), cols=4)
    _table_format(details, [2.5, 6.5, 2.5, 6.5])
    for row, values in zip(details.rows, rows):
        for index, (cell, value) in enumerate(zip(row.cells, values)):
            _cell(cell, value, bold=index % 2 == 0, size=9)
            cell.paragraphs[0].paragraph_format.line_spacing = Pt(12)
            cell.paragraphs[0].paragraph_format.space_after = Pt(0)
    paragraph(document, after=1, size=1)
    if data['missing_profile']:
        paragraph(document, 'شرح شغل تکمیل نشده است؛ بخش‌های خالی پیش از امضا تکمیل شود.', bold=True, size=9)

    clauses = document.add_table(rows=len(data['commitment_fields']), cols=2)
    _table_format(clauses, [3.8, 14.2])
    for index, (row, field) in enumerate(zip(clauses.rows, data['commitment_fields']), 1):
        _cell(row.cells[0], fa_number(index) + '. ' + field['label'], bold=True, size=9)
        _cell(row.cells[1], field['value'] or 'تکمیل شود: ................................................', size=10)
        for cell in row.cells:
            cell.paragraphs[0].paragraph_format.line_spacing = Pt(13)
            cell.paragraphs[0].paragraph_format.space_after = Pt(0)
    paragraph(document, 'اعلام آگاهی و تعهد شغلی', bold=True, size=10, after=1, keep_next=True)
    paragraph(document, DECLARATION, size=9, after=4, keep_next=True)
    signatures = document.add_table(rows=1, cols=3)
    _table_format(signatures, [6, 6, 6])
    for cell, (title, name) in zip(signatures.rows[0].cells, [
        ('کارمند / کارگر', identity['name']), ('نماینده مجاز شرکت', '........................'),
        ('منابع انسانی', '........................'),
    ]):
        _cell(cell, title, bold=True, size=9)
        paragraph(cell, 'نام: ' + name, size=9, after=1)
        paragraph(cell, 'امضا: ........................', size=9, after=5)
        paragraph(cell, 'تاریخ: ...... / ...... / ......', size=9, after=0)
    references = [
        'مرجع شناسنامه شغل: ' + (data['annex_master_number'] or 'تکمیل نشده'),
        'بازنگری اختصاصی فرد: ' + data['personal_revision'],
    ]
    if data['annex_legacy_number']:
        references.append('شناسه قبلی: ' + data['annex_legacy_number'])
    paragraph(document, '  |  '.join(references), size=8, after=0)

    document.settings._element.append(_element('updateFields', val='true'))
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def ideal_profile_data(conn, cid, jid):
    profile = conn.execute('SELECT * FROM job_profiles WHERE id=? AND company_id=?', (jid, cid)).fetchone()
    if profile is None:
        return None
    organization = conn.execute('SELECT name FROM companies WHERE id=?', (cid,)).fetchone()
    return dict(
        identity=dict(company=text(organization['name']), name='', code='',
                      job=text(profile['job']), department=text(profile['department'])),
        number=text(profile['document_number']), master_number=text(profile['document_number']),
        legacy_number='', personal_revision='', logo=holding_logo(conn),
        fields=[dict(key=key, label=label, value=text(profile[key])) for key, label in PROFILE_FIELDS],
        status_label='الگوی مطلوب شغل — مرجع سازمانی', document_kind='مطلوب شغل',
        requires_review=False,
    )


def render_profile_docx(data):
    """One full 18-field format for the ideal job and actual individual document."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    title = 'شناسنامه شغلی — قالب کامل'
    document = _create_document(data, title, data['status_label'])
    heading = _paragraph(document, title, bold=True, size=15, keep_next=True)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _paragraph(document, data['status_label'], bold=True, size=10, keep_next=True)
    identity = data['identity']
    details = document.add_table(rows=4, cols=4)
    _table_format(details, [2.4, 6.3, 2.4, 6.3])
    rows = (
        ('شرکت', identity['company'], 'شناسه سند', data['number']),
        ('نام و نام خانوادگی', identity['name'] or '—', 'کد پرسنلی', identity['code'] or '—'),
        ('سمت شغلی', identity['job'] or '—', 'واحد سازمانی', identity['department'] or '—'),
        ('نوع شناسنامه', data['document_kind'], 'مرجع الگوی شغل', data['master_number'] or '—'),
    )
    for row, values in zip(details.rows, rows):
        for index, (cell, value) in enumerate(zip(row.cells, values)):
            _cell(cell, value, bold=index % 2 == 0)
    _paragraph(document, after=2)
    for index, field in enumerate(data['fields'], 1):
        label = _paragraph(document, fa_number(index) + '. ' + field['label'], bold=True,
                           size=10.5, keep_next=True, after=2)
        label.paragraph_format.space_before = Pt(6)
        _paragraph(document, field['value'] or '—', after=5)
    if data['personal_revision']:
        _paragraph(document, 'بازنگری اختصاصی فرد: ' + data['personal_revision'], size=8)
    if data['legacy_number']:
        _paragraph(document, 'شناسه قبلی برای تطبیق سوابق: ' + data['legacy_number'], size=8)
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def _word_response(content, filename):
    return Response(content,
                    mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Content-Disposition': f'attachment; filename="{filename}"'})


def register(app, db, authenticated, company, error):
    @app.get('/api/passports/<int:eid>/commitment')
    @authenticated
    def commitment_preview(eid):
        cid = company()
        data = commitment_data(db(), cid, positive_id(eid))
        if data is None:
            return error('فرد یافت نشد', 404)
        return render_template('commitment.html', **data,
                               docx_url=url_for('commitment_word', eid=eid, company_id=cid))

    @app.get('/api/passports/<int:eid>/commitment.docx')
    @authenticated
    def commitment_word(eid):
        cid = company()
        data = commitment_data(db(), cid, positive_id(eid))
        if data is None:
            return error('فرد یافت نشد', 404)
        return _word_response(render_docx(data), f'Nikan-Employment-Commitment-{cid}-{eid}.docx')

    @app.get('/api/passports/<int:eid>/profile.docx')
    @authenticated
    def personal_profile_word(eid):
        cid = company()
        data = commitment_data(db(), cid, positive_id(eid))
        if data is None:
            return error('فرد یافت نشد', 404)
        return _word_response(render_profile_docx(data), f'Nikan-Personal-Job-Profile-{cid}-{eid}.docx')

    @app.get('/api/job-profiles/<int:jid>/profile.docx')
    @authenticated
    def ideal_profile_word(jid):
        cid = company()
        data = ideal_profile_data(db(), cid, positive_id(jid))
        if data is None:
            return error('شناسنامه شغل یافت نشد', 404)
        return _word_response(render_profile_docx(data), f'Nikan-Ideal-Job-Profile-{cid}-{jid}.docx')
