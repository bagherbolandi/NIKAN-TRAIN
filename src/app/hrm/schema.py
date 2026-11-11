"""Versioned, additive migrations for Nikan HRM.

Every statement is idempotent and preserves the training tables and their rows.
Versions are recorded in the existing ``schema_migrations`` table, so upgrading
a real 0.10.2 / 0.11.2 database is repeatable and auditable.
"""
from datetime import datetime, timezone

# Initial values are *seed rows* in hrm_settings, editable from the UI. They are
# deliberately not constants used by business logic.
DEFAULT_SETTINGS = {
    'contract.alert_days': '30',
    'contract.renewal_limit': '3',
    'contract.default_months': '12',
    'leave.annual_limit': '30',
    'leave.max_per_request': '10',
    'loan.max_active': '1',
    'loan.max_amount': '500000000',
    'welfare.max_per_month': '2',
    'reservation.max_nights': '3',
    'reservation.min_lead_days': '2',
    'reservation.max_per_year': '4',
    'request.approval_enabled': '1',
    'date.calendar': 'jalali',
    'date.storage': 'iso',
}
DEFAULT_LEAVE_TYPES = [
    ('leave_annual', 'مرخصی استحقاقی', 30, 10, 1, 1),
    ('leave_sick', 'مرخصی استعلاجی', 15, 5, 1, 1),
    ('leave_unpaid', 'مرخصی بدون حقوق', 0, 30, 0, 1),
    ('mission', 'مأموریت', 0, 30, 1, 1),
]
DEFAULT_REQUEST_TYPES = [
    ('leave', 'درخواست مرخصی', 'company_hr', 0),
    ('loan', 'درخواست وام', 'company_hr', 1),
    ('welfare', 'درخواست خدمات رفاهی', 'company_hr', 0),
    ('reservation', 'رزرو مهمانسرا / ویلا', 'company_hr', 0),
    ('employment_letter', 'گواهی اشتغال', 'company_hr', 0),
    ('other', 'سایر خدمات کارکنان', 'company_hr', 0),
]

