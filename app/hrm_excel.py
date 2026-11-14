"""Uniform, scoped HRM spreadsheets that reuse the real authenticated domain APIs.

A preview and a failed batch roll back domain data, audit rows and identity
counters together. This module never implements a second business-data writer.
"""
from datetime import date, datetime
import hashlib
import hmac
import io
import json
from pathlib import Path
import re
import sqlite3
import zipfile
from urllib.parse import urlencode

import openpyxl
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.styles import Alignment
from openpyxl.worksheet.datavalidation import DataValidation
from flask import Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from . import hrm_access as access
from .hrm_common import HrmConflict, date_value, integer_value, jalali_display
from .hrm_core import _schema_for_actor
from .validation import positive_id, SQLITE_MAX_INTEGER
from .excel_workbooks import build_template, DATA_SHEET, DATA_START

VERSION = 'hrm-excel-1'
MAX_BYTES = 8 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024
MAX_ROWS = 5000
CLEAR = '[پاک شود]'
MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
EXTRA_REFERENCES = frozenset({'hse_training_options', 'training_competency_options'})
CANONICAL_REPLACEMENTS = {'employees': 'people', 'assessments': 'training_assessments', 'courses': 'training_courses', 'records': 'training_records'}


class DomainFailure(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def _literal(cell, value):
    cell.value = value
    if isinstance(value, str):
        cell.data_type = 's'


def _choices(field):
    result = []
    for choice in field.get('choices', []):
        if isinstance(choice, dict):
            result.append((choice.get('value'), choice.get('label', choice.get('value'))))
        else:
            result.append((choice[0], choice[1]))
    return result


def _columns(schema):
    result = [dict(key='id', label='شناسه رکورد؛ فقط در حالت اصلاح', type='integer'),
              dict(key='version', label='نسخه جاری؛ فقط در حالت اصلاح', type='integer')]
    for field in schema.get('fields', []):
        if field.get('readonly') or field['key'] in {'id', 'version', 'file', 'password'}:
            continue
        item = dict(field)
        if item.get('type') in ('employee', 'company', 'resource', 'site'):
            item['type'] = 'integer' if item['key'].endswith('_id') else 'text'
        item['choices'] = [dict(value=value, label=label) for value, label in _choices(field)]
        if item['key'] == 'employee_id':
            # Either a canonical id or the exact scoped personnel code suffices.
            item['required'] = False
            result.append(dict(key='employee_code', label='کد پرسنلی متنی؛ جایگزین شناسه فرد', type='text'))
        result.append(item)
    return result


def _site_applicable(resource, schema):
    return resource != 'sites' and any(field['key'] in ('site_id', 'employee_id') for field in schema.get('fields', []))


def _signature(app, resource, company_id, site_id, columns):
    message = json.dumps([VERSION, resource, company_id, site_id, [column['key'] for column in columns]],
                         separators=(',', ':'), ensure_ascii=True).encode()
    secret = app.secret_key.encode() if isinstance(app.secret_key, str) else app.secret_key
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _metadata(workbook):
    if '_nikan' not in workbook:
        raise ValueError('فایل فاقد مشخصات الگوی استاندارد نیکان است')
    rows = list(workbook['_nikan'].iter_rows(values_only=True))
    if len(rows) > 30 or any(len(row) < 2 for row in rows):
        raise ValueError('مشخصات الگوی اکسل معتبر نیست')
    pairs = [(row[0], row[1]) for row in rows if row[0] is not None]
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('مشخصات تکراری الگوی اکسل معتبر نیست')
    return dict(pairs)


def _read_workbook(payload):
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError('حجم فایل XLSX باید حداکثر ۸ مگابایت باشد')
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > 1000 or sum(member.file_size for member in members) > MAX_EXPANDED:
                raise ValueError('حجم بازشده یا تعداد اجزای فایل بیش از حد مجاز است')
            if len({member.filename for member in members}) != len(members):
                raise ValueError('اجزای تکراری فایل اکسل مجاز نیست')
            for member in members:
                name = member.filename.lower()
                if 'externallinks/' in name or 'vbaproject' in name or name.startswith('/') or '..' in name.split('/'):
                    raise ValueError('فایل دارای ماکرو، مسیر یا پیوند خارجی پذیرفته نمی‌شود')
                if name.endswith(('.xml', '.rels')):
                    raw = archive.read(member)
                    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or re.search(rb'''TargetMode\s*=\s*['"]External['"]''', raw, re.I):
                        raise ValueError('پیوند خارجی یا ساختار XML نامعتبر است')
        workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=False)
        for sheet in workbook:
            if sheet.max_row > MAX_ROWS + DATA_START + 50 or sheet.max_column > 128:
                raise ValueError('حداکثر ۵۰۰۰ ردیف و ستون‌های اصلی الگو مجاز است')
            for row in sheet.iter_rows():
                if any(cell.data_type == 'f' for cell in row):
                    raise ValueError('فرمول در فایل ورود اطلاعات مجاز نیست؛ مقدار نهایی را وارد کنید')
        return workbook
    except ValueError:
        raise
    except Exception:
        raise ValueError('فایل XLSX سالم و مطابق الگوی سامانه انتخاب کنید') from None


