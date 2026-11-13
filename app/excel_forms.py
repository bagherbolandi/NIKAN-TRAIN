"""Versioned, company-bound Excel forms with atomic preview and import."""
import io
import json
import re
import sqlite3
import zipfile

import openpyxl
from flask import Response, g, jsonify, request

from .catalog import attach, bank_values
from .validation import number, positive_id, jalali_date

SCHEMA_VERSION = '1'
MAX_ROWS = 5000
DATA_SHEET = 'ورود اطلاعات'
DATA_START = 7


def column(key, label, required=False, type='text', reference=None, choices=None):
    result = dict(key=key, label=label, required=required, type=type)
    if reference:
        result['reference'] = reference
    if choices:
        result['choices'] = [dict(value=value, label=label) for value, label in choices]
    return result


SKILL = column('skill_code', 'کد شایستگی هلدینگ', True, reference='skills')
EMPLOYEE = column('employee_code', 'کد پرسنلی', True, reference='employees')
LEVEL = column('level', 'سطح شایستگی (۰ تا ۴)', True, 'integer')
JOB = column('job', 'عنوان شغل', True, reference='jobs')
DOCUMENT_LABELS = dict(purpose='هدف شغل', definition='تعریف شغل', supervisor='مافوق مستقیم', duties='وظایف و مسئولیت‌ها', commitments='تعهدات کاری', authority='حدود اختیار', education='حداقل تحصیلات', experience='سابقه لازم', legal='الزامات قانونی', hse='الزامات ایمنی', equipment='ابزار و تجهیزات', conditions='شرایط کار', performance='معیارهای عملکرد', contract_notes='ملاحظات قرارداد', effective_date='تاریخ اجرای سند', review_date='تاریخ بازنگری', revision='بازنگری سند', approved_by='تأییدکننده')
DOCUMENT_COLUMNS = [column(key, label, type='date' if key.endswith('_date') else 'text') for key, label in DOCUMENT_LABELS.items()]
COURSE_STATUS = [('planned', 'برنامه‌ریزی‌شده'), ('approved', 'مصوب'), ('completed', 'برگزارشده'), ('cancelled', 'لغوشده')]


def schema(kind, title, columns, keys, admin_only=False, description=''):
    return dict(kind=kind, title=title, columns=columns, keys=keys, admin_only=admin_only, description=description or 'خانه‌های اختیاری خالی، اطلاعات موجود را تغییر نمی‌دهند. ورود فایل هیچ رکوردی را حذف نمی‌کند.')


