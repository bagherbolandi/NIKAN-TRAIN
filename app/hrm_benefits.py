"""Scoped personnel benefits, exact-rial ledgers and exclusive accommodation bookings.

Catalog limits are organization-entered data. A zero limit means unrestricted;
no leave, lending or welfare entitlement is assumed by the application.
"""
from collections import deque
from datetime import date, datetime, timedelta, timezone
from flask import jsonify, request

from .hrm_access import (company_scope, employee, record_scope,
                         record_scope_sql, require, site_scope)
from .hrm_common import (HrmConflict, begin_write, check_version, date_value,
                         integer_value, money_value, text_value)
from .validation import json_object, positive_id


MAX_MONEY = 9_007_199_254_740_991
SCHEMA = f'''
CREATE TABLE IF NOT EXISTS hrm_loans(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 employee_id INTEGER NOT NULL REFERENCES employees(id),
 title TEXT NOT NULL,loan_kind TEXT NOT NULL DEFAULT 'internal' CHECK(loan_kind IN ('internal','bank')),
 amount_rials INTEGER NOT NULL CHECK(amount_rials BETWEEN 1 AND {MAX_MONEY}),
 installment_count INTEGER NOT NULL DEFAULT 0 CHECK(installment_count BETWEEN 0 AND 10000),
 issued_date TEXT NOT NULL DEFAULT '',due_date TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'registered' CHECK(status IN ('registered','approved','disbursed','cancelled','rejected')),
 bank_name TEXT NOT NULL DEFAULT '',referral_number TEXT NOT NULL DEFAULT '',
 referral_status TEXT NOT NULL DEFAULT 'not_applicable' CHECK(referral_status IN ('not_applicable','draft','issued','accepted','rejected')),
 notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_loans_person ON hrm_loans(employee_id,company_id);
CREATE TABLE IF NOT EXISTS hrm_loan_payments(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 employee_id INTEGER NOT NULL REFERENCES employees(id),loan_id INTEGER NOT NULL REFERENCES hrm_loans(id),
 amount_rials INTEGER NOT NULL CHECK(amount_rials BETWEEN 1 AND {MAX_MONEY}),
 paid_date TEXT NOT NULL,installment_no INTEGER NOT NULL DEFAULT 0 CHECK(installment_no BETWEEN 0 AND 10000),
 reference TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'posted' CHECK(status IN ('posted','voided')),
 notes TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(loan_id,reference));
CREATE INDEX IF NOT EXISTS hrm_loan_payments_loan ON hrm_loan_payments(loan_id,status);
CREATE TABLE IF NOT EXISTS hrm_welfare_services(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 name TEXT NOT NULL,kind TEXT NOT NULL DEFAULT '',shared INTEGER NOT NULL DEFAULT 0 CHECK(shared IN (0,1)),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 max_uses INTEGER NOT NULL DEFAULT 0 CHECK(max_uses>=0),
 max_quantity INTEGER NOT NULL DEFAULT 0 CHECK(max_quantity>=0),
 period_days INTEGER NOT NULL DEFAULT 0 CHECK(period_days>=0),
 notes TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(company_id,name));
CREATE TABLE IF NOT EXISTS hrm_welfare_uses(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 employee_id INTEGER NOT NULL REFERENCES employees(id),service_id INTEGER NOT NULL REFERENCES hrm_welfare_services(id),
 used_date TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>0),
 cost_rials INTEGER NOT NULL DEFAULT 0 CHECK(cost_rials BETWEEN 0 AND {MAX_MONEY}),
 status TEXT NOT NULL DEFAULT 'recorded' CHECK(status IN ('recorded','cancelled')),
 notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_welfare_uses_person ON hrm_welfare_uses(employee_id,service_id,used_date);
CREATE TABLE IF NOT EXISTS hrm_properties(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 name TEXT NOT NULL,location TEXT NOT NULL DEFAULT '',capacity INTEGER NOT NULL CHECK(capacity>0),
 shared INTEGER NOT NULL DEFAULT 0 CHECK(shared IN (0,1)),active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 min_nights INTEGER NOT NULL DEFAULT 0 CHECK(min_nights>=0),
 max_nights INTEGER NOT NULL DEFAULT 0 CHECK(max_nights>=0),
 cooldown_days INTEGER NOT NULL DEFAULT 0 CHECK(cooldown_days>=0),
 max_bookings INTEGER NOT NULL DEFAULT 0 CHECK(max_bookings>=0),
 period_days INTEGER NOT NULL DEFAULT 0 CHECK(period_days>=0),
 notes TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(company_id,name));
CREATE TABLE IF NOT EXISTS hrm_reservations(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1)),
 employee_id INTEGER NOT NULL REFERENCES employees(id),property_id INTEGER NOT NULL REFERENCES hrm_properties(id),
 start_date TEXT NOT NULL,end_date TEXT NOT NULL CHECK(start_date<end_date),
 guests INTEGER NOT NULL CHECK(guests>0),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','cancelled','rejected')),
 notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_reservations_overlap ON hrm_reservations(property_id,status,start_date,end_date);
'''


