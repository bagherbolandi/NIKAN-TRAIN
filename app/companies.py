"""Holding administrator company management without removing business history."""

import json

from flask import jsonify, request

from .validation import SQLITE_MAX_INTEGER, json_object, positive_id


SCHEMA = '''CREATE TABLE IF NOT EXISTS company_registry(
    id INTEGER PRIMARY KEY CHECK(id=1),
    high_watermark INTEGER NOT NULL CHECK(high_watermark BETWEEN 0 AND 9223372036854775807)
);'''


def initialize(connection, defaults, *, seed_defaults=False):
    """Seed only a newly created companies table and retain retired company IDs."""
    with connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute(SCHEMA)
        registry = connection.execute('SELECT high_watermark FROM company_registry WHERE id=1').fetchone()
        if registry is None and seed_defaults:
            connection.executemany('INSERT INTO companies(name) VALUES (?)', [(name,) for name in defaults])
        highest = max(
            connection.execute('SELECT COALESCE(MAX(id),0) FROM companies').fetchone()[0],
            connection.execute('SELECT COALESCE(MAX(company_id),0) FROM audit').fetchone()[0],
            registry['high_watermark'] if registry else 0,
        )
        connection.execute(
            'INSERT INTO company_registry(id,high_watermark) VALUES (1,?) '
            'ON CONFLICT(id) DO UPDATE SET high_watermark=excluded.high_watermark',
            (highest,),
        )


def name_value(data):
    if set(data) != {'name'} or not isinstance(data['name'], str):
        raise ValueError('نام شرکت را به صورت متنی ارسال کنید')
    name = ' '.join(data['name'].split())
    if not name or len(name) > 200 or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError('نام شرکت باید بین ۱ تا ۲۰۰ نویسه معتبر باشد')
    return name


def duplicate(connection, name, excluded=None):
    canonical = name.casefold()
    return any(
        row['id'] != excluded and ' '.join(row['name'].split()).casefold() == canonical
        for row in connection.execute('SELECT id,name FROM companies')
    )


def business_references(connection, company_id):
    """Include every company-owned table, even future tables without a FK."""
    labels = {
        'users': 'کاربر', 'employees': 'پرسنل', 'skills': 'شایستگی انتخاب‌شده',
        'requirements': 'الزام شغلی', 'assessments': 'ارزیابی', 'courses': 'دوره آموزشی',
        'records': 'سابقه آموزش', 'job_profiles': 'شناسنامه شغل',
        'employee_meta': 'اطلاعات پرسنلی', 'certificates': 'گواهینامه',
        'instructors': 'مدرس', 'needs': 'نیاز آموزشی', 'personal_documents': 'شناسنامه فردی',
        'hrm_sites': 'سایت', 'hrm_units': 'واحد',
        'hrm_employee_details': 'انتساب جاری پرسنل',
        'hrm_employment_history': 'تاریخچه خدمت',
        'hrm_temporary_assignments': 'فعالیت موقت',
        'hrm_user_scopes': 'محدوده دسترسی کاربر',
    }
    found = []
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall():
        if table == 'audit':
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        columns = {row['name'] for row in connection.execute('PRAGMA table_info(' + quoted + ')')}
        linked = {column for column in columns if column == 'company_id' or column.endswith('_company_id')}
        linked.update(row['from'] for row in connection.execute('PRAGMA foreign_key_list(' + quoted + ')')
                      if row['table'] == 'companies')
        if linked:
            predicates = ['"' + column.replace('"', '""') + '"=?' for column in sorted(linked)]
            exists = connection.execute('SELECT 1 FROM ' + quoted + ' WHERE ' +
                                        ' OR '.join(predicates) + ' LIMIT 1',
                                        [company_id] * len(predicates)).fetchone()
            label = labels.get(table, 'اطلاعات وابسته')
            if exists and label not in found:
                found.append(label)
    return found


def remove_company_logos(connection, company_id):
    # Older uploads accepted numeric aliases such as company-0007 or company-+7.
    # Remove those bindings too, preserving holding and other companies' logos.
    keys = []
    for row in connection.execute("SELECT key FROM branding WHERE key LIKE 'company-%'"):
        try:
            linked_id = int(row['key'][8:])
        except ValueError:
            continue
        if linked_id == company_id:
            keys.append((row['key'],))
    connection.executemany('DELETE FROM branding WHERE key=?', keys)


def register(app, db, authenticated, audit, error):
    def admin_only():
        # Delegated HRM users can have an adapted legacy training role. Global
        # organization management always uses the original primary-admin grant.
        from .hrm_access import ctx
        if not ctx(db())['is_primary_admin']:
            raise PermissionError()

    @app.route('/api/companies', methods=['GET', 'POST'])
    @authenticated
    def companies():
        admin_only()
        connection = db()
        if request.method == 'GET':
            return jsonify([dict(row) for row in connection.execute('SELECT id,name FROM companies ORDER BY id')])
        name = name_value(json_object())
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            if duplicate(connection, name):
                return error('شرکتی با این نام قبلاً ثبت شده است', 409)
            highest = max(
                connection.execute('SELECT high_watermark FROM company_registry WHERE id=1').fetchone()[0],
                connection.execute('SELECT COALESCE(MAX(id),0) FROM companies').fetchone()[0],
            )
            if highest >= SQLITE_MAX_INTEGER:
                return error('ظرفیت شناسه شرکت‌ها تکمیل شده است', 409)
            company_id = highest + 1
            connection.execute('INSERT INTO companies(id,name) VALUES (?,?)', (company_id, name))
            connection.execute('UPDATE company_registry SET high_watermark=? WHERE id=1', (company_id,))
            audit('create_company', company_id, json.dumps({'id': company_id, 'name': name}, ensure_ascii=False))
        return jsonify(id=company_id, name=name), 201

    @app.route('/api/companies/<int:company_id>', methods=['PUT', 'DELETE'])
    @authenticated
    def modify_company(company_id):
        admin_only()
        company_id = positive_id(company_id)
        name = name_value(json_object()) if request.method == 'PUT' else None
        connection = db()
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            target = connection.execute('SELECT id,name FROM companies WHERE id=?', (company_id,)).fetchone()
            if target is None:
                return error('شرکت یافت نشد', 404)
            if request.method == 'PUT':
                if duplicate(connection, name, company_id):
                    return error('شرکتی با این نام قبلاً ثبت شده است', 409)
                if name != target['name']:
                    connection.execute('UPDATE companies SET name=? WHERE id=?', (name, company_id))
                    audit('update_company', company_id, json.dumps(
                        {'id': company_id, 'old_name': target['name'], 'name': name}, ensure_ascii=False,
                    ))
            else:
                references = business_references(connection, company_id)
                if references:
                    return error('حذف شرکت دارای ' + '، '.join(references) + ' مجاز نیست؛ اطلاعات و سوابق باید حفظ شوند', 409)
                remove_company_logos(connection, company_id)
                connection.execute('DELETE FROM companies WHERE id=?', (company_id,))
                audit('delete_company', company_id, json.dumps(
                    {'id': company_id, 'name': target['name']}, ensure_ascii=False,
                ))
        return jsonify(id=company_id, name=name) if request.method == 'PUT' else jsonify(ok=True)