BASE = '''
CREATE TABLE IF NOT EXISTS hrm_settings(
    key TEXT NOT NULL, company_id INTEGER NOT NULL DEFAULT 0,
    value TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by INTEGER, PRIMARY KEY(key,company_id));
CREATE TABLE IF NOT EXISTS user_permissions(
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('grant','revoke')),
    granted_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(user_id,permission));
CREATE TABLE IF NOT EXISTS user_companies(
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(id), PRIMARY KEY(user_id,company_id));
CREATE TABLE IF NOT EXISTS user_units(
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    unit_id INTEGER NOT NULL, PRIMARY KEY(user_id,unit_id));

CREATE TABLE IF NOT EXISTS org_units(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    parent_id INTEGER REFERENCES org_units(id), code TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL, manager_id INTEGER, active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id,name));
CREATE TABLE IF NOT EXISTS positions(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    code TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id,title));
CREATE TABLE IF NOT EXISTS work_locations(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    name TEXT NOT NULL, address TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id,name));
CREATE INDEX IF NOT EXISTS org_units_company ON org_units(company_id,parent_id);

CREATE TABLE IF NOT EXISTS personnel_profiles(
    employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    national_id TEXT NOT NULL DEFAULT '', father_name TEXT NOT NULL DEFAULT '',
    birth_date TEXT, gender TEXT NOT NULL DEFAULT '', marital_status TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '', email TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
    emergency_contact TEXT NOT NULL DEFAULT '', education_level TEXT NOT NULL DEFAULT '',
    education_field TEXT NOT NULL DEFAULT '', military_status TEXT NOT NULL DEFAULT '',
    insurance_no TEXT NOT NULL DEFAULT '', bank_account TEXT NOT NULL DEFAULT '',
    start_date TEXT, end_date TEXT, employment_status TEXT NOT NULL DEFAULT 'active',
    unit_id INTEGER REFERENCES org_units(id), position_id INTEGER REFERENCES positions(id),
    work_location_id INTEGER REFERENCES work_locations(id), manager_id INTEGER REFERENCES employees(id),
    note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by INTEGER);
CREATE INDEX IF NOT EXISTS personnel_profiles_company ON personnel_profiles(company_id,employment_status);
CREATE TABLE IF NOT EXISTS employee_history(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    entity TEXT NOT NULL, entity_id INTEGER, field TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '', new_value TEXT NOT NULL DEFAULT '',
    changed_by INTEGER, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, note TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS employee_history_employee ON employee_history(employee_id,id);
CREATE TABLE IF NOT EXISTS hrm_documents(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    entity_type TEXT NOT NULL DEFAULT '', entity_id INTEGER,
    category TEXT NOT NULL DEFAULT '', title TEXT NOT NULL, filename TEXT NOT NULL,
    mime TEXT NOT NULL, content BLOB NOT NULL, confidential INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_documents_employee ON hrm_documents(company_id,employee_id,entity_type,entity_id);

CREATE TABLE IF NOT EXISTS contract_types(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL, default_months INTEGER NOT NULL DEFAULT 12,
    renewal_limit INTEGER NOT NULL DEFAULT 3, alert_days INTEGER NOT NULL DEFAULT 30,
    active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS contracts(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    contract_type_id INTEGER REFERENCES contract_types(id), type TEXT NOT NULL DEFAULT 'permanent',
    number TEXT NOT NULL DEFAULT '', start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('draft','active','expired','terminated','renewed')),
    renewal_of INTEGER REFERENCES contracts(id), renewal_count INTEGER NOT NULL DEFAULT 0,
    wage REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS contracts_company_dates ON contracts(company_id,status,end_date);
CREATE TABLE IF NOT EXISTS contract_renewals(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    contract_id INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    previous_end TEXT NOT NULL, new_end TEXT NOT NULL, renewal_date TEXT NOT NULL,
    number TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', created_by INTEGER);
CREATE TABLE IF NOT EXISTS personnel_actions(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('decree','promotion','transfer','termination','settlement','other')),
    effective_date TEXT NOT NULL, from_value TEXT NOT NULL DEFAULT '', to_value TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '', decision_no TEXT NOT NULL DEFAULT '',
    settlement_amount REAL NOT NULL DEFAULT 0, settlement_date TEXT,
    created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS personnel_actions_company ON personnel_actions(company_id,employee_id,effective_date);

CREATE TABLE IF NOT EXISTS leave_types(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL,
    name TEXT NOT NULL, annual_limit INTEGER NOT NULL DEFAULT 0,
    max_per_request INTEGER NOT NULL DEFAULT 0, requires_approval INTEGER NOT NULL DEFAULT 1,
    paid INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id,code));
CREATE TABLE IF NOT EXISTS attendance_records(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('leave','mission','absence','delay','overtime')),
    leave_type_id INTEGER REFERENCES leave_types(id), date TEXT NOT NULL, end_date TEXT NOT NULL,
    minutes INTEGER NOT NULL DEFAULT 0, hours REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'recorded' CHECK(status IN ('pending','recorded','approved','rejected')),
    source TEXT NOT NULL DEFAULT 'manual', note TEXT NOT NULL DEFAULT '',
    approved_by INTEGER, created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    import_id INTEGER);
CREATE INDEX IF NOT EXISTS attendance_company_date ON attendance_records(company_id,date,kind);
CREATE INDEX IF NOT EXISTS attendance_employee ON attendance_records(employee_id,date);
CREATE TABLE IF NOT EXISTS attendance_imports(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    filename TEXT NOT NULL, rows_count INTEGER NOT NULL, created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, detail TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS performance_periods(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    title TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
    UNIQUE(company_id,title));
CREATE TABLE IF NOT EXISTS performance_indicators(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    title TEXT NOT NULL, weight REAL NOT NULL DEFAULT 1 CHECK(weight>=0),
    target REAL NOT NULL DEFAULT 100, description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1, UNIQUE(company_id,title));
CREATE TABLE IF NOT EXISTS performance_scores(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    period_id INTEGER NOT NULL REFERENCES performance_periods(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    indicator_id INTEGER NOT NULL REFERENCES performance_indicators(id),
    score REAL NOT NULL CHECK(score BETWEEN 0 AND 100), comment TEXT NOT NULL DEFAULT '',
    evaluator_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(period_id,employee_id,indicator_id));
CREATE TABLE IF NOT EXISTS disciplinary_records(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('commendation','notice','warning','reprimand')),
    date TEXT NOT NULL, reason TEXT NOT NULL, decision TEXT NOT NULL DEFAULT '',
    decision_no TEXT NOT NULL DEFAULT '', confidential INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS disciplinary_company ON disciplinary_records(company_id,employee_id,date);

CREATE TABLE IF NOT EXISTS loans(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'internal' CHECK(kind IN ('internal','bank')),
    amount REAL NOT NULL CHECK(amount>0), request_date TEXT NOT NULL, approval_date TEXT,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','approved','rejected','active','settled','cancelled')),
    installments INTEGER NOT NULL DEFAULT 0, paid_amount REAL NOT NULL DEFAULT 0,
    bank_name TEXT NOT NULL DEFAULT '', letter_no TEXT NOT NULL DEFAULT '',
    letter_status TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    request_id INTEGER, created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS loans_company ON loans(company_id,employee_id,status);
CREATE TABLE IF NOT EXISTS loan_payments(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    amount REAL NOT NULL CHECK(amount>0), date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'paid' CHECK(status IN ('planned','paid','late')),
    note TEXT NOT NULL DEFAULT '', created_by INTEGER);

CREATE TABLE IF NOT EXISTS welfare_services(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL DEFAULT 0,
    name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'goods'
        CHECK(kind IN ('goods','stationery','gift','accommodation','other')),
    max_per_period INTEGER NOT NULL DEFAULT 0, period TEXT NOT NULL DEFAULT 'month'
        CHECK(period IN ('month','year','once')), requires_approval INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1, description TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS welfare_usages(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    service_id INTEGER NOT NULL REFERENCES welfare_services(id),
    date TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity>0),
    value REAL NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    request_id INTEGER, created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS welfare_usages_company ON welfare_usages(company_id,date,service_id);
CREATE TABLE IF NOT EXISTS accommodations(
    id INTEGER PRIMARY KEY, company_id INTEGER REFERENCES companies(id),
    name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'villa' CHECK(kind IN ('guest_house','villa')),
    location TEXT NOT NULL DEFAULT '', capacity INTEGER NOT NULL DEFAULT 1 CHECK(capacity>0),
    max_nights INTEGER NOT NULL DEFAULT 3, rules TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS reservations(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    accommodation_id INTEGER NOT NULL REFERENCES accommodations(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    from_date TEXT NOT NULL, to_date TEXT NOT NULL, guests INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','approved','rejected','cancelled','completed')),
    note TEXT NOT NULL DEFAULT '', request_id INTEGER, created_by INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS reservations_overlap ON reservations(accommodation_id,from_date,to_date,status);

CREATE TABLE IF NOT EXISTS request_types(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL DEFAULT 0, code TEXT NOT NULL,
    name TEXT NOT NULL, approver_role TEXT NOT NULL DEFAULT 'company_hr',
    confidential INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(company_id,code));
CREATE TABLE IF NOT EXISTS requests(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    type_id INTEGER NOT NULL REFERENCES request_types(id), title TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload) AND json_type(payload)='object'),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','approved','rejected','cancelled')),
    created_by INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TEXT, decided_by INTEGER, decided_at TEXT, decision_note TEXT NOT NULL DEFAULT '',
    related_type TEXT NOT NULL DEFAULT '', related_id INTEGER);
CREATE INDEX IF NOT EXISTS requests_company ON requests(company_id,status,created_at);
CREATE TABLE IF NOT EXISTS request_steps(
    id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
    step_no INTEGER NOT NULL DEFAULT 1, approver_role TEXT NOT NULL,
    approver_user_id INTEGER, status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','approved','rejected','skipped')),
    decided_at TEXT, note TEXT NOT NULL DEFAULT '', UNIQUE(request_id,step_no));
'''