def field(key, label, kind='text', required=False, **options):
    return dict(key=key, label=label, type=kind, required=required, **options)


EMPLOYEE = field('employee_id', 'پرسنل', 'employee', True, immutable=True)
COMPANY = field('company_id', 'شرکت مسئول', 'company', True, immutable=True)
SITE = field('site_id', 'سایت مسئول (خالی: سطح شرکت)', 'resource', resource='sites', immutable=True)
MONEY = dict(min=0, max=MAX_MONEY)
YES_NO = [[0, 'خیر'], [1, 'بله']]
CATALOG = {
    'loans': dict(title='وام‌ها و تسهیلات کارکنان', module='loans', fields=[
        EMPLOYEE, field('title', 'عنوان وام / تسهیلات', required=True),
        field('loan_kind', 'نوع تسهیلات', 'select', True, choices=[['internal', 'وام داخلی'], ['bank', 'تسهیلات بانکی']]),
        field('amount_rials', 'اصل مبلغ (ریال)', 'number', True, min=1, max=MAX_MONEY),
        field('installment_count', 'تعداد اقساط توافق‌شده (صفر: نامشخص)', 'number', min=0, max=10000),
        field('issued_date', 'تاریخ پرداخت', 'date'), field('due_date', 'سررسید بازپرداخت', 'date'),
        field('status', 'وضعیت', 'select', True, choices=[['registered', 'ثبت اولیه'], ['approved', 'مصوب'], ['disbursed', 'پرداخت‌شده'], ['cancelled', 'لغوشده'], ['rejected', 'ردشده']]),
        field('bank_name', 'نام بانک'), field('referral_number', 'شماره معرفی‌نامه'),
        field('referral_status', 'وضعیت معرفی‌نامه', 'select', choices=[['not_applicable', 'ندارد'], ['draft', 'پیش‌نویس'], ['issued', 'صادرشده'], ['accepted', 'پذیرفته‌شده'], ['rejected', 'ردشده']]),
        field('notes', 'توضیحات', 'textarea'),
    ], columns=['employee_name', 'title', 'loan_kind', 'amount_rials', 'paid_rials', 'outstanding_rials', 'due_date', 'repayment_status', 'status']),
    'loan_payments': dict(title='بازپرداخت و اقساط وام', module='loans', fields=[
        field('loan_id', 'وام / تسهیلات', 'resource', True, resource='loans', immutable=True),
        field('amount_rials', 'مبلغ بازپرداخت (ریال)', 'number', True, min=1, max=MAX_MONEY),
        field('paid_date', 'تاریخ بازپرداخت', 'date', True),
        field('installment_no', 'شماره قسط (صفر: نامشخص)', 'number', min=0, max=10000),
        field('reference', 'شناسه یکتای رسید / سند بازپرداخت', required=True),
        field('status', 'وضعیت سند', 'select', True, choices=[['posted', 'ثبت‌شده'], ['voided', 'ابطال‌شده']]),
        field('notes', 'توضیحات', 'textarea'),
    ], columns=['employee_name', 'loan_title', 'paid_date', 'installment_no', 'amount_rials', 'reference', 'status']),
    'welfare_services': dict(title='تعریف خدمات و ضوابط رفاهی', module='welfare', fields=[
        COMPANY, SITE, field('name', 'عنوان خدمت / قلم', required=True), field('kind', 'گروه خدمت'),
        field('shared', 'قابل استفاده برای سایر شرکت‌ها', 'select', choices=YES_NO),
        field('active', 'فعال', 'select', choices=YES_NO, default=1),
        field('max_uses', 'حداکثر دفعات در دوره (صفر: بدون محدودیت)', 'number', min=0),
        field('max_quantity', 'حداکثر تعداد اقلام در دوره (صفر: بدون محدودیت)', 'number', min=0),
        field('period_days', 'طول دوره شناور به روز (صفر: کل سابقه)', 'number', min=0),
        field('notes', 'ضوابط و توضیحات مصوب سازمان', 'textarea'),
    ], columns=['name', 'kind', 'company_name', 'shared', 'active', 'max_uses', 'max_quantity', 'period_days']),
    'welfare_uses': dict(title='توزیع و استفاده از خدمات رفاهی', module='welfare', fields=[
        EMPLOYEE, field('service_id', 'خدمت / قلم', 'resource', True, resource='welfare_services', immutable=True),
        field('used_date', 'تاریخ تحویل / استفاده', 'date', True),
        field('quantity', 'تعداد', 'number', True, min=1),
        field('cost_rials', 'هزینه کل ثبت (ریال)', 'number', **MONEY),
        field('status', 'وضعیت', 'select', True, choices=[['recorded', 'ثبت‌شده'], ['cancelled', 'لغوشده']]),
        field('notes', 'توضیحات', 'textarea'),
    ], columns=['employee_name', 'service_name', 'used_date', 'quantity', 'cost_rials', 'status']),
    'properties': dict(title='اقامتگاه‌ها و ضوابط رزرو', module='accommodation', fields=[
        COMPANY, SITE, field('name', 'نام اقامتگاه / واحد مستقل', required=True), field('location', 'محل اقامتگاه'),
        field('capacity', 'ظرفیت نفرات', 'number', True, min=1, max=100000),
        field('shared', 'قابل استفاده برای سایر شرکت‌ها', 'select', choices=YES_NO),
        field('active', 'فعال', 'select', choices=YES_NO, default=1),
        field('min_nights', 'حداقل شب اقامت (صفر: بدون ضابطه اضافی)', 'number', min=0),
        field('max_nights', 'حداکثر شب اقامت (صفر: بدون محدودیت)', 'number', min=0),
        field('cooldown_days', 'حداقل فاصله اقامت‌های فرد به روز', 'number', min=0),
        field('max_bookings', 'حداکثر دفعات استفاده در دوره (صفر: بدون محدودیت)', 'number', min=0),
        field('period_days', 'طول دوره شناور به روز (صفر: کل سابقه)', 'number', min=0),
        field('notes', 'ضوابط و توضیحات مصوب سازمان', 'textarea'),
    ], columns=['name', 'location', 'company_name', 'capacity', 'shared', 'active', 'max_nights', 'cooldown_days']),
    'reservations': dict(title='ثبت و سوابق رزرو اقامتگاه‌ها', module='accommodation', fields=[
        EMPLOYEE, field('property_id', 'اقامتگاه / واحد مستقل', 'resource', True, resource='properties', immutable=True),
        field('start_date', 'تاریخ ورود', 'date', True), field('end_date', 'تاریخ خروج (روز خروج آزاد است)', 'date', True),
        field('guests', 'تعداد نفرات', 'number', True, min=1, max=100000),
        field('status', 'وضعیت رزرو', 'select', True, choices=[['pending', 'ثبت اولیه؛ بازه نگه داشته می‌شود'], ['approved', 'تأییدشده'], ['cancelled', 'لغوشده'], ['rejected', 'ردشده']]),
        field('notes', 'توضیحات', 'textarea'),
    ], columns=['employee_name', 'property_name', 'start_date', 'end_date', 'nights', 'guests', 'status']),
}

