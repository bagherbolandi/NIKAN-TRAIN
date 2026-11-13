"""Workbook contracts, real importer interoperability and safe embedded assets."""
import io
import zipfile

import openpyxl
import pytest
from PIL import Image

from app.excel_forms import SCHEMAS, read_rows
from app.excel_workbooks import build_template


COMPANY = {'id': 7, 'name': 'شرکت توسعه منابع انسانی نیکان'}
REFERENCES = {
    'skills': [{'code': 'S-001', 'title': 'ایمنی و سلامت محیط کار', 'category': 'مهارت عمومی'}],
    'employees': [{'code': '00123', 'name': 'آزمایش همکار', 'job': 'کارشناس آموزش', 'company_id': 7},
                  {'code': 'PRIVATE', 'name': 'فرد شرکت دیگر', 'job': 'شغل محرمانه', 'company_id': 8}],
    'jobs': [{'job': 'کارشناس آموزش', 'department': 'منابع انسانی'}],
    'courses': [{'external_code': 'TR-1405-001', 'title': 'ایمنی', 'date': '1405/03/25'}],
    'instructors': [{'name': 'مدرس شرکت'}],
    'departments': [{'department': 'منابع انسانی'}],
    'companies': [{'id': 7, 'name': COMPANY['name']}, {'id': 8, 'name': 'نام شرکت دیگر'}],
}


def image_bytes(format='PNG', size=(100, 60)):
    output = io.BytesIO()
    Image.new('RGB', size, '#245f9e').save(output, format)
    return output.getvalue()


def workbook(payload):
    return openpyxl.load_workbook(io.BytesIO(payload), data_only=False)


def filled(payload, values):
    book = workbook(payload)
    sheet = book['ورود اطلاعات']
    for index, key in enumerate([cell.value for cell in sheet[5]], 1):
        if key in values:
            sheet.cell(7, index).value = values[key]
    output = io.BytesIO()
    book.save(output)
    return output.getvalue()


@pytest.mark.parametrize('kind', tuple(SCHEMAS))
def test_every_template_matches_real_import_contract_and_is_empty(kind):
    schema = SCHEMAS[kind]
    payload = build_template(schema, COMPANY, REFERENCES, image_bytes())
    book = workbook(payload)
    sheet = book['ورود اطلاعات']
    metadata = dict(book['_nikan'].values)
    assert metadata['schema_version'] == '1'
    assert metadata['kind'] == kind
    assert metadata['company_id'] == COMPANY['id']
    assert book['_nikan'].sheet_state == 'veryHidden'
    assert book['_nikan'].max_row <= 20 and book['_nikan'].max_column == 2
    assert sheet.max_column == len(schema['columns'])
    assert [cell.value for cell in sheet[5]] == [column['key'] for column in schema['columns']]
    assert [cell.value for cell in sheet[6]] == [column['label'] + (' *' if column['required'] else '') for column in schema['columns']]
    assert sheet.row_dimensions[5].hidden
    assert sheet.freeze_panes == 'A7'
    assert sheet.page_setup.orientation == 'landscape'
    assert sheet.print_title_rows == '$1:$6'
    assert sheet.auto_filter.ref.startswith('A6:')
    assert all(cell.value is None for row in sheet.iter_rows(min_row=7) for cell in row)
    for visible in (tab for tab in book if tab.sheet_state == 'visible'):
        assert visible.sheet_view.rightToLeft
        assert len(visible._images) == 1
        assert visible._images[0].width <= 144 and visible._images[0].height <= 82
    with pytest.raises(ValueError, match='هیچ ردیف'):
        read_rows(payload, schema, COMPANY['id'])


def test_personnel_code_leading_zero_survives_excel_and_importer():
    payload = build_template(SCHEMAS['employees'], COMPANY, REFERENCES)
    book = workbook(payload)
    assert book['ورود اطلاعات']['A7'].number_format == '@'
    payload = filled(payload, {'code': '00123', 'name': 'همکار آزمون',
                               'department': 'منابع انسانی', 'job': 'کارشناس آموزش'})
    rows = read_rows(payload, SCHEMAS['employees'], COMPANY['id'])
    assert rows == [(7, {'code': '00123', 'name': 'همکار آزمون',
                         'department': 'منابع انسانی', 'job': 'کارشناس آموزش'}, None)]


def test_only_needed_references_are_exported_and_other_company_personnel_is_excluded():
    book = workbook(build_template(SCHEMAS['assessments'], COMPANY, REFERENCES))
    assert 'مرجع پرسنل' in book.sheetnames and 'مرجع شایستگی‌ها' in book.sheetnames
    assert not {'مرجع شرکت‌ها', 'مرجع مشاغل', 'مرجع دوره‌ها', 'مرجع مدرسان', 'مرجع واحدها'} & set(book.sheetnames)
    values = [cell.value for sheet in book for row in sheet for cell in row]
    assert '00123' in values
    assert not {'PRIVATE', 'فرد شرکت دیگر', 'نام شرکت دیگر', 'شغل محرمانه'} & set(values)
    for rule in book['ورود اطلاعات'].data_validations.dataValidation:
        if rule.type == 'list':
            assert rule.formula1.startswith('=NIKAN_ref_')
            assert rule.formula1[1:] in book.defined_names
            assert '5006' in str(rule.sqref)


