"""Consistent, company-bound Persian Excel forms without external resources.

The importer owns field definitions and validation. This module only renders its
schema: row 5 holds immutable keys, row 6 captions, and data begins on row 7.
"""
from __future__ import annotations

import io
import json
import warnings

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter, quote_sheetname
from openpyxl.utils.units import pixels_to_EMU
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.page import PageMargins
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from PIL import Image as PillowImage, UnidentifiedImageError


DATA_SHEET = 'ورود اطلاعات'
GUIDE_SHEET = 'راهنما'
SCHEMA_VERSION = '1'
DATA_START = 7
ENTRY_ROWS = 100
VALIDATION_LAST_ROW = 5006
MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_DIMENSION = 4096
MAX_LOGO_PIXELS = 8_000_000
FONT = 'Tahoma'
DARK = '173658'
BLUE = '245F9E'
LIGHT = 'EAF2FB'
PALE = 'F6F9FD'
GOLD = 'FFFBEE'
INK = '20354E'
MUTED = '60758D'
LINE = 'D9E4F1'
WHITE = 'FFFFFF'

# Reference names and natural-key columns are fixed, never spreadsheet formulas
# constructed from a name supplied by an organization or person.
REFERENCES = {
    'skills': ('مرجع شایستگی‌ها', [('code', 'کد شایستگی'), ('title', 'عنوان شایستگی'), ('category', 'گروه')]),
    'employees': ('مرجع پرسنل', [('code', 'کد پرسنلی'), ('name', 'نام و نام خانوادگی'), ('job', 'سمت شغلی')]),
    'jobs': ('مرجع مشاغل', [('job', 'عنوان شغل'), ('department', 'واحد سازمانی')]),
    'courses': ('مرجع دوره‌ها', [('external_code', 'کد یکتای دوره'), ('title', 'عنوان دوره'), ('date', 'تاریخ شمسی')]),
    'instructors': ('مرجع مدرسان', [('name', 'نام مدرس')]),
    'departments': ('مرجع واحدها', [('department', 'واحد سازمانی')]),
    'companies': ('مرجع شرکت‌ها', [('id', 'شناسه شرکت'), ('name', 'نام شرکت')]),
}
FORM_CODES = {
    'employees': 'NKN-TRN-01', 'job_profiles': 'NKN-TRN-02',
    'competency_selection': 'NKN-TRN-03', 'requirements': 'NKN-TRN-04',
    'assessments': 'NKN-TRN-05', 'courses': 'NKN-TRN-06',
    'records': 'NKN-TRN-07', 'instructors': 'NKN-TRN-08',
    'needs': 'NKN-TRN-09', 'personal_requirements': 'NKN-TRN-10',
    'personal_documents': 'NKN-TRN-11', 'competency-bank': 'NKN-TRN-12',
    'companies': 'NKN-TRN-13',
}
TYPE_LABELS = {'text': 'متن', 'integer': 'عدد صحیح', 'number': 'عدد', 'date': 'تاریخ شمسی؛ متن'}
ALIGN = Alignment(horizontal='right', vertical='center', wrap_text=True, readingOrder=2)
CENTER = Alignment(horizontal='center', vertical='center', wrap_text=True, readingOrder=2)
CELL_BORDER = Border(left=Side(style='hair', color=LINE),
                     right=Side(style='hair', color=LINE),
                     bottom=Side(style='thin', color=LINE))


def _get(value, key, default=None):
    """Accept dictionaries and sqlite3.Row without inspecting unrelated fields."""
    try:
        return value[key]
    except (KeyError, IndexError, TypeError):
        return default


def _literal(cell, value):
    """Write reference text literally, including values beginning with '='."""
    value = '' if value is None else str(value)
    if len(value) > 32767 or ILLEGAL_CHARACTERS_RE.search(value):
        raise ValueError('متن مرجع برای درج در فایل اکسل معتبر نیست')
    cell.value = value
    cell.data_type = 's'
    return cell