for definition in CATALOG.values():
    definition.update(deletable=True, restorable=False)

TABLES = {key: 'hrm_' + key for key in CATALOG}
CATALOG_RESOURCES = {'welfare_services', 'properties'}
DEFAULTS = {
    'loans': dict(loan_kind='internal', installment_count=0, issued_date='', due_date='',
                  status='registered', bank_name='', referral_number='', referral_status='not_applicable', notes=''),
    'loan_payments': dict(installment_no=0, status='posted', notes=''),
    'welfare_services': dict(kind='', shared=0, active=1, max_uses=0, max_quantity=0, period_days=0, notes=''),
    'welfare_uses': dict(quantity=1, cost_rials=0, status='recorded', notes=''),
    'properties': dict(location='', shared=0, active=1, min_nights=0, max_nights=0,
                       cooldown_days=0, max_bookings=0, period_days=0, notes=''),
    'reservations': dict(guests=1, status='pending', notes=''),
}


def _effective_company(person):
    return positive_id(person['company_id'])


def _catalog_visible(conn, row, module):
    """Catalog names are reusable; shared entries contain no personnel facts."""
    actor = require(conn, module)
    return bool(row['shared'] or any(scope['company_id'] == row['company_id'] and
                 (row['site_id'] is None or scope.get('site_id') is None or scope.get('site_id') == row['site_id'])
                 for scope in actor['scopes']))


