"""Permission-scoped navigation across one canonical employee identity.

This module stores no people, facts, summaries or permissions. It returns only
minimal identity and links/counts from the owning modules' existing records.
"""
from collections import defaultdict
import re

from flask import jsonify, request

from . import hrm_access as access
from .hrm_common import text_value
from .validation import positive_id


# These are personal facts, not configuration banks or new copies of them.
# A document is counted under its real permission group below.
FACTS = {
    'personnel': (
        ('education', 'hrm_education', 'department'),
        ('prior_employment', 'hrm_prior_employment', 'department'),
        ('employment_history', 'hrm_employment_history', 'department'),
        ('personnel_actions', 'hrm_personnel_actions', 'record_department'),
        ('temporary_assignments', 'hrm_temporary_assignments', 'department'),
    ),
    'contracts': (('contracts', 'hrm_contracts', 'department'),),
    'documents': (),
    'attendance': (('attendance', 'hrm_attendance', 'department'),),
    'performance': (('performance_scores', 'hrm_performance_scores', 'department'),),
    'productivity': (
        ('accord_entries', 'hrm_accord_entries', 'department'),
        ('accord_results', 'hrm_accord_results', 'department'),
    ),
    'discipline': (('discipline', 'hrm_discipline', 'department'),),
    'loans': (
        ('loans', 'hrm_loans', 'department'),
        ('loan_payments', 'hrm_loan_payments', 'department'),
    ),
    'welfare': (('welfare_uses', 'hrm_welfare_uses', 'department'),),
    'accommodation': (('reservations', 'hrm_reservations', 'department'),),
    'training': (),
    'hse': (
        ('ppe_issues', 'hrm_ppe_issues', 'department'),
        ('hse_incidents', 'hrm_hse_incidents', 'department'),
        ('safety_qualifications', 'hrm_safety_qualifications', 'department'),
    ),
    'health_environment': (
        ('harmful_exposures', 'hrm_harmful_exposures', 'department'),
        ('environment_participations', 'hrm_environment_participation', 'department'),
    ),
    'health': (('health_records', 'hrm_health_records', 'department'),),
}
DOCUMENT_MODULES = frozenset({'documents', 'contracts', 'discipline', 'loans',
                              'performance', 'health', 'hse', 'health_environment'})
TRAINING_RESOURCES = ('training_records', 'training_assessments', 'training_assessment_history')
OPERATIONS = ('read', 'create', 'edit', 'delete', 'export')


def _flags(actor, module):
    supplied = actor['permissions'].get(module, {})
    return {operation: bool(supplied.get(operation)) for operation in OPERATIONS}


def _context_filters(conn, actor):
    values = {}
    for key in ('company_id', 'site_id', 'employee_id'):
        if request.args.get(key) not in (None, ''):
            values[key] = positive_id(request.args[key])
    cid, site_id = values.get('company_id'), values.get('site_id')
    if cid is not None and cid not in actor['company_ids']:
        raise PermissionError()
    if site_id is not None:
        site = conn.execute('SELECT company_id FROM hrm_sites WHERE id=?', (site_id,)).fetchone()
        if site is None or (cid is not None and site['company_id'] != cid):
            raise ValueError('سایت متعلق به شرکت انتخاب‌شده نیست')
        if not any(scope['company_id'] == site['company_id'] and scope['site_id'] in (None, site_id)
                   for scope in actor['scopes']):
            raise PermissionError()
        values['company_id'] = site['company_id']
    department = text_value(request.args.get('department', ''), max_length=200)
    if department:
        values['department'] = department
        if not any((values.get('company_id') is None or scope['company_id'] == values['company_id'])
                   and (site_id is None or scope['site_id'] in (None, site_id))
                   and (not scope['department'] or scope['department'] == department)
                   for scope in actor['scopes']):
            raise PermissionError()
    module = request.args.get('module')
    if module:
        if module not in FACTS:
            raise ValueError('بخش پرونده یکپارچه معتبر نیست')
        if not any(_flags(actor, module).values()):
            raise PermissionError()
        values['module'] = module
    return values


def _matches(row, filters):
    return all(row.get(key) == value for key, value in filters.items()
               if key in ('company_id', 'site_id', 'department', 'employee_id'))


def _reference_permission(actor, filters):
    modules = (filters['module'],) if filters.get('module') else tuple(FACTS)
    for operation in OPERATIONS:
        for module in modules:
            if _flags(actor, module)[operation]:
                return module, operation
    raise PermissionError()


