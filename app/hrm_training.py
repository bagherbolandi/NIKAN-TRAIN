"""A site-scoped management facade over the existing training tables.

Courses, grades, assessments, competency selections and actual attendance keep
their canonical IDs. Only scope/date/version metadata and configurable general
training policies are additive. No historical personnel or grade is rewritten
when this module is initialized.
"""
from datetime import date, timedelta
import json
import math

from flask import jsonify, request

from . import hrm_access as access
from .catalog import attach
from .hrm_common import (HrmConflict, begin_write, check_version, date_value,
                         integer_value, jalali_display, text_value, today_iso)
from .validation import json_object, number, positive_id, jalali_date


SCHEMA = '''
CREATE TABLE IF NOT EXISTS hrm_training_course_context(
 course_id INTEGER PRIMARY KEY REFERENCES courses(id),
 company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '',date_iso TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)));
CREATE TABLE IF NOT EXISTS hrm_training_record_context(
 record_id INTEGER PRIMARY KEY REFERENCES records(id),
 company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '',date_iso TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)));
CREATE TABLE IF NOT EXISTS hrm_training_assessment_context(
 assessment_id INTEGER PRIMARY KEY REFERENCES assessments(id),
 company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '',date_iso TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),
 history_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(history_json) AND json_type(history_json)='array'));
CREATE TABLE IF NOT EXISTS hrm_training_requirements(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 competency_code TEXT NOT NULL REFERENCES holding_competencies(code),required_level INTEGER NOT NULL CHECK(required_level BETWEEN 1 AND 4),
 renewal_days INTEGER CHECK(renewal_days IS NULL OR renewal_days>0),
 onboarding INTEGER NOT NULL DEFAULT 0 CHECK(onboarding IN (0,1)),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)));
CREATE INDEX IF NOT EXISTS hrm_training_requirements_scope ON hrm_training_requirements(company_id,site_id,job);
'''


def field(key, label, type='text', **values):
    return dict(key=key, label=label, type=type, **values)


COMPANY = field('company_id', 'شرکت مسئول', 'company', required=True, immutable=True)
SITE = field('site_id', 'سایت اجرا / مسئول (خالی: سطح شرکت)', 'resource', resource='sites', immutable=True)
PERSON = field('employee_id', 'فرد', 'employee', required=True, immutable=True)
BANK = field('competency_code', 'انتخاب از بانک شایستگی هلدینگ', 'resource', resource='training_competency_options', value_key='code', label_key='title', required=True)
LEVEL = field('level', 'سطح شایستگی (۰ تا ۴)', 'number', required=True, min=0, max=4)
NOTES = field('notes', 'توضیحات ضابطه سازمان', 'textarea')
ACTIVE = [[1, 'فعال'], [0, 'غیرفعال']]
CATALOG = {
    'training_courses': dict(title='مدیریت دوره‌های آموزشی سایت', module='training', fields=[
        COMPANY, SITE, field('department', 'واحد اجرا (خالی: همه واحدها)'), BANK,
        field('title', 'عنوان دوره', required=True), field('date', 'تاریخ دوره', 'date', required=True),
        field('instructor', 'مدرس'), field('hours', 'مدت (ساعت)', 'number', required=True, min=0.01, max=1000000),
        field('cost', 'هزینه کل (میلیون ریال)', 'number', min=0, max=9007199254),
        field('status', 'وضعیت', 'select', choices=[['planned', 'برنامه‌ریزی‌شده'], ['approved', 'مصوب'], ['completed', 'برگزارشده'], ['cancelled', 'لغوشده']]),
        field('calendar_scope', 'تقویم', 'select', choices=[['internal', 'داخلی شرکت / سایت'], ['holding', 'تجمیعی هلدینگ؛ فقط ادمین اصلی']]),
    ], columns=['title', 'company_name', 'site_name', 'department', 'competency_title', 'date', 'hours', 'cost', 'status']),
    'training_records': dict(title='سوابق، اثربخشی و حضور واقعی آموزش', module='training', fields=[
        PERSON, field('course_id', 'دوره موجود', 'resource', resource='training_courses', required=True, immutable=True),
        LEVEL, field('record_date', 'تاریخ سابقه (خالی: تاریخ دوره)', 'date'),
        field('score', 'نمره آزمون (۰ تا ۱۰۰)', 'number', min=0, max=100),
        field('reaction', 'رضایت (۱ تا ۵)', 'number', min=1, max=5),
        field('behavior', 'نظر مدیر (۱ تا ۵)', 'number', min=1, max=5),
        field('result', 'تحقق هدف (درصد)', 'number', min=0, max=100),
        field('attended', 'حضور واقعی (خالی: حضور تغییر نمی‌کند)', 'select', choices=[[1, 'حضور واقعی تأیید می‌شود'], [0, 'حضور واقعی برداشته می‌شود']]),
    ], columns=['employee_name', 'title', 'site_name', 'department', 'record_date', 'level', 'score', 'attended', 'status']),
    'training_assessments': dict(title='ارزیابی فعلی شایستگی کارکنان', module='training', fields=[
        PERSON, BANK, LEVEL, field('assessment_date', 'تاریخ ارزیابی', 'date', required=True),
    ], columns=['employee_name', 'competency_title', 'site_name', 'department', 'assessment_date', 'level']),
    'training_assessment_history': dict(title='تاریخچه نسخه‌های ارزیابی شایستگی', module='training', read_only=True, fields=[],
        columns=['employee_name', 'competency_title', 'company_name', 'site_name', 'department', 'assessment_date', 'level', 'version', 'change_kind']),
    'training_requirements': dict(title='الزامات عمومی، بدو استخدام و تجدید آموزش', module='training', fields=[
        COMPANY, SITE, field('department', 'واحد (خالی: همه واحدها)'), field('job', 'شغل (خالی: همه شغل‌ها)'),
        field('activity_location', 'محل فعالیت (خالی: همه محل‌ها)'), BANK,
        field('required_level', 'سطح لازم (۱ تا ۴)', 'number', required=True, min=1, max=4),
        field('renewal_days', 'فاصله تجدید مصوب به روز (خالی: ضابطه ثبت نشده)', 'number', min=1, max=1000000),
        field('onboarding', 'الزام آموزش پس از شروع همکاری', 'select', choices=[[0, 'خیر'], [1, 'بله']]),
        field('active', 'وضعیت', 'select', choices=ACTIVE, default=1), NOTES,
    ], columns=['company_name', 'site_name', 'department', 'job', 'activity_location', 'competency_title', 'required_level', 'renewal_days', 'onboarding', 'active'],
        help='این ضوابط عمومی سازمان‌اند؛ الزامات تخصصی ایمنی و بهداشت و محیط‌زیست در بخش همان حوزه ثبت می‌شوند. هیچ فاصله تجدید یا سهمیه پیش‌فرضی اعمال نمی‌شود.'),
}
TABLES = {'training_courses': 'courses', 'training_records': 'records',
          'training_assessments': 'assessments', 'training_requirements': 'hrm_training_requirements',
          'training_assessment_history': 'hrm_training_assessment_context'}
