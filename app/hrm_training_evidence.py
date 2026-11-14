"""Shared, scoped evidence for training and HSE compliance.

A grade/assessment can establish competency. Only an explicit actual-attendance
record establishes a course completion date for renewal. Historical company,
site and department ownership is checked before either fact is summarized.
"""
from datetime import date, timedelta

from . import hrm_access as access
from .hrm_common import date_value, today_iso


ALLOWED_MODULES = frozenset({'training', 'hse', 'health_environment'})


def _date(raw, as_of=None):
    try:
        value = date_value(raw)
    except ValueError:
        return ''
    return value if value and value <= (as_of or today_iso()) else ''


def _visible(conn, row, module, operation):
    try:
        access.record_scope(conn, row, module, operation=operation)
        return True
    except (PermissionError, ValueError):
        return False


def scoped_training_evidence(conn, person, code, module, as_of=None, operation='read'):
    """Return minimal compliance evidence without exporting source records.

    Legacy rows without site metadata retain a NULL historical site. They are
    available to an authorized whole-company reader, never attributed to a
    person's current site merely to satisfy a narrower reader's permission.
    """
    if module not in ALLOWED_MODULES or operation not in ('read','export'):
        raise ValueError('حوزه شواهد آموزش معتبر نیست')
    access.require(conn, module, operation=operation)
    eid = person['id']
    assessments = []
    for row in conn.execute('''SELECT a.id,a.level,a.company_id,
            COALESCE(x.company_id,a.company_id) owner_company_id,x.site_id,
            COALESCE(x.department,'') department,COALESCE(x.date_iso,'') assessment_date,COALESCE(x.version,0) version,
            x.recorded_by,u.username recorded_by_name,COALESCE(x.assessor_name,'') assessor_name,
            COALESCE(x.assessment_method,'') assessment_method,COALESCE(x.evidence,'') assessment_evidence
        FROM hrm_live_assessments a JOIN competency_links l ON l.skill_id=a.skill_id
        LEFT JOIN hrm_training_assessment_context x ON x.assessment_id=a.id
        LEFT JOIN users u ON u.id=x.recorded_by
        WHERE a.employee_id=? AND l.code=?''', (eid, code)):
        owner = dict(company_id=row['owner_company_id'], site_id=row['site_id'], department=row['department'])
        if _visible(conn, owner, module, operation) and (not row['assessment_date'] or _date(row['assessment_date'],as_of)):
            assessments.append(dict(row))
    assessments.sort(key=lambda row: (row['owner_company_id'] == person['company_id'], row['assessment_date'], row['id']), reverse=True)
    grades = []
    for row in conn.execute('''SELECT r.id,r.level,COALESCE(x.company_id,r.company_id) owner_company_id,
                x.site_id,COALESCE(x.department,'') department,COALESCE(x.version,0) version,
                COALESCE(x.date_iso,'') record_date,c.date course_date,c.id course_id
        FROM hrm_live_records r JOIN courses c ON c.id=r.course_id
        JOIN competency_links l ON l.skill_id=c.skill_id
        LEFT JOIN hrm_training_record_context x ON x.record_id=r.id
        WHERE r.employee_id=? AND l.code=? AND c.status='completed' ''', (eid, code)):
        owner = dict(company_id=row['owner_company_id'], site_id=row['site_id'], department=row['department'])
        if _visible(conn, owner, module, operation):
            item=dict(row);item['record_date']=_date(row['record_date'] or row['course_date'],as_of)
            if row['record_date'] and not item['record_date']:
                continue
            grades.append(item)
    grades.sort(key=lambda row:(row['owner_company_id']==person['company_id'],row['record_date'],row['id']),reverse=True)
    actual = []
    for row in conn.execute('''SELECT p.attendance_company_id company_id,p.attendance_site_id site_id,
                p.attendance_department department,c.id course_id,c.date,COALESCE(x.date_iso,'') date_iso,COALESCE(x.version,0) version,
                COALESCE(x.is_deleted,0) course_voided
        FROM course_participants p JOIN courses c ON c.id=p.course_id
        JOIN competency_links l ON l.skill_id=c.skill_id
        LEFT JOIN hrm_training_course_context x ON x.course_id=c.id
        WHERE p.employee_id=? AND l.code=? AND p.attended=1 AND c.status='completed' ''', (eid, code)):
        if row['course_voided'] or not _visible(conn, row, module, operation):
            continue
        completed = _date(row['date_iso'] or row['date'],as_of)
        if completed:
            actual.append(dict(row,date=completed))
    actual.sort(key=lambda row:(row['date'],row['course_id']),reverse=True)
    chosen_assessment=assessments[0] if assessments else None
    chosen_grade=grades[0] if grades and not chosen_assessment else None
    latest=actual[0] if actual else None
    def owner(row):
        return dict(company_id=row.get('owner_company_id',row.get('company_id')),site_id=row['site_id'],department=row['department'],version=row['version']) if row else None
    level = chosen_assessment['level'] if chosen_assessment else chosen_grade['level'] if chosen_grade else None
    details_allowed=module=='training' or access.ctx(conn)['permissions']['training'][operation]
    return dict(current_level=level,
                level_source='current_assessment' if assessments else 'completed_course_record' if grades else 'unknown',
                last_training_date=latest['date'] if latest else '',
                actual_completion_status='recorded' if actual else 'unknown',
                missing_actual_completion=not bool(actual),
                assessment_id=chosen_assessment['id'] if chosen_assessment else None,
                assessment_date=chosen_assessment['assessment_date'] if chosen_assessment else '',assessment_owner=owner(chosen_assessment),
                assessor_name=chosen_assessment['assessor_name'] or None if chosen_assessment and details_allowed else None,
                assessment_method=chosen_assessment['assessment_method'] or None if chosen_assessment and details_allowed else None,
                assessment_evidence=chosen_assessment['assessment_evidence'] or None if chosen_assessment and details_allowed else None,
                recorded_by=dict(id=chosen_assessment['recorded_by'],username=chosen_assessment['recorded_by_name']) if chosen_assessment and details_allowed and chosen_assessment['recorded_by'] else None,
                assessment_details_authorized=bool(details_allowed),
                level_record_id=chosen_grade['id'] if chosen_grade else None,level_record_date=chosen_grade['record_date'] if chosen_grade else '',
                level_record_owner=owner(chosen_grade),
                actual_course_id=latest['course_id'] if latest else None,actual_course_date=latest['date'] if latest else '',actual_owner=owner(latest))


