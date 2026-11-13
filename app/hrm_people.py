"""Personnel records, employment contracts and confidential personnel documents.

The existing employee row remains the canonical training identity. HRM current
assignments and all subsequent records are additive, company-owned records.
"""
import io
import json
import re
import sqlite3
import zipfile
from datetime import date

from flask import Response, g, jsonify, request

from . import hrm_access as access
from .hrm_access import company_scope, ctx, employee, require, scope_sql
from .hrm_common import (HrmConflict, begin_write, check_version, date_value,
                         integer_value, text_value, today_iso)
from .validation import json_object, positive_id


SCHEMA = '''
CREATE TABLE IF NOT EXISTS hrm_sites(
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'other', address TEXT NOT NULL DEFAULT '',
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(company_id,name));
CREATE TABLE IF NOT EXISTS hrm_units(
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),
 name TEXT NOT NULL, location TEXT NOT NULL DEFAULT '',
 manager_id INTEGER REFERENCES employees(id), notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS hrm_employee_details(
 employee_id INTEGER PRIMARY KEY REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '', job TEXT NOT NULL DEFAULT '',
 unit_id INTEGER REFERENCES hrm_units(id), location TEXT NOT NULL DEFAULT '',
 manager_id INTEGER REFERENCES employees(id), status TEXT NOT NULL DEFAULT 'active',
 national_id TEXT NOT NULL DEFAULT '', birth_date TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
 start_date TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '',
 marital_status TEXT NOT NULL DEFAULT '', emergency_contact TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1,
 updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_people_current_company ON hrm_employee_details(company_id,department);
CREATE TABLE IF NOT EXISTS hrm_education(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '', degree TEXT NOT NULL,
 field TEXT NOT NULL DEFAULT '', institution TEXT NOT NULL DEFAULT '',
 start_date TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS hrm_prior_employment(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '', employer TEXT NOT NULL,
 job TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '',
 reason TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS hrm_employment_history(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id), previous_site_id INTEGER REFERENCES hrm_sites(id),
 previous_company_id INTEGER REFERENCES companies(id),
 previous_department TEXT NOT NULL DEFAULT '', previous_job TEXT NOT NULL DEFAULT '',
 department TEXT NOT NULL DEFAULT '', job TEXT NOT NULL DEFAULT '',
 location TEXT NOT NULL DEFAULT '', manager_id INTEGER REFERENCES employees(id),
 unit_id INTEGER REFERENCES hrm_units(id), status TEXT NOT NULL DEFAULT 'active',
 effective_date TEXT NOT NULL, start_date TEXT NOT NULL DEFAULT '', end_date TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT 'manual',
 notes TEXT NOT NULL DEFAULT '', actor_id INTEGER REFERENCES users(id),
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS hrm_employment_history_person ON hrm_employment_history(employee_id,effective_date);
CREATE TABLE IF NOT EXISTS hrm_contracts(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '', number TEXT NOT NULL,
 contract_type TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'active', notes TEXT NOT NULL DEFAULT '',
 parent_contract_id INTEGER REFERENCES hrm_contracts(id),
 version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(employee_id,company_id,number,start_date));
CREATE UNIQUE INDEX IF NOT EXISTS hrm_contract_renewal_child ON hrm_contracts(parent_contract_id)
 WHERE parent_contract_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS hrm_contract_expiry ON hrm_contracts(company_id,end_date,status);
CREATE TABLE IF NOT EXISTS hrm_documents(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '', permission_group TEXT NOT NULL DEFAULT 'documents',
 contract_id INTEGER REFERENCES hrm_contracts(id), title TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'employment', filename TEXT NOT NULL, mime TEXT NOT NULL,
 content BLOB NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 supersedes_id INTEGER REFERENCES hrm_documents(id),
 version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_document_person ON hrm_documents(employee_id,company_id,permission_group);
CREATE UNIQUE INDEX IF NOT EXISTS hrm_document_revision_child ON hrm_documents(supersedes_id)
 WHERE supersedes_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS hrm_personnel_actions(
 id INTEGER PRIMARY KEY, employee_id INTEGER NOT NULL REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), action_type TEXT NOT NULL,
 site_id INTEGER REFERENCES hrm_sites(id), record_department TEXT NOT NULL DEFAULT '',
 effective_date TEXT NOT NULL, target_company_id INTEGER REFERENCES companies(id),
 target_site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '', job TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
 manager_id INTEGER REFERENCES employees(id), unit_id INTEGER REFERENCES hrm_units(id),
 employee_version INTEGER NOT NULL DEFAULT 0,
 notes TEXT NOT NULL DEFAULT '', document_id INTEGER REFERENCES hrm_documents(id),
 applied INTEGER NOT NULL DEFAULT 0 CHECK(applied IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS hrm_temporary_assignments(
 id INTEGER PRIMARY KEY,employee_id INTEGER NOT NULL REFERENCES employees(id),
 company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '',target_company_id INTEGER NOT NULL REFERENCES companies(id),
 target_site_id INTEGER NOT NULL REFERENCES hrm_sites(id),start_date TEXT NOT NULL,end_date TEXT NOT NULL,
 activity_location TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active',
 notes TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
'''

RECOVERY_ADDITIONS = {name: 0 for name in (
    'hrm_units', 'hrm_employee_details', 'hrm_education', 'hrm_prior_employment',
    'hrm_employment_history', 'hrm_contracts', 'hrm_documents', 'hrm_personnel_actions',
    'hrm_sites','hrm_temporary_assignments')}
DOCUMENT_GROUPS = ('documents', 'contracts', 'discipline', 'loans', 'performance', 'health', 'hse', 'health_environment')
PEOPLE_STATUS = [('active', 'شاغل'), ('probation', 'دوره آزمایشی'), ('leave', 'مرخصی بلندمدت'),
                 ('terminated', 'قطع همکاری'), ('retired', 'بازنشسته'), ('settled', 'تسویه‌شده')]
CONTRACT_STATUS = [('draft', 'پیش‌نویس'), ('active', 'جاری'), ('ended', 'پایان‌یافته'),
                   ('terminated', 'فسخ‌شده'), ('renewed', 'تمدیدشده')]
ACTION_TYPES = [('appointment', 'انتصاب'), ('promotion', 'تغییر سمت'), ('transfer', 'انتقال'),
                ('termination', 'قطع همکاری'), ('settlement', 'تسویه‌حساب'), ('other', 'سایر احکام')]


def field(key, label, type='text', required=False, **kwargs):
    return dict(key=key, label=label, type=type, required=required, **kwargs)