CONTEXTS = {'training_courses': ('hrm_training_course_context', 'course_id'),
            'training_records': ('hrm_training_record_context', 'record_id'),
            'training_assessments': ('hrm_training_assessment_context', 'assessment_id')}


def _date(raw):
    try:
        return date_value(raw)
    except ValueError:
        return ''


def _validate_query_scope(conn):
    actor = access.require(conn, 'training')
    cid = positive_id(request.args['company_id']) if request.args.get('company_id') else None
    if cid is not None and cid not in actor['company_ids']:
        raise PermissionError()
    sid = positive_id(request.args['site_id']) if request.args.get('site_id') else None
    if sid is not None:
        site = conn.execute('SELECT company_id FROM hrm_sites WHERE id=?', (sid,)).fetchone()
        if site is None:
            raise ValueError('سایت یافت نشد')
        if cid is not None and site['company_id'] != cid:
            raise ValueError('سایت متعلق به شرکت انتخاب‌شده نیست')
        if not any(scope['company_id'] == site['company_id'] and
                   (scope.get('site_id') is None or scope['site_id'] == sid) for scope in actor['scopes']):
            raise PermissionError()


def _scope_filter(conn, row):
    actor = access.require(conn, 'training')
    raw = request.args.get('company_id')
    if raw not in (None, ''):
        cid = positive_id(raw)
        if cid not in actor['company_ids']:
            raise PermissionError()
        if row.get('company_id') != cid:
            return False
    raw_site = request.args.get('site_id')
    if raw_site not in (None, '') and row.get('site_id') != positive_id(raw_site):
        return False
    department = request.args.get('department', '').strip()
    if department and row.get('department') != department:
        return False
    eid = request.args.get('employee_id')
    if eid not in (None, '') and row.get('employee_id', row.get('id')) != positive_id(eid):
        return False
    start, end = date_value(request.args.get('from_date')), date_value(request.args.get('to_date'))
    if start and end and end < start:
        raise ValueError('پایان بازه پیش از شروع است')
    event = row.get('date_iso') or row.get('record_date') or row.get('assessment_date') or row.get('date')
    event = _date(event) if event else ''
    if (start or end) and not event:
        return False
    if event and ((start and event < start) or (end and event > end)):
        return False
    query = request.args.get('q', '').strip().casefold()
    if query and query not in ' '.join(str(v) for v in row.values() if v is not None).casefold():
        return False
    return True


def _context(conn, resource, rid):
    table, key = CONTEXTS[resource]
    row = conn.execute(f'SELECT * FROM {table} WHERE {key}=?', (rid,)).fetchone()
    return dict(row) if row else None


def _get(conn, resource, rid):
    table = TABLES[resource]
    row = conn.execute(f'SELECT * FROM {table} WHERE id=?', (positive_id(rid),)).fetchone()
    if row is None:
        return None
    result = dict(row)
    if resource in CONTEXTS:
        context = _context(conn, resource, rid)
        result.update(context or dict(company_id=row['company_id'], site_id=None, department='', date_iso='', version=0, is_deleted=0))
        result.pop('history_json', None)  # Every historical version has its own scope.
        result['has_context'] = context is not None
        if resource == 'training_courses':
            result['date_jalali'] = row['date']
            result['date_iso'] = result['date_iso'] or _date(row['date'])
            result['date'] = result['date_iso']
        else:
            result['employee_name'] = conn.execute('SELECT name FROM employees WHERE id=?', (row['employee_id'],)).fetchone()[0]
            result['record_date' if resource == 'training_records' else 'assessment_date'] = result['date_iso']
    if resource == 'training_records':
        course = conn.execute('SELECT title,status FROM courses WHERE id=?', (row['course_id'],)).fetchone()
        result.update(title=course['title'], status=course['status'])
        presence = conn.execute('SELECT attended FROM course_participants WHERE course_id=? AND employee_id=?', (row['course_id'], row['employee_id'])).fetchone()
        result['attended'] = int(presence['attended']) if presence else 0
    skill_id = row['skill_id'] if resource in ('training_courses', 'training_assessments') else None
    if skill_id:
        skill = conn.execute('SELECT b.code,b.title FROM competency_links l JOIN holding_competencies b ON b.code=l.code WHERE l.skill_id=?', (skill_id,)).fetchone()
        result.update(competency_code=skill['code'] if skill else '', competency_title=skill['title'] if skill else '')
    elif resource == 'training_requirements':
        result['competency_title'] = conn.execute('SELECT title FROM holding_competencies WHERE code=?', (row['competency_code'],)).fetchone()[0]
    company = conn.execute('SELECT name FROM companies WHERE id=?', (result['company_id'],)).fetchone()
    site = conn.execute('SELECT name FROM hrm_sites WHERE id=?', (result.get('site_id'),)).fetchone() if result.get('site_id') else None
    result.update(company_name=company['name'] if company else '', site_name=site['name'] if site else '')
    return result