def _catalog_write_scope(conn, row, module, operation):
    actor = require(conn, module, operation=operation)
    site_scope(conn, row['company_id'], row['site_id'], module, operation=operation)
    if not any(scope['company_id'] == row['company_id'] and not scope['department'] and
               (scope.get('site_id') is None or scope.get('site_id') == row['site_id']) for scope in actor['scopes']):
        raise PermissionError()


def _visible_record(conn, resource, row, operation=None):
    """Historical ownership stays fixed when the current worker changes sites."""
    module = CATALOG[resource]['module']
    if resource in CATALOG_RESOURCES:
        if operation in (None, 'read', 'export'):
            if not _catalog_visible(conn, row, module):
                raise PermissionError()
        else:
            _catalog_write_scope(conn, row, module, operation)
    else:
        record_scope(conn, row, module, operation=operation)


def _catalog_reference(conn, table, rid, cid, site_id=None):
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (positive_id(rid),)).fetchone()
    if row is None or (not row['shared'] and (row['company_id'] != cid or (row['site_id'] is not None and row['site_id'] != site_id))):
        raise ValueError('خدمت یا اقامتگاه برای این شرکت قابل استفاده نیست')
    return dict(row)


def _quota_check(entries, candidate_date, quantity, period_days, max_count=0, max_quantity=0):
    """Check every rolling window affected by insertion, including backdated use.

    The candidate marker prevents an old, newly-restricted historical window
    from blocking an unrelated future use. Future records cannot be bypassed by
    inserting an earlier date inside their quota period.
    """
    if not max_count and not max_quantity:
        return
    candidate = date.fromisoformat(candidate_date)
    values = [(date.fromisoformat(day), qty, False) for day, qty in entries]
    values.append((candidate, quantity, True))
    if not period_days:
        if ((max_count and len(values) > max_count) or
                (max_quantity and sum(row[1] for row in values) > max_quantity)):
            raise ValueError('سقف دفعات یا تعداد استفاده طبق ضابطه ثبت‌شده تکمیل شده است')
        return
    window, total, marker = deque(), 0, 0
    for day, qty, is_candidate in sorted(values):
        window.append((day, qty, is_candidate)); total += qty; marker += is_candidate
        lower = day.toordinal() - period_days + 1
        while window[0][0].toordinal() < lower:
            _, removed, marked = window.popleft(); total -= removed; marker -= marked
        if marker and ((max_count and len(window) > max_count) or (max_quantity and total > max_quantity)):
            raise ValueError('سقف دفعات یا تعداد استفاده در دوره شناور طبق ضابطه ثبت‌شده تکمیل شده است')


