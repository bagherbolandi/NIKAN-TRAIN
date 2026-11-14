"""One scoped skill matrix over canonical people, bank codes and training evidence.

An absent assessment is unknown, never an assessed zero. Ideal job requirements,
explicit personal overrides and configured training policies remain distinguishable.
This module stores no assessment, competency or personnel copies.
"""
from datetime import date
import io
import json

from flask import Response, jsonify, request

from . import hrm_access as access
from .hrm_common import date_value, jalali_display, text_value, today_iso
from .hrm_training_evidence import scoped_training_evidence, renewal_evidence, applicable_hse_training_rules
from .validation import positive_id

MAX_CELLS = 50000
LEVEL_DEFINITIONS = [
    dict(level=0, symbol='0', label='سطح صفر ثبت‌شده', observable='در ارزیابی، توانایی قابل اتکا احراز نشده است؛ با نبود ارزیابی تفاوت دارد.'),
    dict(level=1, symbol='I', label='آشنایی', observable='موضوع، واژه‌ها و دستورالعمل اصلی را می‌شناسد.'),
    dict(level=2, symbol='L', label='یادگیری', observable='با راهنمایی یا نظارت، کار تعریف‌شده را انجام می‌دهد.'),
    dict(level=3, symbol='U', label='کاربرد', observable='کار تعریف‌شده را مستقل و مطابق معیار انجام می‌دهد.'),
    dict(level=4, symbol='O', label='تسلط', observable='کار پیچیده و رفع اشکال را انجام می‌دهد و توان انتقال تجربه دارد.'),
]
STATUS_LABELS = {
    'unassessed': 'ارزیابی ثبت نشده', 'gap': 'فاصله با سطح لازم',
    'renewal_due': 'موعد تجدید رسیده', 'review_required': 'نیازمند بازبینی شواهد',
    'satisfied': 'سطح لازم احراز شده', 'not_required': 'بدون الزام صریح',
    'requirement_missing': 'سطح لازم تعیین نشده',
}
QUALITY_LABELS = {
    'assessor_not_recorded': 'نام ارزیاب واقعی ثبت نشده است؛ ثبت‌کننده سیستم جایگزین ارزیاب نیست.',
    'assessment_method_not_recorded': 'روش ارزیابی ثبت نشده است.',
    'assessment_evidence_not_recorded': 'مرجع یا شرح شواهد ارزیابی ثبت نشده است.',
    'requirement_not_configured': 'سطح لازم شایستگی تعیین نشده است.',
    'assessment_not_recorded': 'ارزیابی یا شاهد سطح شایستگی در محدوده مجاز ثبت نشده است.',
    'competency_evidence_date_unknown': 'تاریخ شاهد سطح شایستگی مشخص نیست.',
    'renewal_actual_evidence_missing': 'برای تجدید، شاهد حضور واقعی یا تاریخ معتبر در دسترس نیست.',
    'duplicate_requirement_source': 'بیش از یک الزام هم‌نوع برای همین کد ثبت شده است؛ منابع بررسی شوند.',
    'personal_requirement_differs_from_job': 'الزام اختصاصی فرد با مطلوب شغل متفاوت است؛ هر دو فاصله جدا نمایش داده می‌شوند.',
    'employment_start_date_unknown': 'تاریخ شروع همکاری برای کنترل بدو استخدام مشخص نیست.',
    'onboarding_completion_missing': 'آموزش واقعی پس از شروع همکاری احراز نشده است.',
}
SOURCE_LABELS = {'job_profile': 'الزام مطلوب شغل', 'personal_profile': 'الزام اختصاصی فرد',
                 'configured_general_policy': 'ضابطه آموزش شغل و محل', 'hse_curriculum': 'الزام آموزشی ایمنی، بهداشت یا محیط‌زیست'}