def _legacy_person_access(conn, row, operation=None):
    person = access.employee(conn, row['employee_id'], 'training', operation=operation)
    access.company_scope(conn, row['company_id'], 'training', operation=operation,
                         site_id=person.get('site_id'), department=person.get('department'))
    return person


def _course_access(conn, course, operation=None, writing=False):
    actor = access.require(conn, 'training', operation=operation)
    if course.get('has_context'):
        try:
            access.record_scope(conn, course, 'training', operation=operation)
            return
        except PermissionError:
            if writing:
                raise
    else:
        try:
            access.company_scope(conn, course['company_id'], 'training', operation=operation)
            return
        except PermissionError:
            if writing:
                raise
    if actor['is_primary_admin']:
        return
    # A scoped learner may use the shared schedule, without seeing its roster.
    for participant in conn.execute('SELECT * FROM course_participants WHERE course_id=?', (course['id'],)):
        try:
            if participant['attended'] and participant['attendance_company_id']:
                access.record_scope(conn, dict(company_id=participant['attendance_company_id'], site_id=participant['attendance_site_id'], department=participant['attendance_department']), 'training', operation=operation)
            else:
                access.employee(conn, participant['employee_id'], 'training', operation=operation)
            return
        except PermissionError:
            continue
    for record in conn.execute('SELECT id FROM records WHERE course_id=?', (course['id'],)):
        row = _get(conn, 'training_records', record['id'])
        if row['is_deleted']:
            continue
        try:
            _record_access(conn, 'training_records', row, operation=operation)
            return
        except PermissionError:
            continue
    raise PermissionError()


def _course_filter(conn, course):
    """A host-owned shared course also belongs in its scoped learners' list."""
    if not request.args.get('employee_id') and _scope_filter(conn, course):
        return True
    learner_rows = conn.execute('SELECT * FROM course_participants WHERE course_id=?', (course['id'],)).fetchall()
    for row in learner_rows:
        try:
            if row['attended'] and row['attendance_company_id']:
                owner = dict(company_id=row['attendance_company_id'], site_id=row['attendance_site_id'], department=row['attendance_department'])
                access.record_scope(conn, owner, 'training')
            else:
                person = access.employee(conn, row['employee_id'], 'training')
                owner = dict(company_id=person['company_id'], site_id=person.get('site_id'), department=person['department'])
            if _scope_filter(conn, dict(course, **owner, employee_id=row['employee_id'])):
                return True
        except PermissionError:
            continue
    for row in conn.execute('SELECT id FROM records WHERE course_id=?', (course['id'],)):
        record = _get(conn, 'training_records', row['id'])
        if record['is_deleted']:
            continue
        try:
            _record_access(conn, 'training_records', record)
            if _scope_filter(conn, dict(course, company_id=record['company_id'], site_id=record['site_id'], department=record['department'], employee_id=record['employee_id'])):
                return True
        except PermissionError:
            continue
    return False


def _policy_read(conn, row):
    actor = access.require(conn, 'training')
    if actor['is_primary_admin']:
        return
    if not any(scope['company_id'] == row['company_id'] and
               (row['site_id'] is None or scope.get('site_id') is None or scope.get('site_id') == row['site_id']) and
               (not row['department'] or not scope['department'] or scope['department'] == row['department']) for scope in actor['scopes']):
        raise PermissionError()


def _record_access(conn, resource, row, operation=None, writing=False):
    if resource == 'training_courses':
        _course_access(conn, row, operation, writing)
    elif resource == 'training_requirements':
        access.record_scope(conn, row, 'training', operation=operation) if writing else _policy_read(conn, row)
    elif row['has_context']:
        access.record_scope(conn, row, 'training', operation=operation)
    elif writing:
        # No site is invented for a legacy record whose historical site is unknown.
        access.company_scope(conn, row['company_id'], 'training', operation=operation)
    else:
        _legacy_person_access(conn, row, operation)


def _save_context(conn, resource, rid, owner, event_date='', previous=None, deleted=0, replace_owner=False):
    table, key = CONTEXTS[resource]
    version = previous['version'] + 1 if previous else 1
    ownership = ',company_id=excluded.company_id,site_id=excluded.site_id,department=excluded.department' if replace_owner else ''
    conn.execute(f'INSERT INTO {table}({key},company_id,site_id,department,date_iso,version,is_deleted) VALUES (?,?,?,?,?,?,?) ON CONFLICT({key}) DO UPDATE SET date_iso=excluded.date_iso,version=excluded.version,is_deleted=excluded.is_deleted{ownership}',
                 (rid, owner['company_id'], owner.get('site_id'), owner.get('department', ''), event_date, version, deleted))
    return version


