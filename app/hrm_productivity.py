"""Versioned productivity accords over the canonical workforce evaluations.

The module calculates management records, never payroll transfers or employee
requests. Scores remain in hrm_performance_*; finalized accords retain immutable
source evidence and exact whole-rial amounts independent of later transfers.
"""
from contextlib import nullcontext
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from fractions import Fraction
import hashlib
import json

from flask import jsonify, request

from . import hrm_access as access
from .hrm_common import (HrmConflict, begin_write, check_version, integer_value,
                         money_value, text_value)
from .validation import json_object, positive_id
from .hrm_workforce import _date_bounds

from .hrm_common import exact_rial_fields

MODULE = 'productivity'
TABLES = ('hrm_accord_policies', 'hrm_accord_entries', 'hrm_accord_results')
RECOVERY_ADDITIONS = dict.fromkeys(TABLES, 0)
SCHEMA = '''
CREATE TABLE IF NOT EXISTS hrm_accord_policies(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),period_id INTEGER NOT NULL UNIQUE REFERENCES hrm_evaluation_periods(id),
 title TEXT NOT NULL,method TEXT NOT NULL CHECK(method IN ('individual_base','pool_budget')),
 budget_rials INTEGER CHECK(budget_rials IS NULL OR budget_rials>=0),
 minimum_score TEXT NOT NULL DEFAULT '0',note TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','finalized')),
 period_snapshot TEXT NOT NULL DEFAULT '{}',approved_by INTEGER REFERENCES users(id),approved_at TEXT NOT NULL DEFAULT '',
 is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),deleted_by INTEGER REFERENCES users(id),deleted_at TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS hrm_accord_entries(
 id INTEGER PRIMARY KEY,policy_id INTEGER NOT NULL REFERENCES hrm_accord_policies(id),
 employee_id INTEGER NOT NULL REFERENCES employees(id),company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',
 base_rials INTEGER CHECK(base_rials IS NULL OR base_rials>=0),
 scheduled_minutes INTEGER NOT NULL CHECK(scheduled_minutes>0),
 actual_minutes INTEGER NOT NULL CHECK(actual_minutes>=0 AND actual_minutes<=scheduled_minutes),
 note TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),
 deleted_by INTEGER REFERENCES users(id),deleted_at TEXT NOT NULL DEFAULT '',version INTEGER NOT NULL DEFAULT 1,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(policy_id,employee_id));
CREATE INDEX IF NOT EXISTS hrm_accord_entry_scope ON hrm_accord_entries(company_id,site_id,department);
CREATE TABLE IF NOT EXISTS hrm_accord_results(
 id INTEGER PRIMARY KEY,entry_id INTEGER NOT NULL UNIQUE REFERENCES hrm_accord_entries(id),
 policy_id INTEGER NOT NULL REFERENCES hrm_accord_policies(id),period_id INTEGER NOT NULL REFERENCES hrm_evaluation_periods(id),
 employee_id INTEGER NOT NULL REFERENCES employees(id),company_id INTEGER NOT NULL REFERENCES companies(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',
 employee_name TEXT NOT NULL,employee_code TEXT NOT NULL,company_name TEXT NOT NULL,site_name TEXT NOT NULL,
 method TEXT NOT NULL,score_percent TEXT NOT NULL,participation_percent TEXT NOT NULL,
 base_rials INTEGER,accord_rials INTEGER NOT NULL CHECK(accord_rials>=0),
 snapshot_json TEXT NOT NULL,approved_by INTEGER NOT NULL REFERENCES users(id),
 calculated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,UNIQUE(period_id,employee_id));
'''


def field(key, label, type='text', required=False, **extra):
    return dict(key=key, label=label, type=type, required=required, **extra)


CATALOG = {
    'accord_policies': dict(title='ضوابط آکورد بهره‌وری', module=MODULE, deletable=True, restorable=True, fields=[
        field('company_id', 'شرکت', 'company', True),
        field('site_id', 'سایت (خالی یعنی کل شرکت)', 'resource', resource='sites'),
        field('period_id', 'دوره ارزیابی عملکرد', 'resource', True, resource='evaluation_periods'),
        field('title', 'عنوان آکورد', required=True),
        field('method', 'روش محاسبه', 'select', True, choices=[['individual_base', 'مبلغ مبنای هر فرد'], ['pool_budget', 'توزیع بودجه مشترک']]),
        field('budget_rials', 'بودجه مشترک (ریال؛ فقط روش بودجه)', 'number', minimum=0),
        field('minimum_score', 'حداقل امتیاز برای تعلق آکورد (از ۱۰۰؛ خالی یعنی بدون حداقل)', 'number', minimum=0, maximum=100),
        field('note', 'توضیحات ضابطه مصوب', 'textarea')],
        columns=['company_id', 'site_id', 'period_title', 'title', 'method', 'budget_rials', 'minimum_score', 'status']),
    'accord_entries': dict(title='مبنای آکورد کارکنان', module=MODULE, deletable=True, restorable=True, fields=[
        field('policy_id', 'ضابطه آکورد', 'resource', True, resource='accord_policies'),
        field('employee_id', 'پرسنل', 'employee', True),
        field('base_rials', 'مبلغ مبنای فرد (ریال؛ فقط روش مبلغ فردی)', 'number', minimum=0),
        field('scheduled_minutes', 'دقایق موظفی در دوره', 'number', True, minimum=1),
        field('actual_minutes', 'دقایق مشارکت تأییدشده در دوره', 'number', True, minimum=0),
        field('note', 'مستند و توضیح مشارکت', 'textarea')],
        columns=['employee_id', 'site_id', 'department', 'policy_title', 'base_rials', 'scheduled_minutes', 'actual_minutes', 'policy_status']),
    'accord_results': dict(title='آکورد نهایی و مستند محاسبه', module=MODULE, read_only=True, deletable=False, restorable=False, fields=[],
        columns=['employee_name', 'employee_code', 'company_name', 'site_name', 'department', 'period_title', 'policy_title', 'score_percent', 'participation_percent', 'base_rials', 'accord_rials', 'calculated_at']),
}