def _validate(conn, resource, body, previous=None):
    module = CATALOG[resource]['module']
    operation = 'edit' if previous else 'create'
    require(conn, module, operation=operation)
    definitions = {value['key']: value for value in CATALOG[resource]['fields']}
    if set(body) - set(definitions) - {'version'}:
        raise ValueError('فیلد ناشناخته در اطلاعات ارسالی وجود دارد')
    values = dict(previous) if previous else dict(DEFAULTS[resource])
    values.update({key: value for key, value in body.items() if key in definitions})
    result = {}
    for key, definition in definitions.items():
        value = values.get(key)
        label, kind = definition['label'], definition['type']
        if kind in ('employee', 'company', 'resource'):
            result[key] = None if not definition['required'] and value in (None, '') else positive_id(value)
        elif kind == 'date':
            result[key] = date_value('' if value is None else value, required=definition['required'])
        elif kind == 'number':
            numeric = 0 if value is None or value == '' else value
            result[key] = (money_value(numeric, label=label, minimum=definition.get('min', 0)) if key.endswith('_rials') else
                           integer_value(numeric, label=label, minimum=definition.get('min', 0), maximum=definition.get('max', 1_000_000)))
        elif kind == 'select':
            if key in ('shared', 'active'):
                result[key] = integer_value(value, label=label, minimum=0, maximum=1)
            else:
                if not isinstance(value, str) or value not in {choice[0] for choice in definition['choices']}:
                    raise ValueError('گزینه ' + label + ' معتبر نیست')
                result[key] = value
        else:
            result[key] = text_value('' if value is None else value, label=label, required=definition['required'], max_length=4000 if kind == 'textarea' else 240)
    if previous:
        for key in ('employee_id', 'company_id', 'site_id', 'loan_id', 'property_id', 'service_id'):
            if key in result and result[key] != previous[key]:
                raise HrmConflict('مالک یا مرجع سابقه قابل تغییر نیست؛ سابقه اشتباه را لغو و مورد صحیح را جداگانه ثبت کنید')
    if resource in CATALOG_RESOURCES:
        _catalog_write_scope(conn, result, module, operation)
    elif resource == 'loan_payments':
        loan = conn.execute('SELECT * FROM hrm_loans WHERE id=?', (result['loan_id'],)).fetchone()
        if loan is None or loan['deleted']:
            raise ValueError('وام یافت نشد')
        _visible_record(conn, 'loans', loan, operation=operation)
        result.update(employee_id=loan['employee_id'], company_id=loan['company_id'],
                      site_id=loan['site_id'], department=loan['department'])
        if result['status'] == 'posted':
            if loan['status'] != 'disbursed':
                raise ValueError('بازپرداخت فقط برای وام پرداخت‌شده قابل ثبت است')
            if loan['issued_date'] and result['paid_date'] < loan['issued_date']:
                raise ValueError('تاریخ بازپرداخت نمی‌تواند پیش از پرداخت وام باشد')
            if loan['installment_count'] and result['installment_no'] > loan['installment_count']:
                raise ValueError('شماره قسط از تعداد اقساط توافق‌شده بیشتر است')
            paid = conn.execute("SELECT COALESCE(sum(amount_rials),0) FROM hrm_loan_payments WHERE loan_id=? AND status='posted' AND deleted=0 AND id!=?",
                                (loan['id'], previous['id'] if previous else 0)).fetchone()[0]
            if paid + result['amount_rials'] > loan['amount_rials']:
                raise ValueError('مجموع بازپرداخت از اصل مبلغ وام بیشتر می‌شود')
    else:
        if previous:
            _visible_record(conn, resource, previous, operation=operation)
            result.update(company_id=previous['company_id'], site_id=previous['site_id'], department=previous['department'])
        else:
            person = employee(conn, result['employee_id'], module, operation=operation)
            result.update(company_id=_effective_company(person), site_id=person.get('site_id'), department=person['department'])
            record_scope(conn, result, module, operation=operation)
    rid = previous['id'] if previous else 0
    if resource == 'loans':
        if result['issued_date'] and result['due_date'] and result['due_date'] < result['issued_date']:
            raise ValueError('سررسید بازپرداخت نمی‌تواند پیش از تاریخ پرداخت وام باشد')
        if result['status'] == 'disbursed' and not result['issued_date']:
            raise ValueError('تاریخ پرداخت وام پرداخت‌شده الزامی است')
        if result['loan_kind'] == 'bank' and not result['bank_name']:
            raise ValueError('نام بانک برای تسهیلات بانکی الزامی است')
        paid = conn.execute("SELECT COALESCE(sum(amount_rials),0) FROM hrm_loan_payments WHERE loan_id=? AND status='posted' AND deleted=0", (rid,)).fetchone()[0]
        if result['amount_rials'] < paid:
            raise ValueError('اصل مبلغ وام نمی‌تواند کمتر از مجموع بازپرداخت ثبت‌شده باشد')
        if paid and result['status'] != 'disbursed':
            raise ValueError('وام دارای بازپرداخت را نمی‌توان از وضعیت پرداخت‌شده خارج کرد؛ ابتدا اسناد اشتباه را صریحاً ابطال کنید')
        if previous:
            earliest, largest_installment = conn.execute("SELECT min(paid_date),COALESCE(max(installment_no),0) FROM hrm_loan_payments WHERE loan_id=? AND status='posted' AND deleted=0", (rid,)).fetchone()
            if earliest and result['issued_date'] and result['issued_date'] > earliest:
                raise ValueError('تاریخ پرداخت وام نمی‌تواند پس از بازپرداخت ثبت‌شده باشد')
            if result['installment_count'] and largest_installment > result['installment_count']:
                raise ValueError('تعداد اقساط نمی‌تواند کمتر از شماره قسط ثبت‌شده باشد')
    elif resource == 'properties':
        if result['max_nights'] and result['min_nights'] > result['max_nights']:
            raise ValueError('حداقل اقامت نمی‌تواند از حداکثر اقامت بیشتر باشد')
    elif resource == 'welfare_uses':
        service = _catalog_reference(conn, 'hrm_welfare_services', result['service_id'], result['company_id'], result['site_id'])
        if result['status'] == 'recorded':
            if not service['active'] or service['deleted']:
                raise ValueError('خدمت انتخاب‌شده فعال نیست')
            entries = conn.execute("SELECT used_date,quantity FROM hrm_welfare_uses WHERE employee_id=? AND service_id=? AND status='recorded' AND deleted=0 AND id!=?",
                                   (result['employee_id'], service['id'], rid)).fetchall()
            _quota_check(entries, result['used_date'], result['quantity'], service['period_days'], service['max_uses'], service['max_quantity'])
    elif resource == 'reservations':
        place = _catalog_reference(conn, 'hrm_properties', result['property_id'], result['company_id'], result['site_id'])
        start, end = date.fromisoformat(result['start_date']), date.fromisoformat(result['end_date'])
        nights = (end - start).days
        if nights <= 0:
            raise ValueError('تاریخ خروج باید پس از تاریخ ورود باشد')
        if result['status'] in ('pending', 'approved'):
            if not place['active'] or place['deleted']:
                raise ValueError('اقامتگاه انتخاب‌شده فعال نیست')
            if result['guests'] > place['capacity']:
                raise ValueError('تعداد نفرات از ظرفیت اقامتگاه بیشتر است')
            if ((place['min_nights'] and nights < place['min_nights']) or
                    (place['max_nights'] and nights > place['max_nights'])):
                raise ValueError('مدت اقامت با ضوابط ثبت‌شده اقامتگاه سازگار نیست')
            # The transaction lock is acquired before this query. Shared units
            # are exclusive across companies; no reservation details leak.
            if conn.execute("SELECT 1 FROM hrm_reservations WHERE property_id=? AND status IN ('pending','approved') AND deleted=0 AND id!=? AND start_date<? AND end_date>? LIMIT 1",
                            (place['id'], rid, result['end_date'], result['start_date'])).fetchone():
                raise HrmConflict('بازه انتخاب‌شده با رزرو دیگری تداخل دارد')
            other = conn.execute("SELECT start_date,end_date FROM hrm_reservations WHERE employee_id=? AND property_id=? AND status IN ('pending','approved') AND deleted=0 AND id!=?",
                                 (result['employee_id'], place['id'], rid)).fetchall()
            if place['cooldown_days']:
                for row in other:
                    previous_start, previous_end = date.fromisoformat(row['start_date']), date.fromisoformat(row['end_date'])
                    gap = (start - previous_end).days if previous_end <= start else (previous_start - end).days
                    if gap < place['cooldown_days']:
                        raise ValueError('فاصله اقامت‌ها با ضابطه ثبت‌شده برای این اقامتگاه سازگار نیست')
            _quota_check([(row['start_date'], 1) for row in other], result['start_date'], 1,
                         place['period_days'], place['max_bookings'])
    return result