PERSON = field('employee_id', 'فرد', 'employee', True)
START = field('start_date', 'تاریخ شروع', 'date')
END = field('end_date', 'تاریخ پایان', 'date')
NOTES = field('notes', 'توضیحات', 'textarea')
CATALOG = {
 'sites': dict(title='سایت‌ها و محل‌های خدمت', module='personnel', fields=[
    field('name', 'نام سایت', required=True), field('kind', 'نوع سایت'), field('address', 'نشانی سایت', 'textarea'),
    field('active', 'وضعیت سایت', 'select', choices=[[1,'فعال'],[0,'غیرفعال']])],
    columns=['name','company_id','kind','address','active']),
 'people': dict(title='پرونده پرسنلی', module='personnel', fields=[
    field('code', 'کد پرسنلی', required=True), field('name', 'نام و نام خانوادگی', required=True),
    field('company_id', 'شرکت محل خدمت فعلی', 'company', True),
    field('site_id', 'سایت محل خدمت', 'resource', resource='sites'),
    field('unit_id', 'واحد تعریف‌شده', 'resource', resource='units'),
    field('department', 'واحد سازمانی'), field('job', 'سمت فعلی'), field('location', 'محل خدمت'),
    field('manager_id', 'مدیر مستقیم', 'employee'), field('status', 'وضعیت اشتغال', 'select', choices=PEOPLE_STATUS),
    field('national_id', 'شناسه ملی / هویتی'), field('birth_date', 'تاریخ تولد', 'date'),
    field('phone', 'شماره تماس'), field('email', 'ایمیل'), field('address', 'نشانی', 'textarea'),
    START, END, field('marital_status', 'وضعیت تأهل'), field('emergency_contact', 'تماس اضطراری'),
    field('effective_date', 'تاریخ اثر تغییر سازمانی', 'date'), NOTES],
    columns=['code', 'name', 'national_id', 'company_id', 'site_id', 'department', 'job', 'status', 'start_date'],
    help='شناسه فرد در آموزش حفظ می‌شود. انتقال فعلی HRM با دسترسی به هر دو شرکت و ثبت سابقه انجام می‌شود.'),
 'units': dict(title='واحدها و محل خدمت', module='personnel', fields=[
    field('name', 'نام واحد', required=True), field('site_id', 'سایت واحد', 'resource', True, resource='sites'), field('location', 'محل فعالیت'),
    field('manager_id', 'مدیر واحد', 'employee'), NOTES], columns=['name', 'site_id', 'location', 'manager_id']),
 'education': dict(title='سوابق تحصیلی', module='personnel', fields=[
    PERSON, field('degree', 'مقطع / مدرک', required=True), field('field', 'رشته'),
    field('institution', 'مرکز آموزشی'), START, END, NOTES],
    columns=['employee_id', 'degree', 'field', 'institution', 'end_date']),
 'prior_employment': dict(title='سوابق استخدام قبلی', module='personnel', fields=[
    PERSON, field('employer', 'کارفرمای قبلی', required=True), field('job', 'سمت'),
    START, END, field('reason', 'علت پایان همکاری'), NOTES],
    columns=['employee_id', 'employer', 'job', 'start_date', 'end_date']),
 'employment_history': dict(title='تاریخچه خدمت و جابه‌جایی', module='personnel', fields=[
    PERSON, field('effective_date', 'تاریخ اثر', 'date', True), field('department', 'واحد'),
    field('job', 'سمت'), field('location', 'محل فعالیت'), START, END, NOTES],
    columns=['employee_id', 'start_date', 'end_date', 'company_id', 'site_id', 'department', 'job', 'source'],
    help='تغییرات جاری خودکار ثبت می‌شوند؛ ثبت دستی صرفاً سابقه تاریخی است و وضعیت فعلی فرد را تغییر نمی‌دهد.'),
 'contracts': dict(title='قراردادها و تمدیدها', module='contracts', fields=[
    PERSON, field('number', 'شماره قرارداد', required=True), field('contract_type', 'نوع قرارداد', required=True),
    field('start_date', 'تاریخ شروع', 'date', True), END,
    field('status', 'وضعیت', 'select', choices=CONTRACT_STATUS), NOTES],
    columns=['employee_id', 'number', 'contract_type', 'start_date', 'end_date', 'status', 'renewal_count'],
    help='تمدید، قرارداد جدید را به نسخه قبل متصل می‌کند. تعداد مجاز تمدید یا ضابطه قانونی به صورت ثابت اعمال نشده است.'),
 'personnel_actions': dict(title='احکام و امور کارگزینی', module='personnel', fields=[
    PERSON, field('action_type', 'نوع حکم', 'select', True, choices=ACTION_TYPES),
    field('effective_date', 'تاریخ اثر', 'date', True),
    field('target_company_id', 'شرکت مقصد', 'company'),
    field('target_site_id', 'سایت مقصد', 'resource', resource='sites'), field('department', 'واحد جدید'),
    field('job', 'سمت جدید'), field('location', 'محل خدمت جدید'),
    field('unit_id', 'واحد تعریف‌شده مقصد', 'resource', resource='units'),
    field('manager_id', 'مدیر مستقیم جدید', 'employee'),
    field('document_id', 'مدرک حکم', 'resource', resource='documents'), NOTES,
    field('apply_now', 'اجرای حکم در پرونده فعلی', 'select', choices=[[0, 'فقط ثبت سابقه'], [1, 'ثبت و اجرای فوری']])],
    columns=['employee_id', 'action_type', 'effective_date', 'target_company_id', 'target_site_id', 'applied'],
    help='اجرای حکم صریح است. حکم دارای تاریخ آینده تا رسیدن تاریخ اجرا نمی‌شود؛ تغییرات جاری و تاریخچه در یک تراکنش ثبت می‌شوند.'),
 'documents': dict(title='مدارک محرمانه و نسخه‌های اسناد', module='documents', upload=True, fields=[
    PERSON, field('title', 'عنوان سند', required=True), field('kind', 'نوع مدرک'),
    field('permission_group', 'محدوده محرمانگی', 'select', choices=[
       ['documents', 'مدارک پرسنلی'], ['contracts', 'قرارداد'], ['discipline', 'انضباطی'],
       ['loans', 'وام و تسهیلات'], ['performance', 'عملکرد'], ['health', 'پزشکی محرمانه'], ['hse', 'ایمنی'],
       ['health_environment','بهداشت و محیط زیست']]),
    field('contract_id', 'قرارداد مرتبط', 'resource', resource='contracts'),
    field('supersedes_id', 'نسخه قبلی سند', 'resource', resource='documents')],
    columns=['employee_id', 'title', 'kind', 'permission_group', 'revision', 'created'],
    help='PDF، PNG، JPEG یا DOCX معتبر تا ۸ مگابایت. نسخه فایل بازنویسی نمی‌شود؛ برای نسخه جدید، سند تازه با ارجاع به قبلی بارگذاری کنید.'),
 'temporary_assignments': dict(title='فعالیت موقت در سایت دیگر',module='personnel',fields=[
    PERSON,field('target_site_id','سایت فعالیت موقت','resource',True,resource='sites'),
    field('start_date','تاریخ شروع','date',True),field('end_date','تاریخ پایان','date',True),
    field('activity_location','محل فعالیت واقعی'),
    field('status','وضعیت','select',choices=[['planned','برنامه‌ریزی‌شده'],['active','جاری'],['completed','پایان‌یافته'],['cancelled','لغوشده']]),NOTES],
    columns=['employee_id','target_company_id','target_site_id','start_date','end_date','activity_location','status'],
    help='فعالیت موقت شرکت استخدام‌کننده یا سایت انتساب اصلی را تغییر نمی‌دهد. ثبت در شرکت دیگر نیازمند مجوز مبدأ و مقصد است.'),
}

DETAIL_FIELDS = ('company_id', 'site_id', 'department', 'job', 'unit_id', 'location', 'manager_id', 'status',
                 'national_id', 'birth_date', 'phone', 'email', 'address', 'start_date', 'end_date',
                 'marital_status', 'emergency_contact', 'notes')
DETAIL_DATES = ('birth_date', 'start_date', 'end_date')
ASSIGNMENT_FIELDS = ('company_id', 'site_id', 'department', 'job', 'unit_id', 'location', 'manager_id', 'status')
RESOURCE_FIELDS = {
 'sites': ('name','kind','address','active'),
 'units': ('name', 'site_id', 'location', 'manager_id', 'notes'),
 'education': ('degree', 'field', 'institution', 'start_date', 'end_date', 'notes'),
 'prior_employment': ('employer', 'job', 'start_date', 'end_date', 'reason', 'notes'),
 'employment_history': ('effective_date', 'department', 'job', 'location', 'start_date', 'end_date', 'notes'),
 'contracts': ('number', 'contract_type', 'start_date', 'end_date', 'status', 'notes'),
 'personnel_actions': ('action_type', 'effective_date', 'target_company_id', 'target_site_id', 'department', 'job', 'location',
                       'manager_id', 'unit_id', 'notes', 'document_id'),
 'temporary_assignments': ('target_site_id','start_date','end_date','activity_location','status','notes'),
}


