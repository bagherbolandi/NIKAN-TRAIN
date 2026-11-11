"""Personnel file, organisational structure, employment documents and history."""
import json

from flask import g, jsonify, request, Response

from ..validation import positive_id
from . import access
from .crud import Crud, parse_values
from .core import _rowdict

PROFILE_FIELDS = {
    'national_id': 'text', 'father_name': 'text', 'birth_date': 'date',
    'gender': 'text', 'marital_status': 'text', 'phone': 'text', 'email': 'text',
    'address': 'text', 'emergency_contact': 'text', 'education_level': 'text',
    'education_field': 'text', 'military_status': 'text', 'insurance_no': 'text',
    'bank_account': 'text', 'start_date': 'date', 'end_date': 'date',
    'employment_status': 'text', 'unit_id': 'int', 'position_id': 'int',
    'work_location_id': 'int', 'manager_id': 'int', 'note': 'text',
}
EMPLOYEE_FIELDS = {'code': 'text', 'name': 'text', 'department': 'text', 'job': 'text', 'active': 'bool'}
STATUS_LABELS = {
    'active': 'فعال', 'probation': 'آزمایشی', 'suspended': 'تعلیق',
    'terminated': 'قطع همکاری', 'retired': 'بازنشسته',
}


def employee_options(conn, cid, user):
    rows = conn.execute(
        'SELECT e.id,e.code,e.name,e.department,e.job,e.active,p.unit_id,p.position_id,p.employment_status '
        'FROM employees e LEFT JOIN personnel_profiles p ON p.employee_id=e.id '
        'WHERE e.company_id=? ORDER BY e.name', (cid,)).fetchall()
    result = []
    for row in rows:
        if not access.employee_allowed(conn, user, row):
            continue
        result.append({'id': row['id'], 'code': row['code'], 'name': row['name'],
                       'department': row['department'], 'job': row['job'],
                       'unit_id': row['unit_id'], 'position_id': row['position_id'],
                       'active': row['active'],
                       'employment_status': row['employment_status'] or 'active'})
    return result


def _check_references(ctx, values, cid):
    for field, table in [('unit_id', 'org_units'), ('position_id', 'positions'),
                         ('work_location_id', 'work_locations')]:
        if values.get(field):
            ctx.reference(table, values[field], cid)
    if values.get('manager_id'):
        ctx.employee(values['manager_id'], cid)


def _sync_legacy_names(ctx, employee_values, profile_values, cid):
    """Keep department/job (used by the training module) derived, never re-typed."""
    if profile_values.get('unit_id'):
        row = ctx.c.execute('SELECT name FROM org_units WHERE id=? AND company_id=?',
                            (profile_values['unit_id'], cid)).fetchone()
        if row:
            employee_values['department'] = row['name']
    if profile_values.get('position_id'):
        row = ctx.c.execute('SELECT title FROM positions WHERE id=? AND company_id=?',
                            (profile_values['position_id'], cid)).fetchone()
        if row:
            employee_values['job'] = row['title']


