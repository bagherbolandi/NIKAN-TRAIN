"""One employee identity across HRM and retained training entry/import routes.

An HRM details row owns current assignment. Older rows without details keep the
existing employees fields as their current fallback. Historical training owner,
issued passport references and full individual documents are never retagged.
Callers retain transaction commit and audit responsibility.
"""
from flask import g, has_request_context

from . import hrm_access as access
from .hrm_common import HrmConflict, check_version, text_value, today_iso
from .validation import positive_id

CORE_FIELDS = ('code', 'name', 'department', 'job')


def current_employee_rows(conn, cid):
    cid = positive_id(cid)
    rows = conn.execute('''SELECT e.*,d.company_id current_company_id,d.department current_department,
        d.job current_job,d.site_id,d.unit_id,d.location,d.status,d.version
        FROM employees e LEFT JOIN hrm_employee_details d ON d.employee_id=e.id
        WHERE COALESCE(d.company_id,e.company_id)=? ORDER BY e.id DESC''', (cid,))
    result = []
    for entry in rows:
        row = dict(entry)
        row['training_company_id'] = row['company_id']
        row['company_id'] = row.pop('current_company_id') or row['company_id']
        for field in ('department', 'job'):
            current = row.pop('current_' + field)
            if current is not None:
                row[field] = current
        row['version'] = row['version'] or 0
        row['status'] = row['status'] or 'active'
        row['location'] = row['location'] or ''
        result.append(row)
    return result


def find_current_employee(conn, cid, code):
    code = text_value(code, required=True, max_length=128)
    matches = conn.execute('''SELECT e.id FROM employees e
        LEFT JOIN hrm_employee_details d ON d.employee_id=e.id
        WHERE COALESCE(d.company_id,e.company_id)=? AND e.code=?''', (positive_id(cid), code)).fetchall()
    if len(matches) > 1:
        raise HrmConflict('کد پرسنلی در شرکت فعلی مبهم است؛ ابتدا پرونده‌های تکراری بررسی شوند')
    return matches[0]['id'] if matches else None


def _authorize(conn, cid, operation, person=None):
    if not has_request_context():
        raise PermissionError()
    actor = access.ctx(conn)
    if actor['is_primary_admin'] or actor['configured']:
        access.require(conn, 'personnel', operation=operation)
        if person is not None:
            access.employee(conn, person['id'], 'personnel', operation=operation)
        else:
            access.company_scope(conn, cid, 'personnel', operation=operation)
        return
    # Existing company training accounts retain the old full-company personnel
    # entry contract. They cannot act as the former employer after a transfer.
    user = dict(getattr(g, 'hrm_original_user', g.user))
    if user['role'] != 'manager' or user['company_id'] != cid:
        raise PermissionError()
    if person is not None and person['company_id'] != cid:
        raise PermissionError()


def _reserved_code(conn, original_company, code, eid=0):
    if conn.execute('SELECT 1 FROM employees WHERE company_id=? AND code=? AND id!=?',
                    (original_company, code, eid)).fetchone():
        raise HrmConflict('این کد به هویت یا سوابق قبلی همین شرکت تعلق دارد؛ کد تازه یا پرونده موجود را انتخاب کنید')


def write_legacy_employee(conn, cid, values, policy='insert', employee_id=None, version=None):
    """Return (employee_id, created/updated/unchanged) using current ownership."""
    from .hrm_people import (_current, _history, _person_values, _save_details,
                             _unique_current_code)
    cid = positive_id(cid)
    if policy not in ('insert', 'upsert', 'create') or set(values) - set(CORE_FIELDS):
        raise ValueError('اطلاعات یا سیاست ثبت پرسنل معتبر نیست')
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    cleaned = {field: text_value(value, required=field in ('code', 'name'),
                                 max_length=128 if field == 'code' else 250)
               for field, value in values.items()}
    eid = positive_id(employee_id) if employee_id is not None else None
    if eid is None:
        if not all(cleaned.get(field) for field in ('code', 'name')):
            raise ValueError('کد و نام پرسنل الزامی است')
        eid = find_current_employee(conn, cid, cleaned['code'])
        if eid is not None and policy == 'create':
            if not conn.execute('SELECT 1 FROM hrm_employee_details WHERE employee_id=?', (eid,)).fetchone():
                raise ValueError('کد پرسنلی در این شرکت تکراری است')
            raise HrmConflict('کد پرسنلی در شرکت محل خدمت فعلی تکراری است')
    if eid is None:
        _authorize(conn, cid, 'create')
        _unique_current_code(conn, cid, cleaned['code'])
        _reserved_code(conn, cid, cleaned['code'])
        eid = conn.execute('INSERT INTO employees(company_id,code,name,department,job) VALUES (?,?,?,?,?)',
                           (cid, cleaned['code'], cleaned['name'], cleaned.get('department', ''), cleaned.get('job', ''))).lastrowid
        return eid, 'created'
    current = _current(conn, eid)
    if current['company_id'] != cid:
        raise PermissionError()
    changed = {field: value for field, value in cleaned.items() if current[field] != value}
    operation = 'edit' if employee_id is not None or (changed and policy == 'upsert') else 'create'
    _authorize(conn, cid, operation, current)
    if not changed:
        return eid, 'unchanged'
    if employee_id is None and policy != 'upsert':
        raise HrmConflict('پرونده موجود متفاوت است؛ برای اصلاح، به‌روزرسانی را صریح انتخاب کنید')
    if version is not None:
        check_version({'version': version}, current)
    code = cleaned.get('code', current['code'])
    _unique_current_code(conn, cid, code, eid)
    _reserved_code(conn, current['training_company_id'], code, eid)
    managed = conn.execute('SELECT 1 FROM hrm_employee_details WHERE employee_id=?', (eid,)).fetchone()
    if managed:
        # Legacy text fields cannot silently detach a defined organizational unit.
        if 'department' in changed and current.get('unit_id') is not None:
            raise HrmConflict('واحد تعریف‌شده فرد از پرونده مرکزی و با انتخاب واحد مقصد تغییر می‌کند')
        after = _person_values(conn, changed, current, eid)
        conn.execute('UPDATE employees SET code=?,name=? WHERE id=?',
                     (code, cleaned.get('name', current['name']), eid))
        _save_details(conn, eid, after)
        if any(after[field] != current[field] for field in ('department', 'job')):
            _history(conn, eid, current, after, today_iso(), 'assignment_change', 'اصلاح از مسیر سازگار آموزش یا اکسل')
    else:
        conn.execute('UPDATE employees SET ' + ','.join(field + '=?' for field in changed) + ' WHERE id=?',
                     [*changed.values(), eid])
    return eid, 'updated'
