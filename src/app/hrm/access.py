"""Server-side role, company/unit scope and confidential-document permissions.

The three legacy roles (admin/manager/viewer) remain untouched. HRM adds an
orthogonal layer: hrm_role + explicit grants/revokes + company/unit/self scope.
A system administrator has NO implicit access to confidential documents.
"""
from flask import g

ROLES = {
    'system_admin': 'مدیر سیستم',
    'holding_hr': 'مسئول منابع انسانی هلدینگ',
    'company_hr': 'مسئول منابع انسانی شرکت',
    'unit_manager': 'مدیر واحد',
    'employee': 'کارمند',
    'executive': 'مدیریت ارشد',
}
SCOPES = {
    'holding': 'تمام شرکت‌های مجاز',
    'company': 'شرکت تعیین‌شده',
    'unit': 'واحد تحت مسئولیت',
    'self': 'فقط اطلاعات خود',
}
DEFAULT_SCOPE = {
    'system_admin': 'holding',
    'holding_hr': 'holding',
    'company_hr': 'company',
    'unit_manager': 'unit',
    'employee': 'self',
    'executive': 'holding',
}

HR = {'personnel', 'contract', 'attendance', 'performance', 'benefits', 'welfare',
      'documents', 'requests', 'reports', 'settings', 'users'}
ACTIONS = {'view', 'edit'}
PERMISSIONS = {f'{area}.{action}' for area in HR for action in ACTIONS}
PERMISSIONS |= {'attendance.import', 'reports.export', 'reports.management',
                'requests.create', 'requests.approve', 'users.permissions', 'welfare.reserve'}
CONFIDENTIAL = {
    'contract': 'قراردادها و الحاقیه‌ها',
    'documents': 'مدارک شخصی و استخدامی',
    'discipline': 'سوابق تشویقی و انضباطی',
    'loan': 'وام‌ها و تسهیلات',
}
CONFIDENTIAL_CODES = {f'confidential.{key}' for key in CONFIDENTIAL}

_WRITE_AREAS = {
    'company_hr': ['personnel', 'contract', 'attendance', 'performance', 'benefits',
                   'welfare', 'documents', 'requests', 'settings'],
    'holding_hr': ['personnel', 'contract', 'attendance', 'performance', 'benefits',
                   'welfare', 'documents', 'requests', 'settings'],
}
_READ_ALL = {'personnel', 'contract', 'attendance', 'performance', 'benefits',
             'welfare', 'documents', 'requests', 'reports'}

BASE = {
    'system_admin': {'users.view', 'users.edit', 'users.permissions', 'settings.view',
                     'settings.edit', 'reports.view', 'reports.export', 'reports.management'},
    'holding_hr': {f'{area}.view' for area in _READ_ALL} | {f'{area}.edit' for area in _WRITE_AREAS['holding_hr']}
                  | {'attendance.import', 'reports.export', 'requests.create', 'requests.approve', 'welfare.reserve'},
    'company_hr': {f'{area}.view' for area in _READ_ALL} | {f'{area}.edit' for area in _WRITE_AREAS['company_hr']}
                  | {'attendance.import', 'reports.export', 'requests.create', 'requests.approve', 'welfare.reserve'},
    'unit_manager': {'personnel.view', 'attendance.view', 'performance.view', 'requests.view',
                     'requests.approve', 'reports.view', 'documents.view'},
    'employee': {'personnel.view', 'attendance.view', 'performance.view', 'requests.view',
                 'requests.create', 'welfare.view', 'welfare.reserve', 'documents.view'},
    'executive': {f'{area}.view' for area in _READ_ALL} | {'reports.export', 'reports.management'},
}
ALL_PERMISSIONS = sorted(PERMISSIONS | CONFIDENTIAL_CODES | {p for group in BASE.values() for p in group})


def _text(row, key, default=''):
    value = row[key] if key in row.keys() else default
    return value if value is not None else default