# These are editable starting suggestions, never automatically applied policy.
STARTER_CRITERIA = [('تحقق برنامه و کمیت کار', 25), ('کیفیت و کاهش دوباره‌کاری', 25),
                    ('تحویل به‌موقع و رعایت زمان‌بندی', 10), ('حضور مؤثر و مشارکت تأییدشده', 10),
                    ('انضباط و اجرای وظایف محوله', 10), ('رعایت الزامات ایمنی کار', 10),
                    ('همکاری و کار تیمی', 5), ('پیشنهاد و بهبود روش انجام کار', 5)]


def _fraction(value, label, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(label + ' باید عدد معتبر باشد')
    try:
        source = str(value).strip()
        if len(source) > 128:
            raise ValueError()
        decimal = Decimal(source)
        if not decimal.is_finite() or decimal < minimum or (maximum is not None and decimal > maximum):
            raise ValueError()
        return Fraction(decimal)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError(label + ' خارج از محدوده یا نامعتبر است') from None


def _display(value):
    with localcontext() as context:
        context.prec = 48
        number = Decimal(value.numerator) / Decimal(value.denominator)
        return float(number.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP))


def _exact(value):
    return str(value.numerator) + '/' + str(value.denominator)


def _round_rials(value):
    # Exact nonnegative rational HALF_UP, independent of displayed percentage.
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _full_scope(conn, row, module, operation=None):
    access.company_scope(conn, row['company_id'], module, operation=operation, site_id=row['site_id'])


def _has_full_scope(conn, row, module, operation=None):
    try:
        _full_scope(conn, row, module, operation)
        return True
    except PermissionError:
        return False


def _visible_policy(conn, row, operation=None):
    actor = access.require(conn, MODULE, operation=operation)
    if not any(s['company_id'] == row['company_id'] and
               (s['site_id'] is None or row['site_id'] is None or s['site_id'] == row['site_id']) for s in actor['scopes']):
        raise PermissionError()
    return row


def _policy(conn, pid, operation=None, allow_deleted=False):
    row = conn.execute('SELECT * FROM hrm_accord_policies WHERE id=?', (positive_id(pid),)).fetchone()
    if row is None or (row['is_deleted'] and not allow_deleted):
        raise ValueError('ضابطه آکورد یافت نشد')
    _visible_policy(conn, row, operation)
    return row


def _period(conn, row):
    result = conn.execute('SELECT * FROM hrm_evaluation_periods WHERE id=? AND is_deleted=0', (row['period_id'],)).fetchone()
    if result is None or result['company_id'] != row['company_id'] or result['site_id'] != row['site_id']:
        raise ValueError('دوره ارزیابی باید متعلق به همین شرکت و سایت باشد')
    return result


def _policy_output(conn, row):
    item = dict(row)
    period = conn.execute('SELECT * FROM hrm_evaluation_periods WHERE id=?', (row['period_id'],)).fetchone()
    saved = json.loads(row['period_snapshot'])
    item['period_title'] = saved.get('title', period['title'] if period else '')
    item['period_start_date'] = saved.get('start_date', period['start_date'] if period else '')
    item['period_end_date'] = saved.get('end_date', period['end_date'] if period else '')
    item.pop('period_snapshot', None)
    item['minimum_score'] = _display(_fraction(row['minimum_score'], 'حداقل امتیاز', maximum=100))
    item['locked'] = row['status'] == 'finalized'
    if not _has_full_scope(conn, row, MODULE):
        item['budget_rials'] = None
        item['budget_redacted'] = True
        item['note'] = ''
    return exact_rial_fields(item)


