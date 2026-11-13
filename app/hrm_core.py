"""Permission-scoped management dashboards and reports for the existing app."""
import io
from datetime import date
from urllib.parse import quote

from flask import jsonify, request, Response, g
from werkzeug.exceptions import NotFound, MethodNotAllowed

from .hrm_access import ctx, require, scope_sql, company_scope, effective_column
from .hrm_common import date_value, jalali_display, today_iso
from .validation import positive_id

SECTIONS = {
    'personnel': ('personnel', 'contracts', 'documents', 'attendance', 'performance',
                  'discipline', 'loans', 'welfare', 'accommodation'),
    'training': ('training',),
    'hse': ('hse',),
    'health_environment': ('health_environment', 'health'),
}


def _flags(actor, module):
    values = actor['permissions'].get(module, {})
    return dict(read=bool(values.get('read')), create=bool(values.get('create', values.get('write'))),
                edit=bool(values.get('edit', values.get('write'))),
                delete=bool(values.get('delete', False)),
                export=bool(values.get('export', values.get('read'))))


def _schema_for_actor(raw, actor):
    import copy
    schemas = {}
    for key, source in raw.items():
        schema = copy.deepcopy(source)
        module = schema['module']
        if key == 'documents':
            groups = [name for name in ('documents', 'contracts', 'discipline', 'loans',
                                       'performance', 'health', 'hse', 'health_environment')
                      if any(_flags(actor, name).values())]
            if not groups:
                continue
            readable = [name for name in groups if _flags(actor,name)['read']]
            module = module if module in readable else (readable or groups)[0]
            schema['module'] = module
            schema['permission_groups'] = groups
            for field in schema.get('fields', []):
                if field['key'] == 'permission_group':
                    field['choices'] = [choice for choice in field['choices'] if choice[0] in groups]
                    field['choices_by_operation'] = {op: [choice for choice in field['choices'] if _flags(actor,choice[0])[op]]
                                                    for op in ('create','edit')}
        flags = _flags(actor, module)
        if key == 'documents':
            flags = {op:any(_flags(actor,name)[op] for name in groups) for op in flags}
        if not any(flags.values()):
            continue
        schema.update({'can_' + action: value for action, value in flags.items()})
        schema['can_write'] = flags['create'] or flags['edit']
        if schema.get('read_only'):
            schema['can_create']=schema['can_edit']=schema['can_delete']=schema['can_write']=False
        if not schema.get('deletable'):
            schema['can_delete']=False
        # Company-wide policy definitions are different from editing staff in
        # a delegated unit. Their backend performs the same check.
        config = key == 'sites'
        full_company = any(not s.get('department') and not s.get('site_id') for s in actor['scopes'])
        if config and not full_company:
            schema['can_create'] = schema['can_edit'] = schema['can_write'] = False
        schemas[key] = schema
    return schemas


def _staff(conn, module):
    predicate, values = scope_sql(conn, module, 'e')
    rows = [dict(row) for row in conn.execute(
        'SELECT e.id,e.code,e.name,' + effective_column(conn, 'company_id') + ' company_id,'
        + effective_column(conn, 'department') + ' department,'
        + effective_column(conn, 'job') + ' job,hd.site_id,hd.location,hd.status '
        'FROM employees e LEFT JOIN hrm_employee_details hd ON hd.employee_id=e.id '
        'WHERE ' + predicate + ' ORDER BY e.id', values)]
    return filter_rows(conn, rows, module, staff_rows=True)


