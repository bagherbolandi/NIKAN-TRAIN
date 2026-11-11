"""Employee cartable, configurable request types and a simple approval workflow."""
import json

from flask import g, jsonify, request

from ..validation import positive_id
from . import access, dates
from .crud import Crud
from .settings import setting_number
from . import attendance as attendance_module
from . import benefits

MATERIALIZERS = {'leave', 'mission', 'loan', 'welfare', 'reservation', 'employment_letter'}


def _type(ctx, cid, type_id):
    row = ctx.c.execute('SELECT * FROM request_types WHERE id=? AND (company_id=0 OR company_id=?)',
                        (positive_id(type_id), cid)).fetchone()
    if not row or not row['active']:
        raise ValueError('نوع درخواست معتبر یا فعال نیست')
    return row


def _materialize(ctx, cid, request_row, request_type):
    code = request_type['code']
    payload = json.loads(request_row['payload'])
    request_id = request_row['id']
    if code in ('leave', 'mission'):
        body = dict(payload)
        body['kind'] = 'mission' if code == 'mission' else 'leave'
        body['employee_id'] = request_row['employee_id']
        rid = attendance_module.insert_attendance(ctx, cid, body, request_id=request_id, approved=True)
        return 'attendance', rid
    if code == 'loan':
        body = dict(payload); body['employee_id'] = request_row['employee_id']
        return 'loan', benefits.create_loan(ctx, cid, body, request_id)
    if code == 'welfare':
        body = dict(payload); body['employee_id'] = request_row['employee_id']
        return 'welfare_usage', benefits.create_welfare_usage(ctx, cid, body, request_id)
    if code == 'reservation':
        body = dict(payload); body['employee_id'] = request_row['employee_id']
        return 'reservation', benefits.create_reservation(ctx, cid, body, request_id)
    if code == 'employment_letter':
        employee = ctx.c.execute('SELECT * FROM employees WHERE id=?', (request_row['employee_id'],)).fetchone()
        company = ctx.c.execute('SELECT name FROM companies WHERE id=?', (cid,)).fetchone()
        profile = ctx.c.execute('SELECT start_date,position_id FROM personnel_profiles WHERE employee_id=?',
                                (employee['id'],)).fetchone()
        text = (f'گواهی اشتغال\n\nشرکت: {company["name"]}\n'
                f'نام و نام خانوادگی: {employee["name"]}\nکد پرسنلی: {employee["code"]}\n'
                f'واحد سازمانی: {employee["department"]}\nسمت: {employee["job"]}\n'
                f'تاریخ شروع همکاری: {dates.iso_to_jalali(profile["start_date"]) if profile and profile["start_date"] else "ثبت نشده"}\n'
                f'تاریخ صدور: {dates.iso_to_jalali(dates.today_iso())}\n\n'
                'این گواهی به صورت خودکار توسط سامانه یکپارچه منابع انسانی نیکان صادر شده است.\n')
        cur = ctx.c.execute(
            'INSERT INTO hrm_documents(company_id,employee_id,entity_type,entity_id,category,title,'
            'filename,mime,content,confidential,created_by) VALUES (?,?,?,?,?,?,?,?,?,1,?)',
            (cid, employee['id'], 'request', request_id, 'گواهی اشتغال',
             f'employment-letter-{request_id}.txt', 'text/plain; charset=utf-8',
             text.encode('utf-8'), g.user['id']))
        return 'document', cur.lastrowid
    return None, None