def _save(conn, resource, values, previous=None):
    table = TABLES[resource]
    if previous:
        assignments = ','.join(key + '=?' for key in values)
        conn.execute(f'UPDATE {table} SET {assignments},version=version+1,updated=CURRENT_TIMESTAMP WHERE id=?',
                     [*values.values(), previous['id']])
        return previous['id']
    keys = list(values)
    cursor = conn.execute(f'INSERT INTO {table} ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})', list(values.values()))
    return cursor.lastrowid


def _query_rows(conn, resource, include_deleted=False):
    table = TABLES[resource]
    active = '1' if include_deleted else 'r.deleted=0'
    if resource in CATALOG_RESOURCES:
        return [dict(row, is_deleted=row['deleted']) for row in conn.execute(f'SELECT r.*,c.name company_name,s.name site_name FROM {table} r JOIN companies c ON c.id=r.company_id LEFT JOIN hrm_sites s ON s.id=r.site_id WHERE {active} ORDER BY r.name,r.id')]
    scope, params = record_scope_sql(conn, CATALOG[resource]['module'], alias='r', department_column='r.department')
    extras = ''
    join = ''
    if resource == 'loans':
        extras = ",COALESCE((SELECT sum(p.amount_rials) FROM hrm_loan_payments p WHERE p.loan_id=r.id AND p.status='posted' AND p.deleted=0),0) paid_rials"
    elif resource == 'loan_payments':
        extras = ',l.title loan_title'; join = ' JOIN hrm_loans l ON l.id=r.loan_id'
    elif resource == 'welfare_uses':
        extras = ',s.name service_name'; join = ' JOIN hrm_welfare_services s ON s.id=r.service_id'
    elif resource == 'reservations':
        extras = ',p.name property_name'; join = ' JOIN hrm_properties p ON p.id=r.property_id'
    rows = [dict(row, is_deleted=row['deleted']) for row in conn.execute(f'SELECT r.*,e.name employee_name,e.code employee_code,c.name company_name,ss.name site_name{extras} FROM {table} r JOIN employees e ON e.id=r.employee_id JOIN companies c ON c.id=r.company_id LEFT JOIN hrm_sites ss ON ss.id=r.site_id{join} WHERE ({scope}) AND ({active}) ORDER BY r.id DESC', params)]
    today = datetime.now(timezone(timedelta(hours=3, minutes=30))).date().isoformat()
    for row in rows:
        if resource == 'loans':
            row['outstanding_rials'] = row['amount_rials'] - row['paid_rials']
            row['repayment_status'] = ('paid' if row['outstanding_rials'] == 0 else
                                       'overdue' if row['status'] in ('approved', 'disbursed') and row['due_date'] and row['due_date'] < today else
                                       'partial' if row['paid_rials'] else 'unpaid')
        elif resource == 'reservations':
            row['nights'] = (date.fromisoformat(row['end_date']) - date.fromisoformat(row['start_date'])).days
    return rows