def _current_people(conn, actor, filters):
    module, operation = _reference_permission(actor, filters)
    clause, parameters = access.scope_sql(conn, module, alias='e', operation=operation)
    columns = ','.join(access.effective_column(conn, name, 'e') + ' AS ' + name
                       for name in ('company_id', 'site_id', 'department', 'job'))
    sql = ('SELECT e.id,e.code,e.name,' + columns +
           ',COALESCE(h.status,\'active\') AS status FROM employees e '
           'LEFT JOIN hrm_employee_details h ON h.employee_id=e.id WHERE (' + clause + ')')
    if filters.get('employee_id'):
        sql += ' AND e.id=?';parameters.append(filters['employee_id'])
    result = {}
    for row in conn.execute(sql, parameters):
        item = dict(row, employee_id=row['id'])
        if _matches(item, filters):
            result[item['id']] = item
    return result


def _sql_facts(conn, module, resource, table, department_column, filters, *, document_group=False, identities_only=False):
    """Read only identity/scope columns after the actual domain read grant."""
    scope, parameters = access.record_scope_sql(conn, module, alias='r',
                                                department_column='r.' + department_column)
    # All identifiers originate in FACTS, not request data.
    columns = {row['name'] for row in conn.execute('PRAGMA table_info("' + table + '")')}
    if not {'employee_id', 'company_id', 'site_id'} <= columns:
        return []
    projection = 'DISTINCT r.employee_id' if identities_only else 'r.id,r.employee_id'
    sql = ('SELECT ' + projection + ',r.company_id,r.site_id,r.' + department_column +
           ' AS department FROM "' + table + '" r WHERE (' + scope + ') AND r.employee_id IS NOT NULL')
    if 'is_deleted' in columns:
        sql += ' AND r.is_deleted=0'
    if 'deleted' in columns:
        sql += ' AND r.deleted=0'
    if document_group:
        sql += ' AND r.permission_group=?';parameters.append(module)
    if resource == 'accord_entries':
        sql += ' AND EXISTS (SELECT 1 FROM hrm_accord_policies p WHERE p.id=r.policy_id AND p.is_deleted=0)'
    for key in ('company_id', 'site_id', 'employee_id'):
        if key in filters:
            sql += ' AND r.' + key + '=?';parameters.append(filters[key])
    if 'department' in filters:
        sql += ' AND r.' + department_column + '=?';parameters.append(filters['department'])
    return [dict(row, resource=resource) for row in conn.execute(sql, parameters)]


def _facts(conn, actor, filters, *, identities_only=False):
    """No query at all is made against a domain lacking its read permission."""
    result = defaultdict(list)
    modules = (filters['module'],) if filters.get('module') else tuple(FACTS)
    for module in modules:
        if not _flags(actor, module)['read']:
            continue
        for resource, table, department in FACTS[module]:
            result[module].extend(_sql_facts(conn, module, resource, table, department, filters, identities_only=identities_only))
        if module in DOCUMENT_MODULES:
            result[module].extend(_sql_facts(conn, module, 'documents', 'hrm_documents', 'department',
                                             filters, document_group=True, identities_only=identities_only))
        if module == 'training':
            from .hrm_training import list_rows, _actual_rows
            for resource in TRAINING_RESOURCES:
                for row in list_rows(conn, resource, apply_filters=False, employee_id=filters.get('employee_id')):
                    if _matches(row, filters):
                        result[module].append({key: row.get(key) for key in
                                               ('id', 'employee_id', 'company_id', 'site_id', 'department')} |
                                              {'resource': resource})
            for row in _actual_rows(conn, apply_filters=False, employee_id=filters.get('employee_id')):
                if _matches(row, filters):
                    result[module].append({key: row.get(key) for key in
                                           ('employee_id', 'company_id', 'site_id', 'department')} |
                                          {'id': row['course_id'], 'resource': 'training_courses'})
            from .hrm_people import training_certificate_rows
            # Unknown-site legacy certificates can only belong to a broad
            # company reader. The shared helper remains the final authority.
            companies = sorted({scope['company_id'] for scope in actor['scopes']
                                if scope['site_id'] is None and not scope['department']})
            ids = []
            if companies:
                marks = ','.join('?' for _ in companies)
                query = 'SELECT DISTINCT employee_id FROM certificates WHERE company_id IN (' + marks + ')'
                params = list(companies)
                if filters.get('employee_id'):
                    query += ' AND employee_id=?';params.append(filters['employee_id'])
                ids = [row[0] for row in conn.execute(query, params)]
            for eid in ids:
                for item in training_certificate_rows(conn, eid):
                    row = dict(item, site_id=None, department='', resource='training_certificates')
                    if _matches(row, filters):
                        result[module].append(row)
    return result