def test_choice_labels_round_trip_through_importer_and_data_contains_no_examples():
    payload = build_template(SCHEMAS['courses'], COMPANY, REFERENCES)
    book = workbook(payload)
    assert 'برنامه‌ریزی‌شده' in [cell.value for row in book['_lists'] for cell in row]
    payload = filled(payload, {'course_code': 'TR-001', 'skill_code': 'S-001', 'title': 'آموزش ایمنی',
                               'date': '1405/03/25', 'hours': 4, 'status': 'برنامه‌ریزی‌شده',
                               'calendar_scope': 'داخلی شرکت'})
    row, values, error = read_rows(payload, SCHEMAS['courses'], COMPANY['id'])[0]
    assert row == 7 and error is None
    assert values['status'] == 'planned' and values['calendar_scope'] == 'internal'
    assert values['date'] == '1405/03/25'


@pytest.mark.parametrize('format', ('PNG', 'JPEG'))
def test_logo_is_actual_embedded_stored_asset_with_aspect_ratio(format):
    logo = image_bytes(format)
    payload = build_template(SCHEMAS['employees'], COMPANY, REFERENCES, {'content': logo})
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        media = [name for name in archive.namelist() if name.startswith('xl/media/')]
        assert media and all(archive.read(name) == logo for name in media)
    book = workbook(payload)
    assert dict(book['_nikan'].values)['branding'] == 'stored'
    anchor = book['ورود اطلاعات']._images[0].anchor
    assert anchor.ext.cx / anchor.ext.cy == pytest.approx(100 / 60)


@pytest.mark.parametrize('bad_logo', [b'not an image', b'\x89PNG\r\n\x1a\ninvalid',
                                     b'x' * (2 * 1024 * 1024 + 1),
                                     image_bytes(size=(4097, 1))])
def test_invalid_or_excessive_logo_uses_explicit_text_fallback(bad_logo):
    book = workbook(build_template(SCHEMAS['employees'], COMPANY, REFERENCES, bad_logo))
    assert not book['ورود اطلاعات']._images
    assert book['ورود اطلاعات']['A1'].value == 'NIKAN\nنیکان'
    assert dict(book['_nikan'].values)['branding'] == 'fallback-invalid'
    assert any('قابل استفاده نبود' in str(cell.value) for row in book['راهنما'] for cell in row)


def test_reference_text_is_literal_not_executable_formula_and_no_external_links():
    references = {**REFERENCES, 'skills': [{'code': '=1+1', 'title': '=HYPERLINK("https://example.invalid")', 'category': '+sample'}]}
    payload = build_template(SCHEMAS['assessments'], {'id': 7, 'name': '=Company &P'}, references)
    book = workbook(payload)
    reference = book['مرجع شایستگی‌ها']
    assert reference['A7'].value == '=1+1' and reference['A7'].data_type == 's'
    assert reference['B7'].value.startswith('=HYPERLINK') and reference['B7'].data_type == 's'
    assert all(cell.data_type != 'f' for sheet in book for row in sheet for cell in row)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert not any('externallinks' in name.lower() or 'vbaproject' in name.lower() for name in archive.namelist())


def test_long_job_fields_keep_the_same_master_and_personal_order_and_wrapping():
    master = SCHEMAS['job_profiles']
    personal = SCHEMAS['personal_documents']
    fields = [column for column in master['columns'] if column['key'] not in ('job', 'department')]
    assert [(column['key'], column['label']) for column in fields] == [(column['key'], column['label']) for column in personal['columns'][1:]]
    text = 'شرح کامل وظایف و تعهدات فرد در حوزه آموزش سازمانی. ' * 150
    payload = build_template(master, COMPANY, REFERENCES)
    payload = filled(payload, {'job': 'کارشناس آموزش', 'duties': text})
    _, values, error = read_rows(payload, master, COMPANY['id'])[0]
    assert error is None and values['duties'] == text.strip()
    book = workbook(payload)
    index = [cell.value for cell in book['ورود اطلاعات'][5]].index('duties') + 1
    assert book['ورود اطلاعات'].cell(7, index).alignment.wrap_text
    assert book['ورود اطلاعات'].row_dimensions[7].height is None


def test_master_job_dropdown_allows_a_new_job_while_existing_references_remain_available():
    sheet = workbook(build_template(SCHEMAS['job_profiles'], COMPANY, REFERENCES))['ورود اطلاعات']
    lists = [rule for rule in sheet.data_validations.dataValidation if rule.type == 'list']
    assert lists and all(not rule.showErrorMessage for rule in lists)


@pytest.mark.parametrize('kind', ['employees', 'needs'])
def test_organizational_unit_dropdown_suggests_existing_units_and_allows_new_titles(kind):
    form = SCHEMAS[kind]
    sheet = workbook(build_template(form, COMPANY, REFERENCES))['ورود اطلاعات']
    index = [field['key'] for field in form['columns']].index('department') + 1
    cell = sheet.cell(7, index).coordinate
    rule = next(rule for rule in sheet.data_validations.dataValidation if cell in rule)
    assert rule.type == 'list' and rule.formula1 == '=NIKAN_ref_departments'
    assert not rule.showErrorMessage