def _filters(conn, args):
    actor = access.require(conn, MODULE)
    start, end = _date_bounds(args)
    result = {'_from_date': start, '_to_date': end}
    for key in ('company_id', 'site_id', 'employee_id', 'policy_id', 'period_id'):
        if args.get(key) not in (None, ''):
            result[key] = positive_id(args[key])
    if 'company_id' in result and result['company_id'] not in actor['company_ids']:
        raise PermissionError()
    if 'site_id' in result:
        site = conn.execute('SELECT company_id FROM hrm_sites WHERE id=?', (result['site_id'],)).fetchone()
        if site is None or ('company_id' in result and result['company_id'] != site['company_id']):
            raise ValueError('سایت متعلق به شرکت انتخاب‌شده نیست')
        if not any(s['company_id'] == site['company_id'] and s['site_id'] in (None, result['site_id']) for s in actor['scopes']):
            raise PermissionError()
    if args.get('department'):
        result['department'] = text_value(args['department'], max_length=200)
    return result


def _matches_dates(row, filters):
    return ((not filters.get('_from_date') or row.get('period_end_date', '') >= filters['_from_date']) and
            (not filters.get('_to_date') or row.get('period_start_date', '') <= filters['_to_date']))


def _matches(row, filters):
    return _matches_dates(row, filters) and all(row.get(key) == value for key, value in filters.items() if not key.startswith('_'))


def _entry_output(conn, row):
    item = dict(row)
    item['locked'] = row['policy_status'] == 'finalized'
    policy = conn.execute('SELECT period_snapshot FROM hrm_accord_policies WHERE id=?', (row['policy_id'],)).fetchone()
    saved = json.loads(policy['period_snapshot'])
    period = conn.execute('SELECT start_date,end_date FROM hrm_evaluation_periods WHERE id=?', (row['period_id'],)).fetchone()
    item['period_start_date'] = saved.get('start_date', period['start_date'])
    item['period_end_date'] = saved.get('end_date', period['end_date'])
    return exact_rial_fields(item)


def _result_output(conn, row):
    snapshot = json.loads(row['snapshot_json'])
    if not _has_full_scope(conn, snapshot['policy'], MODULE):
        snapshot['policy']['budget_rials'] = None
        snapshot['policy']['note'] = ''
        snapshot['period']['note'] = ''
        snapshot['policy']['budget_redacted'] = True
    item = dict(snapshot['result'])
    item.update({key: row[key] for key in ('id', 'entry_id', 'policy_id', 'period_id', 'employee_id',
                 'company_id', 'site_id', 'department', 'employee_name', 'employee_code', 'company_name', 'site_name',
                 'base_rials', 'accord_rials', 'approved_by', 'calculated_at')})
    item.update(status='finalized', locked=True, is_complete=True, missing_criteria=[])
    item['period_start_date'] = snapshot['period']['start_date']
    item['period_end_date'] = snapshot['period']['end_date']
    item['evidence'] = snapshot
    return exact_rial_fields(item)


def _list_rows(conn, resource, args):
    filters = _filters(conn, args)
    archived = request.path == '/api/hrm/' + resource and args.get('include_deleted') == '1'
    if resource == 'accord_policies':
        rows = []
        for row in conn.execute('SELECT * FROM hrm_accord_policies ORDER BY id DESC'):
            if row['is_deleted'] and not archived:
                continue
            try:
                _visible_policy(conn, row)
            except PermissionError:
                continue
            item = _policy_output(conn, row)
            if any(key in filters and item.get(key) != filters[key] for key in ('company_id', 'period_id')):
                continue
            if 'policy_id' in filters and item['id'] != filters['policy_id']:
                continue
            if 'site_id' in filters and item['site_id'] not in (None, filters['site_id']):
                continue
            if not _matches_dates(item, filters):
                continue
            rows.append(item)
        return rows
    scope, params = access.record_scope_sql(conn, MODULE, alias='r', department_column='r.department')
    if resource == 'accord_results':
        source = [dict(row) for row in conn.execute('SELECT r.* FROM hrm_accord_results r WHERE ' + scope + ' ORDER BY r.id DESC', params)]
        return [item for item in (_result_output(conn, row) for row in source) if _matches(item, filters)]
    sql = '''SELECT r.*,e.name employee_name,e.code employee_code,c.name company_name,s.name site_name,
                    p.title policy_title,p.period_id,p.status policy_status
             FROM hrm_accord_entries r JOIN hrm_accord_policies p ON p.id=r.policy_id
             JOIN employees e ON e.id=r.employee_id JOIN companies c ON c.id=r.company_id
             LEFT JOIN hrm_sites s ON s.id=r.site_id WHERE (''' + scope + ')'
    if not archived:
        sql += ' AND r.is_deleted=0 AND p.is_deleted=0'
    return [item for item in (_entry_output(conn, row) for row in conn.execute(sql + ' ORDER BY r.id DESC', params)) if _matches(item, filters)]