def _optional_id(value):
    return None if value in (None, '') else positive_id(value)


def _flag(value):
    if type(value) is bool:
        return int(value)
    if value in (0, 1, '0', '1') and not isinstance(value, (list, dict)):
        return int(value)
    raise ValueError('انتخاب اجرای حکم معتبر نیست')


def _range(values):
    if values.get('start_date') and values.get('end_date') and values['end_date'] < values['start_date']:
        raise ValueError('تاریخ پایان نباید پیش از شروع باشد')


def _body_fields(data, allowed, *, extra=()):
    if set(data) - set(allowed) - set(extra):
        raise ValueError('فیلد ناشناخته یا غیرقابل تغییر ارسال شده است')


def _scope_company(conn, module, data=None, *, write=False, fallback=None, operation=None):
    supplied = request.args.get('company_id')
    if supplied not in (None, ''):
        cid = positive_id(supplied)
        if data and data.get('company_id') not in (None, '') and positive_id(data['company_id']) != cid:
            raise ValueError('شرکت فرم با شرکت انتخاب‌شده یکسان نیست')
    elif data and data.get('company_id') not in (None, ''):
        cid = positive_id(data['company_id'])
    elif fallback is not None:
        cid = positive_id(fallback)
    else:
        return None
    actor = require(conn, module, write=write, operation=operation)
    if not conn.execute('SELECT 1 FROM companies WHERE id=?', (cid,)).fetchone():
        raise ValueError('شرکت یافت نشد')
    # This is a list/filter context, not permission to a whole company. The
    # employee or record predicate below applies site and unit restrictions.
    if not actor.get('is_primary_admin') and cid not in actor['company_ids']:
        raise PermissionError()
    return cid


def _current(conn, eid):
    row = conn.execute('''SELECT e.id,e.company_id training_company_id,e.code,e.name,
       e.passport_number,e.department training_department,e.job training_job,d.*
       FROM employees e LEFT JOIN hrm_employee_details d ON d.employee_id=e.id WHERE e.id=?''', (eid,)).fetchone()
    if not row:
        raise ValueError('فرد یافت نشد')
    result = dict(row)
    result['id'] = eid
    result['employee_id'] = eid
    if result['company_id'] is None:
        result.update(company_id=result['training_company_id'], department=result['training_department'],
                      job=result['training_job'], version=0, status='active')
    for field_name in DETAIL_FIELDS:
        if result.get(field_name) is None and field_name not in ('site_id', 'unit_id', 'manager_id'):
            result[field_name] = ''
    result['identity_incomplete'] = not bool(result.get('national_id'))
    result['national_id_missing'] = result['identity_incomplete']
    return result


def _placement_refs(conn, values, eid, *, previous_site=None, operation=None):
    cid = values['company_id']
    if values.get('site_id'):
        site = conn.execute('SELECT * FROM hrm_sites WHERE id=? AND company_id=?', (values['site_id'], cid)).fetchone()
        if not site or (not site['active'] and values['site_id'] != previous_site):
            raise ValueError('سایت فعال متعلق به شرکت محل خدمت را انتخاب کنید')
    if values.get('unit_id'):
        unit = conn.execute('SELECT * FROM hrm_units WHERE id=? AND company_id=?', (values['unit_id'], cid)).fetchone()
        if not unit:
            raise ValueError('واحد متعلق به شرکت محل خدمت نیست')
        if unit['site_id'] != values.get('site_id'):
            raise ValueError('واحد سازمانی متعلق به سایت محل خدمت نیست')
        values['department'] = unit['name']
    if values.get('manager_id'):
        manager = employee(conn, values['manager_id'], 'personnel', operation=operation)
        if manager['company_id'] != cid or values['manager_id'] == eid:
            raise ValueError('مدیر مستقیم باید فرد دیگری از همان شرکت باشد')
        visited = {eid}
        cursor = values['manager_id']
        while cursor is not None:
            if cursor in visited:
                raise ValueError('حلقه در رابطه مدیر مستقیم مجاز نیست')
            visited.add(cursor)
            row = conn.execute('SELECT manager_id FROM hrm_employee_details WHERE employee_id=?', (cursor,)).fetchone()
            cursor = row['manager_id'] if row else None


def _person_values(conn, data, current=None, eid=None):
    if current:
        access.record_scope(conn, current, 'personnel', operation='edit')
    values = {key: (current.get(key) if current else None) for key in DETAIL_FIELDS}
    values.update({key: value for key, value in data.items() if key in DETAIL_FIELDS})
    values['company_id'] = positive_id(values['company_id'])
    for key in ('site_id', 'unit_id', 'manager_id'):
        values[key] = _optional_id(values.get(key))
    for key in DETAIL_DATES:
        values[key] = date_value(values.get(key))
    for key in set(DETAIL_FIELDS) - set(DETAIL_DATES) - {'company_id', 'site_id', 'unit_id', 'manager_id'}:
        values[key] = text_value(values.get(key), max_length=2000 if key in ('notes', 'address') else 250)
    values['status'] = values['status'] or 'active'
    if values['status'] not in dict(PEOPLE_STATUS):
        raise ValueError('وضعیت اشتغال معتبر نیست')
    if values['birth_date'] and values['birth_date'] > today_iso():
        raise ValueError('تاریخ تولد نمی‌تواند در آینده باشد')
    if values['email'] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
        raise ValueError('ساختار ایمیل معتبر نیست')
    values['national_id'] = values['national_id'].translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789')).upper()
    if values['national_id'] and conn.execute('SELECT 1 FROM hrm_employee_details WHERE national_id=? COLLATE NOCASE AND employee_id!=?',
                                             (values['national_id'], eid or 0)).fetchone():
        raise HrmConflict('این شناسه هویتی قبلاً در پرونده مرکزی فرد دیگری ثبت شده است')
    _range(values)
    if current and values['company_id'] != current['company_id']:
        access.record_scope(conn, current, 'personnel', operation='edit')
        if 'unit_id' not in data:
            values['unit_id'] = None
        if 'manager_id' not in data:
            values['manager_id'] = None
        if 'site_id' not in data:
            values['site_id'] = None
    if current and values.get('site_id') != current.get('site_id') and 'unit_id' not in data:
        values['unit_id'] = None
    _placement_refs(conn, values, eid, previous_site=current.get('site_id') if current else None,
                    operation='edit' if current else 'create')
    access.record_scope(conn, values, 'personnel', operation='edit' if current else 'create')
    return values


def _save_details(conn, eid, values):
    columns = ['employee_id', *DETAIL_FIELDS]
    conn.execute('INSERT INTO hrm_employee_details (' + ','.join(columns) + ') VALUES (' +
                 ','.join('?' for _ in columns) + ') ON CONFLICT(employee_id) DO UPDATE SET ' +
                 ','.join(key + '=excluded.' + key for key in DETAIL_FIELDS) +
                 ',version=hrm_employee_details.version+1,updated=CURRENT_TIMESTAMP',
                 [eid, *[values[key] for key in DETAIL_FIELDS]])


def _unique_current_code(conn, cid, code, eid=None):
    existing = conn.execute('''SELECT e.id FROM employees e LEFT JOIN hrm_employee_details d ON d.employee_id=e.id
        WHERE COALESCE(d.company_id,e.company_id)=? AND e.code=? AND e.id!=? LIMIT 1''',
        (cid, code, eid or 0)).fetchone()
    if existing:
        raise HrmConflict('کد پرسنلی در شرکت محل خدمت فعلی تکراری است')


