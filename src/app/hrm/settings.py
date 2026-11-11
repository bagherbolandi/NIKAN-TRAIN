"""Configurable HR rules, HRM user/role management and shared lookups."""
import json

from flask import g, jsonify, request
from werkzeug.security import generate_password_hash

from ..validation import positive_id
from . import access
from .schema import DEFAULT_SETTINGS

SETTING_LABELS = {
    'contract.alert_days': 'روز هشدار پیش از انقضای قرارداد',
    'contract.renewal_limit': 'حداکثر دفعات تمدید مجاز',
    'contract.default_months': 'مدت پیش‌فرض قرارداد (ماه)',
    'leave.annual_limit': 'سقف مرخصی سالانه (روز)',
    'leave.max_per_request': 'حداکثر طول هر درخواست مرخصی (روز)',
    'loan.max_active': 'حداکثر وام فعال هم‌زمان هر فرد',
    'loan.max_amount': 'سقف مبلغ وام (ریال)',
    'welfare.max_per_month': 'حداکثر دفعات خدمات رفاهی در ماه',
    'reservation.max_nights': 'حداکثر شب‌های هر رزرو',
    'reservation.min_lead_days': 'حداقل فاصله تا شروع رزرو (روز)',
    'reservation.max_per_year': 'حداکثر رزرو هر فرد در سال',
    'request.approval_enabled': 'فعال‌بودن گردش تأیید درخواست‌ها',
    'date.calendar': 'تقویم رابط کاربری',
    'date.storage': 'قالب ذخیره‌سازی تاریخ در پایگاه داده',
}
LEGACY_ROLE = {
    'system_admin': 'admin', 'holding_hr': 'viewer', 'company_hr': 'manager',
    'unit_manager': 'viewer', 'employee': 'viewer', 'executive': 'viewer',
}