def _brand(brand):
    if brand is None:
        return None, 'fallback'
    raw = _get(brand, 'content') if not isinstance(brand, (bytes, bytearray, memoryview)) else brand
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        return None, 'fallback-invalid'
    raw = bytes(raw)
    if not raw or len(raw) > MAX_LOGO_BYTES:
        return None, 'fallback-invalid'
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', PillowImage.DecompressionBombWarning)
            with PillowImage.open(io.BytesIO(raw)) as image:
                width, height = image.size
                if image.format not in ('PNG', 'JPEG') or min(width, height) < 1:
                    return None, 'fallback-invalid'
                if max(width, height) > MAX_LOGO_DIMENSION or width * height > MAX_LOGO_PIXELS:
                    return None, 'fallback-invalid'
                image.verify()
            # Verify decodability as well as PNG/JPEG container structure before
            # allowing openpyxl to embed any content from an uploaded asset.
            with PillowImage.open(io.BytesIO(raw)) as image:
                image.load()
        return (raw, width, height), 'stored'
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            PillowImage.DecompressionBombError, PillowImage.DecompressionBombWarning):
        return None, 'fallback-invalid'


def _merge_text(sheet, row, start, end, text, *, fill=DARK, color=WHITE, size=11, bold=False):
    for col in range(start, end + 1):
        sheet.cell(row, col).fill = PatternFill('solid', fgColor=fill)
    if end > start:
        sheet.merge_cells(start_row=row, start_column=start, end_row=row, end_column=end)
    cell = _literal(sheet.cell(row, start), text)
    cell.fill = PatternFill('solid', fgColor=fill)
    cell.font = Font(name=FONT, size=size, color=color, bold=bold)
    cell.alignment = ALIGN
    return cell


def _page_setup(sheet, form_code, company_name):
    sheet.sheet_view.rightToLeft = True
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 90
    sheet.sheet_format.defaultRowHeight = 27
    sheet.freeze_panes = 'A7'
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.sheet_properties.outlinePr.summaryRight = True
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.page_margins = PageMargins(left=.25, right=.25, top=.35, bottom=.4, header=.1, footer=.15)
    sheet.print_options.horizontalCentered = True
    sheet.print_options.gridLines = False
    sheet.print_title_rows = '1:6'
    sheet.oddFooter.left.text = str(company_name).replace('&', '&&')
    sheet.oddFooter.center.text = 'نیکان | صفحه &P از &N'
    sheet.oddFooter.right.text = form_code
    for part in (sheet.oddFooter.left, sheet.oddFooter.center, sheet.oddFooter.right):
        part.font = FONT
        part.size = 8
        part.color = MUTED


def _logo(sheet, logo, *, column_width, single=False):
    if logo is None:
        cell = _literal(sheet['A1'], 'NIKAN\nنیکان')
        cell.font = Font(name=FONT, size=23 if single else 20, color=BLUE, bold=True)
        cell.alignment = CENTER
        return
    raw, original_width, original_height = logo
    max_width = min(144, max(50, int(column_width * 7 + 5) - 20))
    max_height = 58 if single else 82
    scale = min(max_width / original_width, max_height / original_height, 1)
    width, height = original_width * scale, original_height * scale
    image = ExcelImage(io.BytesIO(raw))
    image.width, image.height = width, height
    available_height = 72 if single else 120
    left = max(8, (int(column_width * 7 + 5) - width) / 2)
    top = max(4, (available_height - height) / 2)
    image.anchor = OneCellAnchor(
        _from=AnchorMarker(col=0, colOff=pixels_to_EMU(left), row=0, rowOff=pixels_to_EMU(top)),
        ext=XDRPositiveSize2D(pixels_to_EMU(width), pixels_to_EMU(height)),
    )
    sheet.add_image(image)