def filter_rows(conn, rows, module, staff_rows=False):
    """Apply the same explicit reporting filters to JSON, dashboards and XLSX."""
    cid = request.args.get('company_id')
    if cid:
        cid = positive_id(cid)
        actor = require(conn,module)
        if not actor.get('is_primary_admin') and cid not in actor['company_ids']:
            raise PermissionError()
    site = request.args.get('site_id')
    site = positive_id(site) if site else None
    if site:
        record = conn.execute('SELECT id,company_id FROM hrm_sites WHERE id=?', (site,)).fetchone()
        if not record:
            raise ValueError('سایت یافت نشد')
        actor = ctx(conn)
        permitted = any(s['company_id'] == record['company_id'] and
                        (not s.get('site_id') or s['site_id'] == site) for s in actor['scopes'])
        if not permitted:
            raise PermissionError()
    department = request.args.get('department', '').strip()
    eid = request.args.get('employee_id')
    eid = positive_id(eid) if eid else None
    start = date_value(request.args.get('from_date'))
    end = date_value(request.args.get('to_date'))
    if start and end and end < start:
        raise ValueError('پایان بازه پیش از شروع است')
    query = request.args.get('q', '').strip().casefold()
    staff = {}
    if any((site, department)) and not staff_rows:
        for row in conn.execute('SELECT employee_id,company_id,site_id,department FROM hrm_employee_details'):
            staff[row['employee_id']] = dict(row)
    result = []
    for row in rows:
        person_id = row.get('id') if staff_rows else row.get('employee_id')
        detail = row if staff_rows else staff.get(person_id, {})
        row_cid = row.get('company_id', detail.get('company_id'))
        if cid and row_cid is not None and row_cid != cid:
            continue
        row_site = row.get('site_id', detail.get('site_id'))
        if site and row_site != site:
            continue
        row_department = row.get('department', detail.get('department'))
        if department and row_department != department:
            continue
        if eid and person_id != eid:
            continue
        event = next((row[key] for key in ('work_date', 'exam_date', 'issue_date', 'date',
                      'effective_date', 'start_date', 'payment_date', 'use_date') if row.get(key)), None)
        if event and (start or end):
            normalized = date_value(event)
            if (start and normalized < start) or (end and normalized > end):
                continue
        if query and query not in ' '.join(str(value) for value in row.values()
                                           if value is not None).casefold():
            continue
        result.append(row)
    return result


def _training(conn, staff):
    """Actual attendance determines hours; assessment and planning stay distinct."""
    ids = {row['id'] for row in staff}
    counts = dict(staff=len(ids), planned_courses=0, completed_courses=0,
                  actual_attendances=0, actual_person_hours=0, hours_per_employee=None,
                  achievement_percent=None, assessments=0)
    courses = {row['id']: dict(row) for row in conn.execute('SELECT * FROM courses')}
    selected_courses, presences = set(), []
    for row in conn.execute('SELECT * FROM course_participants'):
        if row['employee_id'] in ids:
            selected_courses.add(row['course_id'])
            if row['attended']:
                presences.append(row['course_id'])
    # A course without assigned people cannot be attributed to a particular
    # site. Holding/company views may still count its documented plan.
    if not request.args.get('site_id') and not request.args.get('department'):
        actor=ctx(conn)
        company_ids={scope['company_id'] for scope in actor['scopes'] if not scope.get('site_id') and not scope.get('department')}
        if request.args.get('company_id'):company_ids&={positive_id(request.args['company_id'])}
        selected_courses.update(row['id'] for row in courses.values() if row['company_id'] in company_ids)
    start = date_value(request.args.get('from_date'))
    end = date_value(request.args.get('to_date'))
    def in_period(cid):
        if not start and not end:return True
        try:value = date_value(courses[cid]['date'])
        except ValueError:return False
        return not ((start and value < start) or (end and value > end))
    selected_courses = {cid for cid in selected_courses if cid in courses and in_period(cid)}
    counts['planned_courses'] = sum(courses[cid]['status'] != 'cancelled' for cid in selected_courses)
    counts['completed_courses'] = sum(courses[cid]['status'] == 'completed' for cid in selected_courses)
    presences = [cid for cid in presences if cid in selected_courses]
    counts['actual_attendances'] = len(presences)
    counts['actual_person_hours'] = sum(courses[cid]['hours'] for cid in presences)
    counts['hours_per_employee'] = counts['actual_person_hours'] / len(ids) if ids else None
    if counts['planned_courses']:
        counts['achievement_percent'] = counts['completed_courses'] * 100 / counts['planned_courses']
    counts['assessments'] = sum(row['employee_id'] in ids for row in conn.execute('SELECT employee_id FROM assessments'))
    return counts