def _append_assessment_history(conn, previous, change_kind):
    context = _context(conn, 'training_assessments', previous['id'])
    history = json.loads(context['history_json']) if context else []
    item = {key: previous.get(key) for key in ('company_id', 'site_id', 'department', 'employee_id',
            'employee_name', 'competency_code', 'competency_title', 'level', 'version', 'date_iso', 'is_deleted')}
    item.update(assessment_id=previous['id'], assessment_date=previous.get('date_iso', ''), change_kind=change_kind)
    history.append(item)
    encoded = json.dumps(history, ensure_ascii=False, separators=(',', ':'))
    if context:
        conn.execute('UPDATE hrm_training_assessment_context SET history_json=? WHERE assessment_id=?', (encoded, previous['id']))
    else:
        conn.execute('INSERT INTO hrm_training_assessment_context(assessment_id,company_id,site_id,department,date_iso,version,is_deleted,history_json) VALUES (?,?,?,?,?,?,?,?)',
                     (previous['id'], previous['company_id'], previous.get('site_id'), previous.get('department', ''),
                      previous.get('date_iso', ''), previous['version'], previous['is_deleted'], encoded))


def _history_rows(conn, apply_filters=True):
    rows = []
    for context in conn.execute("SELECT history_json FROM hrm_training_assessment_context WHERE history_json!='[]'"):
        for value in json.loads(context['history_json']):
            if not isinstance(value, dict) or not {'company_id', 'employee_id', 'assessment_id', 'version', 'level'} <= set(value):
                raise ValueError('ساختار تاریخچه ارزیابی شایستگی معتبر نیست')
            row = dict(value, id=f"{value['assessment_id']}:{value['version']}")
            try:
                access.record_scope(conn, row, 'training')
            except PermissionError:
                continue
            company = conn.execute('SELECT name FROM companies WHERE id=?', (row['company_id'],)).fetchone()
            site = conn.execute('SELECT name FROM hrm_sites WHERE id=?', (row.get('site_id'),)).fetchone() if row.get('site_id') else None
            row.update(company_name=company['name'] if company else '', site_name=site['name'] if site else '')
            if not apply_filters or _scope_filter(conn, row):
                rows.append(row)
    return sorted(rows, key=lambda row: (row['assessment_id'], row['version']), reverse=True)


def _body(resource, data):
    allowed = {item['key'] for item in CATALOG[resource]['fields']} | {'version'}
    if set(data) - allowed:
        raise ValueError('فیلد ناشناخته در اطلاعات آموزش وجود دارد')


def _code(conn, raw):
    code = text_value(raw, required=True, max_length=128)
    if not conn.execute('SELECT 1 FROM holding_competencies WHERE code=?', (code,)).fetchone():
        raise ValueError('شایستگی باید از بانک موجود هلدینگ انتخاب شود')
    return code


def _owner(conn, values, operation):
    cid = positive_id(values.get('company_id'))
    site = positive_id(values['site_id']) if values.get('site_id') not in (None, '') else None
    department = text_value(values.get('department', ''), max_length=200)
    actor = access.require(conn, 'training', operation=operation)
    if not department:
        candidates = {scope['department'] for scope in actor['scopes'] if scope['company_id'] == cid and
                      (scope.get('site_id') is None or scope.get('site_id') == site)}
        if len(candidates) == 1:
            department = next(iter(candidates))
    row = dict(company_id=cid, site_id=site, department=department)
    access.record_scope(conn, row, 'training', operation=operation)
    return row


def _course_values(conn, data, previous=None):
    values = {**dict(cost=0, instructor='', status='planned', calendar_scope='internal'), **(previous or {}), **data}
    operation = 'edit' if previous else 'create'
    owner = _owner(conn, values, operation)
    if previous and (previous['company_id'], previous.get('site_id'), previous.get('department', '')) != (owner['company_id'], owner['site_id'], owner['department']):
        raise HrmConflict('محدوده مالکیت دوره قبلی قابل تغییر نیست')
    scope = values['calendar_scope']
    if scope not in ('holding', 'internal'):
        raise ValueError('نوع تقویم معتبر نیست')
    if (scope == 'holding' or previous and previous['calendar_scope'] == 'holding') and not access.ctx(conn)['is_primary_admin']:
        raise PermissionError()
    if previous and scope == 'internal' and previous['calendar_scope'] == 'holding' and conn.execute('SELECT 1 FROM course_participants WHERE course_id=?', (previous['id'],)).fetchone():
        raise HrmConflict('دوره دارای فهرست افراد مشترک است؛ ابتدا ادمین فهرست را بازبینی کند')
    if values['status'] not in ('planned', 'approved', 'completed', 'cancelled'):
        raise ValueError('وضعیت دوره معتبر نیست')
    iso = date_value(values.get('date'), required=True)
    jalali = jalali_date(jalali_display(iso))
    code = _code(conn, values.get('competency_code'))
    sid = attach(conn, owner['company_id'], code)
    if previous and previous['skill_id'] != sid and conn.execute('SELECT 1 FROM records WHERE course_id=? LIMIT 1', (previous['id'],)).fetchone():
        raise HrmConflict('شایستگی دوره دارای سابقه قابل تعویض نیست')
    course = dict(company_id=owner['company_id'], skill_id=sid,
                  title=text_value(values.get('title'), required=True, max_length=250), date=jalali,
                  instructor=text_value(values.get('instructor', ''), max_length=250),
                  hours=number(values.get('hours'), positive=True, maximum=1000000),
                  cost=number(values.get('cost'), minimum=0, maximum=9007199254),
                  status=values['status'], calendar_scope=scope)
    return course, owner, iso