MIGRATIONS = [
    ('0.12.0-hrm-base', BASE),
    ('0.12.1-hrm-request-link', '''
ALTER TABLE attendance_records ADD COLUMN request_id INTEGER REFERENCES requests(id);
CREATE INDEX IF NOT EXISTS attendance_request ON attendance_records(request_id);
'''),
]


def _columns(conn, table):
    return {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}


def migrate(conn):
    """Apply every missing HRM migration; safe on a fresh or legacy database."""
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP)')
    with conn:
        for version, script in MIGRATIONS:
            if conn.execute('SELECT 1 FROM schema_migrations WHERE version=?', (version,)).fetchone():
                continue
            conn.executescript(script)
            conn.execute('INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)', (version,))
        users = _columns(conn, 'users')
        for column, definition in [
            ('hrm_role', "TEXT NOT NULL DEFAULT ''"),
            ('hrm_scope', "TEXT NOT NULL DEFAULT ''"),
            ('employee_id', 'INTEGER'),
            ('unit_id', 'INTEGER'),
        ]:
            if column not in users:
                conn.execute(f'ALTER TABLE users ADD COLUMN {column} {definition}')
        if 'active' not in _columns(conn, 'employees'):
            conn.execute('ALTER TABLE employees ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
        # The pre-existing holding administrator becomes the system administrator.
        conn.execute("UPDATE users SET hrm_role='system_admin',hrm_scope='holding' WHERE role='admin' AND hrm_role=''")
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        conn.executemany(
            'INSERT OR IGNORE INTO hrm_settings(key,company_id,value,updated_at) VALUES (?,0,?,?)',
            [(key, value, now) for key, value in DEFAULT_SETTINGS.items()],
        )
        conn.executemany(
            'INSERT OR IGNORE INTO leave_types(company_id,code,name,annual_limit,max_per_request,requires_approval,paid) VALUES (0,?,?,?,?,?,?)',
            DEFAULT_LEAVE_TYPES,
        )
        conn.executemany(
            'INSERT OR IGNORE INTO request_types(company_id,code,name,approver_role,confidential) VALUES (0,?,?,?,?)',
            DEFAULT_REQUEST_TYPES,
        )
        conn.executemany(
            'INSERT OR IGNORE INTO welfare_services(company_id,name,kind,max_per_period,period,requires_approval,description) VALUES (0,?,?,?,?,?,?)',
            [
                ('لوازم‌التحریر', 'stationery', 2, 'month', 1, 'تحویل اقلام اداری'),
                ('هدیه مناسبت‌ها', 'gift', 1, 'year', 1, 'هدایای سازمانی'),
                ('بسته حمایتی', 'goods', 1, 'month', 1, 'اقلام غیرنقدی'),
            ],
        )
        conn.executemany(
            'INSERT OR IGNORE INTO accommodations(id,company_id,name,kind,location,capacity,max_nights,rules) VALUES (?,NULL,?,?,?,?,?,?)',
            [
                (1, 'مهمانسرای مرکزی هلدینگ', 'guest_house', 'تهران', 4, 5, 'رزرو با تأیید منابع انسانی'),
                (2, 'ویلای محمودآباد ۱', 'villa', 'محمودآباد', 1, 3, 'هر خانواده حداکثر ۳ شب'),
                (3, 'ویلای محمودآباد ۲', 'villa', 'محمودآباد', 1, 3, 'هر خانواده حداکثر ۳ شب'),
            ],
        )
        conn.executemany(
            'INSERT OR IGNORE INTO contract_types(id,company_id,name,default_months,renewal_limit,alert_days) VALUES (?,0,?,?,?,?)',
            [(1, 'قرارداد مدت‌دار', 12, 3, 30), (2, 'قرارداد دائم', 0, 0, 0), (3, 'قرارداد کارآموزی', 6, 1, 15)],
        )
    return [row[0] for row in conn.execute("SELECT version FROM schema_migrations WHERE version LIKE '0.12%' ORDER BY version")]