SCHEMAS = {
    'employees': schema('employees', 'پرسنل و ساختار سازمانی', [column('code', 'کد پرسنلی', True), column('name', 'نام و نام خانوادگی', True), column('department', 'واحد سازمانی', reference='departments'), column('job', 'سمت شغلی', reference='jobs')], ['code']),
    'job_profiles': schema('job_profiles', 'شناسنامه و شرح شغل', [JOB, column('department', 'واحد سازمانی', reference='departments')] + DOCUMENT_COLUMNS, ['job']),
    'competency_selection': schema('competency_selection', 'انتخاب شایستگی برای شرکت', [SKILL], ['skill_code'], description='فقط شایستگی‌های موجود در بانک هلدینگ برای شرکت انتخاب می‌شوند؛ تعریف یا ویرایش بانک انجام نمی‌شود.'),
    'requirements': schema('requirements', 'الزامات شایستگی مشاغل', [JOB, SKILL, column('level', 'سطح لازم شغل (۱ تا ۴)', True, 'integer')], ['job', 'skill_code']),
    'assessments': schema('assessments', 'ارزیابی شایستگی افراد', [EMPLOYEE, SKILL, LEVEL], ['employee_code', 'skill_code']),
    'courses': schema('courses', 'برنامه و دوره‌های آموزشی', [column('course_code', 'کد یکتای دوره', True), SKILL, column('title', 'عنوان دوره', True), column('date', 'تاریخ شمسی (1405/03/25)', True, 'date'), column('instructor', 'مدرس', reference='instructors'), column('hours', 'مدت دوره (ساعت)', True, 'number'), column('cost', 'هزینه (میلیون ریال)', type='number'), column('status', 'وضعیت دوره', choices=COURSE_STATUS), column('calendar_scope', 'نوع تقویم', choices=[('holding', 'تجمیعی هلدینگ'), ('internal', 'داخلی شرکت')])], ['course_code']),
    'records': schema('records', 'سوابق و اثربخشی آموزش', [EMPLOYEE, column('course_code', 'کد دوره', True, reference='courses'), LEVEL, column('score', 'نمره آزمون (۰ تا ۱۰۰)', type='number'), column('reaction', 'رضایت (۱ تا ۵)', type='number'), column('behavior', 'نظر مدیر (۱ تا ۵)', type='number'), column('result', 'تحقق هدف (درصد)', type='number')], ['employee_code', 'course_code']),
    'instructors': schema('instructors', 'بانک مدرسان', [column('name', 'نام مدرس', True), column('specialty', 'تخصص'), column('organization', 'سازمان'), column('qualification', 'صلاحیت حرفه‌ای'), column('rate', 'نرخ ساعتی (میلیون ریال)', type='number'), column('contact', 'اطلاعات تماس')], ['name']),
    'needs': schema('needs', 'درخواست و مصوبات آموزش', [SKILL, column('department', 'واحد سازمانی', reference='departments'), column('reason', 'دلیل نیاز آموزشی', True), column('priority', 'اولویت (۱ تا ۳)', type='integer'), column('year', 'سال شمسی', True, 'integer'), column('status', 'وضعیت درخواست', choices=[('submitted', 'در انتظار بررسی'), ('approved', 'مصوب'), ('rejected', 'ردشده')]), column('decision', 'تصمیم کمیته')], ['skill_code', 'department', 'reason', 'year']),
    'personal_requirements': schema('personal_requirements', 'الزامات شایستگی اختصاصی فرد', [EMPLOYEE, SKILL, LEVEL], ['employee_code', 'skill_code']),
    'personal_documents': schema('personal_documents', 'شناسنامه کامل واقعی فرد', [EMPLOYEE] + DOCUMENT_COLUMNS, ['employee_code'], description='با ورود صریح این فرم، شناسنامه کامل واقعی فرد مستقل ثبت می‌شود. خانه‌های تکمیل‌شده اصلاح و سایر اطلاعات فعلی فرد حفظ و تثبیت می‌شوند؛ الگوی ایده‌آل شغل تغییر نمی‌کند.'),
    'competency-bank': schema('competency-bank', 'بانک شایستگی هلدینگ', [column('code', 'کد شایستگی', True), column('title', 'عنوان شایستگی', True), column('category', 'گروه'), column('importance', 'اهمیت (۱ تا ۳)', type='integer'), column('method', 'روش آموزش'), column('reference', 'مرجع')], ['code'], True),
    'companies': schema('companies', 'ثبت شرکت‌های هلدینگ', [column('name', 'نام شرکت', True)], ['name'], True, description='نام شرکت جدید اضافه می‌شود و نام‌های موجود تکرار نمی‌شوند. تغییر نام یا حذف شرکت از بخش مدیریت شرکت‌ها انجام می‌شود؛ شناسه شرکت از فایل قابل تغییر نیست.'),
}


def migrate(connection):
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(courses)')}
    if 'external_code' not in columns:
        connection.execute("ALTER TABLE courses ADD COLUMN external_code TEXT NOT NULL DEFAULT ''")
    connection.execute("UPDATE courses SET external_code='NT-C-'||upper(hex(randomblob(12))) WHERE external_code=''")
    connection.executescript("""CREATE UNIQUE INDEX IF NOT EXISTS courses_external_code ON courses(company_id,external_code) WHERE external_code!='';
CREATE TRIGGER IF NOT EXISTS course_external_code_created AFTER INSERT ON courses WHEN NEW.external_code='' BEGIN UPDATE courses SET external_code='NT-C-'||upper(hex(randomblob(12))) WHERE id=NEW.id; END;""")
    connection.commit()