def _course_for_person(conn, course_id, person, operation):
    course = _get(conn, 'training_courses', positive_id(course_id))
    if not course or course['is_deleted']:
        raise ValueError('دوره یافت نشد')
    _course_access(conn, course, operation)
    if course['company_id'] != person['company_id']:
        planned = conn.execute('SELECT 1 FROM course_participants WHERE course_id=? AND employee_id=?', (course['id'], person['id'])).fetchone()
        if course['calendar_scope'] != 'holding' or not planned:
            raise PermissionError()
    return course


def _presence_value(raw):
    if raw in (None, ''):
        return None
    if isinstance(raw, bool):
        return int(raw)
    return integer_value(raw, minimum=0, maximum=1)


def _attendance(conn, course, employee_id, value, operation):
    if value is None:
        return
    row = conn.execute('SELECT * FROM course_participants WHERE course_id=? AND employee_id=?', (course['id'], employee_id)).fetchone()
    if value and course['status'] == 'cancelled':
        raise ValueError('حضور تازه در دوره لغوشده ثبت نمی‌شود')
    if row and row['attended']:
        snapshot = dict(company_id=row['attendance_company_id'], site_id=row['attendance_site_id'], department=row['attendance_department'])
        access.record_scope(conn, snapshot, 'training', operation=operation)
        if value:
            return  # Re-saving a grade never retags existing attendance.
    if not value:
        if row:
            conn.execute('UPDATE course_participants SET attended=0,attendance_company_id=NULL,attendance_site_id=NULL,attendance_department=? WHERE course_id=? AND employee_id=?', ('', course['id'], employee_id))
        return
    if course['calendar_scope'] == 'holding' and row is None:
        raise ValueError('برای حضور دوره تجمیعی، فرد باید قبلاً توسط هلدینگ در فهرست برنامه‌ریزی انتخاب شده باشد')
    person = access.employee(conn, employee_id, 'training', operation=operation)
    conn.execute('INSERT INTO course_participants(course_id,employee_id,attended,attendance_company_id,attendance_site_id,attendance_department) VALUES (?,?,1,?,?,?) ON CONFLICT(course_id,employee_id) DO UPDATE SET attended=1,attendance_company_id=excluded.attendance_company_id,attendance_site_id=excluded.attendance_site_id,attendance_department=excluded.attendance_department',
                 (course['id'], employee_id, person['company_id'], person.get('site_id'), person['department']))


def _save_record(conn, data, previous=None):
    operation = 'edit' if previous else 'create'
    values = {**(previous or {}), **data}
    eid = positive_id(values.get('employee_id'))
    if previous:
        if eid != previous['employee_id'] or positive_id(values.get('course_id')) != previous['course_id']:
            raise HrmConflict('فرد یا دوره سابقه قابل تعویض نیست')
        _record_access(conn, 'training_records', previous, operation, True)
        owner = dict(company_id=previous['company_id'], site_id=previous.get('site_id'), department=previous.get('department', ''))
        person = dict(id=eid, **owner)
    else:
        person = access.employee(conn, eid, 'training', operation=operation)
        owner = dict(company_id=person['company_id'], site_id=person.get('site_id'), department=person['department'])
    course = _course_for_person(conn, values.get('course_id'), person, operation)
    if not previous and conn.execute('SELECT 1 FROM records WHERE employee_id=? AND course_id=?', (eid, course['id'])).fetchone():
        raise HrmConflict('سابقه این فرد برای همین دوره قبلاً ثبت شده است؛ رکورد موجود را ویرایش کنید')
    fields = dict(company_id=owner['company_id'], employee_id=eid, course_id=course['id'],
                  level=integer_value(values.get('level'), minimum=0, maximum=4))
    for key, minimum, maximum in (('score', 0, 100), ('reaction', 1, 5), ('behavior', 1, 5), ('result', 0, 100)):
        raw = values.get(key)
        fields[key] = None if raw in (None, '') else number(raw, minimum=minimum, maximum=maximum)
    iso = date_value(values.get('record_date') or course['date_iso'], required=True)
    if previous:
        conn.execute('UPDATE records SET level=?,score=?,reaction=?,behavior=?,result=? WHERE id=?',
                     (fields['level'], fields['score'], fields['reaction'], fields['behavior'], fields['result'], previous['id']))
        rid = previous['id']
    else:
        rid = conn.execute('INSERT INTO records(company_id,employee_id,course_id,level,score,reaction,behavior,result) VALUES (?,?,?,?,?,?,?,?)', tuple(fields.values())).lastrowid
    _attendance(conn, course, eid, _presence_value(data.get('attended')), operation)
    version = _save_context(conn, 'training_records', rid, owner, iso, previous)
    return rid, version, owner