def _header(sheet, title, company, columns, form_code, logo, hint):
    """Keep decoration within the actual columns, including single-column forms."""
    count = len(columns)
    company_id, company_name = _get(company, 'id'), _get(company, 'name', 'هلدینگ نیکان')
    for index, width in enumerate(columns, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    _page_setup(sheet, form_code, company_name)
    if count == 1:
        sheet.row_dimensions[1].height = 54
        sheet.row_dimensions[2].height = 30
        sheet.row_dimensions[3].height = 36
        sheet['A1'].fill = PatternFill('solid', fgColor=WHITE)
        _logo(sheet, logo, column_width=columns[0], single=True)
        _merge_text(sheet, 2, 1, 1, title, size=16, bold=True)
        _merge_text(sheet, 3, 1, 1, f'شرکت: {company_name} | شناسه: {company_id}\nکد فرم: {form_code} | بازنگری: ۰۱', size=10)
    else:
        sheet.row_dimensions[1].height = 33
        sheet.row_dimensions[2].height = 25
        sheet.row_dimensions[3].height = 32
        sheet.merge_cells('A1:A3')
        sheet['A1'].fill = PatternFill('solid', fgColor=WHITE)
        _logo(sheet, logo, column_width=columns[0])
        _merge_text(sheet, 1, 2, count, title, size=17, bold=True)
        _merge_text(sheet, 2, 2, count, f'سامانه مدیریت آموزش نیکان | کد فرم: {form_code} | بازنگری: ۰۱', size=10)
        _merge_text(sheet, 3, 2, count, f'شرکت: {company_name} | شناسه شرکت: {company_id}', size=11)
    sheet.row_dimensions[4].height = 38
    _merge_text(sheet, 4, 1, count, hint, fill=LIGHT, color=INK, size=10)
    sheet.row_dimensions[5].height = 5
    sheet.sheet_properties.tabColor = BLUE


def _captions(sheet, captions):
    sheet.row_dimensions[6].height = 43
    for index, caption in enumerate(captions, 1):
        cell = _literal(sheet.cell(6, index), caption)
        cell.fill = PatternFill('solid', fgColor=BLUE)
        cell.font = Font(name=FONT, size=10, color=WHITE, bold=True)
        cell.alignment = CENTER
        cell.border = Border(bottom=Side(style='medium', color=DARK))


def _row(sheet, row, values, *, text=True):
    sheet.row_dimensions[row].height = 30
    for index, value in enumerate(values, 1):
        cell = sheet.cell(row, index)
        if text:
            _literal(cell, value)
            cell.number_format = '@'
        else:
            cell.value = value
        cell.font = Font(name=FONT, size=10, color=INK)
        cell.fill = PatternFill('solid', fgColor=WHITE if row % 2 else PALE)
        cell.alignment = ALIGN
        cell.border = CELL_BORDER


def _reference_rows(reference_data, key, company_id):
    rows = reference_data.get(key, [])
    result, seen = [], set()
    for row in rows:
        owner = _get(row, 'company_id')
        if owner is not None and str(owner) != str(company_id):
            continue
        columns = REFERENCES[key][1]
        values = tuple('' if _get(row, name) is None else str(_get(row, name)) for name, _ in columns)
        if not values[0] or values in seen:
            continue
        seen.add(values)
        result.append(values)
    return result


def _references(workbook, schema, company, reference_data, form_code, logo):
    ranges, rows_by_key = {}, {}
    needed = {_reference_for(column, schema['kind']) for column in schema['columns']}
    for key, (name, fields) in REFERENCES.items():
        # Global company names belong only in the explicit admin company form.
        if key not in needed or key not in reference_data or (key == 'companies' and schema['kind'] != 'companies'):
            continue
        rows = _reference_rows(reference_data, key, _get(company, 'id'))
        rows_by_key[key] = rows
        sheet = workbook.create_sheet(name)
        widths = [max(20, min(42, 21 if index == 0 else 37)) for index in range(len(fields))]
        if len(fields) == 1:
            widths = [100]
        elif len(fields) == 2:
            widths = [32, 68]
        _header(sheet, f'{schema["title"]} | {name}', company, widths, form_code, logo,
                'مرجع هنگام صدور فایل؛ کد ستون اول را در فرم وارد کنید. این برگه وارد سامانه نمی‌شود.' if rows else
                'این فهرست مرجع در شرکت انتخاب‌شده خالی است؛ ابتدا اطلاعات مرجع را در سامانه ثبت کنید.')
        _captions(sheet, [label for _, label in fields])
        sheet.row_dimensions[5].hidden = True
        for offset, values in enumerate(rows, DATA_START):
            _row(sheet, offset, values)
        last = max(DATA_START, DATA_START + len(rows) - 1)
        sheet.auto_filter.ref = f'A6:{get_column_letter(len(fields))}{last}'
        sheet.print_area = f'A1:{get_column_letter(len(fields))}{last}'
        sheet.sheet_properties.tabColor = MUTED
        range_name = 'NIKAN_ref_' + key
        workbook.defined_names.add(DefinedName(range_name, attr_text=f'{quote_sheetname(name)}!$A$7:$A${last}'))
        ranges[key] = range_name
    return ranges, rows_by_key


def _reference_for(column, kind):
    if kind == 'companies' and column['key'] == 'company_id':
        return 'companies'
    return column.get('reference')


def _widths(columns):
    if len(columns) == 1:
        return [100]
    if len(columns) == 2:
        return [32, 68]
    result = []
    for column in columns:
        key, kind = column['key'], column.get('type', 'text')
        if key in ('code', 'employee_code', 'skill_code', 'course_code', 'company_id'):
            width = 23
        elif kind == 'date' or key.endswith('_date'):
            width = 23
        elif kind in ('integer', 'number'):
            width = 20
        elif key in ('name', 'title', 'job', 'department', 'instructor'):
            width = 34
        elif key in ('reason', 'duties', 'definition', 'commitments', 'conditions', 'performance', 'contract_notes'):
            width = 44
        else:
            width = 30
        result.append(width)
    return result


def _choices(column):
    return [str(_get(choice, 'label', _get(choice, 'value', choice))) for choice in column.get('choices', [])]


def _numeric_limits(column, kind):
    key = column['key']
    if key == 'level':
        return (1 if kind == 'requirements' else 0), 4
    if key in ('importance', 'priority', 'reaction', 'behavior'):
        return 1, 5 if key in ('reaction', 'behavior') else 3
    if key in ('score', 'result'):
        return 0, 100
    if key == 'year':
        return 1300, 1499
    if key in ('cost', 'rate'):
        return 0, None
    if key in ('hours', 'company_id'):
        return 0, None
    return None


def _validations(workbook, sheet, columns, kind, ranges):
    lists = None
    list_column = 0
    for index, column in enumerate(columns, 1):
        address = f'{get_column_letter(index)}{DATA_START}:{get_column_letter(index)}{VALIDATION_LAST_ROW}'
        choices = _choices(column)
        reference = _reference_for(column, kind)
        if choices:
            if lists is None:
                lists = workbook.create_sheet('_lists')
                lists.sheet_state = 'veryHidden'
            list_column += 1
            for row, label in enumerate(choices, 1):
                _literal(lists.cell(row, list_column), label)
            name = f'NIKAN_choice_{list_column}'
            letter = get_column_letter(list_column)
            workbook.defined_names.add(DefinedName(name, attr_text=f'{quote_sheetname(lists.title)}!${letter}$1:${letter}${len(choices)}'))
            rule = DataValidation(type='list', formula1='=' + name, allow_blank=not column.get('required', False))
        elif reference in ranges:
            rule = DataValidation(type='list', formula1='=' + ranges[reference], allow_blank=not column.get('required', False))
        elif column.get('type') in ('integer', 'number'):
            limits = _numeric_limits(column, kind)
            if limits is None:
                continue
            low, high = limits
            operator = 'between' if high is not None else ('greaterThan' if column['key'] in ('hours', 'company_id') else 'greaterThanOrEqual')
            rule = DataValidation(type='whole' if column['type'] == 'integer' else 'decimal',
                                  operator=operator, formula1=low, formula2=high,
                                  allow_blank=not column.get('required', False))
        else:
            continue
        rule.showErrorMessage = True
        if column['key'] == 'department' or (kind == 'job_profiles' and column['key'] == 'job'):
            # Organizational units are editable text, and the master-job form
            # creates new jobs. References remain suggestions for these fields.
            rule.showErrorMessage = False
        rule.errorStyle = 'stop'
        rule.errorTitle = 'مقدار معتبر وارد کنید'
        rule.error = 'گزینه را از فهرست مرجع انتخاب کنید.' if rule.type == 'list' else 'عدد را مطابق نوع و محدوده نوشته‌شده در عنوان ستون وارد کنید.'
        rule.showInputMessage = True
        rule.promptTitle = 'راهنمای تکمیل'
        rule.prompt = 'ستاره یعنی ستون الزامی. برای جزئیات برگه «راهنما» را ببینید.'
        rule.showDropDown = False
        sheet.add_data_validation(rule)
        rule.add(address)


def _example(column, kind, rows_by_key):
    reference = _reference_for(column, kind)
    if reference in rows_by_key:
        return rows_by_key[reference][0][0] if rows_by_key[reference] else 'ابتدا مرجع را در سامانه تکمیل کنید'
    choices = _choices(column)
    if choices:
        return ' / '.join(choices)
    key = column['key']
    examples = {'code': 'S-001' if kind == 'competency-bank' else '00123',
                'employee_code': '00123', 'skill_code': 'S-001', 'course_code': 'TR-1405-001',
                'company_id': 'شناسه موجود از برگه مرجع شرکت‌ها', 'name': 'نام نمونه؛ فقط راهنما',
                'title': 'عنوان نمونه؛ فقط راهنما', 'level': '2', 'importance': '2',
                'priority': '2', 'year': '1405', 'hours': '4', 'cost': '0', 'rate': '0',
                'score': '85', 'reaction': '4', 'behavior': '4', 'result': '80',
                'department': 'منابع انسانی', 'job': 'کارشناس آموزش', 'reason': 'رفع نیاز آموزشی مشخص'}
    if column.get('type') == 'date' or key.endswith('_date'):
        return '1405/03/25'
    return str(column.get('example', examples.get(key, 'متن مربوط به همین فیلد')))


def _field_help(column, schema):
    key = column['key']
    parts = []
    if key in schema.get('keys', []):
        parts.append('جزء شناسه تطبیق رکورد موجود است.')
    if column.get('type') == 'date' or key.endswith('_date'):
        parts.append('تاریخ شمسی را به‌صورت متن با قالب سال/ماه/روز وارد کنید؛ تاریخ میلادی Excel وارد نکنید.')
    if key in ('code', 'employee_code', 'skill_code', 'course_code'):
        parts.append('متنی است؛ صفرهای ابتدای کد را نگه دارید.')
    if column.get('reference'):
        parts.append('از کد یا عنوان دقیق موجود در برگه مرجع استفاده کنید.')
        if key == 'department' or (schema['kind'] == 'job_profiles' and key == 'job'):
            parts.append('در این فرم، عنوان شغل یا واحد جدید نیز قابل ورود است.')
    if column.get('choices'):
        parts.append('یکی از گزینه‌های فهرست کشویی را انتخاب کنید.')
    if not column.get('required'):
        parts.append('خالی‌گذاشتن در به‌روزرسانی، مقدار فعلی را تغییر نمی‌دهد.')
    if schema['kind'] == 'companies' and key == 'company_id':
        parts.append('برای ایجاد شرکت جدید خالی بماند؛ برای تغییر نام، شناسه فعلی و حالت افزودن و به‌روزرسانی لازم است.')
    return ' '.join(parts) or 'مقدار واقعی این فیلد را ثبت کنید؛ از فرمول استفاده نکنید.'


def _guide(workbook, schema, company, rows_by_key, form_code, logo, brand_state):
    sheet = workbook.create_sheet(GUIDE_SHEET, 1)
    _header(sheet, 'راهنمای تکمیل | ' + schema['title'], company,
            [32, 13, 20, 35, 66], form_code, logo,
            'نمونه‌های این برگه وارد سامانه نمی‌شوند؛ اطلاعات واقعی را فقط در برگه «ورود اطلاعات» بنویسید.')
    sheet.row_dimensions[5].hidden = True
    _captions(sheet, ['نام فیلد', 'تکمیل', 'نوع داده', 'نمونه یا گزینه مجاز', 'توضیح'])
    for row, column in enumerate(schema['columns'], DATA_START):
        _row(sheet, row, [column['label'], 'الزامی *' if column.get('required') else 'اختیاری',
                         TYPE_LABELS.get(column.get('type', 'text'), 'متن'),
                         _example(column, schema['kind'], rows_by_key), _field_help(column, schema)])
        sheet.row_dimensions[row].height = 56
    notes = [
        '۱. این فایل برای شرکت درج‌شده در سربرگ صادر شده است؛ نوع فرم و شرکت را هنگام بارگذاری کنترل کنید.',
        '۲. عنوان‌ها، ترتیب ستون‌ها، ردیف فنی مخفی و برگه‌های فنی را تغییر ندهید. داده‌ها از ردیف ۷ آغاز می‌شوند.',
        '۳. خانه‌های دارای ستاره الزامی‌اند. نمونه‌های راهنما، داده واقعی یا پرسنل ثبت‌شده نیستند.',
        '۴. در حالت پیش‌فرض فقط افزودن انجام می‌شود. برای تغییر رکورد موجود، پس از پیش‌نمایش حالت «افزودن و به‌روزرسانی» را انتخاب کنید.',
        '۵. خانه اختیاری خالی، مقدار قبلی را پاک نمی‌کند. این فایل هیچ رکوردی را حذف نمی‌کند.',
        '۶. کدهای پرسنلی و دوره را به‌صورت متن نگه دارید؛ صفرهای ابتدا نباید حذف شوند. حداکثر ۵۰۰۰ ردیف قابل بارگذاری است.',
        '۷. از فرمول، ماکرو، پیوند خارجی و خطای محاسباتی استفاده نکنید؛ مقدار نهایی را وارد کنید.',
        '۸. برگه‌های مرجع، داده شرکت در زمان دریافت فایل هستند. پس از تغییر مرجع، الگوی تازه دریافت کنید.',
        '۹. ابتدا فایل را برای پیش‌نمایش و بررسی خطاها بارگذاری کنید؛ ثبت نهایی در مرحله جداگانه انجام می‌شود.',
        str(schema.get('description') or ''),
    ]
    if schema['kind'] == 'companies':
        notes[3] = '۴. این فرم فقط شرکت جدید اضافه می‌کند؛ تغییر نام شرکت موجود از بخش مدیریت شرکت‌ها انجام می‌شود.'
        notes.append('فرم شرکت‌ها ویژه ادمین هلدینگ است؛ حذف شرکت فقط از بخش مدیریت شرکت‌ها و طبق محدودیت وابستگی‌ها انجام می‌شود.')
    if brand_state == 'stored':
        notes.append('هویت بصری: لوگوی ثبت‌شده هلدینگ در فایل درج شده است.')
    elif brand_state == 'fallback-invalid':
        notes.append('لوگوی ثبت‌شده قابل استفاده نبود؛ نشان نوشتاری NIKAN / نیکان جایگزین است. این نشان، تصویر رسمی لوگوی سازمان نیست.')
    else:
        notes.append('لوگوی هلدینگ هنوز ثبت نشده است؛ نشان نوشتاری NIKAN / نیکان استفاده شده است و تصویر رسمی لوگو نیست.')
    row = DATA_START + len(schema['columns']) + 1
    _merge_text(sheet, row, 1, 5, 'روال تکمیل و ورود امن اطلاعات', size=12, bold=True)
    sheet.row_dimensions[row].height = 28
    for note in filter(None, notes):
        row += 1
        _merge_text(sheet, row, 1, 5, note, fill=WHITE if row % 2 else PALE, color=INK, size=10)
        sheet.row_dimensions[row].height = 36
    sheet.print_area = f'A1:E{row}'
    sheet.sheet_properties.tabColor = MUTED


def build_template(schema, company, reference_data, brand=None) -> bytes:
    """Render an importer schema as a branded XLSX using scoped reference rows.

    ``brand`` is the stored holding PNG/JPEG bytes (or a mapping with ``content``).
    Reference rows without ``company_id`` must already be scoped by the caller;
    rows carrying another company ID are excluded here as a second guard.
    """
    columns = list(schema['columns'])
    if not columns or len({column['key'] for column in columns}) != len(columns):
        raise ValueError('ستون‌های الگوی اکسل معتبر نیستند')
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = DATA_SHEET
    title, kind = str(schema['title']), str(schema['kind'])
    form_code = FORM_CODES.get(kind, 'NKN-TRN-FORM')
    logo, brand_state = _brand(brand)
    workbook.properties.creator = 'Nikan Training'
    workbook.properties.title = 'نیکان | ' + title
    workbook.properties.subject = 'فرم استاندارد ورود اطلاعات آموزش'
    workbook.properties.description = 'فرم نسخه ۱؛ ' + str(_get(company, 'name', 'هلدینگ نیکان'))
    workbook.properties.language = 'fa-IR'
    _header(sheet, title, company, _widths(columns), form_code, logo,
            'داده‌ها را از ردیف ۷ وارد کنید. * یعنی الزامی؛ کدها متنی‌اند. پیش از تکمیل، برگه «راهنما» را بخوانید.')
    sheet.row_dimensions[5].hidden = True
    for index, column in enumerate(columns, 1):
        _literal(sheet.cell(5, index), column['key'])
    _captions(sheet, [column['label'] + (' *' if column.get('required') else '') for column in columns])
    last = DATA_START + ENTRY_ROWS - 1
    for row in range(DATA_START, last + 1):
        for index, column in enumerate(columns, 1):
            cell = sheet.cell(row, index)
            cell.number_format = '0' if column.get('type') == 'integer' else '0.00' if column.get('type') == 'number' else '@'
            cell.fill = PatternFill('solid', fgColor=GOLD if column.get('required') else WHITE if row % 2 else PALE)
            cell.font = Font(name=FONT, size=11, color=INK)
            cell.alignment = ALIGN
            cell.border = CELL_BORDER
            cell.protection = Protection(locked=False)
    for index, column in enumerate(columns, 1):
        sheet.cell(6, index).comment = Comment('شناسه فنی: ' + column['key'] + '\n' + _field_help(column, schema), 'Nikan Training')
    sheet.auto_filter.ref = f'A6:{get_column_letter(len(columns))}{last}'
    sheet.print_area = f'A1:{get_column_letter(len(columns))}{last}'
    ranges, rows_by_key = _references(workbook, schema, company, reference_data, form_code, logo)
    _guide(workbook, schema, company, rows_by_key, form_code, logo, brand_state)
    _validations(workbook, sheet, columns, kind, ranges)
    metadata = workbook.create_sheet('_nikan')
    for row, (key, value) in enumerate([
        ('schema_version', SCHEMA_VERSION), ('kind', kind), ('company_id', _get(company, 'id')),
        ('form_code', form_code), ('revision', '01'), ('data_sheet', DATA_SHEET),
        ('data_start_row', DATA_START), ('column_keys', json.dumps([column['key'] for column in columns])),
        ('branding', brand_state),
    ], 1):
        _literal(metadata.cell(row, 1), key)
        if key in ('company_id', 'data_start_row') and isinstance(value, int):
            metadata.cell(row, 2).value = value
        else:
            _literal(metadata.cell(row, 2), value)
    metadata.sheet_state = 'veryHidden'
    metadata.protection.sheet = True
    workbook.active = 0
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()