def _filters(conn, args, operation):
    actor = access.require(conn, 'training', operation=operation)
    filters = {}
    for key in ('company_id', 'site_id', 'employee_id'):
        if args.get(key) not in (None, ''):
            filters[key] = positive_id(args[key])
    if filters.get('company_id') and filters['company_id'] not in actor['company_ids']:
        raise PermissionError()
    if filters.get('site_id'):
        site = conn.execute('SELECT company_id FROM hrm_sites WHERE id=?', (filters['site_id'],)).fetchone()
        if site is None:
            raise ValueError('سایت یافت نشد')
        if filters.get('company_id') and filters['company_id'] != site['company_id']:
            raise ValueError('سایت متعلق به شرکت انتخاب‌شده نیست')
        if not actor['is_primary_admin'] and not any(s['company_id'] == site['company_id'] and s['site_id'] in (None, filters['site_id']) for s in actor['scopes']):
            raise PermissionError()
    if filters.get('employee_id'):
        # A direct person filter cannot probe identities outside the actor's scope.
        access.employee(conn, filters['employee_id'], 'training', operation=operation)
    for key in ('department', 'job', 'competency_code', 'category', 'q'):
        if args.get(key):
            filters[key] = text_value(args[key], max_length=240)
    view = args.get('view', 'required')
    if view not in ('required', 'all'):
        raise ValueError('نمای ماتریس معتبر نیست')
    as_of = date_value(args.get('as_of') or today_iso(), required=True)
    if as_of > today_iso():
        raise ValueError('ماتریس شواهد آینده را تأیید نمی‌کند؛ تاریخ مبنا را تا امروز انتخاب کنید')
    return filters, view, as_of


def _people(conn, filters, operation):
    predicate, params = access.scope_sql(conn, 'training', alias='e', operation=operation)
    rows = []
    for item in conn.execute('SELECT e.id FROM employees e WHERE ' + predicate + ' ORDER BY e.id', params):
        person = access.employee(conn, item['id'], 'training', operation=operation)
        detail = conn.execute('SELECT location,start_date,status FROM hrm_employee_details WHERE employee_id=?', (person['id'],)).fetchone()
        person.update(dict(detail) if detail else dict(location='', start_date='', status='active'))
        if any(str(person.get('id' if key == 'employee_id' else key, '')) != str(value)
               for key, value in filters.items() if key in ('company_id', 'site_id', 'employee_id', 'department', 'job')):
            continue
        rows.append(person)
    return rows


def _requirements(conn, person, operation):
    """Keep original sources and explicit personal zero rather than COALESCE to 0."""
    rules = {}
    def add(code, level, kind, key, **extra):
        item = rules.setdefault(code, dict(job=[], personal=[], policies=[]))
        item[key].append(dict(kind=kind, label=SOURCE_LABELS[kind], level=level, **extra))
    for row in conn.execute('''SELECT r.id,r.skill_id,r.level,l.code,j.id profile_id,j.revision
            FROM requirements r JOIN competency_links l ON l.skill_id=r.skill_id
            LEFT JOIN job_profiles j ON j.company_id=r.company_id AND j.job=r.job
            WHERE r.company_id=? AND r.job=? ORDER BY r.id''', (person['company_id'], person['job'])):
        add(row['code'], row['level'], 'job_profile', 'job', record_id=row['id'], skill_id=row['skill_id'],
            profile_id=row['profile_id'], revision=row['revision'] or '')
    for row in conn.execute('''SELECT p.skill_id,p.level,l.code FROM personal_requirements p
            JOIN skills s ON s.id=p.skill_id JOIN competency_links l ON l.skill_id=p.skill_id
            WHERE p.employee_id=? AND s.company_id=? ORDER BY p.skill_id''', (person['id'], person['company_id'])):
        add(row['code'], row['level'], 'personal_profile', 'personal', record_id=None,
            record_key=dict(employee_id=person['id'], skill_id=row['skill_id']))
    for raw in conn.execute('SELECT * FROM hrm_training_requirements WHERE company_id=? AND active=1 AND is_deleted=0 ORDER BY id', (person['company_id'],)):
        rule = dict(raw)
        if rule['site_id'] is not None and rule['site_id'] != person.get('site_id'):
            continue
        if any(rule.get(key) and rule[key] != person.get(target, '') for key, target in
               [('department', 'department'), ('job', 'job'), ('activity_location', 'location')]):
            continue
        add(rule['competency_code'], rule['required_level'], 'configured_general_policy', 'policies',
            record_id=rule['id'], version=rule['version'], renewal_days=rule['renewal_days'], onboarding=bool(rule['onboarding']))
    for rule in applicable_hse_training_rules(conn, person, 'training', operation=operation):
        add(rule['competency_code'], rule['required_level'], 'hse_curriculum', 'policies',
            record_id=rule['id'], version=rule['version'], renewal_days=rule['renewal_days'], onboarding=False,
            source_module=rule['source_module'], source_resource=rule['source_resource'], domain=rule['domain'])
    return rules