def renewal_evidence(last_training_date, renewal_days, as_of=None):
    """Missing actual dates are unknown, never compliant or automatically late."""
    if not renewal_days:
        return dict(renewal_due_date='', renewal_due=None, renewal_status='not_configured', renewal_review_required=False)
    if not last_training_date:
        return dict(renewal_due_date='', renewal_due=None, renewal_status='unknown_actual_completion', renewal_review_required=True)
    previous = date.fromisoformat(last_training_date)
    if previous.toordinal() + renewal_days > date.max.toordinal():
        return dict(renewal_due_date='', renewal_due=None, renewal_status='review_interval', renewal_review_required=True)
    expires = (previous + timedelta(days=renewal_days)).isoformat()
    due = expires <= (as_of or today_iso())
    return dict(renewal_due_date=expires, renewal_due=due, renewal_status='due' if due else 'current', renewal_review_required=False)


def applicable_hse_training_rules(conn, person, module='training', operation='read'):
    """Minimal read-only curriculum bridge, never clinical or incident records.

    Training staff may plan the applicable curriculum from the HSE owner's
    original rule. The rule is neither copied to general policies nor made
    editable through the training grant.
    """
    if module not in ALLOWED_MODULES or operation not in ('read','export'):
        raise ValueError('حوزه الزامات آموزشی معتبر نیست')
    access.record_scope(conn,person,module,operation=operation)
    domains = {'safety'} if module == 'hse' else {'health','environment'} if module == 'health_environment' else {'safety','health','environment'}
    result=[]
    for row in conn.execute('''SELECT r.id,r.company_id,r.site_id,r.department,r.job,r.activity_location,
                  r.domain,r.competency_code,b.title competency_title,r.required_level,r.renewal_days,r.version
              FROM hrm_hse_training_requirements r JOIN holding_competencies b ON b.code=r.competency_code
              WHERE r.company_id=? AND r.active=1 AND r.is_deleted=0 ORDER BY r.id''',(person['company_id'],)):
        if row['domain'] not in domains or row['site_id'] not in (None,person.get('site_id')):
            continue
        if any(row[key] and row[key]!=person.get(target,'') for key,target in
               (('department','department'),('job','job'),('activity_location','location'))):
            continue
        item=dict(row)
        item.update(source='hse_curriculum',source_module='hse' if row['domain']=='safety' else 'health_environment',
                    source_resource='hse_training_requirements' if row['domain']=='safety' else 'health_environment_training_requirements',
                    source_id=row['id'],onboarding=0)
        result.append(item)
    return result