def reference_data(connection, cid, include_companies=False):
    query = lambda sql, args=(): [dict(row) for row in connection.execute(sql, args)]
    return dict(
        skills=query('SELECT code,title,category FROM holding_competencies ORDER BY code'),
        employees=query('SELECT code,name,job FROM employees WHERE company_id=? ORDER BY code', (cid,)),
        jobs=query("SELECT job,MAX(department) department FROM (SELECT job,department FROM job_profiles WHERE company_id=? UNION ALL SELECT job,department FROM employees WHERE company_id=? UNION ALL SELECT job,'' department FROM requirements WHERE company_id=?) WHERE job!='' GROUP BY job ORDER BY job", (cid, cid, cid)),
        courses=query('SELECT external_code,title,date FROM courses WHERE company_id=? ORDER BY date,id', (cid,)),
        instructors=query('SELECT DISTINCT name FROM instructors WHERE company_id=? ORDER BY name', (cid,)),
        departments=query("SELECT department FROM employees WHERE company_id=? AND department!='' UNION SELECT department FROM job_profiles WHERE company_id=? AND department!='' ORDER BY department", (cid, cid)),
        companies=query('SELECT id,name FROM companies' + ('' if include_companies else ' WHERE id=?') + ' ORDER BY id', () if include_companies else (cid,)),
    )