def _save_assessment(conn, data, previous=None):
    operation = 'edit' if previous else 'create'
    values = {**(previous or {}), **data}
    eid = positive_id(values.get('employee_id'))
    if previous:
        if eid != previous['employee_id']:
            raise HrmConflict('فرد ارزیابی قابل تعویض نیست')
        _record_access(conn, 'training_assessments', previous, operation, True)
        owner = dict(company_id=previous['company_id'], site_id=previous.get('site_id'), department=previous.get('department', ''))
    else:
        person = access.employee(conn, eid, 'training', operation=operation)
        owner = dict(company_id=person['company_id'], site_id=person.get('site_id'), department=person['department'])
    code = _code(conn, values.get('competency_code'))
    sid = attach(conn, owner['company_id'], code)
    if previous and sid != previous['skill_id']:
        raise HrmConflict('شایستگی ارزیابی قبلی قابل تعویض نیست')
    level = integer_value(values.get('level'), minimum=0, maximum=4)
    iso = date_value(values.get('assessment_date'), required=True)
    if not previous:
        existing = conn.execute('SELECT id FROM assessments WHERE company_id=? AND employee_id=? AND skill_id=?', (owner['company_id'], eid, sid)).fetchone()
        if existing:
            old = _get(conn, 'training_assessments', existing['id'])
            if all(owner.get(key) == old.get(key) for key in ('company_id', 'site_id', 'department')):
                raise HrmConflict('ارزیابی فعلی این شایستگی موجود است؛ همان رکورد را با نسخه معتبر ویرایش کنید')
            if old['date_iso'] and iso < old['date_iso']:
                raise HrmConflict('ارزیابی جدید پس از انتقال نمی‌تواند تاریخ قدیمی‌تر از نسخه فعلی داشته باشد')
            _append_assessment_history(conn, old, 'reevaluated_after_transfer')
            rid = old['id']
            conn.execute('UPDATE assessments SET level=? WHERE id=?', (level, rid))
            version = _save_context(conn, 'training_assessments', rid, owner, iso, old, replace_owner=True)
            return rid, version, owner
    if previous:
        if previous['level'] != level or previous['date_iso'] != iso:
            _append_assessment_history(conn, previous, 'edited')
        rid = previous['id']; conn.execute('UPDATE assessments SET level=? WHERE id=?', (level, rid))
    else:
        rid = conn.execute('INSERT INTO assessments(company_id,employee_id,skill_id,level) VALUES (?,?,?,?)', (owner['company_id'], eid, sid, level)).lastrowid
    version = _save_context(conn, 'training_assessments', rid, owner, iso, previous)
    return rid, version, owner


def _save_policy(conn, data, previous=None):
    operation = 'edit' if previous else 'create'
    values = {**dict(job='', activity_location='', onboarding=0, active=1, notes=''), **(previous or {}), **data}
    owner = _owner(conn, values, operation)
    if previous and any(owner[key] != previous[key] for key in owner):
        raise HrmConflict('محدوده ضابطه قبلی قابل تغییر نیست')
    code = _code(conn, values.get('competency_code'))
    renewal = values.get('renewal_days')
    renewal = None if renewal in (None, '') else integer_value(renewal, minimum=1, maximum=1000000)
    fields = dict(**owner, job=text_value(values['job'], max_length=200),
                  activity_location=text_value(values['activity_location'], max_length=240),
                  competency_code=code, required_level=integer_value(values.get('required_level'), minimum=1, maximum=4),
                  renewal_days=renewal, onboarding=integer_value(values['onboarding'], minimum=0, maximum=1),
                  active=integer_value(values['active'], minimum=0, maximum=1), notes=text_value(values['notes']))
    params = (owner['company_id'], owner['site_id'], owner['department'], fields['job'], fields['activity_location'], code, previous['id'] if previous else 0)
    if conn.execute('SELECT 1 FROM hrm_training_requirements WHERE company_id=? AND site_id IS ? AND department=? AND job=? AND activity_location=? AND competency_code=? AND is_deleted=0 AND id!=?', params).fetchone():
        raise HrmConflict('ضابطه این محدوده و شایستگی قبلاً ثبت شده است')
    if previous:
        rid = previous['id']; conn.execute('UPDATE hrm_training_requirements SET '+','.join(key+'=?' for key in fields)+',version=version+1 WHERE id=?', [*fields.values(), rid]); version = previous['version'] + 1
    else:
        rid = conn.execute('INSERT INTO hrm_training_requirements('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')', list(fields.values())).lastrowid; version = 1
    return rid, version, owner


def list_rows(conn, resource, apply_filters=True):
    access.require(conn, 'training')
    if apply_filters:
        _validate_query_scope(conn)
    if resource == 'training_assessment_history':
        return _history_rows(conn, apply_filters)
    rows = []
    for entry in conn.execute('SELECT id FROM '+TABLES[resource]+' ORDER BY id DESC'):
        row = _get(conn, resource, entry['id'])
        if row['is_deleted']:
            continue
        try:
            _record_access(conn, resource, row)
        except PermissionError:
            continue
        if not apply_filters or (_course_filter(conn, row) if resource == 'training_courses' else _scope_filter(conn, row)):
            rows.append(row)
    return rows


def _people(conn):
    clause, params = access.scope_sql(conn, 'training', alias='e')
    result = []
    for row in conn.execute('SELECT e.id FROM employees e WHERE '+clause, params):
        person = access.employee(conn, row['id'], 'training')
        detail = conn.execute('SELECT start_date,location,status FROM hrm_employee_details WHERE employee_id=?', (row['id'],)).fetchone()
        person.update(dict(detail) if detail else dict(start_date='', location='', status='active'))
        # Event dates filter training events, not today's population denominator.
        probe = {**person, 'employee_id': person['id'], 'date_iso': today_iso()}
        query_args = {key: request.args.get(key) for key in ('company_id', 'site_id', 'department', 'employee_id')}
        if any(value not in (None, '') and str(probe.get('employee_id' if key == 'employee_id' else key)) != str(value) for key, value in query_args.items()):
            continue
        result.append(person)
    return result