def _policy_values(conn, body, previous=None):
    fields = {f['key'] for f in CATALOG['accord_policies']['fields']}
    if set(body) - fields - {'version'}:
        raise ValueError('فیلد ناشناخته در ضابطه آکورد')
    values = {key: previous[key] for key in fields} if previous else {}
    values.update({key: body[key] for key in fields if key in body})
    values['company_id'] = positive_id(values.get('company_id'))
    values['site_id'] = None if values.get('site_id') in (None, '') else positive_id(values['site_id'])
    values['period_id'] = positive_id(values.get('period_id'))
    values['title'] = text_value(values.get('title'), required=True, max_length=300)
    values['note'] = text_value(values.get('note', ''), max_length=4000)
    if values.get('method') not in ('individual_base', 'pool_budget'):
        raise ValueError('روش آکورد معتبر نیست')
    raw_budget = values.get('budget_rials')
    values['budget_rials'] = None if raw_budget in (None, '') else money_value(raw_budget, label='بودجه مشترک')
    if values['method'] == 'pool_budget' and values['budget_rials'] is None:
        raise ValueError('بودجه مشترک را صریح وارد کنید')
    if values['method'] == 'individual_base' and values['budget_rials'] is not None:
        raise ValueError('روش مبلغ فردی بودجه مشترک ندارد')
    raw_minimum = values.get('minimum_score')
    minimum = _fraction(0 if raw_minimum in (None, '') else raw_minimum, 'حداقل امتیاز', maximum=100)
    values['minimum_score'] = str(Decimal(str(0 if raw_minimum in (None, '') else raw_minimum)))
    operation = 'edit' if previous else 'create'
    _full_scope(conn, values, MODULE, operation)
    _full_scope(conn, values, 'performance', 'read')
    _period(conn, values)
    if previous:
        if previous['is_deleted'] or previous['status'] == 'finalized':
            raise HrmConflict('ضابطه بایگانی‌شده یا نهایی قابل ویرایش نیست')
        if any(values[key] != previous[key] for key in ('company_id', 'site_id', 'period_id')):
            raise ValueError('شرکت، سایت و دوره ضابطه قابل جایگزینی نیست')
        if values['method'] != previous['method'] and conn.execute('SELECT 1 FROM hrm_accord_entries WHERE policy_id=? LIMIT 1', (previous['id'],)).fetchone():
            raise HrmConflict('روش ضابطه دارای مبنای ثبت‌شده قابل تغییر نیست')
    return values


def _entry_values(conn, body, previous=None):
    fields = {f['key'] for f in CATALOG['accord_entries']['fields']}
    if set(body) - fields - {'version'}:
        raise ValueError('فیلد ناشناخته در مبنای آکورد')
    values = {key: previous[key] for key in fields} if previous else {}
    values.update({key: body[key] for key in fields if key in body})
    values['policy_id'] = positive_id(values.get('policy_id'))
    values['employee_id'] = positive_id(values.get('employee_id'))
    operation = 'edit' if previous else 'create'
    policy = _policy(conn, values['policy_id'], operation=operation)
    if policy['status'] == 'finalized':
        raise HrmConflict('مبنای آکورد نهایی قابل تغییر نیست')
    if previous:
        access.record_scope(conn, previous, MODULE, operation=operation)
        if previous['is_deleted']:
            raise HrmConflict('ابتدا سابقه بایگانی‌شده را بازیابی کنید')
        if any(values[key] != previous[key] for key in ('policy_id', 'employee_id')):
            raise ValueError('فرد و ضابطه سابقه قابل جایگزینی نیست')
        values.update({key: previous[key] for key in ('company_id', 'site_id', 'department')})
    else:
        person = access.employee(conn, values['employee_id'], MODULE, operation=operation)
        if person['company_id'] != policy['company_id'] or (policy['site_id'] is not None and policy['site_id'] != person['site_id']):
            raise ValueError('پرسنل متعلق به محدوده شرکت و سایت ضابطه نیست')
        values.update({key: person.get(key) for key in ('company_id', 'site_id', 'department')})
    raw_base = values.get('base_rials')
    values['base_rials'] = None if raw_base in (None, '') else money_value(raw_base, label='مبلغ مبنای فرد')
    if policy['method'] == 'individual_base' and values['base_rials'] is None:
        raise ValueError('مبلغ مبنای فرد را صریح وارد کنید')
    if policy['method'] == 'pool_budget' and values['base_rials'] is not None:
        raise ValueError('در روش بودجه مشترک مبلغ مبنای فرد وارد نمی‌شود')
    values['scheduled_minutes'] = integer_value(values.get('scheduled_minutes'), label='دقایق موظفی', minimum=1, maximum=10_000_000)
    values['actual_minutes'] = integer_value(values.get('actual_minutes'), label='دقایق مشارکت', minimum=0, maximum=values['scheduled_minutes'])
    values['note'] = text_value(values.get('note', ''), max_length=4000)
    return values


def _touch_policy(conn, pid):
    conn.execute('UPDATE hrm_accord_policies SET version=version+1 WHERE id=?', (pid,))