def _cell_value(value, column):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        value = value.strip()
        if len(value) > 32767:
            raise ValueError('متن سلول بیش از حد مجاز است')
        if value == CLEAR:
            return ''
    if column.get('choices'):
        matches = [(choice['value']) for choice in column['choices']
                   if str(value) in (str(choice['value']), str(choice['label']))]
        if len(set(map(str, matches))) != 1:
            raise ValueError('گزینه «' + column['label'] + '» در فهرست الگو نیست')
        return matches[0]
    if column.get('type') == 'integer':
        return integer_value(value, label=column['label'], minimum=0, maximum=SQLITE_MAX_INTEGER)
    if column.get('type') == 'date':
        if isinstance(value, datetime):
            value = value.date().isoformat()
        elif isinstance(value, date):
            value = value.isoformat()
        return date_value(value)
    if column['key'].endswith('_rials') and isinstance(value, (int, float)) and abs(value) > 999_999_999_999_999:
        raise ValueError('مبلغ بیش از ۱۵ رقم باید به صورت متن وارد شود تا Excel آن را گرد نکند')
    if column.get('type') in ('text', 'textarea', 'email', 'tel') and not isinstance(value, str):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    return value


def register(app, db, authenticated, audit, error):
    def catalog():
        return _schema_for_actor(app.extensions.get('hrm_catalog', {}), access.ctx(db()))

    def dispatch(resource, method='GET', body=None, record_id=None, query=None, *, options=False, legacy=False):
        """Call the actual decorated route using the same app context/connection."""
        if legacy:
            if resource != 'forms' or method != 'GET':
                raise ValueError('مسیر مرجع اکسل معتبر نیست')
            path = '/api/excel/forms'
        else:
            if not re.fullmatch(r'[a-z_]+', resource):
                raise ValueError('بخش اکسل معتبر نیست')
            if options:
                if resource not in app.extensions['hrm_catalog']:
                    raise ValueError('مرجع فرم معتبر نیست')
                path = '/api/hrm/options/' + resource
            else:
                if resource not in app.extensions['hrm_catalog'] and resource not in EXTRA_REFERENCES:
                    raise ValueError('بخش فرم معتبر نیست')
                path = '/api/hrm/' + resource + ('/' + str(positive_id(record_id)) if record_id else '')
        environ = dict(request.environ)
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else b''
        environ.update(REQUEST_METHOD=method, PATH_INFO=path, QUERY_STRING=urlencode(query or {}),
                       CONTENT_TYPE='application/json', CONTENT_LENGTH=str(len(payload)),
                       **{'wsgi.input': io.BytesIO(payload)})
        environ.pop('werkzeug.request', None)
        saved = {key: value for key, value in vars(g).items() if key != 'db'}
        try:
            with app.request_context(environ):
                endpoint, arguments = app.url_map.bind_to_environ(environ).match(path, method=method)
                response = app.make_response(app.view_functions[endpoint](**arguments))
                value = response.get_json(silent=True)
                if response.status_code >= 400:
                    raise DomainFailure((value or {}).get('error', 'اعتبارسنجی فرم ناموفق بود'), response.status_code)
                return value
        except HrmConflict as exc:
            raise DomainFailure(str(exc), 409) from None
        except PermissionError:
            raise DomainFailure('دسترسی به این رکورد یا مرجع مجاز نیست', 403) from None
        except sqlite3.IntegrityError:
            raise DomainFailure('رکورد تکراری یا دارای ارتباط نامعتبر است', 409) from None
        except ValueError as exc:
            raise DomainFailure(str(exc), 400) from None
        except HTTPException as exc:
            raise DomainFailure('مسیر این بخش برای ورود اکسل در دسترس نیست', exc.code) from None
        finally:
            for key in list(vars(g)):
                if key != 'db':
                    delattr(g, key)
            for key, value in saved.items():
                setattr(g, key, value)

    def selection(schema, resource, operation):
        actor = access.require(db(), schema['module'], operation=operation)
        if schema.get('admin_only_write') and operation in ('create', 'edit') and not actor['is_primary_admin']:
            raise PermissionError()
        cid = positive_id(request.args.get('company_id'))
        if not db().execute('SELECT 1 FROM companies WHERE id=?', (cid,)).fetchone():
            raise ValueError('شرکت را انتخاب کنید')
        if cid not in actor['company_ids']:
            raise PermissionError()
        site = request.args.get('site_id')
        site = positive_id(site) if site not in (None, '') else None
        if site:
            row = db().execute('SELECT company_id FROM hrm_sites WHERE id=?', (site,)).fetchone()
            if row is None or row['company_id'] != cid:
                raise ValueError('سایت متعلق به شرکت انتخاب‌شده نیست')
            if not any(scope['company_id'] == cid and scope.get('site_id') in (None, site) for scope in actor['scopes']):
                raise PermissionError()
        return cid, site if _site_applicable(resource, schema) else None

    def form_definition(resource, schema):
        is_binary = resource == 'documents' or any(field.get('type') == 'file' for field in schema.get('fields', []))
        readonly = bool(schema.get('read_only'))
        writable = not readonly and not is_binary
        create, edit = bool(schema.get('can_create')), bool(schema.get('can_edit'))
        return dict(resource=resource, kind=resource, title=schema['title'], module=schema['module'],
                    columns=_columns(schema), importable=writable, can_create=create and writable,
                    can_edit=edit and writable, can_download=bool(create or edit or schema.get('can_export')) and not readonly,
                    can_export=bool(schema.get('can_export')), read_only=readonly, binary_attachment=is_binary,
                    can_download_existing=bool(writable and edit and schema.get('can_read') and schema.get('can_export')),
                    help=('مشخصات مدرک در این فرم قابل توزیع است؛ بارگذاری فایل واقعی فقط از فرم امن مدارک انجام می‌شود.' if is_binary else
                          'این بخش نتیجه یا تاریخچه است؛ از خروجی گزارش استفاده کنید.' if readonly else
                          'ثبت جدید: شناسه و نسخه خالی. اصلاح: شناسه و نسخه جاری الزامی؛ خانه خالی حفظ می‌شود و «' + CLEAR + '» مقدار اختیاری را پاک می‌کند.'))

    def references(resource, schema, operation, cid, site):
        query = {'company_id': cid}
        if site:
            query['site_id'] = site
        result = {}
        for field in schema.get('fields', []):
            key = field['key']
            try:
                if key in ('employee_id', 'manager_id') or field.get('type') == 'employee':
                    rows = dispatch('people', query={**query, 'module': schema['module'], 'operation': operation})
                    result[key] = [(row['id'], row.get('code', ''), row.get('name', '')) for row in rows]
                    if key == 'employee_id':
                        result['employee_code'] = [(row.get('code', ''), row['id'], row.get('name', '')) for row in rows]
                elif field.get('type') == 'company':
                    actor = access.ctx(db())
                    result[key] = [(row['id'], '', row['name']) for row in actor['companies'] if row['id'] == cid]
                elif field.get('resource') in EXTRA_REFERENCES:
                    rows = dispatch(field['resource'], query={**query, 'operation': operation})
                    result[key] = [(row.get('code'), '', row.get('title', '')) for row in rows]
                elif field.get('resource') and operation != 'export':
                    rows = dispatch(field['resource'], query={**query, 'module': schema['module'], 'operation': operation, **field.get('filters', {})}, options=True)
                    result[key] = [(row['id'], '', row['label']) for row in rows]
            except DomainFailure:
                # A blank template must not turn a creation grant into access
                # to private document titles or another module's references.
                result[key] = []
        return result

    @app.get('/api/hrm/excel/forms', endpoint='hrm_excel_forms')
    @authenticated
    def forms():
        cid = positive_id(request.args.get('company_id'))
        actor = access.ctx(db())
        if cid not in actor['company_ids']:
            raise PermissionError()
        choices = [form_definition(resource, schema) for resource, schema in catalog().items()]
        legacy = []
        try:
            legacy = [dict(form) for form in dispatch('forms', query={'company_id': cid}, legacy=True) if form['kind'] not in CANONICAL_REPLACEMENTS and (not form.get('admin_only') or actor['is_primary_admin'])]
            for form in legacy:
                if form['kind'] == 'job_profiles':
                    form['description'] = 'قالب کامل ۱۸بخشی شناسنامه مطلوب شغل؛ بخشی متمایز از پرونده مرکزی پرسنل است. ' + form.get('description', '')
                elif form['kind'] == 'personal_documents':
                    form['description'] = 'قالب کامل ۱۸بخشی شناسنامه واقعی فرد در شرکت مالک سوابق آموزش؛ انتقال محل خدمت، مالکیت این سند قدیمی را تغییر نمی‌دهد. ' + form.get('description', '')
        except DomainFailure:
            pass
        return jsonify(version=VERSION, company_id=cid, forms=choices, legacy_forms=legacy, canonical_replacements=CANONICAL_REPLACEMENTS)

    @app.get('/api/hrm/excel/templates/<resource>', endpoint='hrm_excel_template')
    @authenticated
    def template(resource):
        schemas = catalog()
        if resource not in schemas:
            raise PermissionError()
        schema = schemas[resource]
        form = form_definition(resource, schema)
        if not form['can_download']:
            raise PermissionError()
        operation = 'create' if schema.get('can_create') else 'edit' if schema.get('can_edit') else 'export'
        cid, site = selection(schema, resource, operation)
        company = dict(db().execute('SELECT id,name FROM companies WHERE id=?', (cid,)).fetchone())
        brand = db().execute("SELECT content FROM branding WHERE key='holding'").fetchone()
        base = dict(kind='hrm_' + resource, title=form['title'], columns=form['columns'],
                    keys=['id'], description=form['help'])
        approved_logo = Path(app.static_folder) / 'logos' / 'nikanholding-logo.png'
        logo = brand['content'] if brand else approved_logo.read_bytes() if approved_logo.is_file() else None
        payload = build_template(base, company, {}, logo)
        workbook = openpyxl.load_workbook(io.BytesIO(payload))
        meta = workbook['_nikan']
        for key, value in [('hrm_version', VERSION), ('resource', resource), ('site_id', site or 0),
                           ('signature', _signature(app, resource, cid, site, form['columns']))]:
            meta.append([key, value])
        sheet = workbook[DATA_SHEET]
        if request.args.get('include_existing') == '1':
            if not form['can_download_existing']:
                raise PermissionError()
            existing = dispatch(resource, query={'company_id': cid, **({'site_id': site} if site else {})})
            existing = [row for row in existing if row.get('company_id', cid) == cid and (site is None or row.get('site_id') == site)]
            if len(existing) > MAX_ROWS:
                raise ValueError('برای دریافت اطلاعات اصلاح، محدوده را به یک سایت محدود کنید')
            for row_number, record in enumerate(existing, DATA_START):
                for index, column in enumerate(form['columns'], 1):
                    key = column['key']; value = record.get(key)
                    if key == 'employee_code':
                        # The canonical id in an update remains valid after a
                        # transfer; do not resolve it through today's roster.
                        value = None
                    if column.get('choices') and value is not None:
                        value = next((choice['label'] for choice in column['choices'] if str(choice['value']) == str(value)), value)
                    if column.get('type') == 'date' and value:
                        value = jalali_display(value)
                    if value is not None and (key in ('id', 'version', 'code', 'employee_code', 'national_id') or key.endswith('_id') or key.endswith('_rials')):
                        value = str(value)
                        sheet.cell(row_number, index).number_format = '@'
                    _literal(sheet.cell(row_number, index), value)
        refs = references(resource, schema, operation, cid, site)
        for index, column in enumerate(form['columns'], 1):
            if column['key'] in ('employee_code', 'code', 'national_id', 'phone') or column['key'].endswith('_rials'):
                for row in range(DATA_START, DATA_START + 100):
                    sheet.cell(row, index).number_format = '@'
            ref = refs.get(column['key'])
            if ref is not None:
                ref_sheet = workbook.create_sheet('مرجع ' + str(index))
                ref_sheet.sheet_view.rightToLeft = True
                ref_sheet.append(['شناسه / کد قابل ورود', 'شناسه یا کد مکمل', 'عنوان در محدوده مجاز'])
                for row in ref:
                    ref_sheet.append(row)
                    for cell in ref_sheet[ref_sheet.max_row]:
                        if isinstance(cell.value, str):
                            _literal(cell, cell.value)
                for col, width in [('A', 24), ('B', 24), ('C', 55)]:
                    ref_sheet.column_dimensions[col].width = width
                if ref:
                    range_name = 'NIKAN_HRM_reference_' + str(index)
                    workbook.defined_names.add(DefinedName(range_name, attr_text="'" + ref_sheet.title + "'!$A$2:$A$" + str(len(ref) + 1)))
                    rule = DataValidation(type='list', formula1='=' + range_name, allow_blank=True)
                    rule.showErrorMessage = True
                    rule.error = 'شناسه یا کد را از مرجع مجاز همین فرم انتخاب کنید.'
                    sheet.add_data_validation(rule)
                    rule.add(f'{openpyxl.utils.get_column_letter(index)}{DATA_START}:{openpyxl.utils.get_column_letter(index)}{MAX_ROWS + DATA_START - 1}')
        example = workbook.create_sheet('نمونه تکمیل؛ وارد نمی‌شود')
        example.sheet_view.rightToLeft = True
        example.append(['این برگه فقط نمونه است؛ اطلاعات آن وارد نرم‌افزار نمی‌شود.'])
        example.append([column['label'] for column in form['columns']])
        example.append([('نمونه متنی' if column.get('type') in ('text', 'textarea') else 'از راهنما و مرجع فرم') for column in form['columns']])
        for tab in workbook:
            tab.sheet_view.rightToLeft = True
            for row in tab:
                for cell in row:
                    cell.alignment = Alignment(horizontal='right', vertical='top', wrap_text=True)
                    if cell.data_type != 'f' and isinstance(cell.value, str):
                        _literal(cell, cell.value)
        output = io.BytesIO(); workbook.save(output)
        return Response(output.getvalue(), mimetype=MIME, headers={'Content-Disposition': f'attachment; filename=Nikan-HRM-{resource}-company-{cid}.xlsx'})

    @app.post('/api/hrm/excel/import/<resource>', endpoint='hrm_excel_import')
    @authenticated
    def import_rows(resource):
        schemas = catalog()
        if resource not in schemas:
            raise PermissionError()
        schema = schemas[resource]
        form = form_definition(resource, schema)
        if not form['importable']:
            raise ValueError(form['help'])
        mode, policy = request.form.get('mode', 'preview'), request.form.get('policy', 'insert')
        if mode not in ('preview', 'commit') or policy not in ('insert', 'update'):
            raise ValueError('حالت بررسی یا ثبت جدید/اصلاح معتبر نیست')
        operation = 'create' if policy == 'insert' else 'edit'
        cid, site = selection(schema, resource, operation)
        upload = request.files.get('file')
        if upload is None or not upload.filename.lower().endswith('.xlsx'):
            raise ValueError('فایل استاندارد XLSX را انتخاب کنید')
        workbook = _read_workbook(upload.read(MAX_BYTES + 1))
        try:
            meta = _metadata(workbook)
            expected = _signature(app, resource, cid, site, form['columns'])
            if meta.get('hrm_version') != VERSION or meta.get('resource') != resource or str(meta.get('company_id')) != str(cid) or str(meta.get('site_id')) != str(site or 0) or not hmac.compare_digest(str(meta.get('signature', '')), expected):
                raise ValueError('نوع، شرکت، سایت یا نسخه این قالب با بخش انتخاب‌شده یکسان نیست؛ قالب جدید همان محدوده را بگیرید')
            if DATA_SHEET not in workbook:
                raise ValueError('برگه ورود اطلاعات یافت نشد')
            sheet = workbook[DATA_SHEET]
            keys = [column['key'] for column in form['columns']]
            if sheet.max_column != len(keys) or [sheet.cell(5, index).value for index in range(1, len(keys) + 1)] != keys:
                raise ValueError('ستون‌های فنی الگو تغییر کرده‌اند')
            rows, seen = [], set()
            for row_number, cells in enumerate(sheet.iter_rows(min_row=DATA_START, max_col=len(keys)), DATA_START):
                if not any(cell.value is not None and (not isinstance(cell.value, str) or cell.value.strip()) for cell in cells):
                    continue
                values = {column['key']: _cell_value(cell.value, column) for column, cell in zip(form['columns'], cells)}
                values = {key: value for key, value in values.items() if value is not None}
                key = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
                if key in seen:
                    raise ValueError('ردیف تکراری در همین فایل وجود دارد')
                seen.add(key)
                if policy == 'insert' and any(key in values for key in ('id', 'version')):
                    raise ValueError('در ثبت جدید شناسه و نسخه را خالی بگذارید؛ برای اصلاح حالت اصلاح را انتخاب کنید')
                if policy == 'update':
                    values['id'] = positive_id(values.get('id'))
                    if type(values.get('version')) is not int or values['version'] < 0:
                        raise ValueError('برای اصلاح، نسخه عددی جاری هر رکورد را وارد کنید')
                if values.get('company_id', cid) != cid:
                    raise ValueError('شرکت داخل ردیف با شرکت صادرکننده فرم یکسان نیست')
                rows.append((row_number, values))
            if not rows:
                raise ValueError('فایل دارای ردیف اطلاعات نیست؛ برگه نمونه وارد نمی‌شود')
            if len(rows) > MAX_ROWS:
                raise ValueError('حداکثر ۵۰۰۰ ردیف اطلاعات مجاز است')
        finally:
            workbook.close()
        fingerprint = hashlib.sha256(json.dumps([VERSION, resource, cid, site, policy, sorted(value for value in seen)], ensure_ascii=False).encode()).hexdigest()
        connection = db()
        if connection.in_transaction:
            raise HrmConflict('عملیات دیگری روی پایگاه در جریان است؛ دوباره تلاش کنید')
        action = 'hrm_excel_import_' + resource
        connection.execute('BEGIN IMMEDIATE')
        result = dict(ok=True, can_commit=False, applied=False, mode=mode, policy=policy, resource=resource,
                      company_id=cid, site_id=site, total=len(rows), created=0, updated=0,
                      planned_created=0, planned_updated=0, errors=[], records=[])
        try:
            for previous in connection.execute('SELECT detail FROM audit WHERE action=? AND company_id=?', (action, cid)):
                try:
                    if json.loads(previous['detail']).get('fingerprint') == fingerprint:
                        raise HrmConflict('همین اطلاعات قبلاً با موفقیت وارد شده است؛ ثبت تکراری انجام نمی‌شود')
                except json.JSONDecodeError:
                    continue
            # No domain helper may commit or roll back the surrounding batch.
            def guard(code, first, second, database, source):
                if code == sqlite3.SQLITE_TRANSACTION and str(first).upper() in ('COMMIT', 'ROLLBACK'):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            connection.set_authorizer(guard)
            for row_number, original in rows:
                connection.execute('SAVEPOINT hrm_excel_row')
                try:
                    values = dict(original)
                    record_id = values.pop('id', None)
                    if 'company_id' in {field['key'] for field in schema.get('fields', [])}:
                        values.setdefault('company_id', cid)
                    if site and 'site_id' in {field['key'] for field in schema.get('fields', [])}:
                        if values.get('site_id', site) != site:
                            raise DomainFailure('سایت داخل ردیف با سایت صادرکننده فرم یکسان نیست')
                        values.setdefault('site_id', site)
                    code = values.pop('employee_code', None)
                    if code is not None:
                        people = dispatch('people', query={'company_id': cid, 'module': schema['module'], 'operation': operation, **({'site_id': site} if site else {})})
                        matches = [person for person in people if str(person['code']) == str(code)]
                        if len(matches) != 1 or (values.get('employee_id') is not None and values['employee_id'] != matches[0]['id']):
                            raise DomainFailure('کد پرسنلی با شناسه فرد در محدوده مجاز این فرم تطبیق ندارد')
                        values['employee_id'] = matches[0]['id']
                    output = dispatch(resource, 'POST' if policy == 'insert' else 'PUT', values, record_id,
                                      {'company_id': cid, **({'site_id': site} if site else {})})
                    if isinstance(output, dict) and output.get('company_id', cid) != cid:
                        raise DomainFailure('مالک رکورد با شرکت صادرکننده فرم یکسان نیست')
                    if site and isinstance(output, dict) and output.get('site_id') != site:
                        raise DomainFailure('انتساب رکورد با سایت صادرکننده فرم یکسان نیست')
                    if not isinstance(output, dict):
                        raise DomainFailure('پاسخ بخش برای ورود یکپارچه معتبر نیست')
                    connection.execute('RELEASE hrm_excel_row')
                    result['planned_created' if policy == 'insert' else 'planned_updated'] += 1
                    result['records'].append(dict(row=row_number, id=output.get('id'), version=output.get('version')))
                except (DomainFailure, sqlite3.DatabaseError) as exc:
                    connection.execute('ROLLBACK TO hrm_excel_row')
                    connection.execute('RELEASE hrm_excel_row')
                    result['errors'].append(dict(row=row_number, message=exc.message if isinstance(exc, DomainFailure) else 'این عملیات در تراکنش یکپارچه پذیرفته نشد؛ هیچ ردیفی ثبت نمی‌شود', status=exc.status if isinstance(exc, DomainFailure) else 400))
            connection.set_authorizer(None)
            result['ok'] = result['can_commit'] = not result['errors']
            result['applied'] = mode == 'commit' and result['ok']
            if result['applied']:
                result['created'], result['updated'] = result['planned_created'], result['planned_updated']
                audit(action, cid, json.dumps(dict(fingerprint=fingerprint, policy=policy, total=len(rows), created=result['created'], updated=result['updated'])))
                connection.commit()
                result['message'] = 'تمام ردیف‌های معتبر فایل یکجا ثبت شدند.'
            else:
                connection.rollback()
                result['records'] = []
                result['message'] = 'بررسی انجام شد؛ هیچ اطلاعاتی ثبت نشد.'
        except BaseException:
            connection.set_authorizer(None)
            connection.rollback()
            raise
        return jsonify(result), 400 if mode == 'commit' and not result['ok'] else 200