def _candidate_codes(conn, person):
    # Existence alone is not exported. Each candidate is subsequently resolved
    # against historical company/site/unit ownership by the common evidence code.
    eid = person['id']
    rows = conn.execute('''SELECT l.code FROM hrm_live_assessments a JOIN competency_links l ON l.skill_id=a.skill_id WHERE a.employee_id=?
        UNION SELECT l.code FROM hrm_live_records r JOIN courses c ON c.id=r.course_id JOIN competency_links l ON l.skill_id=c.skill_id WHERE r.employee_id=?
        UNION SELECT l.code FROM course_participants p JOIN courses c ON c.id=p.course_id JOIN competency_links l ON l.skill_id=c.skill_id WHERE p.employee_id=? AND p.attended=1''', (eid, eid, eid))
    return {row[0] for row in rows}


def _audit_actor(conn, evidence):
    aid = evidence.get('assessment_id')
    owner = evidence.get('assessment_owner')
    if not aid or not owner:
        return None
    row = conn.execute('''SELECT u.username,a.created FROM audit a LEFT JOIN users u ON u.id=a.user_id
        WHERE a.company_id=? AND a.detail=? AND a.action IN ('hrm_training_assessments_create','hrm_training_assessments_edit')
        ORDER BY a.id DESC LIMIT 1''', (owner['company_id'], str(aid))).fetchone()
    return dict(username=row['username'], recorded_at=row['created'], label='ثبت‌کننده ارزیابی') if row else None


