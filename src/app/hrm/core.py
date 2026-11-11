"""Shared HRM request context: routing, validation, scope, audit and settings."""
import json
import sqlite3
from functools import wraps
from datetime import datetime, timezone

from flask import g, jsonify, request

from ..validation import json_object, positive_id, number, jalali_date
from . import access, dates


def _rowdict(row):
    return dates.display_row(row) if row is not None else None


class Hrm:
    def __init__(self, app, db, authenticated, audit, error):
        self.app = app
        self.db = db
        self.authenticated = authenticated
        self.audit = audit
        self.error = error
        self.conn = None

    @property
    def c(self):
        return self.db()

    def now(self):
        return datetime.now(timezone.utc).isoformat(timespec='seconds')

    def route(self, rule, methods=('GET',), permission=None, confidential=None, company=True):
        """Register /api/hrm/<rule> with authentication + server-side authorization."""
        def decorator(fn):
            @self.app.route('/api/hrm/' + rule, methods=list(methods))
            @self.authenticated
            @wraps(fn)
            def inner(*args, **kwargs):
                cid = self.cid(permission, confidential, required=company)
                g.hrm_cid = cid
                return fn(*args, **kwargs)
            return inner
        return decorator

    def cid(self, permission=None, confidential=None, required=True):
        data = request.get_json(silent=True) if request.method in ('POST', 'PUT', 'PATCH') else None
        raw = request.args.get('company_id')
        if raw in (None, '') and isinstance(data, dict):
            raw = data.get('company_id')
        if raw in (None, ''):
            raw = request.form.get('company_id')
        if raw in (None, '') and not required:
            access.require(self.c, g.user, None, permission, confidential)
            return None
        cid = positive_id(raw)
        if not self.c.execute('SELECT 1 FROM companies WHERE id=?', (cid,)).fetchone():
            raise ValueError('شرکت انتخاب‌شده معتبر نیست')
        access.require(self.c, g.user, cid, permission, confidential)
        return cid

    def body(self):
        return json_object()

    def form_or_json(self):
        """Multipart uploads carry fields in request.form, not JSON."""
        if request.form or request.files:
            return {key: value for key, value in request.form.items()}
        return json_object()

    def rows(self, sql, params=()):
        return [_rowdict(r) for r in self.c.execute(sql, params)]

    def one(self, sql, params=()):
        return _rowdict(self.c.execute(sql, params).fetchone())

    def employee(self, employee_id, cid=None):
        cid = cid or g.hrm_cid
        row = self.c.execute('SELECT e.*,p.unit_id,p.position_id,p.employment_status '
                             'FROM employees e LEFT JOIN personnel_profiles p ON p.employee_id=e.id '
                             'WHERE e.id=? AND e.company_id=?', (positive_id(employee_id), cid)).fetchone()
        if not row:
            raise ValueError('پرسنل مورد نظر در این شرکت یافت نشد')
        if not access.employee_allowed(self.c, g.user, row):
            raise PermissionError('دسترسی به پرونده این فرد برای شما تعریف نشده است')
        return row

    def reference(self, table, value, cid, global_ok=False):
        rid = positive_id(value)
        sql = f'SELECT * FROM {table} WHERE id=?'
        params = [rid]
        if global_ok:
            sql += ' AND (company_id=? OR company_id=0 OR company_id IS NULL)'
        else:
            sql += ' AND company_id=?'
        params.append(cid)
        if not self.c.execute(sql, params).fetchone():
            raise ValueError('رکورد مرجع معتبر نیست یا متعلق به این شرکت نیست')
        return rid

    def setting(self, key, cid=None):
        cid = cid or g.get('hrm_cid', 0)
        row = self.c.execute(
            'SELECT value FROM hrm_settings WHERE key=? AND company_id IN (?,0) ORDER BY company_id DESC LIMIT 1',
            (key, cid or 0)).fetchone()
        return row['value'] if row else None

    def number_setting(self, key, cid=None, default=0, integer=True):
        raw = self.setting(key, cid)
        if raw is None:
            return default
        try:
            return int(raw) if integer else float(raw)
        except (TypeError, ValueError):
            return default

    def iso(self, value, field='تاریخ'):
        return dates.optional_iso(value, field)

    def required_iso(self, value, field='تاریخ'):
        if value in (None, ''):
            raise ValueError(f'{field} الزامی است')
        return self.iso(value, field)

    def text(self, data, key, required=False, maximum=500):
        value = data.get(key, '')
        if value is None:
            value = ''
        if not isinstance(value, str):
            raise ValueError(f'مقدار {key} باید متنی باشد')
        value = value.strip()
        if len(value) > maximum:
            raise ValueError(f'مقدار {key} بیش از حد مجاز است')
        if required and not value:
            raise ValueError('فیلدهای الزامی را تکمیل کنید')
        return value

    def amount(self, data, key, minimum=0, maximum=None, required=False):
        if data.get(key) in (None, '') and not required:
            return 0.0 if minimum == 0 else None
        return number(data.get(key), minimum=minimum, maximum=maximum,
                      nullable=not required)

    def integer(self, data, key, minimum=0, maximum=None, default=0):
        value = data.get(key, default)
        if value in (None, ''):
            return default
        return number(value, integer=True, minimum=minimum, maximum=maximum)

    def audit_log(self, action, detail='', cid=None):
        self.audit(action, cid if cid is not None else g.get('hrm_cid'), detail)

    def history(self, employee_id, entity, entity_id, changes, cid=None):
        cid = cid if cid is not None else g.get('hrm_cid')
        uid = g.user['id']
        stamp = self.now()
        self.c.executemany(
            'INSERT INTO employee_history(company_id,employee_id,entity,entity_id,field,old_value,new_value,changed_by,changed_at) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            [(cid, employee_id, entity, entity_id, field, str(old or ''), str(new or ''), uid, stamp)
             for field, old, new in changes],
        )

    def upload(self):
        file = request.files.get('file')
        if not file or not file.filename:
            raise ValueError('فایل سند را انتخاب کنید')
        payload = file.read()
        if not payload:
            raise ValueError('فایل خالی است')
        if len(payload) > 12 * 1024 * 1024:
            raise ValueError('حجم فایل سند حداکثر ۱۲ مگابایت است')
        return file.filename[:255], file.mimetype or 'application/octet-stream', payload

    def ok(self, **payload):
        return jsonify(dict(ok=True, **payload))