def _actual_rows(conn, apply_filters=True):
    result = []
    for row in conn.execute('SELECT p.*,c.hours,c.status,c.date,c.skill_id,c.title,e.name employee_name FROM course_participants p JOIN courses c ON c.id=p.course_id JOIN employees e ON e.id=p.employee_id WHERE p.attended=1'):
        row = dict(row)
        snapshot = dict(company_id=row['attendance_company_id'], site_id=row['attendance_site_id'], department=row['attendance_department'])
        try:
            access.record_scope(conn, snapshot, 'training')
        except (PermissionError, ValueError):
            continue
        course_context = _context(conn, 'training_courses', row['course_id'])
        if course_context and course_context['is_deleted']:
            continue
        row.update(snapshot, date_iso=course_context['date_iso'] if course_context else _date(row['date']))
        code = conn.execute('SELECT code FROM competency_links WHERE skill_id=?', (row['skill_id'],)).fetchone()
        row['competency_code'] = code[0] if code else ''
        if row['status'] != 'cancelled' and (not apply_filters or _scope_filter(conn, row)):
            result.append(row)
    return result


def training_report(conn):
    """Numbers are traceable to actual presence, configured rules and current assessments."""
    access.require(conn, 'training')
    _validate_query_scope(conn)
    people = _people(conn)
    actual = _actual_rows(conn)
    # Activity totals follow the selected period. Compliance uses the complete
    # authorized history, so a valid earlier course never becomes "unknown"
    # merely because its date is outside the displayed activity window.
    all_actual = _actual_rows(conn, apply_filters=False)
    courses = list_rows(conn, 'training_courses')
    records = list_rows(conn, 'training_records', apply_filters=False)
    assessments = list_rows(conn, 'training_assessments', apply_filters=False)
    policies = list_rows(conn, 'training_requirements', apply_filters=False)
    today = today_iso(); requirements = []
    for person in people:
        rules = {}
        for row in conn.execute('SELECT b.code,b.title,r.level FROM requirements r JOIN competency_links l ON l.skill_id=r.skill_id JOIN holding_competencies b ON b.code=l.code WHERE r.company_id=? AND r.job=?', (person['company_id'], person['job'])):
            rules[row['code']] = dict(competency_code=row['code'], competency_title=row['title'], required_level=row['level'], renewal_days=None, onboarding=0, sources=['job_profile'])
        for row in conn.execute('SELECT b.code,b.title,r.level FROM personal_requirements r JOIN skills s ON s.id=r.skill_id JOIN competency_links l ON l.skill_id=s.id JOIN holding_competencies b ON b.code=l.code WHERE r.employee_id=? AND s.company_id=?', (person['id'], person['company_id'])):
            rules[row['code']] = dict(competency_code=row['code'], competency_title=row['title'], required_level=row['level'], renewal_days=None, onboarding=0, sources=['personal_profile'])
        for rule in policies:
            if not rule['active'] or rule['company_id'] != person['company_id'] or (rule['site_id'] and rule['site_id'] != person.get('site_id')) or any(rule.get(key) and rule[key] != person.get(target, '') for key, target in [('department', 'department'), ('job', 'job'), ('activity_location', 'location')]):
                continue
            previous = rules.get(rule['competency_code'])
            if previous:
                previous['required_level'] = max(previous['required_level'], rule['required_level'])
                previous['onboarding'] = previous['onboarding'] or rule['onboarding']
                previous['renewal_days'] = min(value for value in (previous['renewal_days'], rule['renewal_days']) if value is not None) if previous['renewal_days'] or rule['renewal_days'] else None
                previous['sources'].append('configured_general_policy')
            else:
                rules[rule['competency_code']] = dict(competency_code=rule['competency_code'], competency_title=rule['competency_title'], required_level=rule['required_level'], renewal_days=rule['renewal_days'], onboarding=rule['onboarding'], sources=['configured_general_policy'])
        for rule in rules.values():
            if rule['required_level'] == 0 and not rule['onboarding'] and rule['renewal_days'] is None:
                continue
            current = [row for row in assessments if row['employee_id'] == person['id'] and row['competency_code'] == rule['competency_code']]
            current.sort(key=lambda row: (row['company_id'] == person['company_id'], row.get('date_iso', ''), row['id']), reverse=True)
            completed = [row for row in records if row['employee_id'] == person['id'] and row['status'] == 'completed' and conn.execute('SELECT 1 FROM courses c JOIN competency_links l ON l.skill_id=c.skill_id WHERE c.id=? AND l.code=?', (row['course_id'], rule['competency_code'])).fetchone()]
            level = current[0]['level'] if current else max((row['level'] for row in completed), default=None)
            dates = [row['date_iso'] for row in all_actual if row['employee_id'] == person['id'] and row['competency_code'] == rule['competency_code'] and row['status'] == 'completed' and row['date_iso']]
            last = max(dates) if dates else ''
            expiry = (date.fromisoformat(last) + timedelta(days=rule['renewal_days'])).isoformat() if last and rule['renewal_days'] and date.fromisoformat(last).toordinal() + rule['renewal_days'] <= date.max.toordinal() else ''
            onboarding_missing = (not last or last < person['start_date']) if rule['onboarding'] and person['start_date'] else None
            requirements.append(dict(employee_id=person['id'], employee_name=person['name'], company_id=person['company_id'], site_id=person.get('site_id'), department=person['department'], **rule,
                                     current_level=level, level_source='current_assessment' if current else 'completed_course_record' if completed else 'unknown',
                                     gap=max(0, rule['required_level'] - level) if level is not None else None,
                                     last_training_date=last, renewal_due_date=expiry,
                                     renewal_due=expiry <= today if expiry else None,
                                     onboarding_missing=onboarding_missing,
                                     missing_actual_completion=not bool(last),
                                     onboarding_status='not_required' if not rule['onboarding'] else 'unknown_start_date' if not person['start_date'] else 'missing' if onboarding_missing else 'recorded'))
    planned = [row for row in courses if row['status'] != 'cancelled']
    completed_courses = [row for row in planned if row['status'] == 'completed']
    person_hours = sum(row['hours'] for row in actual)
    metrics = dict(staff=len(people), planned_courses=len(planned), completed_courses=len(completed_courses),
                   actual_attendances=len(actual), actual_headcount=len({row['employee_id'] for row in actual}),
                   actual_person_hours=person_hours if math.isfinite(person_hours) else None,
                   hours_per_employee=person_hours / len(people) if people and math.isfinite(person_hours) else None,
                   achievement_percent=100 * len(completed_courses) / len(planned) if planned else None,
                   requirements=len(requirements), assessed_requirements=sum(row['current_level'] is not None for row in requirements),
                   unknown_requirements=sum(row['current_level'] is None for row in requirements),
                   competency_gaps=sum(row['gap'] is not None and row['gap'] > 0 for row in requirements),
                   renewal_due=sum(row['renewal_due'] is True for row in requirements),
                   onboarding_missing=sum(row['onboarding_missing'] is True for row in requirements))
    return dict(as_of=today, metrics=metrics, actual_attendance=actual, requirements=requirements,
                denominator_basis='current_scoped_personnel',
                data_status='no_scoped_personnel' if not people else 'no_configured_requirements' if not requirements else 'recorded_data',
                unknowns=dict(renewal_without_actual_date=sum(row['renewal_days'] is not None and not row['last_training_date'] for row in requirements),
                              onboarding_without_start_date=sum(row['onboarding_status'] == 'unknown_start_date' for row in requirements)))