def _cell(conn, person, bank, rule, evidence, as_of):
    job = max((source['level'] for source in rule['job']), default=None)
    personal = max((source['level'] for source in rule['personal']), default=None)
    resolved = personal if personal is not None else job
    policies = [source['level'] for source in rule['policies']]
    if policies:
        resolved = max([resolved] + policies) if resolved is not None else max(policies)
    renewal_days = min((source['renewal_days'] for source in rule['policies'] if source['renewal_days'] is not None), default=None)
    onboarding = any(source['onboarding'] for source in rule['policies'])
    renewal = renewal_evidence(evidence['last_training_date'], renewal_days, as_of)
    current = evidence['current_level']
    gap = max(0, resolved-current) if resolved is not None and current is not None else None
    job_gap = max(0, job-current) if job is not None and current is not None else None
    personal_gap = max(0, personal-current) if personal is not None and current is not None else None
    flags = []
    if resolved is None: flags.append('requirement_not_configured')
    if current is None: flags.append('assessment_not_recorded')
    trace_missing = False
    if current is not None and evidence.get('assessment_id') is not None:
        for key, flag in (('assessor_name','assessor_not_recorded'),
                          ('assessment_method','assessment_method_not_recorded')):
            if not evidence.get(key):
                flags.append(flag);trace_missing = True
        if not (evidence.get('assessment_evidence') or evidence.get('evidence')):
            flags.append('assessment_evidence_not_recorded');trace_missing = True
    if current is not None and not (evidence.get('assessment_date') or evidence.get('level_record_date')):
        flags.append('competency_evidence_date_unknown')
    if renewal['renewal_review_required']: flags.append('renewal_actual_evidence_missing')
    if len(rule['job']) > 1 or len(rule['personal']) > 1: flags.append('duplicate_requirement_source')
    variance = personal is not None and job is not None and personal != job
    if variance: flags.append('personal_requirement_differs_from_job')
    start = person.get('start_date', '')
    onboarding_missing = (not evidence['last_training_date'] or evidence['last_training_date'] < start) if onboarding and start else None
    if onboarding and not start: flags.append('employment_start_date_unknown')
    if onboarding_missing is True: flags.append('onboarding_completion_missing')
    if resolved is None: status = 'requirement_missing'
    elif resolved == 0: status = 'review_required' if job and (current is None or job_gap>0) else 'not_required'
    elif current is None: status = 'unassessed'
    elif gap > 0: status = 'gap'
    elif renewal['renewal_due'] is True: status = 'renewal_due'
    elif trace_missing or renewal['renewal_review_required'] or onboarding_missing is True or 'competency_evidence_date_unknown' in flags or (variance and job_gap and job_gap > 0): status = 'review_required'
    else: status = 'satisfied'
    sources = rule['job'] + rule['personal'] + rule['policies']
    item = dict(employee_id=person['id'], employee_code=person['code'], employee_name=person['name'],
        company_id=person['company_id'], site_id=person.get('site_id'), department=person['department'], job=person['job'],
        competency_code=bank['code'], competency_title=bank['title'], category=bank['category'], importance=bank['importance'],
        job_required_level=job, personal_required_level=personal, required_level=resolved,
        requirement_status='not_configured' if resolved is None else 'explicitly_not_required' if resolved == 0 else 'configured',
        requirement_sources=sources, requirement_variance=variance, gap=gap, job_gap=job_gap, personal_gap=personal_gap,
        status=status, status_label=STATUS_LABELS[status], renewal_days=renewal_days, onboarding=onboarding,
        onboarding_missing=onboarding_missing, **evidence, **renewal, data_quality_flags=flags,
        data_quality_messages=[QUALITY_LABELS[flag] for flag in flags])
    item['recorded_by'] = evidence.get('recorded_by') or _audit_actor(conn, evidence)
    item['has_current_level'] = current is not None
    item['has_assessment'] = evidence.get('assessment_id') is not None
    item['assessor_name'] = evidence.get('assessor_name') or None
    item['assessment_method'] = evidence.get('assessment_method') or None
    item['assessment_evidence'] = evidence.get('assessment_evidence') or evidence.get('evidence') or None
    return item