def _minimal_identity(conn, row, *, current):
    item = {key: row[key] for key in ('id', 'code', 'name')}
    item.update(identity_basis='current_assignment' if current else 'authorized_history',
                current_assignment_visible=bool(current))
    if current:
        item.update({key: row[key] for key in ('company_id', 'site_id', 'department', 'job', 'status')})
        company = conn.execute('SELECT name FROM companies WHERE id=?', (row['company_id'],)).fetchone()
        site = conn.execute('SELECT name FROM hrm_sites WHERE id=?', (row['site_id'],)).fetchone() if row['site_id'] else None
        item.update(company_name=company['name'] if company else '', site_name=site['name'] if site else '')
    else:
        item.update(company_id=None, company_name=None, site_id=None, site_name=None,
                    department='', job='', status=None)
    return item


def _identities(conn, current, facts):
    result = {eid: _minimal_identity(conn, row, current=True) for eid, row in current.items()}
    historical = {row['employee_id'] for rows in facts.values() for row in rows} - set(result)
    if historical:
        placeholders = ','.join('?' for _ in historical)
        for row in conn.execute('SELECT id,code,name FROM employees WHERE id IN (' + placeholders + ')', sorted(historical)):
            result[row['id']] = _minimal_identity(conn, row, current=False)
    company_index, site_index = defaultdict(set), defaultdict(set)
    for rows in facts.values():
        for row in rows:
            company_index[row['employee_id']].add(row['company_id'])
            if row['site_id'] is not None:
                site_index[row['employee_id']].add(row['site_id'])
    for item in result.values():
        item['visible_fact_company_ids'] = sorted(company_index[item['id']])
        item['visible_fact_site_ids'] = sorted(site_index[item['id']])
    return result


def _search_text(value):
    return re.sub(r'\s+', ' ', str(value).translate(str.maketrans({'ي': 'ی', 'ك': 'ک', '\u200c': ' '}))).strip().casefold()


def register(app, db, authenticated, audit, error):
    @app.get('/api/hrm/people-directory')
    @authenticated
    def hrm_people_directory():
        actor = access.ctx(db())
        filters = _context_filters(db(), actor)
        current = _current_people(db(), actor, filters)
        facts = _facts(db(), actor, filters, identities_only=True)
        identities = _identities(db(), current, facts)
        query = _search_text(text_value(request.args.get('q', ''), max_length=240))
        rows = [row for row in identities.values()
                if not query or query in _search_text(row['name'] + ' ' + row['code'])]
        rows.sort(key=lambda row: (_search_text(row['name']), row['code'], row['id']))
        return jsonify(people=rows, total=len(rows), company_id=filters.get('company_id'),
                       site_id=filters.get('site_id'), identity_basis='authorized_current_and_history')

    @app.get('/api/hrm/people/<int:employee_id>/overview')
    @authenticated
    def hrm_person_overview(employee_id):
        actor = access.ctx(db())
        filters = _context_filters(db(), actor)
        if filters.get('employee_id') not in (None, employee_id):
            raise ValueError('شناسه فرد با فیلتر پرونده یکسان نیست')
        filters['employee_id'] = positive_id(employee_id)
        current = _current_people(db(), actor, filters)
        # Overview presents all authorized domains; the optional directory module
        # must not become a second permission model for this cross-domain view.
        fact_filters = {key: value for key, value in filters.items() if key != 'module'}
        facts = _facts(db(), actor, fact_filters)
        identities = _identities(db(), current, facts)
        person = identities.get(employee_id)
        if person is None:
            raise PermissionError()
        catalog = app.extensions.get('hrm_catalog', {})
        sections = {}
        for module in FACTS:
            flags = _flags(actor, module)
            rows = [row for row in facts[module] if row['employee_id'] == employee_id]
            counts = defaultdict(int)
            for row in rows:
                counts[row['resource']] += 1
            resources = [name for name in catalog if catalog[name]['module'] == module and
                         (name in counts or any(name == item[0] for item in FACTS[module]) or
                          (module == 'training' and name in (*TRAINING_RESOURCES, 'training_courses')))]
            if module in DOCUMENT_MODULES and 'documents' in catalog and any(flags.values()):
                resources.append('documents')
            if module == 'personnel' and person['current_assignment_visible'] and 'people' in catalog:
                resources.insert(0, 'people')
            resources = list(dict.fromkeys(resources)) if any(flags.values()) else []
            if not person['current_assignment_visible']:
                flags.update(create=False, edit=False, delete=False)
            sections[module] = dict(**flags, count=len(rows) if flags['read'] else None,
                                    resources=resources, resource_counts=dict(counts) if flags['read'] else None,
                                    resource_filters={'documents': {'permission_group': module}} if module in DOCUMENT_MODULES else {},
                                    count_basis='authorized_active_records_all_dates')
        return jsonify(person=person, identity_basis=person['identity_basis'],
                       current_assignment_visible=person['current_assignment_visible'],
                       sections=sections, date_filters_applied=False,
                       company_id=filters.get('company_id'), site_id=filters.get('site_id'))