def register(ctx):
    Crud(ctx, 'request-types', 'request_types',
         {'code': 'text', 'name': 'text', 'approver_role': 'text',
          'confidential': 'bool', 'active': 'bool'},
         required=('code', 'name'), view='requests.view', edit='settings.edit',
         global_table=True, label='نوع درخواست').register()

    @ctx.route('requests', permission='requests.view')
    def hrm_requests_list():
        cid = g.hrm_cid
        rows = ctx.rows(
            'SELECT r.*,e.name employee_name,e.code employee_code,t.name type_name,t.code type_code,'
            't.approver_role,p.unit_id FROM requests r JOIN employees e ON e.id=r.employee_id '
            'JOIN request_types t ON t.id=r.type_id '
            'LEFT JOIN personnel_profiles p ON p.employee_id=e.id WHERE r.company_id=? '
            'ORDER BY r.created_at DESC,r.id DESC LIMIT 1000', (cid,))
        visible = [r for r in rows if access.employee_allowed(ctx.c, g.user, {
            'id': r['employee_id'], 'unit_id': r.get('unit_id')})]
        for row in visible:
            row['payload'] = json.loads(row['payload'])
        can_approve = 'requests.approve' in access.effective_permissions(ctx.c, g.user)
        pending = [r for r in visible if r['status'] == 'submitted'] if can_approve else []
        mine = [r for r in visible if g.user['employee_id'] and
                r['employee_id'] == g.user['employee_id']]
        return jsonify(rows=visible, pending=pending, mine=mine, can_approve=can_approve)

    @ctx.route('requests', methods=('POST',), permission='requests.create')
    def hrm_requests_create():
        cid = g.hrm_cid
        body = ctx.body()
        eid = positive_id(body.get('employee_id'))
        if g.user['hrm_scope'] == 'self' and g.user['employee_id'] != eid:
            raise PermissionError(' فقط می‌توانید برای خود درخواست ثبت کنید')
        ctx.employee(eid, cid)
        request_type = _type(ctx, cid, body.get('type_id'))
        payload = body.get('payload') or {}
        if not isinstance(payload, dict):
            raise ValueError('اطلاعات درخواست معتبر نیست')
        title = ctx.text(body, 'title', maximum=200) or request_type['name']
        approval = setting_number(ctx.c, cid, 'request.approval_enabled', 1)
        status = 'submitted' if approval else 'approved'
        cur = ctx.c.execute(
            'INSERT INTO requests(company_id,employee_id,type_id,title,payload,status,created_by,submitted_at) '
            'VALUES (?,?,?,?,?,?,?,?)',
            (cid, eid, request_type['id'], title, json.dumps(payload, ensure_ascii=False),
             status, g.user['id'], ctx.now()))
        rid = cur.lastrowid
        ctx.c.execute(
            'INSERT INTO request_steps(company_id,request_id,step_no,approver_role,status) VALUES (?,?,1,?,?)',
            (cid, rid, request_type['approver_role'], 'pending' if approval else 'skipped'))
        related_type, related_id = None, None
        if not approval and request_type['code'] in MATERIALIZERS:
            related_type, related_id = _materialize(ctx, cid, {'id': rid, 'employee_id': eid,
                                                              'payload': json.dumps(payload, ensure_ascii=False)},
                                                    request_type)
            ctx.c.execute('UPDATE requests SET decided_by=?,decided_at=?,related_type=?,related_id=? WHERE id=?',
                          (g.user['id'], ctx.now(), related_type or '', related_id, rid))
        ctx.history(eid, 'request', rid, [('created', '', request_type['code'])])
        ctx.audit_log('create_request', f'{rid}:{request_type["code"]}')
        ctx.c.commit()
        return jsonify(id=rid, status=status, related_type=related_type, related_id=related_id), 201

    @ctx.route('requests/<int:rid>', permission='requests.view')
    def hrm_requests_detail(rid):
        row = ctx.one('SELECT r.*,t.name type_name,t.code type_code,t.approver_role,e.name employee_name '
                      'FROM requests r JOIN request_types t ON t.id=r.type_id '
                      'JOIN employees e ON e.id=r.employee_id WHERE r.id=? AND r.company_id=?',
                      (positive_id(rid), g.hrm_cid))
        if not row:
            return ctx.error('درخواست یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        row['payload'] = json.loads(row['payload'])
        row['steps'] = ctx.rows('SELECT * FROM request_steps WHERE request_id=? ORDER BY step_no', (rid,))
        return jsonify(row)

    @ctx.route('requests/<int:rid>/decision', methods=('POST',), permission='requests.approve')
    def hrm_requests_decision(rid):
        cid = g.hrm_cid
        row = ctx.c.execute('SELECT r.*,t.code type_code,t.approver_role FROM requests r '
                            'JOIN request_types t ON t.id=r.type_id WHERE r.id=? AND r.company_id=?',
                            (positive_id(rid), cid)).fetchone()
        if not row:
            return ctx.error('درخواست یافت نشد', 404)
        if row['status'] != 'submitted':
            raise ValueError('این درخواست قبلاً بررسی شده است')
        ctx.employee(row['employee_id'], cid)
        perms = access.effective_permissions(ctx.c, g.user)
        if (g.user['hrm_role'] != row['approver_role'] and
                'users.permissions' not in perms and g.user['role'] != 'admin'):
            raise PermissionError('تأیید این درخواست به نقش مسئول مربوط واگذار شده است')
        body = ctx.body()
        decision = body.get('status')
        if decision not in ('approved', 'rejected'):
            raise ValueError('تصمیم معتبر نیست')
        note = ctx.text(body, 'note', maximum=1000)
        related_type, related_id = None, None
        if decision == 'approved' and row['type_code'] in MATERIALIZERS:
            request_type = {'id': row['type_id'], 'code': row['type_code']}
            related_type, related_id = _materialize(ctx, cid, dict(row), request_type)
        ctx.c.execute(
            'UPDATE requests SET status=?,decided_by=?,decided_at=?,decision_note=?,related_type=?,related_id=? WHERE id=?',
            (decision, g.user['id'], ctx.now(), note, related_type or '', related_id, rid))
        ctx.c.execute('UPDATE request_steps SET status=?,approver_user_id=?,decided_at=?,note=? '
                      'WHERE request_id=? AND step_no=1',
                      (decision, g.user['id'], ctx.now(), note, rid))
        ctx.history(row['employee_id'], 'request', rid, [('decision', 'submitted', decision)])
        ctx.audit_log('request_decision', f'{rid}:{decision}')
        ctx.c.commit()
        return ctx.ok(related_type=related_type, related_id=related_id)

    @ctx.route('requests/<int:rid>/cancel', methods=('POST',), permission='requests.create')
    def hrm_requests_cancel(rid):
        row = ctx.c.execute('SELECT * FROM requests WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('درخواست یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        if row['status'] != 'submitted':
            raise ValueError('فقط درخواست‌های در انتظار تأیید قابل لغو هستند')
        ctx.c.execute("UPDATE requests SET status='cancelled' WHERE id=?", (rid,))
        ctx.c.execute("UPDATE request_steps SET status='skipped' WHERE request_id=?", (rid,))
        ctx.audit_log('cancel_request', str(rid))
        ctx.c.commit()
        return ctx.ok()