def matrix_report(conn, args=None, *, operation='read'):
    if operation not in ('read', 'export'):
        raise ValueError('عملیات ماتریس معتبر نیست')
    args = request.args if args is None else args
    filters, view, as_of = _filters(conn, args, operation)
    people = _people(conn, filters, operation)
    bank = {row['code']: dict(row) for row in conn.execute('SELECT * FROM holding_competencies ORDER BY category,code')}
    if filters.get('competency_code') and filters['competency_code'] not in bank:
        raise ValueError('کد شایستگی در بانک مرجع وجود ندارد')
    if filters.get('competency_code'):
        bank = {filters['competency_code']: bank[filters['competency_code']]}
    if filters.get('category'):
        bank = {code: row for code, row in bank.items() if row['category'] == filters['category']}
    if view == 'all' and len(bank) * len(people) > MAX_CELLS:
        raise ValueError('ماتریس بسیار بزرگ است؛ شرکت، سایت، فرد یا دسته شایستگی را محدود کنید')
    rows, used, quality = [], set(), []
    summaries = []
    for person in people:
        requirements = _requirements(conn, person, operation)
        codes = set(bank) if view == 'all' else (set(requirements) | _candidate_codes(conn, person)) & set(bank)
        person_rows = []
        selected = {row['code']: row['skill_id'] for row in conn.execute('SELECT l.code,l.skill_id FROM competency_links l JOIN skills s ON s.id=l.skill_id WHERE s.company_id=?', (person['company_id'],))}
        for code in sorted(codes):
            evidence = scoped_training_evidence(conn, person, code, 'training', as_of=as_of, operation=operation)
            if view != 'all' and code not in requirements and evidence['current_level'] is None and not evidence['last_training_date']:
                continue
            raw = _cell(conn, person, bank[code], requirements.get(code, dict(job=[], personal=[], policies=[])), evidence, as_of)
            raw['skill_id'] = selected.get(code)
            if filters.get('q') and filters['q'].casefold() not in ' '.join(str(raw.get(key, '')) for key in ('employee_name','employee_code','job','department','competency_code','competency_title')).casefold():
                continue
            person_rows.append(raw);rows.append(raw);used.add(code)
            if len(rows) > MAX_CELLS:
                raise ValueError('ماتریس بسیار بزرگ است؛ محدوده شرکت، سایت یا فرد را کوچک‌تر کنید')
        meaningful = [row for row in person_rows if row['required_level'] is not None and row['required_level'] > 0]
        summary = {key: person.get(key) for key in ('id', 'code', 'name', 'company_id', 'site_id', 'department', 'job', 'location', 'status')}
        summary.update(required_competencies=len(meaningful), unknown_competencies=sum(row['current_level'] is None for row in meaningful),
                       gaps=sum(row['gap'] is not None and row['gap'] > 0 for row in meaningful),
                       job_ideal_gaps=sum(row['job_gap'] is not None and row['job_gap'] > 0 for row in person_rows),
                       job_ideal_unknowns=sum(row['job_required_level'] is not None and row['job_required_level']>0 and row['current_level'] is None for row in person_rows))
        summaries.append(summary)
        if not requirements:
            quality.append(dict(employee_id=person['id'], code='no_configured_requirements', message='برای شغل یا فرد، سطح لازم شایستگی تنظیم نشده است.'))
        if not person['job']:
            quality.append(dict(employee_id=person['id'], code='job_not_recorded', message='شغل جاری فرد ثبت نشده است.'))
        if not person.get('site_id'):
            quality.append(dict(employee_id=person['id'], code='site_not_recorded', message='سایت جاری فرد ثبت نشده است.'))
    columns = [dict(row) for code, row in bank.items() if code in used or view == 'all']
    grouped = {person['id']: {} for person in summaries}
    for row in rows: grouped[row['employee_id']][row['competency_code']] = row
    matrix = [dict(employee_id=person['id'], cells={column['code']: grouped[person['id']].get(column['code']) for column in columns}) for person in summaries]
    required = [row for row in rows if row['required_level'] is not None and row['required_level'] > 0]
    ideal = [row for row in rows if row['job_required_level'] is not None and row['job_required_level'] > 0]
    metrics = dict(job_ideal_gaps=sum(row['job_gap'] is not None and row['job_gap']>0 for row in ideal),
                   job_ideal_unknowns=sum(row['current_level'] is None for row in ideal),
                   critical_job_ideal_gaps=sum(row['importance']==3 and row['job_gap'] is not None and row['job_gap']>0 for row in ideal),
                   employees=len(summaries), competencies=len(columns), cells=len(rows), required_cells=len(required),
                   assessed_requirements=sum(row['current_level'] is not None for row in required),
                   unknown_requirements=sum(row['current_level'] is None for row in required),
                   competency_gaps=sum(row['gap'] is not None and row['gap'] > 0 for row in required),
                   critical_gaps=sum(row['importance'] == 3 and row['gap'] is not None and row['gap'] > 0 for row in required),
                   critical_unknowns=sum(row['importance'] == 3 and row['current_level'] is None for row in required),
                   renewal_due=sum(row['renewal_due'] is True for row in required),
                   evidence_reviews=sum(row['status'] == 'review_required' for row in required))
    return dict(as_of=as_of, population_basis='current_assignment', requirement_basis='current_job_personal_override_and_configured_policy',
                evidence_basis='authorized_evidence_on_or_before_as_of', qualification_basis='effective_personal_or_job_plus_mandatory_policies;ideal_job_gaps_separate', view=view, filters=filters, employees=summaries,
                competencies=columns, rows=rows, matrix=matrix, metrics=metrics, data_quality=quality,
                level_definitions=LEVEL_DEFINITIONS, status_labels=STATUS_LABELS,
                data_status='no_scoped_personnel' if not summaries else 'no_configured_requirements' if not required else 'recorded_data')