def register(ctx):
    @ctx.route('settings', permission='settings.view')
    def hrm_settings_list():
        rows = ctx.rows(
            'SELECT key,company_id,value,updated_at,updated_by FROM hrm_settings '
            'WHERE company_id=0 OR company_id=? ORDER BY key,company_id', (g.hrm_cid,))
        by_key = {}
        for row in rows:
            by_key.setdefault(row['key'], row)
            if row['company_id'] == g.hrm_cid:
                by_key[row['key']] = row
        return jsonify([dict(key=key, label=SETTING_LABELS.get(key, key), value=row['value'],
                             global_value=row['value'] if row['company_id'] == 0 else None,
                             company_id=row['company_id'], updated_at=row['updated_at'])
                        for key, row in sorted(by_key.items())])

    @ctx.route('settings', methods=('PUT',), permission='settings.edit')
    def hrm_settings_update():
        body = ctx.body()
        key = ctx.text(body, 'key', required=True, maximum=120)
        if key not in DEFAULT_SETTINGS:
            raise ValueError('ضابطه تعریف‌شده‌ای با این نام وجود ندارد')
        raw = body.get('value')
        if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
            raise ValueError('مقدار ضابطه معتبر نیست')
        value = str(raw).strip()
        if len(value) > 120:
            raise ValueError('مقدار ضابطه بیش از حد مجاز است')
        try:
            float(value)
        except ValueError:
            if key not in ('date.calendar', 'date.storage'):
                raise ValueError('مقدار ضابطه باید عددی باشد') from None
        ctx.c.execute(
            'INSERT INTO hrm_settings(key,company_id,value,updated_at,updated_by) VALUES (?,?,?,?,?) '
            'ON CONFLICT(key,company_id) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,updated_by=excluded.updated_by',
            (key, g.hrm_cid, value, ctx.now(), g.user['id']))
        ctx.audit_log('update_hrm_setting', f'{key}={value}')
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('catalog', company=False)
    def hrm_catalog():
        return jsonify(roles=access.ROLES, scopes=access.SCOPES,
                       permissions=access.ALL_PERMISSIONS,
                       confidential=access.CONFIDENTIAL,
                       setting_labels=SETTING_LABELS)

    def user_payload(row):
        conn = ctx.c
        return {
            'id': row['id'], 'username': row['username'], 'legacy_role': row['role'],
            'role': row['hrm_role'] or '', 'scope': row['hrm_scope'] or access.DEFAULT_SCOPE.get(row['hrm_role'], ''),
            'company_id': row['company_id'], 'employee_id': row['employee_id'],
            'unit_id': row['unit_id'], 'active': row['active'],
            'permissions': sorted(access.effective_permissions(conn, row)),
            'confidential': access.granting_roles(conn, row),
            'companies': [r['company_id'] for r in conn.execute(
                'SELECT company_id FROM user_companies WHERE user_id=?', (row['id'],))],
            'units': [r['unit_id'] for r in conn.execute(
                'SELECT unit_id FROM user_units WHERE user_id=?', (row['id'],))],
        }

    @ctx.route('users', permission='users.view')
    def hrm_users_list():
        allowed = {c['id'] for c in access.visible_companies(ctx.c, g.user)}
        if not allowed:
            return jsonify([])
        marks = ','.join('?' for _ in allowed)
        rows = ctx.c.execute('SELECT * FROM users ORDER BY id').fetchall()
        return jsonify([user_payload(r) for r in rows
                        if r['role'] == 'admin' or r['company_id'] in allowed])

    @ctx.route('users', methods=('POST',), permission='users.permissions')
    def hrm_users_create():
        body = ctx.body()
        username = ctx.text(body, 'username', required=True, maximum=100)
        password = body.get('password')
        role = body.get('role')
        if len(username) < 3 or not isinstance(password, str) or len(password) < 12:
            raise ValueError('نام کاربری حداقل ۳ و رمز عبور حداقل ۱۲ نویسه باشد')
        if role not in access.ROLES:
            raise ValueError('نقش منابع انسانی معتبر نیست')
        company_id = positive_id(body.get('company_id', g.hrm_cid))
        access.require(ctx.c, g.user, company_id)
        scope = body.get('scope') or access.DEFAULT_SCOPE[role]
        if scope not in access.SCOPES:
            raise ValueError('دامنه دسترسی معتبر نیست')
        employee_id = body.get('employee_id')
        if employee_id not in (None, ''):
            employee_id = positive_id(employee_id)
            ctx.employee(employee_id, company_id)
        cur = ctx.c.execute(
            'INSERT INTO users(username,password,role,company_id,hrm_role,hrm_scope,employee_id) VALUES (?,?,?,?,?,?,?)',
            (username, generate_password_hash(password), LEGACY_ROLE[role], company_id,
             role, scope, employee_id))
        uid = cur.lastrowid
        grants = body.get('grants', [])
        if not isinstance(grants, list):
            raise ValueError('فهرست مجوزها معتبر نیست')
        for code in grants:
            if code not in access.ALL_PERMISSIONS:
                raise ValueError('کد مجوز معتبر نیست')
            ctx.c.execute(
                'INSERT OR REPLACE INTO user_permissions(user_id,permission,mode,granted_by) VALUES (?,?,?,?)',
                (uid, code, 'grant', g.user['id']))
        ctx.audit_log('create_hrm_user', username)
        ctx.c.commit()
        return jsonify(id=cur.lastrowid), 201

    @ctx.route('users/<int:uid>', methods=('PUT',), permission='users.permissions')
    def hrm_users_update(uid):
        uid = positive_id(uid)
        target = ctx.c.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
        if not target:
            return ctx.error('کاربر یافت نشد', 404)
        body = ctx.body()
        changes = {}
        if 'role' in body:
            role = body['role']
            if role not in access.ROLES and role != '':
                raise ValueError('نقش منابع انسانی معتبر نیست')
            changes['hrm_role'] = role
            if role:
                changes['role'] = LEGACY_ROLE[role]
                changes['hrm_scope'] = access.DEFAULT_SCOPE[role]
        if 'scope' in body:
            if body['scope'] not in access.SCOPES:
                raise ValueError('دامنه دسترسی معتبر نیست')
            changes['hrm_scope'] = body['scope']
        if 'company_id' in body:
            cid = positive_id(body['company_id'])
            access.require(ctx.c, g.user, cid)
            changes['company_id'] = cid
        if 'employee_id' in body:
            eid = body['employee_id']
            changes['employee_id'] = None if eid in (None, '') else positive_id(eid)
        if 'unit_id' in body:
            uid2 = body['unit_id']
            changes['unit_id'] = None if uid2 in (None, '') else positive_id(uid2)
        if 'active' in body:
            if uid == g.user['id']:
                raise ValueError('حساب خود را نمی‌توانید غیرفعال کنید')
            changes['active'] = 1 if body['active'] in (1, True, '1') else 0
        if 'password' in body:
            if not isinstance(body['password'], str) or len(body['password']) < 12:
                raise ValueError('رمز عبور حداقل ۱۲ نویسه باشد')
            changes['password'] = generate_password_hash(body['password'])
            changes['auth_version'] = target['auth_version'] + 1
        if changes:
            ctx.c.execute('UPDATE users SET ' + ','.join(k + '=?' for k in changes) + ' WHERE id=?',
                          list(changes.values()) + [uid])
        for key, table, column, validate in [
            ('companies', 'user_companies', 'company_id', True),
            ('units', 'user_units', 'unit_id', False),
        ]:
            if key in body:
                values = body[key]
                if not isinstance(values, list):
                    raise ValueError('فهرست دسترسی‌ها معتبر نیست')
                parsed = []
                for value in values:
                    rid = positive_id(value)
                    if validate:
                        access.require(ctx.c, g.user, rid)
                    parsed.append(rid)
                ctx.c.execute(f'DELETE FROM {table} WHERE user_id=?', (uid,))
                ctx.c.executemany(f'INSERT INTO {table}(user_id,{column}) VALUES (?,?)',
                                  [(uid, rid) for rid in parsed])
        grants = body.get('grants', [])
        revokes = body.get('revokes', [])
        for label, values, mode in [('grant', grants, 'grant'), ('revoke', revokes, 'revoke')]:
            if not isinstance(values, list):
                raise ValueError('فهرست مجوزها معتبر نیست')
            for code in values:
                if code not in access.ALL_PERMISSIONS:
                    raise ValueError('کد مجوز معتبر نیست')
                ctx.c.execute(
                    'INSERT INTO user_permissions(user_id,permission,mode,granted_by) VALUES (?,?,?,?) '
                    'ON CONFLICT(user_id,permission) DO UPDATE SET mode=excluded.mode,granted_by=excluded.granted_by',
                    (uid, code, mode, g.user['id']))
        ctx.audit_log('update_hrm_user', json.dumps(
            {'id': uid, 'changes': sorted(changes), 'grants': grants, 'revokes': revokes}, ensure_ascii=False))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('lookups')
    def hrm_lookups():
        from .personnel import employee_options
        cid = g.hrm_cid
        def q(table, where='company_id=?', params=None):
            return ctx.rows(f'SELECT * FROM {table} WHERE {where} ORDER BY id', params or (cid,))
        return jsonify(
            employees=employee_options(ctx.c, cid, g.user),
            org_units=q('org_units'),
            positions=q('positions'),
            work_locations=q('work_locations'),
            contract_types=q('contract_types', 'company_id=0 OR company_id=?', (cid,)),
            leave_types=q('leave_types', 'company_id=0 OR company_id=?', (cid,)),
            request_types=q('request_types', 'company_id=0 OR company_id=?', (cid,)),
            welfare_services=q('welfare_services', 'company_id=0 OR company_id=?', (cid,)),
            accommodations=q('accommodations', 'company_id IS NULL OR company_id=0 OR company_id=?', (cid,)),
            performance_periods=q('performance_periods'),
            performance_indicators=q('performance_indicators'),
            users=[dict(r) for r in ctx.c.execute(
                'SELECT id,username,hrm_role,company_id FROM users WHERE active=1 ORDER BY id')],
            confidential=access.granting_roles(ctx.c, g.user),
        )


def setting_number(conn, cid, key, default=0, integer=True):
    row = conn.execute(
        'SELECT value FROM hrm_settings WHERE key=? AND company_id IN (?,0) ORDER BY company_id DESC LIMIT 1',
        (key, cid)).fetchone()
    if not row:
        return default
    try:
        return int(row['value']) if integer else float(row['value'])
    except (TypeError, ValueError):
        return default
