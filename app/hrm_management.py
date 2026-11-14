"""Read-only, scoped follow-up summaries from the existing management records.

This is a projection, not a task/request system. Counts describe records or
requirement cells, never an invented count of distinct employees. The six
examples per category are bounded; counts cover the complete authorized set.
"""
from datetime import date, timedelta

from flask import jsonify, request

from . import hrm_access as access
from .hrm_common import date_value, today_iso
from .hrm_core import SECTIONS, _staff
from .hrm_integration import _context_filters

SAMPLE_LIMIT = 6
HORIZON_DAYS = 30
ACTIVE_STAFF = frozenset({'active', 'probation', 'leave'})


def _target(page, resource, row=None):
    result = dict(page=page, resource=resource)
    if row:
        for key in ('id', 'employee_id', 'company_id', 'site_id'):
            if row.get(key) is not None:
                result[key] = row[key]
    return result


def _item(row, page, resource, title, detail='', due_date='', severity='warning'):
    return dict(id=row.get('id'), employee_id=row.get('employee_id'),
                employee_name=row.get('employee_name', row.get('name', '')),
                company_id=row.get('company_id'), site_id=row.get('site_id'),
                title=title, detail=detail, due_date=due_date, severity=severity,
                target=_target(page, resource, row))


def _category(key, module, title, description, items, page, resource, *, configured=True):
    items.sort(key=lambda row: (row['due_date'] or '9999-12-31',
                               row.get('employee_id') or 0, row.get('id') or 0,
                               row['title']))
    return dict(key=key, module=module, title=title, description=description,
                count=len(items), status='attention' if items else ('clear' if configured else 'not_configured'),
                items=items[:SAMPLE_LIMIT], sample_limit=SAMPLE_LIMIT,
                truncated=len(items) > SAMPLE_LIMIT, target=_target(page, resource))


def _record_rows(conn, table, module, columns, filters, extra):
    """Minimal record projection with the owning module's historical scope.

    Identifiers/conditions are constants at the call sites. Unlike some legacy
    interactive lists, this query has no 5,000-row cap hidden beneath its count.
    No current job/site is joined onto an old employer's authorized record.
    """
    predicate, params = access.record_scope_sql(conn, module, 'r', department_column='r.department')
    sql = ('SELECT r.id,r.employee_id,r.company_id,r.site_id,r.department,' + columns +
           ',e.name employee_name FROM ' + table + ' r JOIN employees e ON e.id=r.employee_id '
           'WHERE (' + predicate + ') AND (' + extra + ')')
    for key in ('company_id', 'site_id', 'department', 'employee_id'):
        if key in filters:
            sql += ' AND r.' + key + '=?';params.append(filters[key])
    return [dict(row) for row in conn.execute(sql, params)]


