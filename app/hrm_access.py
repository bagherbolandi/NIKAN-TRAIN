"""Additive HRM permissions: explicit module grants and company/unit scopes.

The primary holding administrator has full access. Every delegated manager
requires explicit operation grants and company/site/unit scopes.
"""
import re
from flask import g, jsonify, request, has_request_context
from werkzeug.security import generate_password_hash
from .validation import json_object, positive_id

ROLES = {
    'system_admin': 'مدیر سیستم',
    'holding_manager': 'مدیر هلدینگی سامانه',
    'holding_hr': 'مسئول منابع انسانی هلدینگ',
    'company_hr': 'مسئول منابع انسانی شرکت',
    'unit_manager': 'مدیر واحد',
    'training_officer': 'مسئول آموزش',
    'hse_officer': 'مسئول HSE',
    'executive': 'مدیریت ارشد',
}
MODULES = {
    'personnel': 'پرونده پرسنلی', 'contracts': 'قراردادها و کارگزینی',
    'documents': 'مدارک استخدامی', 'attendance': 'حضور و غیاب',
    'performance': 'ارزیابی عملکرد', 'productivity': 'آکورد بهره‌وری', 'discipline': 'تشویق و انضباط',
    'loans': 'وام‌ها و تسهیلات', 'welfare': 'خدمات رفاهی',
    'accommodation': 'رزرو اقامتگاه', 'hse': 'ایمنی و حوادث',
    'health_environment': 'بهداشت و محیط زیست', 'health': 'سلامت شغلی محرمانه',
    'training': 'آموزش و توسعه', 'reports': 'داشبورد و گزارش‌ها',
    'backup.full_export': 'خروجی کامل و کلید بازیابی', 'settings': 'تنظیمات منابع انسانی',
}
CONFIDENTIAL = frozenset({'contracts', 'documents', 'discipline', 'loans', 'health', 'productivity', 'backup.full_export'})
MIGRATION_VERSION = 'hrm-1'
ACCESS_DDL = """CREATE TABLE {name}(
 user_id INTEGER PRIMARY KEY REFERENCES users(id),role TEXT NOT NULL,
 employee_id INTEGER UNIQUE REFERENCES employees(id),
 training_access INTEGER NOT NULL DEFAULT 0 CHECK(training_access IN (0,1)),
 all_companies INTEGER NOT NULL DEFAULT 0 CHECK(all_companies IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"""
SCOPES_DDL = """CREATE TABLE {name}(
 user_id INTEGER NOT NULL REFERENCES hrm_user_access(user_id) ON DELETE CASCADE,
 company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '')"""
PERMISSIONS_DDL = """CREATE TABLE {name}(
 user_id INTEGER NOT NULL REFERENCES hrm_user_access(user_id) ON DELETE CASCADE,
 module TEXT NOT NULL,can_read INTEGER NOT NULL DEFAULT 0 CHECK(can_read IN (0,1)),
 can_write INTEGER NOT NULL DEFAULT 0 CHECK(can_write IN (0,1)),
 can_create INTEGER NOT NULL DEFAULT 0 CHECK(can_create IN (0,1)),
 can_edit INTEGER NOT NULL DEFAULT 0 CHECK(can_edit IN (0,1)),
 can_delete INTEGER NOT NULL DEFAULT 0 CHECK(can_delete IN (0,1)),
 can_export INTEGER NOT NULL DEFAULT 0 CHECK(can_export IN (0,1)),PRIMARY KEY(user_id,module))"""
OPERATIONS = ('read', 'create', 'edit', 'delete', 'export')
ALL_COMPANY_ROLES = frozenset({'system_admin','holding_manager','holding_hr','training_officer','hse_officer','executive'})
RECOVERY_ADDITIONS = {'hrm_user_access': 0, 'hrm_user_scopes': 0,
                      'hrm_user_permissions': 0, 'hrm_schema_migrations': 3}