def register(app, db, authenticated, audit, error):
    with app.app_context():
        db().executescript(SCHEMA)
        if 'history_json' not in {row['name'] for row in db().execute('PRAGMA table_info(hrm_training_assessment_context)')}:
            db().execute("ALTER TABLE hrm_training_assessment_context ADD COLUMN history_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(history_json) AND json_type(history_json)='array')")
        db().commit()
    app.extensions.setdefault('hrm_catalog', {}).update(CATALOG)

    def route(resource, rid=None):
        conn = db(); operation = None if request.method == 'GET' else 'create' if rid is None else 'delete' if request.method == 'DELETE' else 'edit'
        access.require(conn, 'training', operation=operation)
        if request.method == 'GET' and rid is None:
            return jsonify(list_rows(conn, resource))
        if rid is not None:
            rid = positive_id(rid)
        data = json_object()
        _body(resource, data)
        with begin_write(conn):
            previous = _get(conn, resource, rid) if rid is not None else None
            if rid is not None and (previous is None or previous['is_deleted']):
                return error('سابقه آموزش یافت نشد', 404)
            if previous:
                _record_access(conn, resource, previous, operation, True)
                check_version(data, previous)
            if operation == 'delete':
                if resource == 'training_courses':
                    if conn.execute('SELECT 1 FROM course_participants WHERE course_id=? AND attended=1', (rid,)).fetchone():
                        raise HrmConflict('دوره دارای حضور واقعی است؛ حضور را بدون بازبینی سوابق حذف نکنید')
                    conn.execute("UPDATE courses SET status='cancelled' WHERE id=?", (rid,))
                if resource in CONTEXTS:
                    if resource == 'training_assessments':
                        _append_assessment_history(conn, previous, 'voided')
                    version = _save_context(conn, resource, rid, previous, previous['date_iso'], previous, 1)
                else:
                    version = previous['version'] + 1; conn.execute('UPDATE hrm_training_requirements SET is_deleted=1,active=0,version=? WHERE id=?', (version, rid))
                owner = previous
            elif resource == 'training_courses':
                values, owner, iso = _course_values(conn, data, previous)
                if previous:
                    rid = previous['id']; conn.execute('UPDATE courses SET '+','.join(key+'=?' for key in values)+' WHERE id=?', [*values.values(), rid])
                else:
                    rid = conn.execute('INSERT INTO courses('+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')', list(values.values())).lastrowid
                version = _save_context(conn, resource, rid, owner, iso, previous)
            elif resource == 'training_records':
                rid, version, owner = _save_record(conn, data, previous)
            elif resource == 'training_assessments':
                rid, version, owner = _save_assessment(conn, data, previous)
            else:
                rid, version, owner = _save_policy(conn, data, previous)
            audit('hrm_' + resource + '_' + operation, owner['company_id'], str(rid))
        return jsonify(id=rid, version=version), 201 if operation == 'create' else 200

    for resource in CATALOG:
        app.add_url_rule('/api/hrm/' + resource, 'hrm_training_' + resource,
                         authenticated(lambda resource=resource: route(resource)), methods=['GET'] if CATALOG[resource].get('read_only') else ['GET', 'POST'])
        if CATALOG[resource].get('read_only'):
            continue
        app.add_url_rule('/api/hrm/' + resource + '/<int:rid>', 'hrm_training_' + resource + '_detail',
                         authenticated(lambda rid, resource=resource: route(resource, rid)), methods=['PUT', 'DELETE'])

    @app.get('/api/hrm/training_competency_options')
    @authenticated
    def hrm_training_competency_options():
        actor = access.ctx(db())
        if not any(actor['permissions']['training'][key] for key in ('read', 'create', 'edit')):
            raise PermissionError()
        return jsonify([dict(id=row['code'], code=row['code'], title=row['title'], name=row['title']) for row in db().execute('SELECT code,title FROM holding_competencies ORDER BY category,code')])

    @app.get('/api/hrm/training_report')
    @authenticated
    def hrm_training_report():
        return jsonify(training_report(db()))