def _archive(conn, resource, old):
    """Delete from active use while retaining the recoverable audited record."""
    status = None
    if resource == 'loans':
        paid = conn.execute("SELECT COALESCE(sum(amount_rials),0) FROM hrm_loan_payments WHERE loan_id=? AND status='posted' AND deleted=0", (old['id'],)).fetchone()[0]
        if paid or old['status'] == 'disbursed':
            raise HrmConflict('وام پرداخت‌شده یا دارای بازپرداخت حذف نمی‌شود؛ ابتدا سوابق مالی را بازبینی و در صورت اشتباه، صریحاً اصلاح یا ابطال کنید')
        status = 'cancelled'
    elif resource == 'loan_payments':
        status = 'voided'
    elif resource in ('welfare_uses', 'reservations'):
        status = 'cancelled'
    elif resource == 'properties':
        today = datetime.now(timezone(timedelta(hours=3, minutes=30))).date().isoformat()
        if conn.execute("SELECT 1 FROM hrm_reservations WHERE property_id=? AND status IN ('pending','approved') AND deleted=0 AND end_date>? LIMIT 1", (old['id'], today)).fetchone():
            raise HrmConflict('اقامتگاه دارای رزرو باز است؛ پیش از حذف، رزروهای ثبت‌شده را تعیین تکلیف کنید')
    changes = ['deleted=1', 'version=version+1', 'updated=CURRENT_TIMESTAMP']
    parameters = []
    if status:
        changes.append('status=?'); parameters.append(status)
    if resource in CATALOG_RESOURCES:
        changes.append('active=0')
    conn.execute(f'UPDATE {TABLES[resource]} SET {",".join(changes)} WHERE id=?', [*parameters, old['id']])