def management_overview(conn):
    actor = access.ctx(conn)
    readable = {module for module, flags in actor['permissions'].items() if flags['read']}
    if not readable:
        raise PermissionError()
    filters = _context_filters(conn, actor)
    today = today_iso()
    if request.args.get('as_of') and date_value(request.args['as_of'], required=True) != today:
        raise ValueError('مرکز پیگیری بر مبنای وضعیت امروز است؛ تاریخ دیگری برای آن انتخاب نکنید')
    due_before = (date.fromisoformat(today) + timedelta(days=HORIZON_DAYS)).isoformat()
    section = request.args.get('section', '')
    if section and section not in ('holding', *SECTIONS):
        raise ValueError('بخش مرکز پیگیری معتبر نیست')
    if section and section != 'holding':
        readable &= set(SECTIONS[section])
        if not readable:
            raise PermissionError()
    categories = []
    metrics = {}

    def add(*args, **kwargs):
        value = _category(*args, **kwargs);categories.append(value)
        metrics[value['key']] = value['count']

    if 'personnel' in readable:
        people = [row for row in _staff(conn, 'personnel', filters=filters) if (row.get('status') or 'active') in ACTIVE_STAFF]
        metrics['current_personnel'] = len(people)
        items = []
        for row in people:
            missing = [label for key, label in (('site_id', 'سایت محل خدمت'), ('job', 'سمت جاری')) if not row.get(key)]
            if missing:
                item = dict(row, employee_id=row['id'])
                items.append(_item(item, 'hrm_personnel', 'people', row['name'],
                                   'نیازمند تکمیل: ' + '، '.join(missing), severity='info'))
        add('personnel_incomplete', 'personnel', 'تکمیل انتساب کارکنان',
            'تعداد کارکنان جاری فاقد سایت یا سمت؛ هر فرد یک بار شمارش می‌شود.',
            items, 'hrm_personnel', 'people')

    if 'contracts' in readable:
        rows = _record_rows(conn, 'hrm_contracts', 'contracts',
                            'r.number,r.start_date,r.end_date', filters, "r.status='active'")
        expired, upcoming = [], []
        for row in rows:
            if row['start_date'] > today or not row['end_date']:
                continue
            item = _item(row, 'hrm_contracts', 'contracts', 'قرارداد ' + row['number'],
                         'قرارداد دارای وضعیت جاری؛ وضعیت تمدید یا خاتمه را بررسی کنید.',
                         row['end_date'], 'danger' if row['end_date'] < today else 'warning')
            if row['end_date'] < today:
                expired.append(item)
            elif row['end_date'] <= due_before:
                upcoming.append(item)
        add('contracts_expired', 'contracts', 'قراردادهای جاری با پایان گذشته',
            'قراردادهای دارای وضعیت جاری و پایان پیش از امروز؛ پایان‌یافته، فسخ‌شده و تمدیدشده کنار گذاشته می‌شوند.',
            expired, 'hrm_contracts', 'contracts')
        add('contracts_due', 'contracts', 'سررسید قرارداد تا ۳۰ روز',
            'قراردادهای جاری که از امروز تا ۳۰ روز آینده پایان می‌یابند؛ هر قرارداد یک مورد است.',
            upcoming, 'hrm_contracts', 'contracts')

    if 'training' in readable:
        from .hrm_matrix import matrix_report
        matrix_args = {key: value for key, value in filters.items()
                       if key in ('company_id', 'site_id', 'department', 'employee_id')}
        matrix_args['as_of'] = today
        try:
            matrix = matrix_report(conn, matrix_args)
        except ValueError as exc:
            # The shared matrix deliberately refuses an excessively broad
            # calculation. Never replace that uncomputed result with green zero.
            if not str(exc).startswith('ماتریس بسیار بزرگ است؛'):
                raise
            for key, title in (('training_gaps', 'شکاف شایستگی'),
                               ('training_unassessed', 'شایستگی نیازمند ارزیابی'),
                               ('training_evidence_reviews', 'بازبینی شواهد ارزیابی'),
                               ('training_renewal_due', 'تجدید آموزش سررسیدشده')):
                value = _category(key, 'training', title, str(exc), [], 'hrm_training', 'skill_matrix')
                value.update(count=None, status='scope_too_large');categories.append(value);metrics[key] = None
        else:
            active = {row['id'] for row in matrix['employees'] if (row.get('status') or 'active') in ACTIVE_STAFF}
            rows = [row for row in matrix['rows'] if row['employee_id'] in active]
            items = {key: [] for key in ('training_gaps', 'training_unassessed', 'training_evidence_reviews', 'training_renewal_due')}
            for row in rows:
                required = any((row.get(key) or 0) > 0 for key in ('required_level', 'job_required_level'))
                if not required:
                    continue
                title = row['competency_code'] + ' · ' + row['competency_title']
                item = _item(row, 'hrm_training', 'skill_matrix', title,
                             row['status_label'], row.get('renewal_due_date') or '',
                             'danger' if row['importance'] == 3 else 'warning')
                if any((row.get(key) or 0) > 0 for key in ('gap', 'job_gap')):
                    items['training_gaps'].append(item)
                if row['current_level'] is None:
                    items['training_unassessed'].append(item)
                flags = row['data_quality_flags']
                if row['status'] == 'review_required' or (row['current_level'] is not None and any(
                    flag in flags for flag in ('assessor_not_recorded', 'assessment_method_not_recorded',
                                                'assessment_evidence_not_recorded'))):
                    items['training_evidence_reviews'].append(item)
                if row['renewal_due'] is True:
                    items['training_renewal_due'].append(item)
            configured = any((row.get('required_level') or 0) > 0 or (row.get('job_required_level') or 0) > 0 for row in rows)
            for key, title in (('training_gaps', 'شکاف شایستگی'),
                               ('training_unassessed', 'شایستگی نیازمند ارزیابی'),
                               ('training_evidence_reviews', 'بازبینی شواهد ارزیابی'),
                               ('training_renewal_due', 'تجدید آموزش سررسیدشده')):
                add(key, 'training', title,
                    'تعداد زوج فرد و شایستگی در ماتریس مشترک؛ صفر ارزیابی‌شده با وضعیت نامشخص متفاوت است.',
                    items[key], 'hrm_training', 'skill_matrix', configured=configured)

    if 'hse' in readable:
        rows = _record_rows(conn, 'hrm_ppe_issues', 'hse', 'r.issue_date,r.next_due_date', filters,
                            "r.is_deleted=0 AND r.status='delivered'")
        items = [_item(row, 'hrm_hse', 'ppe_issues', 'پیگیری موعد تعویض حفاظت فردی',
                       'سابقه تحویل هنوز جاری است؛ تحویل جایگزین، عودت یا خروج از استفاده را بررسی کنید.',
                       row['next_due_date'], 'danger' if row['next_due_date'] < today else 'warning')
                 for row in rows if row['issue_date'] <= today and row['next_due_date'] and row['next_due_date'] <= due_before]
        add('ppe_follow_up', 'hse', 'پیگیری موعد تحویل‌های حفاظت فردی',
            'سوابق تحویلِ عودت‌نشده با موعد گذشته یا تا ۳۰ روز؛ این شمارش به معنی کمبود قطعی تجهیز فرد نیست.',
            items, 'hrm_hse', 'ppe_issues')

    if 'health' in readable:
        from .hrm_hse import _medical_status
        # No clinical reader or clinical count is invoked for environment-only,
        # safety-only, personnel-only or financial-only users.
        people = [row for row in _staff(conn, 'health', filters=filters) if (row.get('status') or 'active') in ACTIVE_STAFF]
        report = _medical_status(conn, people)
        items = []
        for row in report['requirements']:
            if row['status'] == 'missing' or (row['next_review_date'] and row['next_review_date'] <= due_before):
                items.append(_item(row, 'hrm_health_environment', 'health_records', 'پیگیری معاینه شغلی',
                                   'سابقه لازم ثبت نشده است.' if row['status'] == 'missing' else 'موعد بازبینی معاینه ثبت‌شده',
                                   row['next_review_date'], 'danger' if row['status'] == 'due' else 'warning'))
        add('medical_follow_up', 'health', 'پیگیری معاینات شغلی',
            'تعداد الزامات معاینه فاقد سابقه یا دارای موعد گذشته/تا ۳۰ روز؛ اطلاعات بالینی در این نما برگردانده نمی‌شود.',
            items, 'hrm_health_environment', 'health_records', configured=bool(report['requirements']))

    if 'performance' in readable and not filters.get('employee_id'):
        from .hrm_workforce import _list_rows
        periods = _list_rows(conn, 'evaluation_periods', filters)
        items = [_item(row, 'hrm_performance', 'evaluation_periods', row['title'],
                       'دوره باز است؛ تکمیل امتیازها و تصمیم بستن دوره را بررسی کنید.', row['end_date'],
                       'danger' if row['end_date'] < today else 'warning')
                 for row in periods if row['status'] == 'open' and row['start_date'] <= today and row['end_date'] <= due_before]
        add('performance_periods_due', 'performance', 'پیگیری دوره‌های باز عملکرد',
            'دوره‌های باز با پایان گذشته یا تا ۳۰ روز؛ باز بودن دوره به معنی کامل بودن نمره‌ها نیست.',
            items, 'hrm_performance', 'evaluation_periods', configured=bool(periods))

    return dict(as_of=today, horizon_days=HORIZON_DAYS, due_before=due_before,
                population_basis='current_assignment_for_staff_and_training;authorized_record_owner_for_contracts_and_ppe',
                date_filters_applied=False, section=section or None, filters=filters,
                categories=categories, metrics=metrics,
                summary_basis='authorized_records_and_requirement_cells;categories_can_overlap;not_unique_people')


def register(app, db, authenticated, audit, error):
    @app.get('/api/hrm/management-overview')
    @authenticated
    def hrm_management_overview():
        return jsonify(management_overview(db()))