def register(app, db, authenticated, audit, error):
    def schemas():
        return _schema_for_actor(app.extensions.get('hrm_catalog', {}), ctx(db()))

    def rows_for(resource):
        # Invoke the existing protected listing, rather than inventing a second
        # export query with weaker company/site or confidentiality predicates.
        adapter = app.url_map.bind('localhost')
        try:
            endpoint, args = adapter.match('/api/hrm/' + resource, method='GET')
        except (NotFound, MethodNotAllowed):
            raise ValueError('گزارش این بخش در دسترس نیست') from None
        result = app.make_response(app.view_functions[endpoint](**args))
        if result.status_code != 200:
            if result.status_code == 403:
                raise PermissionError()
            raise ValueError((result.get_json(silent=True) or {}).get('error', 'دریافت گزارش ناموفق بود'))
        rows = result.get_json()
        if not isinstance(rows, list):
            raise ValueError('این بخش گزارش جدولی ندارد')
        return rows

    def object_for(path):
        endpoint,args=app.url_map.bind('localhost').match('/api/hrm/'+path,method='GET')
        result=app.make_response(app.view_functions[endpoint](**args))
        if result.status_code==403:raise PermissionError()
        if result.status_code!=200:raise ValueError('دریافت شاخص تخصصی ناموفق بود')
        value=result.get_json()
        if not isinstance(value,dict):raise ValueError('گزارش تخصصی معتبر نیست')
        return value

    @app.get('/api/hrm/catalog')
    @authenticated
    def hrm_catalog():
        return jsonify(schemas())

    @app.get('/api/hrm/options/<resource>')
    @authenticated
    def hrm_reference_options(resource):
        module = request.args.get('module', '')
        operation = request.args.get('operation', 'read')
        if operation not in ('read', 'create', 'edit'):
            raise ValueError('نوع دسترسی گزینه مرجع معتبر نیست')
        require(db(), module, operation=operation)
        raw = app.extensions.get('hrm_catalog', {})
        if resource not in raw:
            raise ValueError('فهرست مرجع یافت نشد')
        source = raw[resource]
        if resource in ('sites', 'units'):
            table = 'hrm_' + resource
            actor = ctx(db())
            candidates = [dict(row) for row in db().execute('SELECT * FROM ' + table)]
            rows = []
            for row in candidates:
                site = row.get('site_id') if resource == 'units' else row['id']
                if not any(s['company_id'] == row['company_id'] and
                           (not s.get('site_id') or s['site_id'] == site) and
                           (not s.get('department') or resource == 'sites' or s['department'] == row.get('name'))
                           for s in actor['scopes']):
                    continue
                if request.args.get('company_id') and row['company_id'] != positive_id(request.args['company_id']):
                    continue
                rows.append(dict(id=row['id'], label=row['name'], company_id=row['company_id'], site_id=site))
            return jsonify(rows)
        if resource == 'documents':
            group = request.args.get('permission_group', 'documents')
            # Existing private document titles are not a creation prerequisite.
            require(db(), group, operation='read')
            module, operation = group, 'read'
        elif source['module'] != module:
            raise PermissionError()
        g.hrm_reference_operation = dict(module=module, operation=operation)
        protected_rows=rows_for(resource)
        rows=protected_rows if resource=='training_courses' else filter_rows(db(),protected_rows,module)
        result = []
        for row in rows:
            label = next((row.get(key) for key in ('title', 'name', 'number', 'code') if row.get(key)), None)
            if label is None:
                label = str(row.get('id', ''))
            if row.get('employee_name'):
                label = row['employee_name'] + ' · ' + str(label)
            result.append(dict(id=row['id'], label=label, company_id=row.get('company_id'), site_id=row.get('site_id')))
        return jsonify(result)

    @app.get('/api/hrm/dashboard')
    @authenticated
    def hrm_dashboard():
        actor = ctx(db())
        section = request.args.get('section', 'holding')
        if section == 'health':
            section = 'health_environment'
        if section not in ('holding', *SECTIONS):
            raise ValueError('داشبورد معتبر نیست')
        allowed = SECTIONS[section] if section != 'holding' else tuple({key for group in SECTIONS.values() for key in group})
        modules = [module for module in allowed if _flags(actor, module)['read']]
        if not modules:
            return jsonify(section=section, metrics=[], contract_alerts=[], hse_alerts=[], medical_alerts=[],
                           companies=[], training={}, data_quality=['برای این حوزه هنوز دسترسی مشاهده تعیین نشده است.'])
        catalog = schemas()
        metrics, quality, contract_alerts, hse_alerts, medical_alerts, assignment_reviews = [], [], [], [], [], []
        company_stats={company['id']:dict(company_id=company['id'],name=company['name'],
                       employees=None,contracts=None,outstanding_rials=None,actual_person_hours=None,
                       ppe_missing=None,medical_due=None) for company in actor['companies']}
        if request.args.get('company_id'):
            selected=positive_id(request.args['company_id'])
            company_stats={key:value for key,value in company_stats.items() if key==selected}
        site_stats={}
        for site in db().execute('SELECT id,name,company_id FROM hrm_sites ORDER BY company_id,id'):
            if site['company_id'] not in company_stats:continue
            if request.args.get('site_id') and site['id']!=positive_id(request.args['site_id']):continue
            if not any(scope['company_id']==site['company_id'] and
                       (not scope.get('site_id') or scope['site_id']==site['id']) for scope in actor['scopes']):continue
            site_stats[site['id']]=dict(site_id=site['id'],company_id=site['company_id'],name=site['name'],
                company_name=company_stats[site['company_id']]['name'],employees=None,contracts=None,
                outstanding_rials=None,actual_person_hours=None,ppe_missing=None,medical_due=None)
        def metric(key, label, value, unit=None):
            metrics.append(dict(key=key, label=label, value=value, unit=unit))
        if 'personnel' in modules:
            staff = _staff(db(), 'personnel')
            metric('employees', 'کارکنان در محدوده مجاز', len(staff), 'نفر')
            for summary in company_stats.values():summary['employees']=sum(p['company_id']==summary['company_id'] for p in staff)
            for summary in site_stats.values():summary['employees']=sum(p.get('site_id')==summary['site_id'] for p in staff)
            incomplete = sum(not row.get('site_id') or not row.get('job') for row in staff)
            if incomplete:
                quality.append(f'سایت یا شغل {incomplete} نفر تکمیل نشده است.')
        names = {'contracts': 'قراردادها', 'attendance': 'سوابق حضور و غیاب',
                 'performance_scores': 'امتیازهای عملکرد', 'discipline': 'سوابق تشویق و انضباط',
                 'loans': 'وام‌ها و تسهیلات', 'welfare_uses': 'سوابق خدمات رفاهی',
                 'reservations': 'سوابق اقامت', 'ppe_issues': 'سوابق تحویل تجهیزات',
                 'hse_incidents': 'حوادث و اقدامات پیگیری', 'health_records': 'سوابق معاینات طب کار'}
        for resource, label in names.items():
            schema = catalog.get(resource)
            if not schema or schema['module'] not in modules or not schema['can_read']:
                continue
            rows = filter_rows(db(), rows_for(resource), schema['module'])
            rows=[row for row in rows if not row.get('is_deleted') and not row.get('deleted')]
            metric(resource, label, len(rows), 'رکورد')
            if resource == 'contracts':
                for summary in company_stats.values():summary['contracts']=sum(r['company_id']==summary['company_id'] for r in rows)
                for summary in site_stats.values():summary['contracts']=sum(r.get('site_id')==summary['site_id'] for r in rows)
                today = date.fromisoformat(today_iso())
                for row in rows:
                    if not row.get('end_date') or row.get('status') not in ('active', 'draft'):
                        continue
                    remaining = (date.fromisoformat(row['end_date']) - today).days
                    # This is a user-adjustable display window, not a legal rule.
                    days = int(request.args.get('expiry_days', 30))
                    if not 0 <= days <= 730:
                        raise ValueError('بازه هشدار قرارداد باید صفر تا ۷۳۰ روز باشد')
                    if remaining <= days:
                        contract_alerts.append(dict(id=row['id'], number=row['number'],
                            employee_id=row['employee_id'], employee_name=row.get('employee_name', row.get('name', '')),
                            end_date=row['end_date'], days_remaining=remaining))
                if not rows:
                    quality.append('در این محدوده قرارداد ثبت نشده است؛ نبود داده به معنی نبود قرارداد یا ریسک نیست.')
            if resource == 'loans':
                metric('outstanding_loans', 'مانده ثبت‌شده وام‌ها', sum(r.get('outstanding_rials', 0) for r in rows), 'ریال')
                for summary in company_stats.values():summary['outstanding_rials']=sum(r.get('outstanding_rials',0) for r in rows if r['company_id']==summary['company_id'])
                for summary in site_stats.values():summary['outstanding_rials']=sum(r.get('outstanding_rials',0) for r in rows if r.get('site_id')==summary['site_id'])
        training = {}
        if 'training' in modules:
            training_detail=object_for('training_report')
            training=training_detail['metrics']
            for summary in company_stats.values():
                summary['actual_person_hours']=sum(row['hours'] for row in training_detail.get('actual_attendance',[]) if row['company_id']==summary['company_id'])
            for summary in site_stats.values():summary['actual_person_hours']=sum(row['hours'] for row in training_detail.get('actual_attendance',[]) if row.get('site_id')==summary['site_id'])
            metric('training_hours', 'نفرساعت حضور واقعی ثبت‌شده', training['actual_person_hours'], 'نفرساعت')
            metric('training_completed', 'دوره‌های برگزارشده در محدوده', training['completed_courses'], 'دوره')
            metric('training_per_capita', 'سرانه آموزش کارکنان فعلی محدوده', training['hours_per_employee'], 'ساعت')
            metric('training_competency_gaps','الزامات دارای گپ شایستگی',training['competency_gaps'] if training.get('requirements') else None,'مورد')
            metric('training_unknown_requirements','الزامات بدون ارزیابی معتبر',training.get('unknown_requirements') if training.get('requirements') else None,'مورد')
            metric('training_renewal_due','آموزش‌های دوره‌ای سررسیدشده',training.get('renewal_due') if training.get('requirements') else None,'مورد')
            if training['achievement_percent'] is None:
                quality.append('برای محاسبه تحقق آموزش، برنامه مستند در این محدوده ثبت نشده است.')
            if training_detail.get('data_status')=='no_configured_requirements':
                quality.append('الزامات آموزش شغل، محل یا بدو استخدام در این محدوده هنوز تکمیل نشده‌اند.')
        if 'hse' in modules:
            safety=object_for('hse_report')
            safety_labels={'ppe_missing':'الزامات PPE دارای کسری','ppe_overdue':'PPE نیازمند تعویض',
                           'training_due':'آموزش ایمنی تکمیل‌نشده یا سررسیدشده','incidents_open':'حوادث با پیگیری باز',
                           'qualifications_expired':'صلاحیت‌های ایمنی منقضی‌شده'}
            values=safety.get('metrics',{})
            for key,label in safety_labels.items():
                value=values.get(key)
                if key in ('ppe_missing','ppe_overdue') and not values.get('ppe_requirements'):value=None
                if key=='training_due' and not values.get('training_requirements'):value=None
                metric('safety_'+key,label,value,'مورد')
            metric('safety_assignment_reviews','فعالیت موقت نیازمند بازنگری ایمنی',values.get('temporary_assignment_reviews',0),'مورد')
            hse_alerts=[{key:row.get(key) for key in ('employee_id','employee_name','company_id','site_id','ppe_name',
                        'missing_quantity','next_due_date','overdue','review_required')} for row in safety.get('ppe',[])
                        if row.get('missing_quantity') or row.get('overdue') or row.get('review_required')]
            assignment_reviews.extend(safety.get('assignment_reviews',[]))
            for summary in company_stats.values():
                owned=[row for row in safety.get('ppe',[]) if row['company_id']==summary['company_id']]
                summary['ppe_missing']=sum(bool(row.get('missing_quantity')) for row in owned) if owned else None
            for summary in site_stats.values():
                owned=[row for row in safety.get('ppe',[]) if row.get('site_id')==summary['site_id']]
                summary['ppe_missing']=sum(bool(row.get('missing_quantity')) for row in owned) if owned else None
            if safety.get('data_status')=='no_configured_requirements':
                quality.append('ماتریس PPE و الزامات آموزش ایمنی این محدوده هنوز تنظیم نشده‌اند.')
            if safety.get('assignment_reviews'):
                quality.append('فعالیت موقت ثبت‌شده نیازمند بازنگری الزامات ایمنی متناسب با محل فعالیت واقعی است.')
        medical=None
        if 'health_environment' in modules:
            combined=object_for('health_environment_report')
            values=combined.get('metrics',{})
            for key,label in {'training_due':'آموزش بهداشت و محیط‌زیست تکمیل‌نشده یا سررسیدشده',
                              'exposures':'سوابق مواجهه شغلی','environment_participations':'مشارکت‌های محیط‌زیستی'}.items():
                value=values.get(key)
                if key=='training_due' and not values.get('training_requirements'):value=None
                metric('health_environment_'+key,label,value,'مورد')
            medical=combined.get('medical')
            assignment_reviews.extend(combined.get('assignment_reviews',[]))
        if 'health' in modules and (medical is None or not medical.get('authorized')):
            medical=object_for('health_report').get('medical')
        if medical and medical.get('authorized'):
            values=medical.get('metrics',{})
            configured=bool(values.get('required'))
            metric('medical_missing','معاینات الزامی ثبت‌نشده',values.get('missing') if configured else None,'مورد')
            metric('medical_due','معاینات سررسیدشده',values.get('due') if configured else None,'مورد')
            medical_alerts=[{key:row.get(key) for key in ('employee_id','employee_name','company_id','site_id',
                            'exam_type','next_review_date','status')} for row in [*medical.get('missing',[]),*medical.get('due',[])]]
            for summary in company_stats.values():
                owned=[row for row in medical.get('requirements',[]) if row['company_id']==summary['company_id']]
                summary['medical_due']=sum(row['status']=='due' for row in owned) if owned else None
            for summary in site_stats.values():
                owned=[row for row in medical.get('requirements',[]) if row.get('site_id')==summary['site_id']]
                summary['medical_due']=sum(row['status']=='due' for row in owned) if owned else None
            if not configured:quality.append('ضوابط معاینه شغلی این محدوده تنظیم نشده است؛ وضعیت پوشش معاینات نامشخص است.')
        if not metrics:
            quality.append('شاخص قابل محاسبه‌ای برای مجوزهای این حوزه وجود ندارد.')
        return jsonify(section=section, modules=modules, metrics=metrics, contract_alerts=contract_alerts,
                       hse_alerts=hse_alerts, medical_alerts=medical_alerts, assignment_reviews=assignment_reviews,
                       companies=list(company_stats.values()), sites=list(site_stats.values()), training=training,
                       data_quality=quality, filters=dict(request.args))

    @app.get('/api/hrm/reports/<resource>.xlsx')
    @authenticated
    def hrm_export(resource):
        catalog = schemas()
        if resource not in catalog:
            raise PermissionError()
        schema = catalog[resource]
        module=schema['module']
        if resource=='documents':
            export_groups=[name for name in schema['permission_groups'] if _flags(ctx(db()),name)['export']]
            if not export_groups:raise PermissionError()
            module=export_groups[0]
        require(db(), module, operation='export')
        # A report is still a read of records. Export-only users use this route;
        # their domain listing's explicit export path is enabled for this call.
        protected_rows=rows_for(resource)
        # A shared course is scoped by its authorized learners, whose employer
        # may differ from the course host. Its listing has already applied all
        # company/site/date filters before exposing any header.
        rows=protected_rows if resource=='training_courses' else filter_rows(db(),protected_rows,module)
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment
        from .commitments import holding_logo
        from openpyxl.drawing.image import Image
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'گزارش مدیریتی'
        sheet.sheet_view.rightToLeft = True
        columns = list(schema.get('columns', []))
        if 'employee_id' in columns:
            at=columns.index('employee_id');columns[at:at+1]=['employee_name','employee_code']
        labels = {field['key']: field['label'] for field in schema.get('fields', [])}
        labels.update(employee_name='نام و نام خانوادگی',employee_code='کد پرسنلی',
                      company_id='شرکت',site_id='سایت',department='واحد سازمانی',
                      renewal_count='تعداد تمدید',revision='بازنگری',created='زمان ثبت')
        title = schema['title']
        sheet.append(['نیکان — ' + title])
        sheet.append(['تاریخ گزارش', jalali_display(today_iso())])
        sheet.append(['مبنای گزارش', 'اطلاعات ثبت‌شده در محدوده مجوز و فیلترهای انتخابی'])
        sheet.append([labels.get(key, key) for key in columns])
        date_keys = {field['key'] for field in schema.get('fields', []) if field.get('type') == 'date'}
        people={row['id']:dict(row) for row in db().execute('SELECT id,name,code FROM employees')}
        companies={row['id']:row['name'] for row in db().execute('SELECT id,name FROM companies')}
        sites={row['id']:row['name'] for row in db().execute('SELECT id,name FROM hrm_sites')}
        for row in rows:
            values = []
            for key in columns:
                value = row.get(key, '')
                if key in ('employee_name','employee_code') and not value:
                    person=people.get(row.get('employee_id'),{})
                    value=person.get('name' if key=='employee_name' else 'code','')
                if key=='company_id':value=companies.get(value,value)
                if key=='site_id':value=sites.get(value,value)
                if key in date_keys and value:
                    value = jalali_display(value)
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                if not isinstance(value, (str, int, float, bool)) and value is not None:
                    value = str(value)
                if type(value) is int and abs(value)>999_999_999_999_999:
                    value=str(value)
                values.append(value)
            sheet.append(values)
        for cell in sheet[4]:
            cell.font = Font(bold=True, color='FFFFFF')
            cell.fill = PatternFill('solid', fgColor='173B55')
        for row in sheet:
            for cell in row:
                cell.alignment = Alignment(horizontal='right', vertical='top', wrap_text=True)
        for index in range(1, max(1, len(columns)) + 1):
            sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = 25
        sheet.freeze_panes = 'A5'
        sheet.auto_filter.ref = f'A4:{openpyxl.utils.get_column_letter(max(1,len(columns)))}{max(4,sheet.max_row)}'
        sheet.print_options.horizontalCentered = True
        sheet.page_setup.orientation, sheet.page_setup.paperSize = 'landscape', sheet.PAPERSIZE_A4
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
        logo = holding_logo(db())
        if logo:
            picture = Image(io.BytesIO(logo['content']))
            picture.width, picture.height = 110, 45
            sheet.add_image(picture, 'J1')
        result = io.BytesIO()
        workbook.save(result)
        audit('hrm_report_export', request.args.get('company_id'), resource + ':' + str(len(rows)))
        db().commit()
        return Response(result.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': "attachment; filename=Nikan-HRM-" + resource + ".xlsx; filename*=UTF-8''" + quote(title + '.xlsx')})