def _compute(conn, policy, operation=None):
    _full_scope(conn, policy, MODULE, operation)
    _full_scope(conn, policy, 'performance', 'read')
    period = _period(conn, policy)
    criteria = [dict(row) for row in conn.execute('SELECT * FROM hrm_performance_criteria WHERE period_id=? AND is_deleted=0 ORDER BY id', (period['id'],))]
    entries = [dict(row) for row in conn.execute('''SELECT r.*,e.name employee_name,e.code employee_code,c.name company_name,
                COALESCE(s.name,'') site_name FROM hrm_accord_entries r JOIN employees e ON e.id=r.employee_id
                JOIN companies c ON c.id=r.company_id LEFT JOIN hrm_sites s ON s.id=r.site_id
                WHERE r.policy_id=? AND r.is_deleted=0 ORDER BY r.id''', (policy['id'],))]
    source = dict(policy=dict(policy), period=dict(period), criteria=criteria, entries=[])
    total_weight = sum((_fraction(row['weight'], 'وزن شاخص') for row in criteria), Fraction(0))
    if criteria and total_weight <= 0:
        raise ValueError('مجموع وزن شاخص‌ها باید مثبت باشد')
    minimum = _fraction(policy['minimum_score'], 'حداقل امتیاز', maximum=100)
    rows, weights = [], []
    for entry in entries:
        access.record_scope(conn, entry, MODULE, operation=operation)
        access.record_scope(conn, entry, 'performance', operation='read')
        scores = [dict(row) for row in conn.execute('''SELECT * FROM hrm_performance_scores WHERE period_id=? AND employee_id=?
                    AND company_id=? AND site_id IS ? AND department=? AND is_deleted=0 ORDER BY criterion_id''',
                    (period['id'], entry['employee_id'], entry['company_id'], entry['site_id'], entry['department']))]
        by_criterion = {score['criterion_id']: score for score in scores}
        source['entries'].append(dict(entry=entry, scores=scores))
        missing = [criterion['title'] for criterion in criteria if criterion['id'] not in by_criterion]
        if not criteria:
            missing = ['شاخص ارزیابی تعریف نشده است']
        complete = bool(criteria) and not missing
        weighted = Fraction(0)
        evidence = []
        for criterion in criteria:
            score = by_criterion.get(criterion['id'])
            if score is None:
                evidence.append(dict(criterion_id=criterion['id'], title=criterion['title'], weight=str(criterion['weight']), max_score=str(criterion['max_score']), score=None, missing=True, contribution_percent=None))
                continue
            denominator = _fraction(criterion['max_score'], 'سقف شاخص')
            if denominator <= 0:
                raise ValueError('سقف شاخص باید مثبت باشد')
            raw = _fraction(score['score'], 'امتیاز شاخص', maximum=denominator)
            weight = _fraction(criterion['weight'], 'وزن شاخص')
            weighted += raw / denominator * weight
            evidence.append(dict(criterion_id=criterion['id'], title=criterion['title'], weight=str(criterion['weight']),
                                 max_score=str(criterion['max_score']), score=str(score['score']),
                                 score_id=score['id'], score_version=score['version'], criterion_version=criterion['version'], missing=False, contribution_percent=_display(raw / denominator * weight / total_weight * 100)))
        normalized = weighted / total_weight if complete else None
        participation = Fraction(entry['actual_minutes'], entry['scheduled_minutes'])
        eligible = complete and normalized * 100 >= minimum
        payout_weight = normalized * participation if eligible else Fraction(0)
        item = {key: entry[key] for key in ('id', 'policy_id', 'employee_id', 'company_id', 'site_id', 'department',
                                             'employee_name', 'employee_code', 'company_name', 'site_name', 'base_rials',
                                             'scheduled_minutes', 'actual_minutes', 'note', 'version')}
        item.update(period_id=period['id'], period_title=period['title'], period_start_date=period['start_date'], period_end_date=period['end_date'], policy_title=policy['title'],
                    score_percent=_display(normalized * 100) if normalized is not None else None,
                    completion_percent=_display(sum((_fraction(c['weight'], 'وزن شاخص') for c in criteria if c['id'] in by_criterion), Fraction(0)) / total_weight * 100) if criteria else 0,
                    completion_criteria_percent=_display(Fraction(len(criteria) - len(missing), len(criteria)) * 100) if criteria else 0,
                    participation_percent=_display(participation * 100), is_complete=complete,
                    missing_criteria=missing, criterion_evidence=evidence, criteria=evidence, method=policy['method'],
                    score_fraction=_exact(normalized) if normalized is not None else None,
                    participation_fraction=_exact(participation), eligible=bool(eligible), accord_rials=None,
                    status='draft' if complete else 'incomplete', locked=False)
        if policy['method'] == 'individual_base' and complete:
            item['accord_rials'] = _round_rials(Fraction(entry['base_rials']) * payout_weight)
        rows.append(item)
        weights.append(payout_weight)
    total_points = sum(weights, Fraction(0))
    complete_all = bool(rows) and all(row['is_complete'] for row in rows)
    if policy['method'] == 'pool_budget' and complete_all:
        if total_points:
            exacts = [Fraction(policy['budget_rials']) * weight / total_points for weight in weights]
            amounts = [value.numerator // value.denominator for value in exacts]
            remainder = policy['budget_rials'] - sum(amounts)
            order = sorted(range(len(rows)), key=lambda i: (-(exacts[i] - amounts[i]), rows[i]['employee_id'], rows[i]['id']))
            for index in order[:remainder]:
                amounts[index] += 1
            for row, amount in zip(rows, amounts):
                row['accord_rials'] = amount
        else:
            for row in rows:
                row['accord_rials'] = 0
    blockers = []
    if period['status'] != 'closed':
        blockers.append('دوره ارزیابی عملکرد باید بسته باشد')
    if not rows:
        blockers.append('هیچ مبنای آکورد فعالی ثبت نشده است')
    if not complete_all and rows:
        blockers.append('ارزیابی همه افراد باید برای تمام شاخص‌های فعال کامل باشد')
    token = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return dict(rows=[exact_rial_fields(row) for row in rows], period=dict(period), calculation_token=token, finalize_blockers=blockers)


def _metrics(rows, policy, *, full=True):
    incomplete = sum(not row['is_complete'] for row in rows)
    known = all(row['accord_rials'] is not None for row in rows)
    total = sum(row['accord_rials'] for row in rows) if known else None
    unallocated = None
    if full and policy['method'] == 'pool_budget' and known:
        unallocated = policy['budget_rials'] - total
    return dict(employees=len(rows), incomplete=incomplete, total_accord_rials=total,
                unallocated_budget_rials=unallocated, total_accord_rials_exact=str(total) if total is not None else None,
                unallocated_budget_rials_exact=str(unallocated) if unallocated is not None else None, eligible=sum(bool(row.get('eligible')) for row in rows))


def _report(conn, policy, args):
    if policy['status'] == 'finalized':
        rows = _list_rows(conn, 'accord_results', {**dict(args), 'policy_id': str(policy['id'])})
        all_rows = rows
        blockers, token = [], None
        full = _has_full_scope(conn, policy, MODULE)
        if full:
            all_rows = _list_rows(conn, 'accord_results', {'policy_id': str(policy['id'])})
    else:
        computed = _compute(conn, policy)
        all_rows = computed['rows']
        filters = _filters(conn, args)
        rows = [row for row in all_rows if _matches(row, filters)]
        blockers, token = computed['finalize_blockers'], computed['calculation_token']
        full = True
    output = _policy_output(conn, policy)
    can_edit = _has_full_scope(conn, policy, MODULE, 'edit')
    return dict(policy=output, rows=rows, status=policy['status'], calculation_token=token,
                formula=('مبلغ مبنای فرد × امتیاز کامل وزنی ÷ ۱۰۰ × دقایق مشارکت تأییدشده ÷ دقایق موظفی؛ گرد کردن نیم‌به‌بالا تا ریال'
                         if policy['method'] == 'individual_base' else
                         'بودجه مشترک × سهم امتیاز وزنی و مشارکت هر فرد از جمع؛ توزیع ریال با روش بزرگ‌ترین باقیمانده'),
                metrics=_metrics(rows, policy, full=full and len(rows) == len(all_rows)),
                policy_metrics=_metrics(all_rows, policy) if full else None,
                can_finalize=policy['status'] == 'draft' and not blockers and can_edit,
                finalize_blockers=blockers)


def register(app, db, authenticated, audit, error):
    with app.app_context():
        db().executescript(SCHEMA)
        db().commit()
    app.extensions.setdefault('hrm_catalog', {}).update(CATALOG)

    def collection(resource):
        if request.method == 'GET':
            access.require(db(), MODULE)
            return jsonify(_list_rows(db(), resource, request.args))
        access.require(db(), MODULE, operation='create')
        body = json_object()
        with begin_write(db()):
            values = _policy_values(db(), body) if resource == 'accord_policies' else _entry_values(db(), body)
            if resource == 'accord_policies':
                duplicate = db().execute('SELECT id FROM hrm_accord_policies WHERE period_id=?', (values['period_id'],)).fetchone()
            else:
                duplicate = db().execute('SELECT id FROM hrm_accord_entries WHERE policy_id=? AND employee_id=?', (values['policy_id'], values['employee_id'])).fetchone()
            if duplicate:
                raise HrmConflict('سابقه این دوره یا فرد قبلاً ثبت شده است؛ رکورد موجود را ویرایش یا بازیابی کنید')
            keys = list(values)
            inserted = db().execute('INSERT INTO hrm_' + resource + '(' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', [values[key] for key in keys])
            if resource == 'accord_entries':
                _touch_policy(db(), values['policy_id'])
            audit('hrm_create_' + resource, values['company_id'], str(inserted.lastrowid))
            row = dict(db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (inserted.lastrowid,)).fetchone())
        return jsonify(row), 201

    def item(resource, record_id):
        operation = {'GET': 'read', 'PUT': 'edit', 'DELETE': 'delete'}[request.method]
        access.require(db(), MODULE, operation=operation)
        body = json_object() if request.method != 'GET' else None
        with begin_write(db()) if request.method != 'GET' else nullcontext():
            row = db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (positive_id(record_id),)).fetchone()
            if row is None or (row['is_deleted'] and request.args.get('include_deleted') != '1'):
                return error('سابقه آکورد یافت نشد', 404)
            if resource == 'accord_policies':
                _visible_policy(db(), row, operation)
            else:
                access.record_scope(db(), row, MODULE, operation=operation)
            if request.method == 'GET':
                if resource == 'accord_policies':
                    return jsonify(_policy_output(db(), row))
                output = dict(row)
                policy = _policy(db(), row['policy_id'], allow_deleted=True)
                output.update(policy_status=policy['status'], locked=policy['status'] == 'finalized')
                return jsonify(output)
            check_version(body, row)
            policy = row if resource == 'accord_policies' else _policy(db(), row['policy_id'], operation=operation)
            if policy['status'] == 'finalized':
                raise HrmConflict('آکورد نهایی قابل ویرایش یا بایگانی نیست')
            if request.method == 'DELETE':
                if set(body) != {'version'}:
                    raise ValueError('فقط شماره نسخه را برای بایگانی ارسال کنید')
                if resource == 'accord_policies':
                    _full_scope(db(), row, MODULE, 'delete')
                    if db().execute('SELECT 1 FROM hrm_accord_entries WHERE policy_id=? AND is_deleted=0', (row['id'],)).fetchone():
                        raise HrmConflict('ابتدا مبناهای فعال ضابطه را بایگانی کنید')
                if row['is_deleted']:
                    raise HrmConflict('این سابقه قبلاً بایگانی شده است')
                db().execute('UPDATE hrm_' + resource + ' SET is_deleted=1,deleted_at=CURRENT_TIMESTAMP,deleted_by=?,version=version+1 WHERE id=?', (access.ctx(db())['user_id'], row['id']))
                if resource == 'accord_entries':
                    _touch_policy(db(), row['policy_id'])
                audit('hrm_archive_' + resource, row['company_id'], str(row['id']))
                return jsonify(ok=True, id=row['id'], is_deleted=1, version=row['version'] + 1)
            values = _policy_values(db(), body, row) if resource == 'accord_policies' else _entry_values(db(), body, row)
            keys = list(values)
            db().execute('UPDATE hrm_' + resource + ' SET ' + ','.join(key + '=?' for key in keys) + ',version=version+1 WHERE id=?', [values[key] for key in keys] + [row['id']])
            if resource == 'accord_entries':
                _touch_policy(db(), row['policy_id'])
            audit('hrm_update_' + resource, row['company_id'], str(row['id']))
            result = dict(db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (row['id'],)).fetchone())
        return jsonify(result)

    def restore(resource, record_id):
        access.require(db(), MODULE, operation='edit')
        body = json_object()
        if set(body) != {'version'}:
            raise ValueError('فقط شماره نسخه را برای بازیابی ارسال کنید')
        with begin_write(db()):
            row = db().execute('SELECT * FROM hrm_' + resource + ' WHERE id=?', (positive_id(record_id),)).fetchone()
            if row is None:
                return error('سابقه آکورد یافت نشد', 404)
            if resource == 'accord_policies':
                _full_scope(db(), row, MODULE, 'edit')
                policy = row
            else:
                access.record_scope(db(), row, MODULE, operation='edit')
                policy = _policy(db(), row['policy_id'], operation='edit')
            check_version(body, row)
            if not row['is_deleted'] or policy['status'] == 'finalized':
                raise HrmConflict('این سابقه قابل بازیابی نیست')
            db().execute('UPDATE hrm_' + resource + " SET is_deleted=0,deleted_at='',deleted_by=NULL,version=version+1 WHERE id=?", (row['id'],))
            if resource == 'accord_entries':
                _touch_policy(db(), row['policy_id'])
            audit('hrm_restore_' + resource, row['company_id'], str(row['id']))
        return jsonify(ok=True, id=row['id'], version=row['version'] + 1)

    for resource in ('accord_policies', 'accord_entries'):
        app.add_url_rule('/api/hrm/' + resource, 'hrm_productivity_' + resource, authenticated(lambda resource=resource: collection(resource)), methods=['GET', 'POST'])
        app.add_url_rule('/api/hrm/' + resource + '/<int:record_id>', 'hrm_productivity_item_' + resource,
                         authenticated(lambda record_id, resource=resource: item(resource, record_id)), methods=['GET', 'PUT', 'DELETE'])
        app.add_url_rule('/api/hrm/' + resource + '/<int:record_id>/restore', 'hrm_productivity_restore_' + resource,
                         authenticated(lambda record_id, resource=resource: restore(resource, record_id)), methods=['POST'])

    @app.get('/api/hrm/accord_results')
    @authenticated
    def accord_results():
        access.require(db(), MODULE)
        return jsonify(_list_rows(db(), 'accord_results', request.args))

    @app.get('/api/hrm/accord_results/<int:record_id>')
    @authenticated
    def accord_result_item(record_id):
        row = db().execute('SELECT * FROM hrm_accord_results WHERE id=?', (positive_id(record_id),)).fetchone()
        if row is None:
            return error('نتیجه آکورد یافت نشد', 404)
        access.record_scope(db(), row, MODULE)
        return jsonify(_result_output(db(), row))

    @app.get('/api/hrm/accord_report')
    @authenticated
    def accord_report():
        access.require(db(), MODULE)
        if not request.args.get('policy_id'):
            raise ValueError('ضابطه آکورد را انتخاب کنید')
        policy = _policy(db(), request.args['policy_id'])
        return jsonify(_report(db(), policy, request.args))

    @app.post('/api/hrm/accord_policies/<int:policy_id>/seed-criteria')
    @authenticated
    def accord_seed(policy_id):
        body = json_object()
        if set(body) != {'version'}:
            raise ValueError('فقط شماره نسخه ضابطه را ارسال کنید')
        with begin_write(db()):
            policy = _policy(db(), policy_id, operation='edit')
            _full_scope(db(), policy, MODULE, 'edit')
            _full_scope(db(), policy, 'performance', 'create')
            check_version(body, policy)
            period = _period(db(), policy)
            if policy['status'] != 'draft' or period['status'] != 'open':
                raise HrmConflict('پیشنهاد شاخص فقط برای ضابطه پیش‌نویس و دوره باز است')
            if db().execute('SELECT 1 FROM hrm_performance_criteria WHERE period_id=?', (period['id'],)).fetchone() or db().execute('SELECT 1 FROM hrm_performance_scores WHERE period_id=?', (period['id'],)).fetchone():
                raise HrmConflict('دوره دارای شاخص یا سابقه امتیاز است؛ پیشنهاد خودکار آن را جایگزین نمی‌کند')
            for title, weight in STARTER_CRITERIA:
                db().execute('INSERT INTO hrm_performance_criteria(company_id,site_id,period_id,title,weight,max_score,note) VALUES (?,?,?,?,?,100,?)',
                             (policy['company_id'], policy['site_id'], period['id'], title, weight,
                              'پیشنهاد اولیه قابل ویرایش؛ امتیاز فقط با ارزیابی مسئول مجاز ثبت می‌شود'))
            db().execute('UPDATE hrm_evaluation_periods SET version=version+1 WHERE id=?', (period['id'],))
            _touch_policy(db(), policy['id'])
            audit('hrm_seed_accord_criteria', policy['company_id'], str(policy['id']))
        return jsonify(ok=True, criteria_count=len(STARTER_CRITERIA), policy_id=policy['id'], version=policy['version'] + 1)

    @app.post('/api/hrm/accord_policies/<int:policy_id>/finalize')
    @authenticated
    def accord_finalize(policy_id):
        body = json_object()
        if set(body) != {'version', 'calculation_token'}:
            raise ValueError('شماره نسخه و شناسه محاسبه بررسی‌شده را ارسال کنید')
        with begin_write(db()):
            policy = _policy(db(), policy_id, operation='edit')
            _full_scope(db(), policy, MODULE, 'edit')
            _full_scope(db(), policy, 'performance', 'read')
            check_version(body, policy)
            if policy['status'] != 'draft':
                raise HrmConflict('آکورد قبلاً نهایی شده است')
            computed = _compute(db(), policy, operation='edit')
            if not isinstance(body['calculation_token'], str) or body['calculation_token'] != computed['calculation_token']:
                raise HrmConflict('مبنای محاسبه تغییر کرده است؛ نتیجه را دوباره بررسی کنید')
            if computed['finalize_blockers']:
                raise HrmConflict('؛ '.join(computed['finalize_blockers']))
            actor = access.ctx(db())['user_id']
            for row in computed['rows']:
                duplicate = db().execute('SELECT 1 FROM hrm_accord_results WHERE period_id=? AND employee_id=?', (policy['period_id'], row['employee_id'])).fetchone()
                if duplicate:
                    raise HrmConflict('آکورد این فرد در همین دوره قبلاً نهایی شده است')
                snapshot = dict(policy={key: policy[key] for key in ('id', 'title', 'method', 'minimum_score', 'budget_rials', 'company_id', 'site_id', 'version', 'note')},
                                period=computed['period'], result=row, calculation_token=computed['calculation_token'])
                columns = ('employee_id', 'company_id', 'site_id', 'department', 'employee_name', 'employee_code', 'company_name', 'site_name', 'base_rials', 'accord_rials')
                values = {key: row[key] for key in columns}
                values.update(entry_id=row['id'], policy_id=policy['id'], period_id=policy['period_id'], method=policy['method'],
                              score_percent=str(row['score_percent']), participation_percent=str(row['participation_percent']),
                              snapshot_json=json.dumps(snapshot, sort_keys=True, ensure_ascii=False), approved_by=actor)
                keys = list(values)
                db().execute('INSERT INTO hrm_accord_results(' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', [values[key] for key in keys])
            db().execute("UPDATE hrm_accord_policies SET status='finalized',period_snapshot=?,approved_by=?,approved_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=?",
                         (json.dumps(computed['period'], sort_keys=True, ensure_ascii=False), actor, policy['id']))
            audit('hrm_finalize_accord', policy['company_id'], json.dumps(dict(policy_id=policy['id'], entries=len(computed['rows']), calculation_token=computed['calculation_token'])))
            output = (_report(db(), _policy(db(), policy['id']), {'policy_id': str(policy['id'])})
                      if access.ctx(db())['permissions'][MODULE]['read'] else
                      dict(ok=True, policy_id=policy['id'], status='finalized', version=policy['version'] + 1, metrics=_metrics(computed['rows'], policy)))
        return jsonify(output)
