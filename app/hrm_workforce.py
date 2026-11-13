"""Attendance, performance and documented disciplinary history on existing people.

This module adds no legal leave entitlement: optional company policy is explicitly
configured by HR. Planned training participation remains unrelated to attendance.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from decimal import Decimal

from flask import Response, jsonify, request

from . import hrm_access as access
from .hrm_common import (HrmConflict, begin_write, check_version, date_value,
                         integer_value, jalali_display, text_value)
from .validation import json_object, number, positive_id

KINDS = [('work', 'حضور'), ('leave', 'مرخصی'), ('mission', 'مأموریت'),
         ('absence', 'غیبت'), ('late', 'تأخیر'), ('overtime', 'اضافه‌کاری')]
DISCIPLINE_KINDS = [('reward', 'تشویق'), ('reminder', 'تذکر'),
                    ('warning', 'اخطار'), ('reprimand', 'توبیخ')]
SCHEMA = '''
CREATE TABLE IF NOT EXISTS hrm_attendance(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',
 employee_id INTEGER NOT NULL REFERENCES employees(id),work_date TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('work','leave','mission','absence','late','overtime')),
 minutes INTEGER NOT NULL CHECK(minutes BETWEEN 0 AND 1440),note TEXT NOT NULL DEFAULT '',
 source TEXT NOT NULL DEFAULT 'manual',
 is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),deleted_at TEXT NOT NULL DEFAULT '',deleted_by INTEGER REFERENCES users(id),version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(employee_id,work_date,kind));
CREATE INDEX IF NOT EXISTS hrm_attendance_company_date ON hrm_attendance(company_id,work_date);
CREATE TABLE IF NOT EXISTS hrm_attendance_policies(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 year INTEGER NOT NULL CHECK(year BETWEEN 1300 AND 1499),
 title TEXT NOT NULL DEFAULT 'ضوابط حضور و مرخصی',
 annual_leave_minutes INTEGER CHECK(annual_leave_minutes IS NULL OR annual_leave_minutes>=0),
 max_leave_record_minutes INTEGER CHECK(max_leave_record_minutes IS NULL OR max_leave_record_minutes>0),
 note TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),deleted_at TEXT NOT NULL DEFAULT '',deleted_by INTEGER REFERENCES users(id),version INTEGER NOT NULL DEFAULT 1);
CREATE UNIQUE INDEX IF NOT EXISTS hrm_attendance_policy_scope_year ON hrm_attendance_policies(company_id,IFNULL(site_id,0),year);
CREATE TABLE IF NOT EXISTS hrm_evaluation_periods(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 title TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
 note TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),deleted_at TEXT NOT NULL DEFAULT '',deleted_by INTEGER REFERENCES users(id),version INTEGER NOT NULL DEFAULT 1,
 CHECK(end_date>=start_date));
CREATE UNIQUE INDEX IF NOT EXISTS hrm_period_scope_title ON hrm_evaluation_periods(company_id,IFNULL(site_id,0),title);
CREATE TABLE IF NOT EXISTS hrm_performance_criteria(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 period_id INTEGER NOT NULL REFERENCES hrm_evaluation_periods(id),
 title TEXT NOT NULL,weight REAL NOT NULL CHECK(weight>0),max_score REAL NOT NULL CHECK(max_score>0),
 note TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),deleted_at TEXT NOT NULL DEFAULT '',deleted_by INTEGER REFERENCES users(id),version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(period_id,title));
CREATE TABLE IF NOT EXISTS hrm_performance_scores(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',
 employee_id INTEGER NOT NULL REFERENCES employees(id),
 period_id INTEGER NOT NULL REFERENCES hrm_evaluation_periods(id),
 criterion_id INTEGER NOT NULL REFERENCES hrm_performance_criteria(id),
 score REAL NOT NULL CHECK(score>=0),note TEXT NOT NULL DEFAULT '',
 is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),deleted_at TEXT NOT NULL DEFAULT '',deleted_by INTEGER REFERENCES users(id),version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(employee_id,period_id,criterion_id));
CREATE INDEX IF NOT EXISTS hrm_performance_employee_period ON hrm_performance_scores(employee_id,period_id);
CREATE TABLE IF NOT EXISTS hrm_discipline(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',
 employee_id INTEGER NOT NULL REFERENCES employees(id),event_date TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('reward','reminder','warning','reprimand')),
 title TEXT NOT NULL,note TEXT NOT NULL DEFAULT '',
 document_id INTEGER REFERENCES hrm_documents(id),
 is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),deleted_at TEXT NOT NULL DEFAULT '',deleted_by INTEGER REFERENCES users(id),version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
'''
TABLES = ('hrm_attendance', 'hrm_attendance_policies', 'hrm_evaluation_periods',
          'hrm_performance_criteria', 'hrm_performance_scores', 'hrm_discipline')


def field(key, label, type='text', required=False, **extra):
    return dict(key=key, label=label, type=type, required=required, **extra)


EMPLOYEE = field('employee_id', 'پرسنل', 'employee', True)
COMPANY = field('company_id', 'شرکت', 'company', True)
SITE = field('site_id', 'سایت (خالی یعنی کل شرکت)', 'resource', resource='sites')
NOTE = field('note', 'توضیحات', 'textarea')
CATALOG = {
    'attendance': dict(title='حضور، غیاب و مرخصی', module='attendance', fields=[
        EMPLOYEE, field('work_date', 'تاریخ شمسی', 'date', True),
        field('kind', 'نوع ثبت', 'select', True, choices=KINDS),
        field('minutes', 'مدت (دقیقه)', 'number', True, minimum=0, maximum=1440), NOTE],
        columns=['employee_id', 'site_id', 'department', 'work_date', 'kind', 'minutes', 'note']),
    'attendance_policies': dict(title='ضوابط حضور و مرخصی شرکت', module='attendance', fields=[
        COMPANY, SITE, field('year', 'سال شمسی ضابطه', 'number', True, minimum=1300, maximum=1499), field('title', 'عنوان ضابطه', required=True),
        field('annual_leave_minutes', 'سهمیه سالانه مرخصی (دقیقه؛ خالی یعنی تعیین نشده)', 'number'),
        field('max_leave_record_minutes', 'حداکثر هر ثبت مرخصی (دقیقه؛ اختیاری)', 'number'), NOTE],
        columns=['company_id', 'site_id', 'year', 'title', 'annual_leave_minutes', 'max_leave_record_minutes', 'note']),
    'evaluation_periods': dict(title='دوره‌های ارزیابی عملکرد', module='performance', fields=[
        COMPANY, SITE, field('title', 'عنوان دوره', required=True),
        field('start_date', 'تاریخ شروع', 'date', True), field('end_date', 'تاریخ پایان', 'date', True),
        field('status', 'وضعیت', 'select', True, choices=[['open', 'باز'], ['closed', 'بسته']]), NOTE],
        columns=['company_id', 'site_id', 'title', 'start_date', 'end_date', 'status']),
    'performance_criteria': dict(title='شاخص‌های ارزیابی عملکرد', module='performance', fields=[
        COMPANY, SITE, field('period_id', 'دوره ارزیابی', 'resource', True, resource='evaluation_periods'),
        field('title', 'عنوان شاخص', required=True),
        field('weight', 'وزن شاخص', 'number', True, minimum=.000001),
        field('max_score', 'حداکثر امتیاز شاخص', 'number', True, minimum=.000001), NOTE],
        columns=['period_id', 'title', 'weight', 'max_score']),
    'performance_scores': dict(title='امتیاز و روند عملکرد فرد', module='performance', fields=[
        EMPLOYEE, field('period_id', 'دوره ارزیابی', 'resource', True, resource='evaluation_periods'),
        field('criterion_id', 'شاخص ارزیابی', 'resource', True, resource='performance_criteria'),
        field('score', 'امتیاز', 'number', True, minimum=0), NOTE],
        columns=['employee_id', 'site_id', 'department', 'period_id', 'criterion_id', 'score', 'note']),
    'discipline': dict(title='تشویق و سوابق انضباطی', module='discipline', fields=[
        EMPLOYEE, field('event_date', 'تاریخ رویداد', 'date', True),
        field('kind', 'نوع رویداد', 'select', True, choices=DISCIPLINE_KINDS),
        field('title', 'عنوان', required=True), NOTE,
        field('document_id', 'سند محرمانه مرتبط', 'resource', resource='documents',
              filters={'permission_group': 'discipline'})],
        columns=['employee_id', 'site_id', 'department', 'event_date', 'kind', 'title', 'document_id']),
}


def _person_connection(conn, employee_id, module, write=False, operation=None):
    person = access.employee(conn, positive_id(employee_id), module, write=write, operation=operation)
    return person


def _period(conn, pid, cid, *, open_only=False):
    row = conn.execute('SELECT * FROM hrm_evaluation_periods WHERE id=? AND company_id=? AND is_deleted=0',
                       (positive_id(pid), cid)).fetchone()
    if row is None:
        raise ValueError('دوره ارزیابی متعلق به این شرکت نیست')
    if open_only and row['status'] != 'open':
        raise HrmConflict('دوره ارزیابی بسته است؛ ابتدا آن را صریحاً باز کنید')
    return row


def _configuration_scope(conn, cid, module, site_id=None, operation='edit'):
    """A unit-scoped writer cannot change policy affecting the whole site."""
    access.company_scope(conn, cid, module, site_id=site_id, operation=operation)
    if not any(scope['company_id'] == cid and not scope['department']
               and (scope.get('site_id') is None or scope.get('site_id') == site_id)
               for scope in access.ctx(conn)['scopes']):
        raise PermissionError()


def _filter_company_scope(conn, cid, module, site_id=None, operation=None):
    """Validate a filter without treating it as a request for every unit's rows.

    The subsequent employee/record predicate remains the actual row boundary.
    """
    actor = access.require(conn, module, operation=operation)
    if cid not in actor['company_ids']:
        raise PermissionError()
    if site_id is not None:
        if not conn.execute('SELECT 1 FROM hrm_sites WHERE id=? AND company_id=?',(site_id,cid)).fetchone():
            raise ValueError('سایت متعلق به این شرکت نیست')
        if not any(scope['company_id'] == cid and scope.get('site_id') in (None, site_id) for scope in actor['scopes']):
            raise PermissionError()


def _configuration_read_sql(conn, module, alias='r'):
    # A company-wide definition applies to the authorized sites too. This is
    # deliberately limited to definitions without personal employee facts.
    actor = access.require(conn, module)
    clauses, values = [], []
    for scope in actor['scopes']:
        clause = alias + '.company_id=?'
        values.append(scope['company_id'])
        if scope.get('site_id') is not None:
            clause += ' AND (' + alias + '.site_id IS NULL OR ' + alias + '.site_id=?)'
            values.append(scope['site_id'])
        clauses.append('(' + clause + ')')
    return ' OR '.join(clauses) or '0', values


def _row_access(conn, resource, row, write=False, operation=None):
    module = CATALOG[resource]['module']
    if 'employee_id' in row.keys():
        access.record_scope(conn, row, module, write=write, operation=operation)
    elif write or operation in ('create','edit','delete'):
        _configuration_scope(conn, row['company_id'], module, row['site_id'], operation=operation or 'edit')
    else:
        actor = access.require(conn, module)
        if not any(scope['company_id'] == row['company_id'] and
                   (scope.get('site_id') is None or row['site_id'] is None or scope.get('site_id') == row['site_id'])
                   for scope in actor['scopes']):
            raise PermissionError()


def _filter_dates(args):
    filters = []
    for key, operator in (('from', '>='), ('to', '<=')):
        if args.get(key):
            filters.append(('work_date', operator, date_value(args[key], required=True)))
    if len(filters) == 2 and filters[0][2] > filters[1][2]:
        raise ValueError('بازه تاریخ معتبر نیست')
    return filters


def _list_rows(conn, resource, args):
    module = CATALOG[resource]['module']
    access.require(conn, module)
    table = 'hrm_' + resource
    clauses, params = [], []
    if str(args.get('include_deleted','0')) != '1':
        clauses.append('r.is_deleted=0')
    facts = resource in ('attendance', 'performance_scores', 'discipline')
    scope, scope_args = (access.record_scope_sql(conn, module, alias='r', department_column='r.department')
                         if facts else _configuration_read_sql(conn, module))
    if facts:
        sql = ('SELECT r.*,e.name employee_name,e.code employee_code FROM ' + table +
               ' r JOIN employees e ON e.id=r.employee_id')
    else:
        sql = 'SELECT r.* FROM ' + table + ' r'
    clauses.append(scope)
    params.extend(scope_args)
    for key in ('company_id', 'site_id', 'employee_id', 'period_id'):
        if args.get(key) and (key in ('company_id', 'site_id') or any(f['key'] == key for f in CATALOG[resource]['fields'])):
            value = positive_id(args[key])
            if key == 'company_id':
                _filter_company_scope(conn, value, module, site_id=positive_id(args['site_id']) if args.get('site_id') else None)
            clauses.append('(r.site_id=? OR r.site_id IS NULL)' if key == 'site_id' and not facts else 'r.' + key + '=?')
            params.append(value)
    if args.get('department') and facts:
        clauses.append('r.department=?')
        params.append(text_value(args['department'], required=True, max_length=300))
    if resource == 'attendance':
        for key, operator, value in _filter_dates(args):
            clauses.append('r.' + key + operator + '?')
            params.append(value)
    if clauses:
        sql += ' WHERE ' + ' AND '.join('(' + part + ')' for part in clauses)
    sql += ' ORDER BY r.id DESC'
    result = []
    for row in conn.execute(sql, params):
        try:
            _row_access(conn, resource, row)
        except PermissionError:
            continue
        result.append(dict(row))
    return result


def _validated(conn, resource, body, previous=None, operation=None):
    fields = {item['key']: item for item in CATALOG[resource]['fields']}
    if previous is not None and previous['is_deleted']:
        raise HrmConflict('این سابقه بایگانی شده است؛ ابتدا آن را با شماره نسخه بازیابی کنید')
    if set(body) - set(fields) - {'version'}:
        raise ValueError('فیلد ناشناخته در اطلاعات ارسال شده وجود دارد')
    values = {key: previous[key] for key in fields if previous is not None and key in previous.keys()}
    values.update({key: value for key, value in body.items() if key in fields})
    module = CATALOG[resource]['module']
    operation = operation or ('edit' if previous else 'create')
    if 'employee_id' in fields:
        employee_id = positive_id(values.get('employee_id'))
        if previous:
            _row_access(conn, resource, previous, write=True, operation=operation)
            cid, site_id, department = previous['company_id'], previous['site_id'], previous['department']
        else:
            person = _person_connection(conn, employee_id, module, write=True, operation=operation)
            cid, site_id, department = person['company_id'], person.get('site_id'), person['department']
        values['employee_id'] = employee_id
        values['company_id'], values['site_id'], values['department'] = cid, site_id, department
    else:
        cid = positive_id(values.get('company_id'))
        site_id = None if values.get('site_id') in (None, '') else positive_id(values['site_id'])
        _configuration_scope(conn, cid, module, site_id, operation=operation)
        values['company_id'], values['site_id'] = cid, site_id
    if previous:
        if previous['company_id'] != cid or previous['site_id'] != site_id or ('employee_id' in previous.keys() and previous['employee_id'] != values['employee_id']):
            raise ValueError('شرکت یا پرسنل رکورد ثبت‌شده قابل جایگزینی نیست')
    for key, item in fields.items():
        if key in ('company_id', 'site_id', 'employee_id'):
            continue
        value = values.get(key)
        if item['type'] == 'date':
            values[key] = date_value(value, required=item['required'])
        elif item['type'] in ('text', 'textarea'):
            values[key] = text_value(value, label=item['label'], required=item['required'],
                                     max_length=4000 if item['type'] == 'textarea' else 300)
        elif item['type'] == 'select':
            if value not in {option[0] for option in item['choices']}:
                raise ValueError('گزینه «' + item['label'] + '» معتبر نیست')
            values[key] = value
        elif key == 'year':
            values[key] = integer_value(value, label=item['label'], minimum=1300, maximum=1499)
        elif key == 'minutes':
            values[key] = integer_value(value, label=item['label'], minimum=0, maximum=1440)
        elif key in ('annual_leave_minutes', 'max_leave_record_minutes'):
            values[key] = None if value in (None, '') else integer_value(value, label=item['label'], minimum=1 if key.startswith('max_') else 0, maximum=100_000_000)
        elif item['type'] == 'number':
            values[key] = number(value, minimum=0, maximum=1_000_000, positive=key in ('weight', 'max_score'))
        elif item['type'] == 'resource':
            values[key] = None if value in (None, '') and not item['required'] else positive_id(value)
    if resource == 'attendance' and values['kind'] == 'leave':
        year = int(jalali_display(values['work_date']).split('/')[0])
        policy = conn.execute('SELECT max_leave_record_minutes FROM hrm_attendance_policies WHERE company_id=? AND year=? AND is_deleted=0 AND (site_id=? OR site_id IS NULL) ORDER BY site_id IS NOT NULL DESC LIMIT 1', (cid, year, site_id)).fetchone()
        if policy and policy['max_leave_record_minutes'] is not None and values['minutes'] > policy['max_leave_record_minutes']:
            raise ValueError('مدت مرخصی از سقف هر ثبت در ضابطه شرکت بیشتر است')
    if resource == 'evaluation_periods':
        if values['end_date'] < values['start_date']:
            raise ValueError('تاریخ پایان دوره پیش از شروع آن است')
        if previous and (values['start_date'], values['end_date']) != (previous['start_date'], previous['end_date']):
            if conn.execute('SELECT 1 FROM hrm_performance_scores WHERE period_id=? LIMIT 1', (previous['id'],)).fetchone():
                raise HrmConflict('بازه دوره دارای امتیاز قابل تغییر نیست')
    if resource == 'performance_criteria':
        term = _period(conn, values['period_id'], cid, open_only=True)
        if term['site_id'] != site_id:
            raise ValueError('سایت شاخص باید با سایت دوره یکسان باشد')
        if previous:
            _period(conn, previous['period_id'], previous['company_id'], open_only=True)
        if previous and any(values[key] != previous[key] for key in ('period_id', 'weight', 'max_score')):
            if conn.execute('SELECT 1 FROM hrm_performance_scores WHERE criterion_id=? LIMIT 1', (previous['id'],)).fetchone():
                raise HrmConflict('وزن، سقف امتیاز یا دوره شاخص دارای ارزیابی قابل تغییر نیست')
    if resource == 'performance_scores':
        term = _period(conn, values['period_id'], cid, open_only=True)
        if term['site_id'] is not None and term['site_id'] != site_id:
            raise ValueError('دوره ارزیابی متعلق به سایت دیگری است')
        if previous:
            _period(conn, previous['period_id'], previous['company_id'], open_only=True)
        criterion = conn.execute('SELECT * FROM hrm_performance_criteria WHERE id=? AND period_id=? AND company_id=? AND is_deleted=0',
                                 (values['criterion_id'], values['period_id'], cid)).fetchone()
        if criterion is None:
            raise ValueError('شاخص انتخاب‌شده متعلق به دوره و شرکت فرد نیست')
        if values['score'] > criterion['max_score']:
            raise ValueError('امتیاز از سقف شاخص بیشتر است')
    if resource == 'discipline' and values.get('document_id'):
        document = conn.execute('SELECT * FROM hrm_documents WHERE id=?', (values['document_id'],)).fetchone()
        if document is None or document['company_id'] != cid or document['site_id'] != site_id or document['employee_id'] != values['employee_id'] or document['permission_group'] != 'discipline':
            raise ValueError('سند باید در گروه محرمانه انضباطی همین فرد و شرکت باشد')
    return values


def _report(conn, args):
    args = {key:value for key,value in args.items() if key != 'include_deleted'}
    result = {}
    try:
        records = _list_rows(conn, 'attendance', args)
    except PermissionError:
        records = None
    if records is not None:
        people = {}
        for row in records:
            group_key = (row['employee_id'], row['company_id'], row['site_id'], row['department'])
            item = people.setdefault(group_key, dict(employee_id=row['employee_id'], company_id=row['company_id'],site_id=row['site_id'],name=row['employee_name'], code=row['employee_code'], department=row['department'], total_records=0, **{kind + '_minutes': 0 for kind, _ in KINDS}))
            item[row['kind'] + '_minutes'] += row['minutes']
            item['total_records'] += 1
        result['attendance'] = list(people.values())
        # Annual balances always use the complete recorded year. A narrowed
        # screen filter must not turn a part-year subtotal into a false balance.
        annual = {}
        for row in _list_rows(conn, 'attendance', {key: value for key, value in args.items() if key not in ('from', 'to')}):
            if row['kind'] != 'leave':
                continue
            year = int(jalali_display(row['work_date']).split('/')[0])
            key = (row['employee_id'], row['company_id'], row['site_id'], year)
            annual[key] = annual.get(key, 0) + row['minutes']
        balances = []
        from_year = int(jalali_display(date_value(args['from'], required=True)).split('/')[0]) if args.get('from') else 1300
        to_year = int(jalali_display(date_value(args['to'], required=True)).split('/')[0]) if args.get('to') else 1499
        scope, scope_args = access.scope_sql(conn, 'attendance', alias='e')
        roster = [access.employee(conn, row['id'], 'attendance') for row in conn.execute('SELECT e.id FROM employees e WHERE ' + scope, scope_args)]
        policies = _list_rows(conn, 'attendance_policies', args)
        for person in roster:
            cid, site_id = person['company_id'], person.get('site_id')
            if args.get('site_id') and site_id != positive_id(args['site_id']):
                continue
            if args.get('department') and person['department'] != args['department']:
                continue
            selected = {}
            for policy in policies:
                if policy['company_id'] != cid or policy['site_id'] not in (None, site_id) or not from_year <= policy['year'] <= to_year:
                    continue
                old = selected.get(policy['year'])
                if old is None or policy['site_id'] is not None:
                    selected[policy['year']] = policy
            for year, policy in selected.items():
                if policy['annual_leave_minutes'] is None:
                    continue
                used = sum(minutes for (eid, company, record_site, record_year), minutes in annual.items()
                           if eid == person['id'] and company == cid and record_year == year
                           and (policy['site_id'] is None or record_site == policy['site_id']))
                balances.append(dict(employee_id=person['id'], company_id=cid, site_id=site_id,
                                     department=person['department'],name=person['name'], code=person['code'],
                                     year=year,policy_scope='site' if policy['site_id'] is not None else 'company',
                                     annual_leave_minutes=policy['annual_leave_minutes'],
                                     recorded_leave_minutes=used, remaining_minutes=policy['annual_leave_minutes']-used))
        result['leave_balances'] = balances
    try:
        scores = _list_rows(conn, 'performance_scores', args)
    except PermissionError:
        scores = None
    if scores is not None:
        people = {}
        for row in scores:
            key = (row['employee_id'], row['period_id'],row['site_id'],row['department'])
            period = _period(conn, row['period_id'], row['company_id'])
            if args.get('from') and period['end_date'] < date_value(args['from'], required=True):
                continue
            if args.get('to') and period['start_date'] > date_value(args['to'], required=True):
                continue
            item = people.setdefault(key, dict(employee_id=row['employee_id'], name=row['employee_name'], code=row['employee_code'],company_id=row['company_id'],site_id=row['site_id'],department=row['department'], period_id=row['period_id'], period_title=period['title'], start_date=period['start_date'], end_date=period['end_date'], criteria_count=0, _weighted=Decimal(0), _weight=Decimal(0)))
            criterion = conn.execute('SELECT weight,max_score FROM hrm_performance_criteria WHERE id=?', (row['criterion_id'],)).fetchone()
            weight = Decimal(str(criterion['weight']))
            item['_weighted'] += Decimal(str(row['score'])) / Decimal(str(criterion['max_score'])) * weight * 100
            item['_weight'] += weight
            item['criteria_count'] += 1
        for item in people.values():
            expected = conn.execute('SELECT count(*),COALESCE(sum(weight),0) FROM hrm_performance_criteria WHERE period_id=? AND is_deleted=0', (item['period_id'],)).fetchone()
            item['expected_criteria_count'] = expected[0]
            item['is_complete'] = item['criteria_count'] == expected[0]
            item['completion_percent'] = round(float(item['_weight']) / expected[1] * 100, 2)
            item['score_basis'] = 'recorded_criteria_only'
            item['score_percent'] = float(round(item.pop('_weighted') / item.pop('_weight'), 2))
        result['performance'] = sorted(people.values(), key=lambda item: (item['start_date'], item['employee_id']))
    if not result:
        raise PermissionError()
    return result


def _reject_archived_natural_key(conn,resource,values):
    keys={'attendance':('employee_id','work_date','kind'),'attendance_policies':('company_id','site_id','year'),
          'evaluation_periods':('company_id','site_id','title'),'performance_criteria':('period_id','title'),
          'performance_scores':('employee_id','period_id','criterion_id')}.get(resource)
    if not keys:return
    old=conn.execute('SELECT id FROM hrm_'+resource+' WHERE is_deleted=1 AND '+ ' AND '.join(key+' IS ?' for key in keys),[values[key] for key in keys]).fetchone()
    if old:raise HrmConflict('این کلید به سابقه بایگانی‌شده تعلق دارد؛ سابقه '+str(old['id'])+' را با شماره نسخه بازیابی کنید')


def _check_archive_dependencies(conn,resource,row):
    if resource=='performance_scores':
        _period(conn,row['period_id'],row['company_id'],open_only=True)
    if resource=='performance_criteria':
        _period(conn,row['period_id'],row['company_id'],open_only=True)
        if conn.execute('SELECT 1 FROM hrm_performance_scores WHERE period_id=? LIMIT 1',(row['period_id'],)).fetchone():
            raise HrmConflict('شاخص دوره دارای سابقه امتیاز بایگانی نمی‌شود تا نتایج گذشته تغییر نکند')
    if resource=='evaluation_periods':
        if conn.execute('SELECT 1 FROM hrm_performance_scores WHERE period_id=? LIMIT 1',(row['id'],)).fetchone() or conn.execute('SELECT 1 FROM hrm_performance_criteria WHERE period_id=? AND is_deleted=0 LIMIT 1',(row['id'],)).fetchone():
            raise HrmConflict('دوره دارای شاخص فعال یا سابقه امتیاز بایگانی نمی‌شود')
    if resource=='attendance_policies':
        rows=conn.execute("SELECT work_date FROM hrm_attendance WHERE company_id=? AND kind='leave' AND (? IS NULL OR site_id=?)",(row['company_id'],row['site_id'],row['site_id']))
        if any(int(jalali_display(item['work_date']).split('/')[0])==row['year'] for item in rows):
            raise HrmConflict('ضابطه سال دارای سابقه مرخصی بایگانی نمی‌شود؛ اصلاح نسخه ثبت کنید')


def _attendance_form():
    from .excel_forms import column, schema
    return schema('hrm-attendance', 'حضور و غیاب منابع انسانی', [
        column('employee_code', 'کد پرسنلی', True, reference='employees'),
        column('work_date', 'تاریخ شمسی', True, 'date'),
        column('kind', 'نوع ثبت', True, choices=KINDS),
        column('minutes', 'مدت (دقیقه)', True, 'integer'), column('note', 'توضیحات')],
        ['employee_code', 'work_date', 'kind'], description='یک ردیف روزانه برای هر فرد و نوع ثبت. اعداد بر حسب دقیقه‌اند؛ صفر اول کد پرسنلی را نگه دارید. پیش‌نمایش هیچ اطلاعاتی ثبت نمی‌کند.')


def register(app, db, authenticated, audit, error):
    with app.app_context():
        for table in TABLES:
            columns = {row['name'] for row in db().execute('PRAGMA table_info(' + table + ')')}
            if not columns:
                continue
            for name, definition in [('is_deleted','INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1))'),('deleted_at',"TEXT NOT NULL DEFAULT ''"),('deleted_by','INTEGER REFERENCES users(id)')]:
                if name not in columns:
                    db().execute('ALTER TABLE ' + table + ' ADD COLUMN ' + name + ' ' + definition)
            if 'site_id' not in columns:
                db().execute('ALTER TABLE ' + table + ' ADD COLUMN site_id INTEGER REFERENCES hrm_sites(id)')
            if table in ('hrm_attendance','hrm_performance_scores','hrm_discipline') and 'department' not in columns:
                db().execute("ALTER TABLE " + table + " ADD COLUMN department TEXT NOT NULL DEFAULT ''")
        db().executescript(SCHEMA)
        db().commit()
    app.extensions.setdefault('hrm_catalog', {}).update(CATALOG)

    def collection(resource):
        module = CATALOG[resource]['module']
        access.require(db(), module) if request.method == 'GET' else access.require(db(), module, operation='create')
        if request.method == 'GET':
            return jsonify(_list_rows(db(), resource, request.args))
        body = json_object()
        with begin_write(db()):
            values = _validated(db(), resource, body)
            _reject_archived_natural_key(db(),resource,values)
            keys = list(values)
            record = db().execute('INSERT INTO hrm_' + resource + '(' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', [values[key] for key in keys])
            audit('hrm_create_' + resource, values['company_id'], str(record.lastrowid))
            row = dict(db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (record.lastrowid,)).fetchone())
        return jsonify(row), 201

    def item(resource, record_id):
        record_id = positive_id(record_id)
        body = json_object() if request.method != 'GET' else None
        with begin_write(db()) if request.method != 'GET' else nullcontext():
            row = db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (record_id,)).fetchone()
            if row is None or (row['is_deleted'] and str(request.args.get('include_deleted','0')) != '1'):
                return error('رکورد یافت نشد', 404)
            _row_access(db(), resource, row, write=request.method != 'GET', operation={'GET':'read','PUT':'edit','DELETE':'delete'}[request.method])
            if request.method == 'GET':
                return jsonify(dict(row))
            check_version(body, row)
            if request.method == 'DELETE':
                if row['is_deleted']:
                    raise HrmConflict('این سابقه پیش‌تر بایگانی شده است')
                _check_archive_dependencies(db(), resource, row)
                db().execute("UPDATE hrm_" + resource + " SET is_deleted=1,deleted_at=CURRENT_TIMESTAMP,deleted_by=?,version=version+1 WHERE id=?", (access.ctx(db())['user_id'],record_id))
                audit('hrm_archive_' + resource, row['company_id'], str(record_id))
                return jsonify(ok=True,id=record_id,is_deleted=1,version=row['version']+1)
            values = _validated(db(), resource, body, row)
            keys = list(values)
            db().execute('UPDATE hrm_' + resource + ' SET ' + ','.join(key + '=?' for key in keys) + ',version=version+1 WHERE id=?', [values[key] for key in keys] + [record_id])
            audit('hrm_update_' + resource, row['company_id'], str(record_id))
            output = dict(db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (record_id,)).fetchone())
        return jsonify(output)

    def restore_item(resource,record_id):
        body=json_object()
        if set(body)!={'version'}:
            raise ValueError('فقط نسخه سابقه را برای بازیابی ارسال کنید')
        with begin_write(db()):
            row=db().execute('SELECT * FROM hrm_'+resource+' WHERE id=?',(positive_id(record_id),)).fetchone()
            if row is None:return error('سابقه یافت نشد',404)
            _row_access(db(),resource,row,write=True,operation='edit')
            check_version(body,row)
            if not row['is_deleted']:raise HrmConflict('این سابقه فعال است')
            if resource in ('performance_scores','performance_criteria'):
                _period(db(),row['period_id'],row['company_id'],open_only=True)
            if resource=='performance_scores':
                criterion=db().execute('SELECT is_deleted FROM hrm_performance_criteria WHERE id=?',(row['criterion_id'],)).fetchone()
                if not criterion or criterion['is_deleted']:raise HrmConflict('ابتدا شاخص مرجع را بازیابی کنید')
            db().execute("UPDATE hrm_"+resource+" SET is_deleted=0,deleted_at='',deleted_by=NULL,version=version+1 WHERE id=?",(record_id,))
            audit('hrm_restore_'+resource,row['company_id'],str(record_id))
            output=dict(db().execute('SELECT * FROM hrm_'+resource+' WHERE id=?',(record_id,)).fetchone())
        return jsonify(output)

    for resource in CATALOG:
        CATALOG[resource]['deletable']=True
        CATALOG[resource]['restorable']=True
        app.add_url_rule('/api/hrm/'+resource+'/<int:record_id>/restore','hrm_workforce_'+resource+'_restore',authenticated(lambda record_id,resource=resource:restore_item(resource,record_id)),methods=['POST'])
        app.add_url_rule('/api/hrm/' + resource, 'hrm_workforce_' + resource, authenticated(lambda resource=resource: collection(resource)), methods=['GET', 'POST'])
        app.add_url_rule('/api/hrm/' + resource + '/<int:record_id>', 'hrm_workforce_' + resource + '_item', authenticated(lambda record_id, resource=resource: item(resource, record_id)), methods=['GET', 'PUT', 'DELETE'])

    @app.get('/api/hrm/workforce_report')
    @authenticated
    def hrm_workforce_report():
        return jsonify(_report(db(), request.args))

    @app.get('/api/hrm/attendance/template.xlsx')
    @authenticated
    def hrm_attendance_template():
        from .excel_workbooks import build_template
        cid = positive_id(request.args.get('company_id'))
        _filter_company_scope(db(), cid, 'attendance', site_id=positive_id(request.args['site_id']) if request.args.get('site_id') else None, operation='export')
        company = dict(db().execute('SELECT id,name FROM companies WHERE id=?', (cid,)).fetchone())
        scope, params = access.scope_sql(db(), 'attendance', alias='e', operation='export')
        people = []
        # The reference list is filtered with the same effective-assignment scope
        # as HRM reads; an Excel download never includes another unit's people.
        for person in db().execute('SELECT e.id,e.code,e.name FROM employees e WHERE ' + scope, params):
            effective = access.employee(db(), person['id'], 'attendance', operation='export')
            if effective['company_id'] == cid and (not request.args.get('site_id') or effective.get('site_id') == positive_id(request.args['site_id'])):
                people.append(dict(code=person['code'], name=person['name'], job=effective['job']))
        brand = db().execute("SELECT content FROM branding WHERE key='holding'").fetchone()
        payload = build_template(_attendance_form(), company, {'employees': people}, brand['content'] if brand else None)
        return Response(payload, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': 'attachment; filename=Nikan-HRM-Attendance.xlsx'})

    @app.post('/api/hrm/attendance/import')
    @authenticated
    def hrm_attendance_import():
        from .excel_forms import read_rows
        cid = positive_id(request.args.get('company_id'))
        _filter_company_scope(db(), cid, 'attendance', site_id=positive_id(request.args['site_id']) if request.args.get('site_id') else None, operation='create')
        mode, policy = request.form.get('mode'), request.form.get('policy', 'insert')
        if mode not in ('preview', 'commit') or policy not in ('insert', 'upsert'):
            raise ValueError('نوع بررسی و سیاست ورود معتبر نیست')
        if policy == 'upsert':
            access.require(db(), 'attendance', operation='edit')
        file = request.files.get('file')
        if file is None:
            raise ValueError('فایل XLSX را انتخاب کنید')
        rows = read_rows(file.read(), _attendance_form(), cid)
        connection = db()
        report = dict(added=0, updated=0, unchanged=0, total=len(rows), errors=[])
        # Resolve natural keys only inside the permitted current-company roster.
        with begin_write(connection):
            connection.execute('SAVEPOINT hrm_attendance_import')
            seen = set()
            for row_number, raw, problem in rows:
                try:
                    if problem:
                        raise ValueError(problem)
                    code = text_value(raw.get('employee_code'), required=True, max_length=128)
                    scope, params = access.scope_sql(connection, 'attendance', alias='e', operation='create')
                    employees = []
                    for person in connection.execute('SELECT e.id,e.code FROM employees e WHERE e.code=? AND (' + scope + ')', [code] + list(params)):
                        effective = access.employee(connection, person['id'], 'attendance', operation='create')
                        if effective['company_id'] == cid and (not request.args.get('site_id') or effective.get('site_id') == positive_id(request.args['site_id'])):
                            employees.append(person['id'])
                    if len(employees) != 1:
                        raise ValueError('کد پرسنلی در شرکت و محدوده مجاز یافت نشد یا پس از انتقال نیازمند رفع تعارض است')
                    values = _validated(connection, 'attendance', dict(employee_id=employees[0], **{key: raw[key] for key in ('work_date', 'kind', 'minutes', 'note') if key in raw}))
                    key = (values['employee_id'], values['work_date'], values['kind'])
                    if key in seen:
                        raise ValueError('ترکیب پرسنل، تاریخ و نوع ثبت در فایل تکراری است')
                    seen.add(key)
                    previous = connection.execute('SELECT * FROM hrm_attendance WHERE employee_id=? AND work_date=? AND kind=?', key).fetchone()
                    if previous:
                        if previous['is_deleted']:
                            raise HrmConflict('این کلید به سابقه بایگانی‌شده تعلق دارد؛ ابتدا آن سابقه را صریحاً بازیابی کنید')
                        _row_access(connection, 'attendance', previous, operation='edit' if policy == 'upsert' else 'create')
                        values = _validated(connection,'attendance',dict(employee_id=previous['employee_id'], **{key: raw[key] for key in ('work_date','kind','minutes','note') if key in raw}),previous,operation='edit' if policy=='upsert' else 'create')
                        if values['company_id'] != cid or (request.args.get('site_id') and values['site_id'] != positive_id(request.args['site_id'])):
                            raise HrmConflict('این سابقه متعلق به محدوده تاریخی دیگری است و از این فایل جابه‌جا نمی‌شود')
                        # Blank optional cells preserve existing values, as in
                        # the existing training importer.
                        if 'note' not in raw:
                            values['note'] = previous['note']
                        changed = any(previous[key] != value for key, value in values.items())
                        if not changed:
                            report['unchanged'] += 1
                        elif policy == 'insert':
                            raise ValueError('رکورد موجود متفاوت است؛ برای اصلاح، سیاست به‌روزرسانی را صریحاً انتخاب کنید')
                        else:
                            keys = list(values)
                            connection.execute('UPDATE hrm_attendance SET ' + ','.join(key + '=?' for key in keys) + ',version=version+1 WHERE id=?', [values[key] for key in keys] + [previous['id']])
                            report['updated'] += 1
                    else:
                        keys = list(values)
                        connection.execute('INSERT INTO hrm_attendance(' + ','.join(keys) + ",source) VALUES (" + ','.join('?' for _ in keys) + ",'excel')", [values[key] for key in keys])
                        report['added'] += 1
                except (ValueError, PermissionError) as exc:
                    report['errors'].append(dict(row=row_number, message=str(exc) or 'دسترسی به اطلاعات این ردیف مجاز نیست'))
            report['can_commit'] = not report['errors']
            report['applied'] = mode == 'commit' and report['can_commit']
            if not report['applied']:
                connection.execute('ROLLBACK TO hrm_attendance_import')
            else:
                audit('hrm_import_attendance', cid, json.dumps({key: report[key] for key in ('added', 'updated', 'unchanged', 'total')}))
            connection.execute('RELEASE hrm_attendance_import')
        return jsonify(report)