def _history(conn, eid, before, after, effective_date, source, notes=''):
    previous = conn.execute("SELECT id,start_date FROM hrm_employment_history WHERE employee_id=? AND end_date='' AND source!='manual' ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
    if previous:
        if previous['start_date'] and effective_date < previous['start_date']:
            raise ValueError('تاریخ جابه‌جایی جاری نباید پیش از شروع آخرین انتساب باشد')
        conn.execute('UPDATE hrm_employment_history SET end_date=? WHERE id=?', (effective_date, previous['id']))
    columns = ['employee_id', 'company_id', 'site_id', 'previous_site_id', 'previous_company_id', 'previous_department', 'previous_job',
               'department', 'job', 'location', 'manager_id', 'unit_id', 'status', 'effective_date', 'source', 'notes', 'actor_id']
    values = [eid, after['company_id'], after.get('site_id'), before.get('site_id') if before else None, before.get('company_id') if before else None,
              before.get('department', '') if before else '', before.get('job', '') if before else '',
              *[after.get(key) for key in ('department', 'job', 'location', 'manager_id', 'unit_id', 'status')],
              effective_date, source, notes, g.user['id']]
    columns.append('start_date'); values.append(effective_date)
    conn.execute('INSERT INTO hrm_employment_history (' + ','.join(columns) + ') VALUES (' +
                 ','.join('?' for _ in values) + ')', values)


def _contract_extras(conn, values):
    values = dict(values)
    depth = 0
    parent = values.get('parent_contract_id')
    visited = {values['id']}
    while parent is not None:
        if parent in visited:
            raise ValueError('زنجیره تمدید قرارداد معتبر نیست')
        visited.add(parent)
        row = conn.execute('SELECT parent_contract_id FROM hrm_contracts WHERE id=?', (parent,)).fetchone()
        if row is None:
            raise ValueError('نسخه قبلی قرارداد یافت نشد')
        depth += 1
        parent = row[0]
    values['renewal_count'] = depth
    values['days_to_expiry'] = ((date.fromisoformat(values['end_date']) - date.fromisoformat(today_iso())).days
                                if values.get('end_date') else None)
    return values


def _document_metadata(row):
    return {key: row[key] for key in row.keys() if key != 'content'}


def validate_document_upload(upload):
    """Validate bounded local documents without executing or serving active content."""
    content = upload.read(8 * 1024 * 1024 + 1)
    if not content or len(content) > 8 * 1024 * 1024:
        raise ValueError('حجم فایل باید بین یک بایت و ۸ مگابایت باشد')
    if content.startswith(b'%PDF-'):
        return content, 'application/pdf', '.pdf'
    if content.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8\xff')):
        from PIL import Image, UnidentifiedImageError
        try:
            with Image.open(io.BytesIO(content)) as picture:
                if picture.format not in ('PNG', 'JPEG') or picture.width * picture.height > 25_000_000:
                    raise ValueError()
                image_format = picture.format
                picture.verify()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            raise ValueError('تصویر فایل معتبر نیست') from None
        return content, 'image/png' if image_format == 'PNG' else 'image/jpeg', '.png' if image_format == 'PNG' else '.jpg'
    if content.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                items = archive.infolist()
                names = [item.filename for item in items]
                if len(items) > 1000 or len(set(names)) != len(names) or sum(item.file_size for item in items) > 50 * 1024 * 1024:
                    raise ValueError()
                if '[Content_Types].xml' not in names or 'word/document.xml' not in names:
                    raise ValueError()
                for item in items:
                    name = item.filename.lower()
                    if name.startswith('/') or '..' in name.split('/') or item.flag_bits & 1:
                        raise ValueError()
                    if any(term in name for term in ('vbaproject', 'activex', 'embeddings/')):
                        raise ValueError()
                    if name.endswith(('.xml', '.rels')):
                        raw = archive.read(item)
                        if b'\x00' in raw or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or b'macroenabled' in raw.lower():
                            raise ValueError()
                        if name.endswith('.rels') and re.search(rb'''TargetMode\s*=\s*['"]External['"]''', raw, re.I):
                            raise ValueError()
                if archive.testzip() is not None:
                    raise ValueError()
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
            raise ValueError('ساختار Word معتبر نیست یا محتوای فعال و پیوند خارجی دارد') from None
        return content, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.docx'
    raise ValueError('فقط PDF، PNG، JPEG و Word بدون ماکرو پذیرفته می‌شود')


def register(app, db, authenticated, audit, error):
    with app.app_context():
        db().executescript(SCHEMA)
        additions = {
            'hrm_units': {'site_id': 'INTEGER REFERENCES hrm_sites(id)'},
            'hrm_employee_details': {'site_id': 'INTEGER REFERENCES hrm_sites(id)'},
            'hrm_education': {'site_id': 'INTEGER REFERENCES hrm_sites(id)', 'department': "TEXT NOT NULL DEFAULT ''"},
            'hrm_prior_employment': {'site_id': 'INTEGER REFERENCES hrm_sites(id)', 'department': "TEXT NOT NULL DEFAULT ''"},
            'hrm_employment_history': {'site_id': 'INTEGER REFERENCES hrm_sites(id)', 'previous_site_id': 'INTEGER REFERENCES hrm_sites(id)',
                                       'start_date': "TEXT NOT NULL DEFAULT ''", 'end_date': "TEXT NOT NULL DEFAULT ''"},
            'hrm_contracts': {'site_id': 'INTEGER REFERENCES hrm_sites(id)', 'department': "TEXT NOT NULL DEFAULT ''"},
            'hrm_documents': {'site_id': 'INTEGER REFERENCES hrm_sites(id)', 'department': "TEXT NOT NULL DEFAULT ''"},
            'hrm_personnel_actions': {'site_id': 'INTEGER REFERENCES hrm_sites(id)', 'target_site_id': 'INTEGER REFERENCES hrm_sites(id)',
                                      'record_department': "TEXT NOT NULL DEFAULT ''", 'employee_version': 'INTEGER NOT NULL DEFAULT 0'},
        }
        for table, columns in additions.items():
            existing = {row['name'] for row in db().execute('PRAGMA table_info(' + table + ')')}
            for column, declaration in columns.items():
                if column not in existing:
                    db().execute('ALTER TABLE ' + table + ' ADD COLUMN ' + column + ' ' + declaration)
        db().execute("CREATE UNIQUE INDEX IF NOT EXISTS hrm_person_national_id ON hrm_employee_details(national_id COLLATE NOCASE) WHERE national_id!=''")
        db().execute('CREATE UNIQUE INDEX IF NOT EXISTS hrm_unit_site_name ON hrm_units(company_id,COALESCE(site_id,0),name)')
        db().execute("UPDATE hrm_employment_history SET start_date=effective_date WHERE start_date=''")
        db().commit()
    app.extensions.setdefault('hrm_catalog', {}).update(CATALOG)

    def configuration_scope(cid, module='personnel'):
        actor = require(db(), module, write=True)
        if actor.get('is_primary_admin'):
            return
        if not any(scope['company_id'] == cid and not scope['department'] and not scope.get('site_id') for scope in actor['scopes']):
            raise PermissionError()

    def own_row(resource, rid, *, write=False, module=None, operation=None):
        module = module or CATALOG[resource]['module']
        require(db(), module, write=write, operation=operation)
        row = db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (positive_id(rid),)).fetchone()
        if row is None:
            return None
        snapshot = dict(row)
        if resource == 'personnel_actions':
            snapshot['department'] = row['record_department']
        elif resource == 'units':
            snapshot['department'] = row['name']
        elif resource == 'sites':
            snapshot['site_id'] = row['id']
        access.record_scope(db(), snapshot, module, write=write, operation=operation)
        selected_cid = _scope_company(db(), module, write=write, fallback=row['company_id'], operation=operation)
        if selected_cid != row['company_id']:
            raise ValueError('رکورد متعلق به شرکت انتخاب‌شده نیست')
        if resource in ('units', 'sites') and write:
            configuration_scope(row['company_id'])
        return row

    def values_for(resource, data, *, current=None):
        allowed = RESOURCE_FIELDS[resource]
        values = {key: (current[key] if current else '') for key in allowed}
        values.update({key: data[key] for key in allowed if key in data})
        required = {item['key'] for item in CATALOG[resource]['fields'] if item['required']}
        for key in allowed:
            if key == 'active':
                values[key] = integer_value(values[key] if values[key] != '' else 1, minimum=0, maximum=1)
            elif key.endswith('_id'):
                values[key] = _optional_id(values.get(key))
            elif key.endswith('_date'):
                values[key] = date_value(values.get(key), required=key in required)
            else:
                values[key] = text_value(values.get(key), required=key in required,
                                         max_length=4000 if key == 'notes' else 500)
        if resource == 'contracts':
            values['status'] = values['status'] or 'active'
            if values['status'] not in dict(CONTRACT_STATUS):
                raise ValueError('وضعیت قرارداد معتبر نیست')
        if resource == 'personnel_actions' and values['action_type'] not in dict(ACTION_TYPES):
            raise ValueError('نوع حکم معتبر نیست')
        if resource == 'sites':
            values['kind'] = values['kind'] or 'other'
        if resource == 'temporary_assignments':
            values['status'] = values['status'] or 'active'
            if values['status'] not in ('planned','active','completed','cancelled'):
                raise ValueError('وضعیت فعالیت موقت معتبر نیست')
        if resource == 'employment_history':
            values['start_date'] = values['start_date'] or values['effective_date']
        _range(values)
        return values

    def check_related(resource, values, person, cid):
        if resource == 'units':
            if not values.get('site_id') or not db().execute('SELECT 1 FROM hrm_sites WHERE id=? AND company_id=? AND active=1', (values['site_id'],cid)).fetchone():
                raise ValueError('سایت فعال واحد متعلق به این شرکت نیست')
        if resource == 'units' and values.get('manager_id'):
            manager = employee(db(), values['manager_id'], 'personnel', write=True)
            if manager['company_id'] != cid:
                raise ValueError('مدیر واحد متعلق به این شرکت نیست')
        if resource == 'personnel_actions':
            target = values.get('target_company_id') or cid
            target_site = values.get('target_site_id')
            if target_site is None and target == cid:
                target_site = person.get('site_id')
            candidate = dict(values, company_id=target, site_id=target_site,
                             department=values['department'] or person['department'])
            _placement_refs(db(), candidate, person['id'], operation=access._operation('personnel',write=True))
            access.record_scope(db(), candidate, 'personnel', write=True)
            if values.get('document_id'):
                document = db().execute('SELECT * FROM hrm_documents WHERE id=?', (values['document_id'],)).fetchone()
                if not document or document['employee_id'] != person['id'] or document['company_id'] != cid:
                    raise ValueError('مدرک حکم متعلق به این فرد و شرکت نیست')
                require(db(), document['permission_group'])
        if resource == 'temporary_assignments':
            target = db().execute('SELECT * FROM hrm_sites WHERE id=? AND active=1', (values['target_site_id'],)).fetchone()
            if not target:
                raise ValueError('سایت فعال فعالیت موقت را انتخاب کنید')
            access.site_scope(db(), target['company_id'], target['id'], 'personnel', write=True, department=person['department'])
            values['target_company_id'] = target['company_id']

    def apply_action(row):
        if row['applied']:
            raise HrmConflict('این حکم قبلاً اجرا شده است')
        if row['effective_date'] > today_iso():
            raise ValueError('حکم دارای تاریخ آینده تا رسیدن تاریخ اثر قابل اجرا نیست')
        before = _current(db(), row['employee_id'])
        if before['version'] != row['employee_version']:
            raise HrmConflict('پرونده فرد پس از ثبت حکم تغییر کرده است؛ حکم را با وضعیت فعلی بازبینی و دوباره ثبت کنید')
        if before['company_id'] != row['company_id']:
            raise HrmConflict('شرکت محل خدمت از زمان ثبت حکم تغییر کرده است؛ حکم تازه ثبت کنید')
        target = row['target_company_id'] or before['company_id']
        change = {key: row[key] for key in ('department', 'job', 'location', 'manager_id', 'unit_id') if row[key] not in ('',None)}
        change['company_id'] = target
        change['site_id'] = row['target_site_id'] if row['target_site_id'] is not None else (before.get('site_id') if target==before['company_id'] else None)
        if row['action_type'] in ('termination', 'settlement'):
            change['status'] = 'terminated' if row['action_type'] == 'termination' else 'settled'
            change['end_date'] = row['effective_date']
        # Blank placement fields on non-placement actions retain the current assignment.
        if row['action_type'] not in ('appointment', 'promotion', 'transfer'):
            change = {key: value for key, value in change.items() if value not in ('', None)}
        after = _person_values(db(), change, before, row['employee_id'])
        _save_details(db(), row['employee_id'], after)
        employee(db(), row['employee_id'], 'personnel', operation='edit')
        _history(db(), row['employee_id'], before, after, row['effective_date'], 'personnel_action', row['notes'])
        db().execute('UPDATE hrm_personnel_actions SET applied=1,version=version+1 WHERE id=?', (row['id'],))
        audit('hrm_apply_personnel_action', row['company_id'], str(row['id']))

    @app.route('/api/hrm/people', methods=['GET', 'POST'], endpoint='hrm_people_list')
    @authenticated
    def people():
        module = request.args.get('module', 'personnel') if request.method == 'GET' else 'personnel'
        if module not in ('personnel', 'contracts', 'documents', 'attendance', 'performance', 'discipline',
                          'loans', 'welfare', 'accommodation', 'training', 'health', 'hse', 'health_environment', 'reports'):
            raise ValueError('نوع دسترسی فهرست پرسنل معتبر نیست')
        operation = request.args.get('operation') if request.method == 'GET' else None
        if operation is not None and operation not in ('read','create','edit','export'):
            raise ValueError('نوع عملیات فهرست پرسنل معتبر نیست')
        require(db(), module, write=request.method != 'GET', operation=operation)
        if request.method == 'GET':
            cid = _scope_company(db(), module, operation=operation)
            clause, parameters = scope_sql(db(), module, alias='e', operation=operation)
            query = '''SELECT e.id,e.code,e.name,COALESCE(d.company_id,e.company_id) company_id,
                COALESCE(d.department,e.department) department,COALESCE(d.job,e.job) job,d.site_id
                FROM employees e LEFT JOIN hrm_employee_details d ON d.employee_id=e.id WHERE (''' + clause + ')'
            params = list(parameters)
            if cid is not None:
                query += ' AND COALESCE(d.company_id,e.company_id)=?'
                params.append(cid)
            if request.args.get('site_id') not in (None,''):
                query += ' AND d.site_id=?';params.append(positive_id(request.args['site_id']))
            rows = db().execute(query + ' ORDER BY e.name,e.id LIMIT 5000', params).fetchall()
            full = module == 'personnel' and 'module' not in request.args and operation in (None,'read')
            return jsonify([_current(db(), row['id']) if full else dict(row) for row in rows])
        data = json_object()
        _body_fields(data, (*DETAIL_FIELDS, 'code', 'name', 'effective_date'))
        cid = _scope_company(db(), 'personnel', data, write=True)
        if cid is None:
            raise ValueError('شرکت محل خدمت را انتخاب کنید')
        with begin_write(db()):
            code = text_value(data.get('code'), required=True, max_length=128)
            name = text_value(data.get('name'), required=True, max_length=250)
            _unique_current_code(db(), cid, code)
            values = _person_values(db(), dict(data, company_id=cid))
            cursor = db().execute('INSERT INTO employees(company_id,code,name,department,job) VALUES (?,?,?,?,?)',
                                  (cid, code, name, values['department'], values['job']))
            eid = cursor.lastrowid
            if values['manager_id'] == eid:
                raise ValueError('فرد نمی‌تواند مدیر مستقیم خودش باشد')
            _save_details(db(), eid, values)
            employee(db(), eid, 'personnel', operation='create')
            effective = date_value(data.get('effective_date') or today_iso(), required=True)
            _history(db(), eid, None, values, effective, 'hire')
            audit('hrm_create_person', cid, str(eid))
        return jsonify(_current(db(), eid)), 201

    @app.route('/api/hrm/people/<int:eid>', methods=['GET', 'PUT'], endpoint='hrm_people_detail')
    @authenticated
    def person_detail(eid):
        eid = positive_id(eid)
        employee(db(), eid, 'personnel', write=request.method != 'GET')
        current = _current(db(), eid)
        if request.method == 'GET':
            _scope_company(db(), 'personnel', fallback=current['company_id'])
            return jsonify(current)
        data = json_object()
        _body_fields(data, (*DETAIL_FIELDS, 'code', 'name', 'effective_date'), extra=('version',))
        with begin_write(db()):
            employee(db(), eid, 'personnel', operation='edit')
            current = _current(db(), eid)
            check_version(data, current)
            if request.args.get('company_id') not in (None, ''):
                selected_cid = positive_id(request.args['company_id'])
                if selected_cid not in (current['company_id'], positive_id(data.get('company_id', current['company_id']))):
                    raise ValueError('شرکت فرم با محل خدمت یا مقصد فرد یکسان نیست')
            values = _person_values(db(), data, current, eid)
            code = current['code'] if data.get('code', current['code']) == current['code'] else text_value(data['code'], required=True, max_length=128)
            name = current['name'] if data.get('name', current['name']) == current['name'] else text_value(data['name'], required=True, max_length=250)
            _unique_current_code(db(), values['company_id'], code, eid)
            effective = date_value(data.get('effective_date') or today_iso(), required=True)
            changed_assignment = any(current.get(key) != values.get(key) for key in ASSIGNMENT_FIELDS)
            if changed_assignment and effective > today_iso():
                raise ValueError('برای جابه‌جایی آینده، حکم ثبت کنید؛ وضعیت فعلی پیش از تاریخ اثر تغییر نمی‌کند')
            # Identity edits retain every issued training/contract reference.
            db().execute('UPDATE employees SET code=?,name=? WHERE id=?', (code, name, eid))
            _save_details(db(), eid, values)
            employee(db(), eid, 'personnel', write=True)
            if changed_assignment or current['version'] == 0:
                _history(db(), eid, current, values, effective, 'assignment_change')
            audit('hrm_update_person', values['company_id'], str(eid))
        return jsonify(_current(db(), eid))

    @app.get('/api/hrm/contracts/expiring', endpoint='hrm_people_expiring_contracts')
    @authenticated
    def expiring_contracts():
        require(db(), 'contracts')
        days = integer_value(request.args.get('days', '30'), minimum=0, maximum=3650)
        rows = resource_rows('contracts')
        return jsonify([row for row in rows if row['status'] == 'active' and row['days_to_expiry'] is not None
                        and 0 <= row['days_to_expiry'] <= days])

    def resource_rows(resource):
        reference = resource in ('sites', 'units') and ('module' in request.args or 'operation' in request.args)
        module = request.args.get('module', CATALOG[resource]['module']) if reference else CATALOG[resource]['module']
        operation = request.args.get('operation') if reference else None
        if operation is not None and operation not in ('read','create','edit','export'):
            raise ValueError('نوع عملیات فهرست مرجع معتبر نیست')
        require(db(), module, operation=operation)
        cid = _scope_company(db(), module, operation=operation)
        if resource in ('units', 'sites'):
            query = 'SELECT * FROM hrm_' + resource + (' WHERE company_id=?' if cid else '') + ' ORDER BY name,id'
            rows = db().execute(query, (cid,) if cid else ()).fetchall()
        else:
            dept_col = 'r.record_department' if resource == 'personnel_actions' else 'r.department'
            clause, parameters = access.record_scope_sql(db(), module, alias='r', department_column=dept_col)
            query = 'SELECT r.*,e.name employee_name,e.code employee_code FROM hrm_' + resource + \
                    ' r JOIN employees e ON e.id=r.employee_id WHERE (' + clause + ')'
            params = list(parameters)
            if cid:
                query += ' AND r.company_id=?'
                params.append(cid)
            rows = db().execute(query + ' ORDER BY r.id DESC LIMIT 5000', params).fetchall()
        result = []
        for row in rows:
            try:
                if resource in ('sites','units'):
                    actor = ctx(db())
                    site_id = row['id'] if resource == 'sites' else row['site_id']
                    if not actor.get('is_primary_admin') and not any(scope['company_id'] == row['company_id'] and
                          (not scope.get('site_id') or scope['site_id'] == site_id) and
                          (resource == 'sites' or not scope['department'] or scope['department'] == row['name']) for scope in actor['scopes']):
                        continue
                else:
                    snapshot = dict(row)
                    if resource == 'personnel_actions':
                        snapshot['department'] = row['record_department']
                    access.record_scope(db(), snapshot, module)
            except PermissionError:
                continue
            if reference:
                result.append({key:row[key] for key in ('id','company_id','site_id','name','kind','active') if key in row.keys()})
            else:
                result.append(_contract_extras(db(), row) if resource == 'contracts' else dict(row))
        return result

    @app.route('/api/hrm/<resource>', methods=['GET', 'POST'], endpoint='hrm_people_resource')
    @authenticated
    def personnel_resource(resource):
        if resource not in RESOURCE_FIELDS:
            return error('بخش منابع انسانی یافت نشد', 404)
        module = CATALOG[resource]['module']
        if not (request.method == 'GET' and resource in ('sites','units') and ('module' in request.args or 'operation' in request.args)):
            require(db(), module, write=request.method != 'GET')
        if request.method == 'GET':
            return jsonify(resource_rows(resource))
        data = json_object()
        _body_fields(data, RESOURCE_FIELDS[resource], extra=('employee_id', 'company_id', 'apply_now') if resource == 'personnel_actions' else ('employee_id', 'company_id'))
        with begin_write(db()):
            person = employee(db(), positive_id(data.get('employee_id')), module, write=True) if resource not in ('units','sites') else None
            cid = _scope_company(db(), module, data, write=True, fallback=person['company_id'] if person else None)
            if cid is None:
                raise ValueError('شرکت را انتخاب کنید')
            if person and cid != person['company_id']:
                raise ValueError('ثبت سابقه جدید فقط در شرکت محل خدمت فعلی فرد مجاز است')
            if resource in ('units','sites'):
                configuration_scope(cid)
            values = values_for(resource, data)
            check_related(resource, values, person, cid)
            if person:
                values['site_id'] = person.get('site_id')
                if resource == 'personnel_actions':
                    values['record_department'] = person['department']
                    values['employee_version'] = _current(db(), person['id'])['version']
                elif 'department' not in values:
                    values['department'] = person['department']
                snapshot = dict(values,company_id=cid)
                if resource == 'personnel_actions':
                    snapshot['department'] = values['record_department']
                access.record_scope(db(),snapshot,module,write=True)
            columns = ['company_id', *(['employee_id'] if person else []), *values]
            row_values = [cid, *([person['id']] if person else []), *values.values()]
            cursor = db().execute('INSERT INTO hrm_' + resource + ' (' + ','.join(columns) + ') VALUES (' +
                                  ','.join('?' for _ in columns) + ')', row_values)
            rid = cursor.lastrowid
            if resource == 'employment_history':
                db().execute('UPDATE hrm_employment_history SET actor_id=? WHERE id=?', (g.user['id'], rid))
            audit('hrm_create_' + resource, cid, str(rid))
            if resource == 'personnel_actions' and _flag(data.get('apply_now', 0)):
                apply_action(db().execute('SELECT * FROM hrm_personnel_actions WHERE id=?', (rid,)).fetchone())
        row = db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (rid,)).fetchone()
        return jsonify(_contract_extras(db(), row) if resource == 'contracts' else dict(row)), 201

    @app.route('/api/hrm/<resource>/<int:rid>', methods=['GET', 'PUT'], endpoint='hrm_people_record')
    @authenticated
    def personnel_record(resource, rid):
        if resource not in RESOURCE_FIELDS:
            return error('بخش منابع انسانی یافت نشد', 404)
        row = own_row(resource, rid, write=request.method != 'GET')
        if row is None:
            return error('رکورد یافت نشد', 404)
        if request.method == 'GET':
            return jsonify(_contract_extras(db(), row) if resource == 'contracts' else dict(row))
        data = json_object()
        _body_fields(data, RESOURCE_FIELDS[resource], extra=('version', 'employee_id', 'company_id', 'apply_now') if resource == 'personnel_actions' else ('version', 'employee_id', 'company_id'))
        with begin_write(db()):
            row = own_row(resource, rid, write=True)
            check_version(data, row)
            for owner in ('employee_id', 'company_id'):
                if owner in data and (owner not in row.keys() or positive_id(data[owner]) != row[owner]):
                    raise ValueError('مالک رکورد قابل تغییر نیست')
            if resource == 'employment_history' and row['source'] != 'manual':
                raise HrmConflict('سابقه خودکار قابل بازنویسی نیست؛ اصلاح را در پرونده فعلی ثبت کنید')
            if resource == 'personnel_actions' and row['applied'] and any(key in data and data[key] != row[key] for key in RESOURCE_FIELDS[resource] if key not in ('notes', 'document_id')):
                raise HrmConflict('مفاد حکم اجراشده قابل بازنویسی نیست؛ حکم اصلاحی تازه ثبت کنید')
            if resource == 'contracts' and row['status'] == 'renewed' and any(key in data and data[key] != row[key] for key in RESOURCE_FIELDS[resource] if key != 'notes'):
                raise HrmConflict('قرارداد دارای تمدید بازنویسی نمی‌شود؛ نسخه بعدی را اصلاح کنید')
            values = values_for(resource, data, current=row)
            if resource == 'units' and values['name'] != row['name'] and db().execute('SELECT 1 FROM hrm_employee_details WHERE unit_id=? LIMIT 1', (rid,)).fetchone():
                raise HrmConflict('نام واحد دارای پرسنل برای حفظ دسترسی واحدی تغییر نمی‌کند؛ واحد جدید تعریف و جابه‌جایی ثبت کنید')
            person = dict(id=row['employee_id'],department=row['record_department'] if resource=='personnel_actions' else row['department']) if 'employee_id' in row.keys() else None
            related_keys = {'manager_id','unit_id','target_company_id','target_site_id','document_id','site_id','department','job','location'}
            if resource in ('sites','units') or any(key in data and data[key] != row[key] for key in related_keys if key in row.keys()):
                check_related(resource, values, person, row['company_id'])
            candidate = dict(row);candidate.update(values)
            access.record_scope(db(),candidate,CATALOG[resource]['module'],operation='edit')
            db().execute('UPDATE hrm_' + resource + ' SET ' + ','.join(key + '=?' for key in values) +
                         ',version=version+1 WHERE id=?', [*values.values(), rid])
            audit('hrm_update_' + resource, row['company_id'], str(rid))
        updated = db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (rid,)).fetchone()
        return jsonify(_contract_extras(db(), updated) if resource == 'contracts' else dict(updated))

    @app.post('/api/hrm/contracts/<int:rid>/renew', endpoint='hrm_people_renew_contract')
    @authenticated
    def renew_contract(rid):
        data = json_object()
        _body_fields(data, RESOURCE_FIELDS['contracts'], extra=('version',))
        with begin_write(db()):
            require(db(),'contracts',operation='create')
            parent = own_row('contracts', rid, write=True, operation='edit')
            if parent is None:
                return error('قرارداد یافت نشد', 404)
            check_version(data, parent)
            if parent['status'] in ('terminated', 'renewed') or db().execute('SELECT 1 FROM hrm_contracts WHERE parent_contract_id=?', (rid,)).fetchone():
                raise HrmConflict('این قرارداد قابل تمدید دوباره نیست؛ آخرین نسخه جاری را انتخاب کنید')
            person = employee(db(), parent['employee_id'], 'contracts', operation='create')
            if person['company_id'] != parent['company_id']:
                raise ValueError('پس از انتقال به شرکت دیگر، قرارداد مستقل شرکت جدید را ثبت کنید')
            values = values_for('contracts', data)
            if values['start_date'] <= parent['start_date']:
                raise ValueError('تاریخ شروع تمدید باید پس از شروع نسخه قبلی باشد')
            if values['status'] not in ('draft', 'active'):
                raise ValueError('تمدید تازه باید پیش‌نویس یا جاری باشد')
            columns = ['employee_id', 'company_id', 'site_id', 'department', 'parent_contract_id', *values]
            cursor = db().execute('INSERT INTO hrm_contracts (' + ','.join(columns) + ') VALUES (' +
                                  ','.join('?' for _ in columns) + ')',
                                  [parent['employee_id'], parent['company_id'], person.get('site_id'), person['department'], rid, *values.values()])
            db().execute("UPDATE hrm_contracts SET status='renewed',version=version+1 WHERE id=?", (rid,))
            audit('hrm_renew_contract', parent['company_id'], str(cursor.lastrowid))
        return jsonify(_contract_extras(db(), db().execute('SELECT * FROM hrm_contracts WHERE id=?', (cursor.lastrowid,)).fetchone())), 201

    @app.post('/api/hrm/personnel_actions/<int:rid>/apply', endpoint='hrm_people_apply_action')
    @authenticated
    def execute_personnel_action(rid):
        data = json_object()
        _body_fields(data, (), extra=('version',))
        with begin_write(db()):
            row = own_row('personnel_actions', rid, write=True,operation='edit')
            if row is None:
                return error('حکم یافت نشد', 404)
            check_version(data, row)
            apply_action(row)
        return jsonify(dict(db().execute('SELECT * FROM hrm_personnel_actions WHERE id=?', (rid,)).fetchone()))

    @app.route('/api/hrm/documents', methods=['GET', 'POST'], endpoint='hrm_people_documents')
    @authenticated
    def confidential_documents():
        if request.method == 'GET':
            selected_group = request.args.get('permission_group')
            if selected_group is not None and selected_group not in DOCUMENT_GROUPS:
                raise ValueError('محدوده محرمانگی سند معتبر نیست')
            result = []
            allowed = False
            for group in ([selected_group] if selected_group else DOCUMENT_GROUPS):
                try:
                    # require() resolves the actual operation in this request:
                    # read on the collection, export on the XLSX report. A
                    # medical-read grant cannot piggyback on documents.export.
                    require(db(), group)
                except PermissionError:
                    if selected_group:
                        raise
                    continue
                allowed = True
                cid = _scope_company(db(), group)
                clause, parameters = access.record_scope_sql(db(), group, alias='d', department_column='d.department')
                query = '''SELECT d.id,d.employee_id,d.company_id,d.site_id,d.department,d.permission_group,
                    d.contract_id,d.title,d.kind,d.filename,d.mime,d.revision,d.supersedes_id,d.version,d.created,
                    e.name employee_name,e.code employee_code FROM hrm_documents d
                    JOIN employees e ON e.id=d.employee_id WHERE d.permission_group=? AND (''' + clause + ')'
                params = [group, *parameters]
                if cid is not None:
                    query += ' AND d.company_id=?'; params.append(cid)
                for key in ('employee_id','contract_id','site_id'):
                    if request.args.get(key) not in (None,''):
                        query += ' AND d.' + key + '=?'; params.append(positive_id(request.args[key]))
                result.extend(dict(row) for row in db().execute(query + ' ORDER BY d.id DESC LIMIT 5000', params))
            if not allowed:
                raise PermissionError()
            return jsonify(sorted(result, key=lambda row:row['id'], reverse=True))
        allowed = {'employee_id', 'company_id', 'permission_group', 'title', 'kind', 'contract_id', 'supersedes_id'}
        if set(request.form) - allowed or set(request.files) != {'file'}:
            raise ValueError('فیلدها یا فایل سند معتبر نیست')
        group = request.form.get('permission_group', 'documents')
        if group not in DOCUMENT_GROUPS:
            raise ValueError('محدوده محرمانگی سند معتبر نیست')
        require(db(), group, write=True)
        content, mime, extension = validate_document_upload(request.files['file'])
        with begin_write(db()):
            eid = positive_id(request.form.get('employee_id'))
            contract_id = _optional_id(request.form.get('contract_id'))
            supersedes_id = _optional_id(request.form.get('supersedes_id'))
            source = None
            if contract_id:
                contract = own_row('contracts', contract_id, write=True)
                if not contract or contract['employee_id'] != eid or group != 'contracts':
                    raise ValueError('قرارداد مرتبط یا محدوده محرمانگی سند معتبر نیست')
                source = dict(contract)
            revision = 1
            if supersedes_id:
                previous = db().execute('SELECT * FROM hrm_documents WHERE id=?', (supersedes_id,)).fetchone()
                if not previous or previous['employee_id'] != eid or previous['permission_group'] != group or previous['contract_id'] != contract_id:
                    raise ValueError('نسخه قبلی سند متعلق به همین پرونده و محدوده نیست')
                access.record_scope(db(), previous, group, operation='create')
                if source and (source['company_id'],source.get('site_id')) != (previous['company_id'],previous['site_id']):
                    raise ValueError('محدوده نسخه قبلی با قرارداد یکسان نیست')
                source = dict(previous)
                if db().execute('SELECT 1 FROM hrm_documents WHERE supersedes_id=?', (supersedes_id,)).fetchone():
                    raise HrmConflict('این نسخه قبلاً جایگزین شده است؛ آخرین نسخه را انتخاب کنید')
                revision = previous['revision'] + 1
            if source is None:
                source = employee(db(), eid, group, operation='create')
            cid = _scope_company(db(), group, request.form, write=True, fallback=source['company_id'])
            if cid != source['company_id']:
                raise ValueError('شرکت سند با پرونده مرتبط یکسان نیست')
            access.record_scope(db(), source, group, operation='create')
            title = text_value(request.form.get('title'), required=True, max_length=250)
            kind = text_value(request.form.get('kind') or 'employment', max_length=100)
            cursor = db().execute('''INSERT INTO hrm_documents(employee_id,company_id,site_id,department,permission_group,contract_id,
                title,kind,filename,mime,content,revision,supersedes_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (eid, cid, source.get('site_id'), source.get('department',''), group, contract_id, title, kind, 'document' + extension, mime, content, revision, supersedes_id))
            audit('hrm_upload_document', cid, str(cursor.lastrowid))
        row = db().execute('SELECT * FROM hrm_documents WHERE id=?', (cursor.lastrowid,)).fetchone()
        return jsonify(_document_metadata(row)), 201

    def document_row(rid, *, write=False):
        row = db().execute('SELECT * FROM hrm_documents WHERE id=?', (positive_id(rid),)).fetchone()
        if row is None:
            require(db(), 'documents', write=write)
            return None
        require(db(), row['permission_group'], write=write)
        access.record_scope(db(), row, row['permission_group'], write=write)
        selected_cid = _scope_company(db(), row['permission_group'], write=write, fallback=row['company_id'])
        if selected_cid != row['company_id']:
            raise ValueError('سند متعلق به شرکت انتخاب‌شده نیست')
        return row

    @app.route('/api/hrm/documents/<int:rid>', methods=['GET', 'PUT'], endpoint='hrm_people_document')
    @authenticated
    def confidential_document(rid):
        row = document_row(rid, write=request.method != 'GET')
        if row is None:
            return error('سند یافت نشد', 404)
        if request.method == 'GET':
            return jsonify(_document_metadata(row))
        data = json_object()
        _body_fields(data, ('title', 'kind'), extra=('version',))
        with begin_write(db()):
            row = document_row(rid, write=True)
            check_version(data, row)
            title = text_value(data.get('title', row['title']), required=True, max_length=250)
            kind = text_value(data.get('kind', row['kind']), max_length=100)
            db().execute('UPDATE hrm_documents SET title=?,kind=?,version=version+1 WHERE id=?', (title, kind, rid))
            audit('hrm_update_document_metadata', row['company_id'], str(rid))
        return jsonify(_document_metadata(db().execute('SELECT * FROM hrm_documents WHERE id=?', (rid,)).fetchone()))

    @app.get('/api/hrm/documents/<int:rid>/file', endpoint='hrm_people_document_file')
    @authenticated
    def confidential_document_file(rid):
        row = document_row(rid)
        if row is None:
            return error('سند یافت نشد', 404)
        audit('hrm_download_document', row['company_id'], str(rid))
        db().commit()
        suffix = {'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg',
                  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx'}[row['mime']]
        return Response(row['content'], mimetype='application/octet-stream', headers={
            'Content-Disposition': 'attachment; filename="personnel-document-' + str(rid) + suffix + '"'})

    def training_person(eid):
        try:
            return employee(db(), positive_id(eid), 'training')
        except PermissionError:
            return employee(db(), positive_id(eid), 'personnel')

    @app.get('/api/hrm/people/<int:eid>/training', endpoint='hrm_people_training')
    @authenticated
    def personnel_training(eid):
        person = training_person(eid)
        records = [dict(row) for row in db().execute('''SELECT r.*,c.title,c.date,c.status,c.company_id training_company_id
            FROM records r JOIN courses c ON c.id=r.course_id WHERE r.employee_id=? ORDER BY c.date DESC,r.id DESC''', (eid,))]
        assessments = [dict(row) for row in db().execute('''SELECT a.*,s.title skill_title,s.code skill_code
            FROM assessments a JOIN skills s ON s.id=a.skill_id WHERE a.employee_id=? ORDER BY s.title''', (eid,))]
        certificates = [dict(row) for row in db().execute('''SELECT id,title,issuer,issued,expires,filename
            FROM certificates WHERE employee_id=? ORDER BY id DESC''', (eid,))]
        return jsonify(employee=person, records=records, assessments=assessments, certificates=certificates)

    @app.get('/api/hrm/people/<int:eid>/training-certificates/<int:rid>/file', endpoint='hrm_people_training_certificate')
    @authenticated
    def personnel_training_certificate(eid, rid):
        person = training_person(eid)
        row = db().execute('SELECT * FROM certificates WHERE id=? AND employee_id=?', (positive_id(rid), eid)).fetchone()
        if row is None:
            return error('گواهینامه یافت نشد', 404)
        audit('hrm_download_training_certificate', person['company_id'], str(rid))
        db().commit()
        extension = { 'application/pdf': '.pdf', 'image/png': '.png', 'image/jpeg': '.jpg' }.get(row['mime'], '.bin')
        return Response(row['content'], mimetype='application/octet-stream', headers={
            'Content-Disposition': 'attachment; filename="training-certificate-' + str(rid) + extension + '"'})