def read_rows(payload, form, cid):
    """Read trusted template structure without trusting spreadsheet contents."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = archive.infolist()
            if len(members) > 1000 or sum(item.file_size for item in members) > 100 * 1024 * 1024:
                raise ValueError('حجم بازشده یا تعداد اجزای فایل بیش از حد مجاز است')
            names = [item.filename for item in members]
            if len(names) != len(set(names)):
                raise ValueError('ساختار فایل اکسل تکراری و نامعتبر است')
            for item in members:
                name = item.filename.lower()
                if 'externallinks/' in name or 'vbaproject' in name:
                    raise ValueError('فایل دارای پیوند خارجی یا ماکرو پذیرفته نمی‌شود')
                if name.endswith(('.xml', '.rels')):
                    raw = archive.read(item)
                    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper() or (name.endswith('.rels') and re.search(rb'''TargetMode\s*=\s*['"]External['"]''', raw, re.I)):
                        raise ValueError('محتوای خارجی یا ساختار XML نامعتبر است')
        workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=False, keep_links=False)
    except ValueError:
        raise
    except Exception:
        raise ValueError('فایل XLSX سالم و مطابق الگوی سامانه انتخاب کنید') from None
    try:
        if DATA_SHEET not in workbook.sheetnames or '_nikan' not in workbook.sheetnames:
            raise ValueError('فایل را از الگوی استاندارد همین بخش دریافت کنید')
        if len(workbook.worksheets) > 32 or any(sheet.max_row > 50006 or sheet.max_column > 100 for sheet in workbook.worksheets):
            raise ValueError('ابعاد برگه‌های فایل بیش از حد مجاز است')
        metadata_sheet = workbook['_nikan']
        if metadata_sheet.max_row > 20 or metadata_sheet.max_column > 2:
            raise ValueError('مشخصات الگوی اکسل معتبر نیست')
        metadata_rows = list(metadata_sheet.iter_rows(min_col=1, max_col=2, values_only=True))
        if len({row[0] for row in metadata_rows}) != len(metadata_rows):
            raise ValueError('مشخصات تکراری الگوی اکسل معتبر نیست')
        metadata = dict(metadata_rows)
        if str(metadata.get('schema_version')) != SCHEMA_VERSION or metadata.get('kind') != form['kind']:
            raise ValueError('نسخه یا نوع فرم اکسل با بخش انتخاب‌شده یکسان نیست')
        if str(metadata.get('company_id')) != str(cid):
            raise ValueError('این الگو برای شرکت دیگری صادر شده است؛ الگوی شرکت فعلی را دریافت کنید')
        sheet = workbook[DATA_SHEET]
        if sheet.max_row > MAX_ROWS + DATA_START - 1 or sheet.max_column > len(form['columns']):
            raise ValueError('حداکثر ۵۰۰۰ ردیف و ستون‌های اصلی الگو مجاز است')
        expected = [field['key'] for field in form['columns']]
        keys = [cell.value for cell in next(sheet.iter_rows(min_row=5, max_row=5, max_col=len(expected)))]
        if keys != expected:
            raise ValueError('ستون‌های فرم تغییر کرده‌اند؛ از الگوی اصلی استفاده کنید')
        result = []
        for row in sheet.iter_rows(min_row=DATA_START, max_col=len(expected)):
            if not any(cell.value is not None and (not isinstance(cell.value, str) or cell.value.strip()) for cell in row):
                continue
            data = {}
            problem = None
            for field, cell in zip(form['columns'], row):
                value = cell.value
                if cell.data_type == 'f' or cell.data_type == 'e':
                    problem = 'فرمول و خطای محاسباتی اکسل پذیرفته نمی‌شود؛ مقدار نهایی را وارد کنید'
                    break
                if value is None or (isinstance(value, str) and not value.strip()):
                    if field['required']:
                        problem = 'فیلد «' + field['label'] + '» الزامی است'
                        break
                    continue
                if field['type'] in ('integer', 'number'):
                    try:
                        value = number(value, integer=field['type'] == 'integer')
                    except ValueError:
                        problem = 'مقدار «' + field['label'] + '» باید عدد معتبر باشد'
                        break
                else:
                    if isinstance(value, (bool, list, dict)):
                        problem = 'مقدار متنی نامعتبر است'
                        break
                    value = str(value).strip()
                    if len(value) > 10000 or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                        problem = 'متن بیش از حد مجاز یا دارای نویسه نامعتبر است'
                        break
                    if field['required'] and not value:
                        problem = 'فیلد «' + field['label'] + '» الزامی است'
                        break
                if field.get('choices'):
                    aliases = {str(item['label']): item['value'] for item in field['choices']}
                    value = aliases.get(str(value), value)
                    if value not in {item['value'] for item in field['choices']}:
                        problem = 'گزینه «' + field['label'] + '» معتبر نیست؛ از فهرست راهنما استفاده کنید'
                        break
                data[field['key']] = value
            result.append((row[0].row, data, problem))
        if not result:
            raise ValueError('فایل هیچ ردیف تکمیل‌شده‌ای ندارد')
        return result
    except (KeyError, TypeError, AttributeError):
        raise ValueError('ساختار الگوی اکسل معتبر نیست') from None
    finally:
        workbook.close()


def upsert(connection, table, cid, values, keys, policy, company_owned=True):
    where = ('company_id=? AND ' if company_owned else '') + ' AND '.join(key + '=?' for key in keys)
    parameters = ([cid] if company_owned else []) + [values[key] for key in keys]
    existing = connection.execute('SELECT * FROM ' + table + ' WHERE ' + where, parameters).fetchall()
    if len(existing) > 1:
        raise ValueError('چند رکورد با این مشخصات وجود دارد؛ ابتدا موارد تکراری را در سامانه اصلاح کنید')
    if existing:
        changed = {key: value for key, value in values.items() if existing[0][key] != value}
        if not changed:
            return 'unchanged'
        if policy != 'upsert':
            raise ValueError('رکورد موجود اطلاعات متفاوت دارد؛ پس از بررسی، حالت «افزودن و به‌روزرسانی» را انتخاب کنید')
        connection.execute('UPDATE ' + table + ' SET ' + ','.join(key + '=?' for key in changed) + ' WHERE ' + where, list(changed.values()) + parameters)
        return 'updated'
    insert = dict(values)
    if company_owned:
        insert['company_id'] = cid
    connection.execute('INSERT INTO ' + table + '(' + ','.join(insert) + ') VALUES (' + ','.join('?' for _ in insert) + ')', list(insert.values()))
    return 'created'


def write_row(connection, kind, cid, values, policy, role, validate_refs):
    data = dict(values)
    if kind == 'companies':
        from .companies import duplicate, name_value
        name = name_value(data)
        if duplicate(connection, name):
            return 'unchanged'
        highest = connection.execute('SELECT high_watermark FROM company_registry WHERE id=1').fetchone()[0]
        company_id = positive_id(highest + 1)
        connection.execute('INSERT INTO companies(id,name) VALUES (?,?)', (company_id, name))
        connection.execute('UPDATE company_registry SET high_watermark=? WHERE id=1', (company_id,))
        return 'created'
    if kind == 'competency-bank':
        data = bank_values(data, creating=True)
        result = upsert(connection, 'holding_competencies', cid, data, ['code'], policy, False)
        if result == 'updated':
            connection.execute('UPDATE skills SET title=(SELECT title FROM holding_competencies WHERE code=?),category=(SELECT category FROM holding_competencies WHERE code=?),importance=(SELECT importance FROM holding_competencies WHERE code=?) WHERE id IN (SELECT skill_id FROM competency_links WHERE code=?)', (data['code'],) * 4)
        return result
    if 'skill_code' in data:
        code = data.pop('skill_code')
        if not connection.execute('SELECT 1 FROM holding_competencies WHERE code=?', (code,)).fetchone():
            raise ValueError('کد شایستگی در بانک هلدینگ یافت نشد؛ تعریف بانک فقط در اختیار ادمین است')
        selected = connection.execute('SELECT s.id FROM skills s JOIN competency_links l ON l.skill_id=s.id WHERE s.company_id=? AND l.code=?', (cid, code)).fetchone()
        skill_id = attach(connection, cid, code)
        if kind == 'competency_selection':
            return 'unchanged' if selected else 'created'
        data['skill_id'] = skill_id
    if 'employee_code' in data:
        employee = connection.execute('SELECT id FROM employees WHERE company_id=? AND code=?', (cid, data.pop('employee_code'))).fetchone()
        if not employee:
            raise ValueError('کد پرسنلی در شرکت انتخاب‌شده یافت نشد')
        data['employee_id'] = employee['id']
    if kind in ('employees', 'requirements') and data.get('job'):
        if not connection.execute('SELECT 1 FROM job_profiles WHERE company_id=? AND job=? UNION SELECT 1 FROM employees WHERE company_id=? AND job=? UNION SELECT 1 FROM requirements WHERE company_id=? AND job=?', (cid, data['job']) * 3).fetchone():
            raise ValueError('عنوان شغل در فهرست شرکت یافت نشد؛ ابتدا شناسنامه شغل را تعریف کنید')
    if kind == 'courses':
        data['external_code'] = data.pop('course_code')
        current = connection.execute('SELECT calendar_scope FROM courses WHERE company_id=? AND external_code=?', (cid, data['external_code'])).fetchone()
        if role != 'admin' and (data.get('calendar_scope') == 'holding' or (current and current['calendar_scope'] == 'holding')):
            raise ValueError('برنامه‌ریزی و تغییر دوره تجمیعی فقط در اختیار ادمین هلدینگ است')
        if not current:
            data.setdefault('calendar_scope', 'holding' if role == 'admin' else 'internal')
        if data.get('calendar_scope') == 'internal' and current:
            if connection.execute('SELECT 1 FROM course_participants p JOIN courses c ON c.id=p.course_id WHERE c.company_id=? AND c.external_code=? LIMIT 1', (cid, data['external_code'])).fetchone():
                raise ValueError('دوره دارای برنامه‌ریزی مشترک است؛ ابتدا فهرست شرکت‌کنندگان را اصلاح کنید')
        if data.get('instructor') and not connection.execute('SELECT 1 FROM instructors WHERE company_id=? AND name=?', (cid, data['instructor'])).fetchone():
            raise ValueError('مدرس در فهرست شرکت یافت نشد')
    if kind == 'records':
        course = connection.execute('SELECT id FROM courses WHERE company_id=? AND external_code=?', (cid, data.pop('course_code'))).fetchone()
        if not course:
            raise ValueError('کد دوره در شرکت انتخاب‌شده یافت نشد')
        data['course_id'] = course['id']
    if kind == 'personal_requirements':
        data['level'] = number(data['level'], integer=True, minimum=0, maximum=4)
        return upsert(connection, kind, cid, data, ['employee_id', 'skill_id'], policy, False)
    if kind == 'personal_documents':
        eid = data.pop('employee_id')
        if not data:
            raise ValueError('حداقل یک اصلاح اختصاصی شناسنامه را تکمیل کنید')
        for field in ('effective_date', 'review_date'):
            if field in data:
                data[field] = jalali_date(data[field])
        existing = connection.execute('SELECT overrides,is_snapshot FROM personal_documents WHERE company_id=? AND employee_id=?', (cid, eid)).fetchone()
        previous = json.loads(existing['overrides']) if existing else {}
        merged = {**previous, **data}
        if existing and existing['is_snapshot'] and merged == previous:
            return 'unchanged'
        if existing and policy != 'upsert':
            raise ValueError('اصلاحات فردی موجود متفاوت است؛ حالت افزودن و به‌روزرسانی را انتخاب کنید')
        from .documents import save_personal_document
        save_personal_document(connection, cid, eid, data)
        return 'updated' if existing else 'created'
    if kind == 'job_profiles':
        for field in ('effective_date', 'review_date'):
            if field in data:
                data[field] = jalali_date(data[field])
    if kind == 'needs':
        data['year'] = number(data['year'], integer=True, minimum=1300, maximum=1499)
    validate_refs(kind, data, cid)
    keys = {'employees': ['code'], 'job_profiles': ['job'], 'requirements': ['job', 'skill_id'], 'assessments': ['employee_id', 'skill_id'], 'courses': ['external_code'], 'records': ['employee_id', 'course_id'], 'instructors': ['name'], 'needs': ['skill_id', 'department', 'reason', 'year']}[kind]
    if kind == 'needs':
        data.setdefault('department', '')
        existing = connection.execute('SELECT status FROM needs WHERE company_id=? AND skill_id=? AND department=? AND reason=? AND year=?', (cid, data['skill_id'], data['department'], data['reason'], data['year'])).fetchone()
        if existing and existing['status'] != 'submitted':
            raise ValueError('نیاز بررسی‌شده قفل است؛ ابتدا هلدینگ آن را بازگشایی کند')
    return upsert(connection, kind, cid, data, keys, policy)


def register(app, db, authenticated, company, audit, error, validate_refs):
    with app.app_context():
        migrate(db())

    def selected_form(kind):
        form = SCHEMAS.get(kind)
        if not form:
            return None
        if form['admin_only'] and g.user['role'] != 'admin':
            raise PermissionError()
        return form

    def role_form(form):
        result = dict(form, columns=[dict(field) for field in form['columns']])
        if form['kind'] == 'courses' and g.user['role'] != 'admin':
            next(field for field in result['columns'] if field['key'] == 'calendar_scope')['choices'] = [dict(value='internal', label='داخلی شرکت')]
        return result

    def template_payload(form, cid):
        from .excel_workbooks import build_template
        selected = dict(db().execute('SELECT id,name FROM companies WHERE id=?', (cid,)).fetchone())
        brand = db().execute("SELECT content FROM branding WHERE key='holding'").fetchone()
        refs = reference_data(db(), cid, include_companies=g.user['role'] == 'admin' and form['kind'] == 'companies')
        if g.user['role'] != 'admin':
            refs.pop('companies', None)
        return build_template(role_form(form), selected, refs, brand['content'] if brand else None)

    @app.get('/api/excel/forms')
    @authenticated
    def forms():
        company()
        return jsonify([dict(role_form(form), can_import=g.user['role'] != 'viewer') for form in SCHEMAS.values() if not form['admin_only'] or g.user['role'] == 'admin'])

    @app.get('/api/excel/templates/<kind>')
    @authenticated
    def template(kind):
        cid = company()
        form = selected_form(kind)
        if form is None:
            return error('فرم اکسل یافت نشد', 404)
        payload = template_payload(form, cid)
        return Response(payload, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', headers={'Content-Disposition': f'attachment; filename=Nikan-{kind}-company-{cid}.xlsx'})

    @app.get('/api/excel/templates.zip')
    @authenticated
    def templates_archive():
        cid = company()
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for form in SCHEMAS.values():
                if not form['admin_only'] or g.user['role'] == 'admin':
                    archive.writestr('Nikan-' + form['kind'] + f'-company-{cid}.xlsx', template_payload(form, cid))
        return Response(output.getvalue(), mimetype='application/zip', headers={'Content-Disposition': f'attachment; filename=Nikan-Excel-Forms-company-{cid}.zip'})

    @app.post('/api/excel/import/<kind>')
    @authenticated
    def import_form(kind):
        cid = company()
        form = selected_form(kind)
        if form is None:
            return error('فرم اکسل یافت نشد', 404)
        if g.user['role'] == 'viewer':
            raise PermissionError()
        mode, policy = request.form.get('mode', 'preview'), request.form.get('policy', 'insert')
        if mode not in ('preview', 'commit') or policy not in ('insert', 'upsert'):
            raise ValueError('حالت بررسی یا ورود فایل معتبر نیست')
        upload = request.files.get('file')
        if not upload:
            raise ValueError('فایل تکمیل‌شده XLSX را انتخاب کنید')
        payload = upload.read(16 * 1024 * 1024 + 1)
        if len(payload) > 16 * 1024 * 1024:
            raise ValueError('حجم فایل حداکثر ۱۶ مگابایت باشد')
        rows = read_rows(payload, form, cid)
        connection = db()
        result = dict(ok=True, mode=mode, policy=policy, kind=kind, company_id=cid, total=len(rows), created=0, updated=0, unchanged=0, errors=[])
        connection.execute('BEGIN IMMEDIATE')
        try:
            seen = set()
            for row_number, values, problem in rows:
                connection.execute('SAVEPOINT excel_form_row')
                try:
                    if problem:
                        raise ValueError(problem)
                    key = tuple(values.get(field, '') for field in form['keys'])
                    if key in seen:
                        raise ValueError('این رکورد در ردیف دیگری از فایل تکرار شده است')
                    seen.add(key)
                    outcome = write_row(connection, kind, cid, values, policy, g.user['role'], validate_refs)
                    result[outcome] += 1
                    connection.execute('RELEASE excel_form_row')
                except (ValueError, sqlite3.IntegrityError) as exc:
                    connection.execute('ROLLBACK TO excel_form_row')
                    connection.execute('RELEASE excel_form_row')
                    message = str(exc) if isinstance(exc, ValueError) else 'رکورد تکراری یا دارای ارتباط نامعتبر است'
                    result['errors'].append(dict(row=row_number, message=message))
            result['ok'] = not result['errors']
            result['can_commit'] = result['ok']
            result['applied'] = mode == 'commit' and result['ok']
            result['message'] = 'اطلاعات فایل ثبت شد.' if result['applied'] else 'بررسی انجام شد؛ هیچ اطلاعاتی ثبت نشد.'
            if mode == 'commit' and result['ok']:
                if result['created'] or result['updated']:
                    audit('excel_form_' + kind, cid, json.dumps({key: result[key] for key in ('policy', 'total', 'created', 'updated', 'unchanged')}, ensure_ascii=False))
                connection.commit()
            else:
                connection.rollback()
        except Exception:
            connection.rollback()
            raise
        return jsonify(result), 400 if mode == 'commit' and not result['ok'] else 200