def person_matrix_rows(conn, employee_id, *, operation='read', include_all=False):
    """Legacy-compatible aliases with canonical current assignment and null unknowns."""
    access.employee(conn, employee_id, 'training', operation=operation)
    report = matrix_report(conn, {'employee_id': employee_id, 'view': 'all' if include_all else 'required'}, operation=operation)
    bank = {row['code']: row for row in report['competencies']}
    return [dict(bank[row['competency_code']], **row, id=row['skill_id'], code=row['competency_code'], title=row['competency_title'],
                 required=row['required_level'], current=row['current_level']) for row in report['rows']]


def _safe(value):
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'" + value
    return value


def workbook_bytes(conn, report):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.drawing.image import Image
    from .commitments import holding_logo
    wb = openpyxl.Workbook();wide = wb.active;wide.title = 'ماتریس شایستگی'
    flat = wb.create_sheet('جزئیات و شواهد')
    legend = wb.create_sheet('تعریف سطح‌ها')
    legend.append(['نیکان — تعریف سطح شایستگی و مبنای ماتریس'])
    legend.append(['بدون ارزیابی', 'نامشخص؛ با سطح صفر ثبت‌شده متفاوت است'])
    for item in LEVEL_DEFINITIONS:legend.append([item['level'], item['symbol'], item['label'], item['observable']])
    legend.append(['مبنای جمعیت', 'انتساب جاری فرد؛ تاریخ مبنا فقط شواهد را تا آن تاریخ محدود می‌کند'])
    legend.append(['مبنای الزام', 'مطلوب شغل و الزام اختصاصی فرد جدا ثبت می‌شوند؛ ضوابط شغل و محل نیز مشخص‌اند'])
    legend.append(['جمع‌بندی', 'میانگین نمره جایگزین فاصله یا نامشخص بودن شایستگی بحرانی نیست'])
    for sheet in (wide, flat):
        sheet.sheet_view.rightToLeft = True
        sheet.append(['نیکان — ماتریس شایستگی کارکنان'])
        sheet.append(['تاریخ مبنا', jalali_display(report['as_of'])])
        sheet.append(['مبنا', 'انتساب جاری و شواهد مجاز ثبت‌شده؛ نبود داده صفر محسوب نمی‌شود'])
    wide.append(['کد پرسنلی','نام و نام خانوادگی','شرکت','سایت','واحد','شغل'] + [row['code']+' · '+row['title'] for row in report['competencies']])
    company = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM companies')}
    sites = {row['id']: row['name'] for row in conn.execute('SELECT id,name FROM hrm_sites')}
    for person, row in zip(report['employees'], report['matrix']):
        cells=[]
        for column in report['competencies']:
            cell=row['cells'][column['code']]
            if cell is None:cells.append('الزام و ارزیابی ثبت نشده');continue
            current='نامشخص' if cell['current_level'] is None else str(cell['current_level'])
            required='تعیین نشده' if cell['required_level'] is None else str(cell['required_level'])
            cells.append(f"فعلی: {current} / لازم: {required} — {cell['status_label']}")
        wide.append([_safe(person['code']),_safe(person['name']),_safe(company.get(person['company_id'],'')),
                     _safe(sites.get(person['site_id'],'')),_safe(person['department']),_safe(person['job']),*cells])
    fields=[('employee_code','کد پرسنلی'),('employee_name','نام فرد'),('job','شغل جاری'),('competency_code','کد شایستگی'),
            ('competency_title','شایستگی'),('job_required_level','سطح مطلوب شغل'),('personal_required_level','الزام اختصاصی فرد'),
            ('required_level','سطح لازم نهایی'),('current_level','سطح فعلی'),('gap','فاصله با الزام جاری'),('job_gap','فاصله با مطلوب شغل'),('personal_gap','فاصله با الزام اختصاصی فرد'),('status_label','وضعیت'),
            ('assessment_date','تاریخ ارزیابی'),('assessor_name','ارزیاب ثبت‌شده'),('assessment_method','روش ارزیابی'),
            ('assessment_evidence','شاهد ارزیابی'),('last_training_date','آخرین حضور واقعی'),('renewal_due_date','موعد تجدید')]
    flat.append([label for _,label in fields]+['مرجع الزام','مرجع شواهد','ثبت‌کننده ارزیابی','کنترل کیفیت داده'])
    for row in report['rows']:
        values=[]
        for key,_ in fields:
            value=row.get(key)
            if value is None:value='نامشخص'
            if key in ('assessment_date','last_training_date','renewal_due_date') and value not in ('','نامشخص'):value=jalali_display(value)
            values.append(_safe(value))
        sources='؛ '.join(source['label']+' '+str(source.get('record_id') or source.get('record_key') or '') for source in row['requirement_sources'])
        evidence='؛ '.join(f'{label}: {row[key]}' for key,label in [('assessment_id','ارزیابی'),('level_record_id','سابقه نمره'),('actual_course_id','حضور دوره')] if row.get(key))
        recorded=row.get('recorded_by') or {}
        recorded=recorded.get('username','') if isinstance(recorded,dict) else str(recorded)
        flat.append(values+[_safe(sources),_safe(evidence),_safe(recorded),_safe('، '.join(row['data_quality_messages']))])
    logo=holding_logo(conn)
    for sheet in (wide,flat,legend):
        sheet.sheet_view.rightToLeft=True
        sheet.freeze_panes='G5' if sheet==wide else 'A5' if sheet==flat else 'A2'
        sheet.print_options.horizontalCentered=True;sheet.sheet_properties.pageSetUpPr.fitToPage=True
        sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A3
        if sheet!=legend:sheet.auto_filter.ref=f'A4:{openpyxl.utils.get_column_letter(sheet.max_column)}{sheet.max_row}'
        for line in sheet:
            for cell in line:cell.alignment=Alignment(horizontal='right',vertical='top',wrap_text=True)
        for cell in sheet[4 if sheet!=legend else 1]:
            cell.fill=PatternFill('solid',fgColor='173B55');cell.font=Font(bold=True,color='FFFFFF')
        for index in range(1,sheet.max_column+1):sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width=29
        if logo:
            image=Image(io.BytesIO(logo['content']));image.width=80;image.height=50;sheet.add_image(image,'G1' if sheet!=legend else 'F1')
    out=io.BytesIO();wb.save(out);return out.getvalue()


def register(app, db, authenticated, audit, error):
    @app.get('/api/hrm/skill-matrix')
    @authenticated
    def skill_matrix():
        return jsonify(matrix_report(db()))

    @app.get('/api/hrm/skill-matrix.xlsx')
    @app.get('/api/hrm/reports/skill-matrix.xlsx')
    @authenticated
    def skill_matrix_excel():
        report=matrix_report(db(),operation='export')
        payload=workbook_bytes(db(),report)
        audit('hrm_skill_matrix_export',report['filters'].get('company_id'),json.dumps(dict(employees=len(report['employees']),cells=len(report['rows']))))
        db().commit()
        return Response(payload,mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="Nikan-Skill-Matrix.xlsx"'})