def register(app, db, authenticated, audit, error):
    with app.app_context():
        db().executescript(SCHEMA)
        # Local/older HRM databases gain unknown snapshots, never an invented
        # historic site inferred from a worker's assignment at upgrade time.
        for resource, table in TABLES.items():
            columns = {row['name'] for row in db().execute('PRAGMA table_info(' + table + ')')}
            if 'site_id' not in columns:
                db().execute(f'ALTER TABLE {table} ADD COLUMN site_id INTEGER REFERENCES hrm_sites(id)')
            if resource not in CATALOG_RESOURCES and 'department' not in columns:
                db().execute(f"ALTER TABLE {table} ADD COLUMN department TEXT NOT NULL DEFAULT ''")
            if 'deleted' not in columns:
                db().execute(f'ALTER TABLE {table} ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))')
        db().commit()
    app.extensions.setdefault('hrm_catalog', {}).update(CATALOG)

    def resource_routes(resource):
        module = CATALOG[resource]['module']

        @authenticated
        def collection():
            conn = db(); actor = require(conn, module, operation='create' if request.method == 'POST' else None)
            if request.method == 'POST':
                body = json_object()
                with begin_write(conn):
                    values = _validate(conn, resource, body)
                    rid = _save(conn, resource, values)
                    audit('hrm_' + resource + '_created', values['company_id'], str(rid))
                return jsonify(id=rid, version=1), 201
            cid = request.args.get('company_id')
            if cid is not None:
                cid = positive_id(cid)
                if cid not in actor['company_ids']:
                    raise PermissionError()
            sid = request.args.get('site_id')
            if sid not in (None, ''):
                sid = positive_id(sid)
                selected_site = conn.execute('SELECT company_id FROM hrm_sites WHERE id=?', (sid,)).fetchone()
                if selected_site is None:
                    raise ValueError('سایت یافت نشد')
                if not any(scope['company_id'] == selected_site['company_id'] and
                           (scope.get('site_id') is None or scope.get('site_id') == sid) for scope in actor['scopes']):
                    raise PermissionError()
                if cid is not None and selected_site['company_id'] != cid:
                    raise ValueError('سایت متعلق به شرکت انتخاب‌شده نیست')
            else:
                sid = None
            eid = request.args.get('employee_id')
            if eid is not None:
                eid = positive_id(eid)
            query = text_value(request.args.get('q', ''), max_length=240).casefold()
            # Only the explicit management list can request its scoped archive.
            # Reusing this listing inside reports/options must never reinsert
            # archived liabilities or cancelled reservations into active totals.
            include_deleted = (request.path == '/api/hrm/' + resource and
                               request.args.get('include_deleted') == '1')
            rows = []
            for row in _query_rows(conn, resource, include_deleted=include_deleted):
                if cid is not None and row['company_id'] != cid and not (resource in CATALOG_RESOURCES and row['shared']):
                    continue
                if sid is not None and row['site_id'] != sid and not (resource in CATALOG_RESOURCES and (row['shared'] or row['site_id'] is None)):
                    continue
                if eid is not None and row.get('employee_id') != eid:
                    continue
                try:
                    if resource in CATALOG_RESOURCES:
                        if not _catalog_visible(conn, row, module):
                            continue
                    else:
                        _visible_record(conn, resource, row)
                except PermissionError:
                    continue
                if query and query not in ' '.join(str(value) for value in row.values()).casefold():
                    continue
                rows.append(row)
            return jsonify(rows)

        @authenticated
        def detail(rid):
            conn = db(); operation = 'delete' if request.method == 'DELETE' else 'edit'
            require(conn, module, operation=operation); rid = positive_id(rid)
            body = json_object()
            with begin_write(conn):
                old = conn.execute(f'SELECT * FROM {TABLES[resource]} WHERE id=?', (rid,)).fetchone()
                if old is None or old['deleted']:
                    return error('سابقه یافت نشد', 404)
                _visible_record(conn, resource, old, operation=operation)
                check_version(body, old)
                if request.method == 'DELETE':
                    _archive(conn, resource, old)
                    audit('hrm_' + resource + '_deleted', old['company_id'], str(rid))
                    return jsonify(id=rid, version=old['version'] + 1, archived=True)
                values = _validate(conn, resource, body, dict(old))
                _save(conn, resource, values, old)
                audit('hrm_' + resource + '_updated', values['company_id'], str(rid))
            return jsonify(id=rid, version=old['version'] + 1)

        app.add_url_rule('/api/hrm/' + resource, 'hrm_benefits_' + resource,
                         collection, methods=['GET', 'POST'])
        app.add_url_rule('/api/hrm/' + resource + '/<int:rid>', 'hrm_benefits_' + resource + '_detail',
                         detail, methods=['PUT', 'DELETE'])

    for resource in CATALOG:
        resource_routes(resource)