def effective_permissions(conn, user):
    role = _text(user, 'hrm_role')
    perms = set(BASE.get(role, set()))
    for row in conn.execute('SELECT permission,mode FROM user_permissions WHERE user_id=?', (user['id'],)):
        if row['mode'] == 'grant':
            perms.add(row['permission'])
        else:
            perms.discard(row['permission'])
    return perms


def granting_roles(conn, user):
    """Explicit confidential grants only — never inherited from a role."""
    perms = effective_permissions(conn, user)
    return sorted(key for key in CONFIDENTIAL if f'confidential.{key}' in perms)


def can_access_company(conn, user, cid):
    if _text(user, 'role') == 'admin' or _text(user, 'hrm_scope') == 'holding':
        return True
    if user['company_id'] is not None and int(user['company_id']) == int(cid):
        return True
    return conn.execute('SELECT 1 FROM user_companies WHERE user_id=? AND company_id=?',
                        (user['id'], cid)).fetchone() is not None


def visible_companies(conn, user):
    if _text(user, 'role') == 'admin' or _text(user, 'hrm_scope') == 'holding':
        return [dict(r) for r in conn.execute('SELECT id,name FROM companies ORDER BY id')]
    ids = {int(user['company_id'])} if user['company_id'] is not None else set()
    ids |= {int(r['company_id']) for r in conn.execute('SELECT company_id FROM user_companies WHERE user_id=?', (user['id'],))}
    if not ids:
        return []
    marks = ','.join('?' for _ in ids)
    return [dict(r) for r in conn.execute(f'SELECT id,name FROM companies WHERE id IN ({marks}) ORDER BY id', tuple(ids))]


def require(conn, user, cid, permission=None, confidential=None):
    """Raise PermissionError unless role, scope and confidential grants all pass."""
    if cid is not None and not can_access_company(conn, user, cid):
        raise PermissionError('دسترسی به اطلاعات این شرکت مجاز نیست')
    if permission and permission not in effective_permissions(conn, user):
        raise PermissionError('مجوز انجام این عملیات برای نقش شما تعریف نشده است')
    if confidential:
        if confidential not in CONFIDENTIAL:
            raise PermissionError('دامنه محرمانگی نامعتبر است')
        code = f'confidential.{confidential}'
        if code not in effective_permissions(conn, user):
            raise PermissionError('دسترسی به اسناد محرمانه نیازمند مجوز صریح جداگانه است')


def employee_allowed(conn, user, employee):
    """Unit/self scope check for a concrete employee row."""
    scope = _text(user, 'hrm_scope')
    if scope == 'self':
        return user['employee_id'] is not None and int(user['employee_id']) == int(employee['id'])
    if scope == 'unit':
        unit_id = employee['unit_id'] if 'unit_id' in employee.keys() else None
        allowed = {int(r['unit_id']) for r in conn.execute('SELECT unit_id FROM user_units WHERE user_id=?', (user['id'],))}
        if user['unit_id'] is not None:
            allowed.add(int(user['unit_id']))
        return unit_id is not None and int(unit_id) in allowed
    return True


def profile(conn, user):
    return {
        'role': _text(user, 'hrm_role'),
        'role_label': ROLES.get(_text(user, 'hrm_role'), 'بدون نقش منابع انسانی'),
        'scope': _text(user, 'hrm_scope'),
        'scope_label': SCOPES.get(_text(user, 'hrm_scope'), 'تعیین‌نشده'),
        'legacy_role': _text(user, 'role'),
        'employee_id': user['employee_id'] if 'employee_id' in user.keys() else None,
        'unit_id': user['unit_id'] if 'unit_id' in user.keys() else None,
        'permissions': sorted(effective_permissions(conn, user)),
        'confidential': granting_roles(conn, user),
        'all_permissions': ALL_PERMISSIONS,
        'confidential_domains': CONFIDENTIAL,
        'roles': ROLES,
        'scopes': SCOPES,
    }


def user_is_hrm(user):
    return _text(user, 'hrm_role') in ROLES