def initialize(conn):
    """Preserve early HRM prototypes while upgrading grants and site scopes."""
    conn.commit()
    conn.execute('PRAGMA foreign_keys=OFF')
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('CREATE TABLE IF NOT EXISTS hrm_schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
            if not _table_exists(conn, 'hrm_user_access'):
                conn.execute(ACCESS_DDL.format(name='hrm_user_access'))
            else:
                fields = {r['name'] for r in conn.execute('PRAGMA table_info(hrm_user_access)')}
                sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='hrm_user_access'").fetchone()[0]
                if 'all_companies' not in fields or 'CHECK(role' in sql.replace(' ', ''):
                    conn.execute(ACCESS_DDL.format(name='_hrm_access_upgrade'))
                    old = ['user_id','role','employee_id','training_access','version','updated']
                    columns = old + (['all_companies'] if 'all_companies' in fields else [])
                    conn.execute('INSERT INTO _hrm_access_upgrade('+','.join(columns)+') SELECT '+','.join(columns)+' FROM hrm_user_access')
                    conn.execute('DROP TABLE hrm_user_access')
                    conn.execute('ALTER TABLE _hrm_access_upgrade RENAME TO hrm_user_access')
            if not _table_exists(conn, 'hrm_user_scopes'):
                conn.execute(SCOPES_DDL.format(name='hrm_user_scopes'))
            elif 'site_id' not in {r['name'] for r in conn.execute('PRAGMA table_info(hrm_user_scopes)')}:
                conn.execute(SCOPES_DDL.format(name='_hrm_scopes_upgrade'))
                conn.execute('INSERT INTO _hrm_scopes_upgrade(user_id,company_id,department) SELECT user_id,company_id,department FROM hrm_user_scopes')
                conn.execute('DROP TABLE hrm_user_scopes')
                conn.execute('ALTER TABLE _hrm_scopes_upgrade RENAME TO hrm_user_scopes')
            conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS hrm_user_scope_unique ON hrm_user_scopes(user_id,company_id,COALESCE(site_id,0),department)')
            if not _table_exists(conn, 'hrm_user_permissions'):
                conn.execute(PERMISSIONS_DDL.format(name='hrm_user_permissions'))
            elif 'can_create' not in {r['name'] for r in conn.execute('PRAGMA table_info(hrm_user_permissions)')}:
                conn.execute(PERMISSIONS_DDL.format(name='_hrm_permissions_upgrade'))
                conn.execute("""INSERT INTO _hrm_permissions_upgrade(user_id,module,can_read,can_write,can_create,can_edit,can_delete,can_export)
                                SELECT user_id,module,can_read,can_write,can_write,can_write,can_write,can_read FROM hrm_user_permissions""")
                conn.execute('DROP TABLE hrm_user_permissions')
                conn.execute('ALTER TABLE _hrm_permissions_upgrade RENAME TO hrm_user_permissions')
            conn.execute('INSERT OR IGNORE INTO hrm_schema_migrations(version) VALUES (?)', (MIGRATION_VERSION,))
    finally:
        conn.execute('PRAGMA foreign_keys=ON')


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def effective_column(conn, column, alias='e'):
    """Current assignment SQL; original employees ownership remains untouched."""
    if column not in ('company_id','department','job','site_id') or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', alias):
        raise ValueError('ستون محدوده دسترسی معتبر نیست')
    present = _table_exists(conn, 'hrm_employee_details')
    columns = {r['name'] for r in conn.execute('PRAGMA table_info(hrm_employee_details)')} if present else set()
    fallback = 'NULL' if column == 'site_id' else f'{alias}.{column}'
    if column in columns:
        return f'COALESCE((SELECT hd.{column} FROM hrm_employee_details hd WHERE hd.employee_id={alias}.id),{fallback})'
    return fallback


def _base_user(user):
    original = getattr(g, 'hrm_original_user', None)
    return original if original is not None and user is not None and original['id'] == user['id'] else user


def _context(conn, user):
    user = _base_user(user)
    if user is None:
        raise PermissionError()
    user = dict(user)
    binding = conn.execute('SELECT * FROM hrm_user_access WHERE user_id=?', (user['id'],)).fetchone()
    role = binding['role'] if binding else ('system_admin' if user['role']=='admin' else 'unassigned')
    primary = user['role']=='admin' or role=='system_admin'
    if binding and role not in ROLES:
        raise PermissionError()
    permissions = {key: {**{op: False for op in OPERATIONS}, 'write': False} for key in MODULES}
    scopes = [dict(row) for row in conn.execute('SELECT company_id,site_id,department FROM hrm_user_scopes WHERE user_id=? ORDER BY company_id,site_id,department',(user['id'],))] if binding else []
    all_companies = primary or bool(binding and binding['all_companies'])
    if all_companies:
        scopes = [{'company_id': row[0], 'site_id': None, 'department': ''} for row in conn.execute('SELECT id FROM companies ORDER BY id')]
    if binding:
        for row in conn.execute('SELECT * FROM hrm_user_permissions WHERE user_id=?',(user['id'],)):
            if row['module'] in permissions:
                flags={op:bool(row['can_'+op]) for op in OPERATIONS}
                flags['write']=flags['create'] or flags['edit'] or flags['delete']
                permissions[row['module']]=flags
    if primary:
        permissions={key:{**{op:True for op in OPERATIONS},'write':True} for key in MODULES}
    ids=sorted({s['company_id'] for s in scopes})
    companies=[dict(r) for r in conn.execute('SELECT id,name FROM companies WHERE id IN ('+','.join('?' for _ in ids)+') ORDER BY id',ids)] if ids else []
    training = primary or (not binding) or bool(binding['training_access'])
    if binding and not primary:
        training = training and permissions['training']['read'] and role in ALL_COMPANY_ROLES
        training = training and all(not x['site_id'] and not x['department'] for x in scopes)
        training = training and (all_companies or len(scopes)==1)
    return dict(user_id=user['id'],configured=binding is not None,role='system_admin' if primary else role,
                employee_id=binding['employee_id'] if binding else None,scopes=scopes,company_ids=ids,companies=companies,
                permissions=permissions,modules=[m for m,f in permissions.items() if any(f[o] for o in OPERATIONS)],
                training_access=bool(training),all_companies=all_companies,is_primary_admin=primary,primary_admin=primary,
                can_manage_access=primary,version=binding['version'] if binding else 0)


def ctx(conn):
    return _context(conn,getattr(g,'user',None))


def _operation(module, write=False, operation=None):
    if operation is not None:
        if operation not in OPERATIONS: raise ValueError('نوع عملیات دسترسی معتبر نیست')
        return operation
    if write:
        method=request.method if has_request_context() else 'PUT'
        return {'POST':'create','PUT':'edit','PATCH':'edit','DELETE':'delete'}.get(method,'edit')
    if has_request_context():
        marker=getattr(g,'hrm_reference_operation',None)
        if isinstance(marker,dict) and marker.get('module')==module and marker.get('operation') in OPERATIONS:
            return marker['operation']
        if request.path.startswith('/api/hrm/reports/') and request.path.endswith('.xlsx'):
            return 'export'
    return 'read'


def require(conn,module,write=False,operation=None):
    if module not in MODULES: raise ValueError('بخش منابع انسانی معتبر نیست')
    actor=ctx(conn)
    if not actor['permissions'][module][_operation(module,write,operation)]: raise PermissionError()
    return actor


def _predicate(actor,company,site,department=None):
    if actor['is_primary_admin']: return '1',[]
    clauses,values=[],[]
    for scope in actor['scopes']:
        if scope['department'] and department is None: continue
        part=company+'=?'; args=[scope['company_id']]
        if scope.get('site_id') is not None:
            part+=' AND '+site+'=?';args.append(scope['site_id'])
        if scope['department']:
            part+=' AND '+department+'=?';args.append(scope['department'])
        clauses.append('('+part+')');values.extend(args)
    return ('('+' OR '.join(clauses)+')',values) if clauses else ('0',[])


def scope_sql(conn,module,alias='e',write=False,operation=None,record_alias=None):
    if record_alias is not None:
        return record_scope_sql(conn,module,record_alias,write,operation)
    actor=require(conn,module,write,operation)
    return _predicate(actor,effective_column(conn,'company_id',alias),effective_column(conn,'site_id',alias),effective_column(conn,'department',alias))


def record_scope_sql(conn,module,alias='r',write=False,operation=None,department_column=None):
    actor=require(conn,module,write,operation)
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',alias): raise ValueError('نام محدوده دسترسی معتبر نیست')
    if department_column is not None and not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*\.[A-Za-z_][A-Za-z_0-9]*',department_column):
        raise ValueError('ستون واحد سابقه معتبر نیست')
    return _predicate(actor,alias+'.company_id',alias+'.site_id',department_column)


def employee(conn,eid,module,write=False,operation=None):
    eid=positive_id(eid); predicate,values=scope_sql(conn,module,'e',write,operation)
    columns=','.join(effective_column(conn,c)+' AS effective_'+c for c in ('company_id','department','job','site_id'))
    row=conn.execute('SELECT e.*,'+columns+' FROM employees e WHERE e.id=? AND ('+predicate+')',[eid,*values]).fetchone()
    if row is None: raise PermissionError()
    result=dict(row);result['training_company_id']=result['company_id']
    for c in ('company_id','department','job','site_id'): result[c]=result.pop('effective_'+c)
    return result


def company_scope(conn,cid,module,write=False,operation=None,site_id=None,department=None):
    actor=require(conn,module,write,operation);cid=positive_id(cid)
    if not conn.execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone(): raise ValueError('شرکت یافت نشد')
    if site_id not in (None,''):
        site_id=positive_id(site_id)
        if not _table_exists(conn,'hrm_sites') or not conn.execute('SELECT 1 FROM hrm_sites WHERE id=? AND company_id=?',(site_id,cid)).fetchone():
            raise ValueError('سایت متعلق به شرکت انتخاب‌شده نیست')
    else: site_id=None
    if actor['is_primary_admin']: return cid
    for scope in actor['scopes']:
        if scope['company_id']==cid and (scope.get('site_id') is None or scope['site_id']==site_id) and (not scope['department'] or scope['department']==department):
            return cid
    raise PermissionError()


def site_scope(conn,cid,site_id,module,write=False,operation=None,department=None):
    company_scope(conn,cid,module,write,operation,site_id,department)
    return positive_id(site_id) if site_id not in (None,'') else None


def record_scope(conn,row,module,write=False,operation=None):
    row=dict(row)
    company_scope(conn,row['company_id'],module,write,operation,row.get('site_id'),row.get('record_department',row.get('department')))
    return row


check_record_scope=record_scope


def actor_summary(conn,user=None):
    actor=_context(conn,user if user is not None else getattr(g,'user',None))
    if actor['is_primary_admin']: actor['configuration_companies']=actor['companies']
    return actor


def legacy_guard(conn,endpoint=None):
    """Delegate training only; never turn a training role into HRM/root access."""
    if not has_request_context(): return
    if not hasattr(g,'hrm_original_user'): g.hrm_original_user=g.user
    actor=ctx(conn);path=request.path
    if actor['is_primary_admin']:
        adapted=dict(g.hrm_original_user);adapted['role']='admin';g.user=adapted
        return
    if path.startswith('/api/hrm/') or path in ('/api/logout','/api/desktop-exit'): return
    if not actor['configured']: return
    forbidden=('/api/users','/api/companies','/api/branding','/api/network','/api/audit','/api/backup','/api/recovery')
    if any(path==prefix or path.startswith(prefix+'/') for prefix in forbidden): raise PermissionError()
    if not actor['training_access']: 
        if path=='/api/me': return
        raise PermissionError()
    # Editing the common competency bank remains a holding administrator task.
    if path=='/api/competency-bank' and request.method not in ('GET','HEAD'): raise PermissionError()
    if path.startswith('/api/competency-bank/') and request.method in ('PUT','DELETE'): raise PermissionError()
    if path.startswith('/api/data/skills') and request.method not in ('GET','HEAD'): raise PermissionError()
    if path=='/api/import': raise PermissionError()
    if path.startswith('/api/excel/') and ('/companies' in path or '/competency-bank' in path): raise PermissionError()
    if (path=='/api/data/employees' or path.startswith('/api/data/employees/')) and request.method not in ('GET','HEAD'):
        require(conn,'personnel',operation={'POST':'create','PUT':'edit','PATCH':'edit','DELETE':'delete'}.get(request.method,'read'))
    if path=='/api/excel/import/employees':
        require(conn,'personnel',operation='create')
        if request.form.get('policy')=='upsert': require(conn,'personnel',operation='edit')
    allowed=('/api/data/','/api/passports/','/api/certificates','/api/competency-bank',
             '/api/calendar','/api/courses/','/api/holding/','/api/development-plans/',
             '/api/assessment-workspace/','/api/job-workspace','/api/job-summary','/api/needs-summary',
             '/api/reference-options','/api/gaps','/api/dashboard','/api/export','/api/import',
             '/api/excel/','/api/search','/api/management-report','/api/job-profiles/')
    if path!='/api/me' and not any(path.startswith(prefix) for prefix in allowed): raise PermissionError()
    if path!='/api/me':
        operation={'POST':'create','PUT':'edit','PATCH':'edit','DELETE':'delete'}.get(request.method,'read')
        if path in ('/api/export','/api/export.xlsx') or path.endswith('.docx'): operation='export'
        require(conn,'training',operation=operation)
    # All-company delegation can use consolidated training routes, but ctx()
    # continues to resolve the real account for every HRM permission check.
    adapted=dict(g.hrm_original_user)
    adapted['role']='admin' if actor['all_companies'] else ('manager' if any(actor['permissions']['training'][o] for o in ('create','edit','delete')) else 'viewer')
    if not actor['all_companies'] and actor['scopes']: adapted['company_id']=actor['scopes'][0]['company_id']
    g.user=adapted

def _access_admin(conn):
    if not ctx(conn)['can_manage_access']:
        raise PermissionError()


def _binding_values(conn,body,legacy_role,legacy_company_id):
    role=body.get('role')
    if role not in ROLES: raise ValueError('نقش منابع انسانی معتبر نیست')
    eid=positive_id(body['employee_id']) if body.get('employee_id') not in (None,'') else None
    if eid and not conn.execute('SELECT 1 FROM employees WHERE id=?',(eid,)).fetchone():
        raise ValueError('فرد متصل به حساب یافت نشد')
    all_companies=body.get('all_companies',False)
    if type(all_companies) is not bool: raise ValueError('دسترسی همه شرکت‌ها باید صریح باشد')
    if all_companies and role not in ALL_COMPANY_ROLES: raise ValueError('این نقش دسترسی همه شرکت‌ها ندارد')
    raw=body.get('scopes',[])
    if not isinstance(raw,list) or len(raw)>500: raise ValueError('فهرست محدوده‌های دسترسی معتبر نیست')
    scopes,seen=[],set()
    for value in raw:
        if not isinstance(value,dict) or set(value)-{'company_id','site_id','department'}: raise ValueError('محدوده دسترسی معتبر نیست')
        cid=positive_id(value.get('company_id'));site=value.get('site_id')
        site=positive_id(site) if site not in (None,'') else None
        dept=value.get('department','')
        if not isinstance(dept,str) or len(dept)>200: raise ValueError('واحد سازمانی معتبر نیست')
        dept=dept.strip()
        if not conn.execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone(): raise ValueError('شرکت یافت نشد')
        if site is not None and (not _table_exists(conn,'hrm_sites') or not conn.execute('SELECT 1 FROM hrm_sites WHERE id=? AND company_id=?',(site,cid)).fetchone()):
            raise ValueError('سایت متعلق به شرکت محدوده نیست')
        if role=='unit_manager' and not dept: raise ValueError('محدوده مدیر واحد باید واحد مشخص داشته باشد')
        if all_companies and (site or dept): raise ValueError('دسترسی همه شرکت‌ها با محدودیت سایت یا واحد هم‌زمان نیست')
        key=(cid,site,dept)
        if key in seen: raise ValueError('محدوده دسترسی تکراری است')
        seen.add(key);scopes.append({'company_id':cid,'site_id':site,'department':dept})
    if role!='system_admin' and not all_companies and not scopes: raise ValueError('حداقل یک محدوده شرکت یا سایت لازم است')
    if role=='company_hr' and len({x['company_id'] for x in scopes})!=1: raise ValueError('مسئول شرکت فقط یک شرکت را مدیریت می‌کند')
    raw_grants=body.get('permissions',{})
    if not isinstance(raw_grants,dict) or set(raw_grants)-set(MODULES): raise ValueError('مجوز بخش معتبر نیست')
    grants={}
    for module,flags in raw_grants.items():
        if not isinstance(flags,dict) or set(flags)-set(OPERATIONS)-{'write'} or any(type(v) is not bool for v in flags.values()):
            raise ValueError('مجوزهای عملیات باید مستقل و صریح باشند')
        old_write=flags.get('write',False)
        normalized={op:flags.get(op,old_write if op in ('create','edit','delete') else (flags.get('read',False) if op=='export' and 'write' in flags else False)) for op in OPERATIONS}
        normalized['write']=any(normalized[o] for o in ('create','edit','delete'));grants[module]=normalized
    training=body.get('training_access',False)
    if type(training) is not bool: raise ValueError('دسترسی آموزش باید صریح باشد')
    if training and role!='system_admin' and legacy_role!='admin':
        if role not in ALL_COMPANY_ROLES or not grants.get('training',{}).get('read'):
            raise ValueError('دسترسی آموزش به مجوز مشاهده آموزش برای نقش مجاز نیاز دارد')
        if not all_companies and not (len(scopes)==1 and not scopes[0]['site_id'] and not scopes[0]['department']):
            raise ValueError('آموزش قدیمی فقط با یک شرکت کامل یا دسترسی همه شرکت‌ها سازگار است')
    return role,eid,scopes,grants,training,all_companies


def _save_binding(conn,uid,values):
    role,eid,scopes,grants,training,all_companies=values
    conn.execute("""INSERT INTO hrm_user_access(user_id,role,employee_id,training_access,all_companies) VALUES (?,?,?,?,?)
       ON CONFLICT(user_id) DO UPDATE SET role=excluded.role,employee_id=excluded.employee_id,
       training_access=excluded.training_access,all_companies=excluded.all_companies,
       version=hrm_user_access.version+1,updated=CURRENT_TIMESTAMP""",(uid,role,eid,int(training),int(all_companies)))
    conn.execute('DELETE FROM hrm_user_scopes WHERE user_id=?',(uid,))
    conn.executemany('INSERT INTO hrm_user_scopes(user_id,company_id,site_id,department) VALUES (?,?,?,?)',[(uid,x['company_id'],x['site_id'],x['department']) for x in scopes])
    conn.execute('DELETE FROM hrm_user_permissions WHERE user_id=?',(uid,))
    columns=['user_id','module','can_write']+['can_'+op for op in OPERATIONS]
    conn.executemany('INSERT INTO hrm_user_permissions('+','.join(columns)+') VALUES ('+','.join('?' for _ in columns)+')',
                    [(uid,module,int(flags['write']),*[int(flags[op]) for op in OPERATIONS]) for module,flags in grants.items()])


def register(app, db, authenticated, audit, error):
    with app.app_context():
        initialize(db())

    def user_view(row):
        user = dict(row)
        return {key: user[key] for key in ('id', 'username', 'company_id', 'active')} | {
            'legacy_role': user['role'], **actor_summary(db(), row)}

    @app.get('/api/hrm/context')
    @authenticated
    def hrm_context():
        return jsonify(actor_summary(db()))

    @app.get('/api/hrm/access/options')
    @authenticated
    def hrm_access_options():
        _access_admin(db())
        rows = db().execute('SELECT e.id,e.name,e.code,' + effective_column(db(), 'company_id') + ' AS company_id,'
                            + effective_column(db(), 'department') + ' AS department,' + effective_column(db(), 'job')
                            + ' AS job FROM employees e ORDER BY e.name,e.id').fetchall()
        people = [dict(row) for row in rows]
        departments = sorted({(row['company_id'], row['department']) for row in people if row['department']})
        return jsonify(roles=[{'key': key, 'label': label} for key, label in ROLES.items()],
                       modules=[{'key': key, 'label': label, 'confidential': key in CONFIDENTIAL} for key, label in MODULES.items()],
                       companies=[dict(row) for row in db().execute('SELECT id,name FROM companies ORDER BY id')],
                       sites=[dict(row) for row in db().execute('SELECT id,company_id,name FROM hrm_sites ORDER BY company_id,name')] if _table_exists(db(),'hrm_sites') else [],
                       employees=people, departments=[{'company_id': cid, 'department': name} for cid, name in departments])

    @app.route('/api/hrm/access/users', methods=['GET', 'POST'])
    @authenticated
    def hrm_access_users():
        _access_admin(db())
        if request.method == 'GET':
            return jsonify([user_view(row) for row in db().execute('SELECT * FROM users ORDER BY id')])
        body = json_object()
        if set(body) - {'username', 'password', 'role', 'employee_id', 'scopes', 'permissions', 'training_access', 'all_companies', 'legacy_role', 'company_id', 'version'}:
            raise ValueError('فیلد تنظیم حساب معتبر نیست')
        username, password = body.get('username'), body.get('password')
        if not isinstance(username, str) or not 3 <= len(username.strip()) <= 100 or not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise ValueError('نام کاربری ۳ تا ۱۰۰ و رمز عبور ۱۲ تا ۱۰۲۴ نویسه لازم است')
        # New administrators get their authority from the revocable HRM binding.
        # The original holding administrator retains its legacy admin identity.
        legacy_role = body.get('legacy_role', 'viewer')
        if legacy_role not in ('admin', 'manager', 'viewer') or (legacy_role == 'admin' and body.get('role') != 'system_admin'):
            raise ValueError('نقش آموزش حساب معتبر نیست')
        scopes = body.get('scopes', [])
        cid_value = body.get('company_id') or (scopes[0].get('company_id') if isinstance(scopes, list) and scopes and isinstance(scopes[0], dict) else None)
        cid = positive_id(cid_value) if cid_value is not None else None
        if legacy_role != 'admin' and cid is None and not body.get('all_companies') and body.get('role') != 'system_admin':
            raise ValueError('شرکت حساب کاربری را مشخص کنید')
        password_hash = generate_password_hash(password)
        with db():
            db().execute('BEGIN IMMEDIATE')
            if cid and not db().execute('SELECT 1 FROM companies WHERE id=?', (cid,)).fetchone():
                raise ValueError('شرکت حساب کاربری یافت نشد')
            values = _binding_values(db(), body, legacy_role, cid)
            uid = db().execute('INSERT INTO users(username,password,role,company_id) VALUES (?,?,?,?)',
                               (username.strip(), password_hash, legacy_role, cid)).lastrowid
            _save_binding(db(), uid, values)
            audit('hrm_access_user_created', cid, str(uid))
        return jsonify(user_view(db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone())), 201

    @app.put('/api/hrm/access/users/<int:uid>')
    @authenticated
    def hrm_access_user_update(uid):
        _access_admin(db());uid = positive_id(uid)
        body = json_object()
        allowed = {'role', 'employee_id', 'scopes', 'permissions', 'training_access', 'all_companies', 'password', 'active', 'version'}
        if set(body) - allowed:
            raise ValueError('فیلد تنظیم حساب معتبر نیست')
        password_hash = None
        if 'password' in body:
            password = body['password']
            if not isinstance(password, str) or not 12 <= len(password) <= 1024:
                raise ValueError('رمز عبور باید ۱۲ تا ۱۰۲۴ نویسه باشد')
            password_hash = generate_password_hash(password)
        if 'active' in body and (type(body['active']) is not int or body['active'] not in (0, 1)):
            raise ValueError('وضعیت حساب معتبر نیست')
        if uid == g.user['id'] and body.get('active') == 0:
            raise ValueError('حساب خود را نمی‌توانید غیرفعال کنید')
        with db():
            db().execute('BEGIN IMMEDIATE')
            target = db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
            if target is None:
                return error('حساب کاربری یافت نشد', 404)
            previous = actor_summary(db(), target)
            if 'version' in body and (type(body['version']) is not int or body['version'] != previous['version']):
                return error('دسترسی حساب تغییر کرده است؛ دوباره دریافت کنید', 409)
            merged = {key: body.get(key, previous[key]) for key in ('role', 'employee_id', 'scopes', 'permissions', 'training_access', 'all_companies')}
            if merged['role'] == 'unassigned':
                raise ValueError('برای تنظیم حساب، نقش منابع انسانی را انتخاب کنید')
            removes_primary = previous['is_primary_admin'] and (merged['role']!='system_admin' or body.get('active')==0)
            if removes_primary:
                if uid==g.user['id']:
                    raise ValueError('نمی‌توانید نقش مدیر اصلی حساب فعلی خود را کاهش دهید یا آن را غیرفعال کنید')
                others=db().execute('''SELECT count(*) FROM users u LEFT JOIN hrm_user_access a ON a.user_id=u.id
                    WHERE u.active=1 AND u.id!=? AND (u.role='admin' OR a.role='system_admin')''',(uid,)).fetchone()[0]
                if not others:
                    raise ValueError('حداقل یک ادمین اصلی فعال باید در سامانه باقی بماند')
            values = _binding_values(db(), merged, target['role'], target['company_id'])
            _save_binding(db(), uid, values)
            if target['role']=='admin' and merged['role']!='system_admin':
                db().execute("UPDATE users SET role='viewer' WHERE id=?",(uid,))
            if password_hash is not None:
                db().execute('UPDATE users SET password=? WHERE id=?', (password_hash, uid))
            if 'active' in body:
                db().execute('UPDATE users SET active=? WHERE id=?', (body['active'], uid))
            # Permission changes revoke all existing sessions for this account.
            columns = {row['name'] for row in db().execute('PRAGMA table_info(users)')}
            if 'auth_version' in columns:
                db().execute('UPDATE users SET auth_version=auth_version+1 WHERE id=?', (uid,))
            audit('hrm_access_user_updated', target['company_id'], str(uid))
        return jsonify(user_view(db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()))