def register(ctx):
    def org_check(values, target=None):
        if values.get('parent_id'):
            ctx.reference('org_units', values['parent_id'], g.hrm_cid)
            if target and values['parent_id'] == target['id']:
                raise ValueError('واحد نمی‌تواند زیرمجموعه خود باشد')
        if values.get('manager_id'):
            ctx.employee(values['manager_id'], g.hrm_cid)

    Crud(ctx, 'org-units', 'org_units',
         {'parent_id': 'int', 'manager_id': 'int', 'code': 'text', 'name': 'text', 'active': 'bool'},
         required=('name',), view='personnel.view', edit='personnel.edit',
         label='واحد سازمانی', extra_check=org_check).register()
    Crud(ctx, 'positions', 'positions', {'code': 'text', 'title': 'text', 'active': 'bool'},
         required=('title',), view='personnel.view', edit='personnel.edit',
         label='سمت').register()
    Crud(ctx, 'work-locations', 'work_locations', {'name': 'text', 'address': 'text', 'active': 'bool'},
         required=('name',), view='personnel.view', edit='personnel.edit',
         label='محل خدمت').register()

    @ctx.route('personnel', permission='personnel.view')
    def hrm_personnel_list():
        cid = g.hrm_cid
        q = (request.args.get('q') or '').strip()
        status = request.args.get('status') or ''
        unit_id = request.args.get('unit_id') or ''
        sql = ('SELECT e.id,e.code,e.name,e.department,e.job,e.active,p.* '
               'FROM employees e LEFT JOIN personnel_profiles p ON p.employee_id=e.id '
               'WHERE e.company_id=?')
        params = [cid]
        if status:
            sql += ' AND COALESCE(p.employment_status,?)=?'
            params += ['active', status]
        if unit_id:
            sql += ' AND p.unit_id=?'
            params.append(positive_id(unit_id))
        sql += ' ORDER BY e.name,e.id'
        rows = ctx.rows(sql, params)
        result = []
        for row in rows:
            if not access.employee_allowed(ctx.c, g.user, row):
                continue
            if q and q.casefold() not in ' '.join(str(row.get(k) or '') for k in
                                                  ('name', 'code', 'department', 'job', 'national_id', 'phone')).casefold():
                continue
            row['employment_status_label'] = STATUS_LABELS.get(row.get('employment_status'), row.get('employment_status') or 'فعال')
            result.append(row)
        return jsonify(result)

    @ctx.route('personnel', methods=('POST',), permission='personnel.edit')
    def hrm_personnel_create():
        cid = g.hrm_cid
        body = ctx.body()
        emp = parse_values(ctx, body, EMPLOYEE_FIELDS, required=('code', 'name'))
        profile = parse_values(ctx, body, PROFILE_FIELDS)
        _check_references(ctx, profile, cid)
        _sync_legacy_names(ctx, emp, profile, cid)
        if profile.get('employment_status') and profile['employment_status'] not in STATUS_LABELS:
            raise ValueError('وضعیت اشتغال معتبر نیست')
        emp.setdefault('active', 1)
        keys = list(emp)
        cur = ctx.c.execute(
            f'INSERT INTO employees (company_id,{",".join(keys)}) VALUES ({",".join("?" for _ in range(len(keys)+1))})',
            [cid] + [emp[k] for k in keys])
        eid = cur.lastrowid
        profile['employee_id'] = eid
        profile['company_id'] = cid
        profile['updated_by'] = g.user['id']
        profile['updated_at'] = ctx.now()
        pkeys = list(profile)
        ctx.c.execute(
            f'INSERT INTO personnel_profiles ({",".join(pkeys)}) VALUES ({",".join("?" for _ in pkeys)})',
            [profile[k] for k in pkeys])
        ctx.history(eid, 'personnel', eid, [(k, '', v) for k, v in emp.items()])
        ctx.audit_log('create_personnel', f'{emp["code"]} / {emp["name"]}')
        ctx.c.commit()
        return jsonify(id=eid), 201

    @ctx.route('personnel/<int:eid>', methods=('PUT',), permission='personnel.edit')
    def hrm_personnel_update(eid):
        cid = g.hrm_cid
        employee = ctx.employee(eid, cid)
        body = ctx.body()
        emp = parse_values(ctx, body, EMPLOYEE_FIELDS)
        profile = parse_values(ctx, body, PROFILE_FIELDS)
        _check_references(ctx, profile, cid)
        _sync_legacy_names(ctx, emp, profile, cid)
        if profile.get('employment_status') and profile['employment_status'] not in STATUS_LABELS:
            raise ValueError('وضعیت اشتغال معتبر نیست')
        if not emp and not profile:
            raise ValueError('تغییری ارسال نشده است')
        old = ctx.c.execute('SELECT * FROM personnel_profiles WHERE employee_id=?', (eid,)).fetchone()
        changes = []
        if emp:
            for key, value in emp.items():
                if employee[key] != value:
                    changes.append((key, employee[key], value))
            ctx.c.execute('UPDATE employees SET ' + ','.join(k + '=?' for k in emp) + ' WHERE id=?',
                          list(emp.values()) + [eid])
        if profile:
            for key, value in profile.items():
                if key in ('updated_by', 'updated_at'):
                    continue
                previous = old[key] if old is not None else ''
                if previous != value:
                    changes.append((key, previous, value))
            if old is None:
                profile['employee_id'] = eid
                profile['company_id'] = cid
            profile['updated_by'] = g.user['id']
            profile['updated_at'] = ctx.now()
            if old is None:
                pkeys = list(profile)
                ctx.c.execute(f'INSERT INTO personnel_profiles ({",".join(pkeys)}) VALUES ({",".join("?" for _ in pkeys)})',
                              [profile[k] for k in pkeys])
            else:
                ctx.c.execute('UPDATE personnel_profiles SET ' + ','.join(k + '=?' for k in profile) + ' WHERE employee_id=?',
                              list(profile.values()) + [eid])
        if changes:
            ctx.history(eid, 'personnel', eid, changes)
        ctx.audit_log('update_personnel', json.dumps([c[0] for c in changes], ensure_ascii=False))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('personnel/<int:eid>', permission='personnel.view')
    def hrm_personnel_detail(eid):
        cid = g.hrm_cid
        employee = ctx.employee(eid, cid)
        profile = _rowdict(ctx.c.execute('SELECT * FROM personnel_profiles WHERE employee_id=?', (eid,)).fetchone())
        history = ctx.rows('SELECT h.*,u.username changed_by_name FROM employee_history h '
                           'LEFT JOIN users u ON u.id=h.changed_by WHERE h.employee_id=? ORDER BY h.id DESC LIMIT 200', (eid,))
        confidential = access.granting_roles(ctx.c, g.user)
        detail = {
            'employee': _rowdict(employee), 'profile': profile, 'history': history,
            'confidential': confidential,
            'status_labels': STATUS_LABELS,
            'training': {
                'records': ctx.rows(
                    'SELECT r.id,r.level,r.score,r.result,c.title course_title,c.date course_date,'
                    's.title skill_title FROM records r JOIN courses c ON c.id=r.course_id '
                    'JOIN skills s ON s.id=c.skill_id WHERE r.employee_id=? AND r.company_id=? '
                    'ORDER BY c.date DESC LIMIT 100', (eid, cid)),
                'certificates': ctx.rows(
                    'SELECT id,title,issuer,issued,expires,filename FROM certificates '
                    'WHERE employee_id=? AND company_id=? ORDER BY id DESC', (eid, cid)),
                'assessments': ctx.rows(
                    'SELECT a.level,s.title skill_title,s.code skill_code FROM assessments a '
                    'JOIN skills s ON s.id=a.skill_id WHERE a.employee_id=? AND a.company_id=? '
                    'ORDER BY s.title', (eid, cid)),
            },
            'performance_trend': ctx.rows(
                'SELECT p.id period_id,p.title period_title,ROUND(AVG(s.score),2) average,COUNT(s.id) indicators '
                'FROM performance_periods p JOIN performance_scores s ON s.period_id=p.id '
                'WHERE s.employee_id=? AND p.company_id=? GROUP BY p.id ORDER BY p.start_date', (eid, cid)),
            'attendance_summary': ctx.rows(
                'SELECT kind,COUNT(*) count,COALESCE(SUM(minutes),0) minutes FROM attendance_records '
                'WHERE employee_id=? AND company_id=? GROUP BY kind', (eid, cid)),
        }
        if 'documents' in confidential:
            detail['documents'] = ctx.rows(
                'SELECT id,entity_type,entity_id,category,title,filename,mime,confidential,created_at '
                'FROM hrm_documents WHERE employee_id=? AND company_id=? ORDER BY id DESC', (eid, cid))
            detail['contracts'] = ctx.rows(
                'SELECT id,type,number,start_date,end_date,status,renewal_count FROM contracts '
                'WHERE employee_id=? AND company_id=? ORDER BY start_date DESC', (eid, cid))
            detail['disciplinary'] = ctx.rows(
                'SELECT id,kind,date,reason,decision FROM disciplinary_records '
                'WHERE employee_id=? AND company_id=? ORDER BY date DESC', (eid, cid))
            detail['loans'] = ctx.rows(
                'SELECT id,kind,amount,request_date,status,paid_amount FROM loans '
                'WHERE employee_id=? AND company_id=? ORDER BY request_date DESC', (eid, cid))
        else:
            detail['documents'] = []
            detail['contracts'] = []
            detail['disciplinary'] = []
            detail['loans'] = []
            detail['locked'] = True
        return jsonify(detail)

    @ctx.route('documents', permission='documents.view')
    def hrm_documents_list():
        eid = request.args.get('employee_id')
        entity = request.args.get('entity_type') or ''
        entity_id = request.args.get('entity_id')
        sql = ('SELECT id,employee_id,entity_type,entity_id,category,title,filename,mime,confidential,created_at '
               'FROM hrm_documents WHERE company_id=?')
        params = [g.hrm_cid]
        if eid:
            params.append(positive_id(eid))
            sql += ' AND employee_id=?'
        if entity:
            sql += ' AND entity_type=?'
            params.append(entity)
        if entity_id:
            sql += ' AND entity_id=?'
            params.append(positive_id(entity_id))
        confidential = 'documents' in access.granting_roles(ctx.c, g.user)
        if not confidential:
            sql += ' AND confidential=0'
        return jsonify(ctx.rows(sql + ' ORDER BY id DESC', params))

    @ctx.route('documents', methods=('POST',), permission='documents.edit')
    def hrm_documents_create():
        body = ctx.form_or_json()
        eid = positive_id(body.get('employee_id'))
        employee = ctx.employee(eid, g.hrm_cid)
        is_confidential = 1 if body.get('confidential', 1) in (1, True, '1', 'on') else 0
        if is_confidential:
            access.require(ctx.c, g.user, g.hrm_cid, confidential='documents')
        filename, mime, payload = ctx.upload()
        cur = ctx.c.execute(
            'INSERT INTO hrm_documents(company_id,employee_id,entity_type,entity_id,category,title,'
            'filename,mime,content,confidential,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (g.hrm_cid, eid, ctx.text(body, 'entity_type', maximum=40),
             None if not body.get('entity_id') else positive_id(body['entity_id']),
             ctx.text(body, 'category', maximum=100), ctx.text(body, 'title', required=True),
             filename, mime, payload, is_confidential, g.user['id']))
        ctx.history(eid, 'document', cur.lastrowid, [('title', '', body['title'])])
        ctx.audit_log('create_document', str(cur.lastrowid))
        ctx.c.commit()
        return jsonify(id=cur.lastrowid), 201

    @ctx.route('documents/<int:did>/download', permission='documents.view')
    def hrm_documents_download(did):
        row = ctx.c.execute('SELECT * FROM hrm_documents WHERE id=? AND company_id=?',
                            (positive_id(did), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('سند یافت نشد', 404)
        if row['confidential']:
            access.require(ctx.c, g.user, g.hrm_cid, confidential='documents')
        ctx.employee(row['employee_id'], g.hrm_cid)
        ctx.audit_log('download_document', str(did))
        ctx.c.commit()
        return Response(bytes(row['content']), mimetype=row['mime'],
                        headers={'Content-Disposition': f'attachment; filename="{row["filename"]}"',
                                 'X-Content-Type-Options': 'nosniff'})

    @ctx.route('documents/<int:did>', methods=('DELETE',), permission='documents.edit')
    def hrm_documents_delete(did):
        row = ctx.c.execute('SELECT * FROM hrm_documents WHERE id=? AND company_id=?',
                            (positive_id(did), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('سند یافت نشد', 404)
        if row['confidential']:
            access.require(ctx.c, g.user, g.hrm_cid, confidential='documents')
        ctx.c.execute('DELETE FROM hrm_documents WHERE id=?', (did,))
        ctx.history(row['employee_id'], 'document', did, [('deleted', row['title'], '')])
        ctx.audit_log('delete_document', str(did))
        ctx.c.commit()
        return ctx.ok()
