"""Contracts, renewals, expiry alerts, personnel actions and settlement."""
import json

from flask import g, jsonify, request

from ..validation import positive_id, number
from . import access, dates
from .crud import Crud, parse_values
from .settings import setting_number

ACTION_KINDS = ('decree', 'promotion', 'transfer', 'termination', 'settlement', 'other')
ACTION_LABELS = {
    'decree': 'حکم کارگزینی', 'promotion': 'تغییر سمت / ارتقا', 'transfer': 'انتقال',
    'termination': 'قطع همکاری', 'settlement': 'تسویه‌حساب', 'other': 'سایر',
}


def _decorate(rows, today=None):
    today = today or dates.today_iso()
    for row in rows:
        end = row.get('end_date')
        row['days_to_expiry'] = dates.days_between(today, end) if end else None
        row['expiring'] = bool(end and row.get('status') == 'active' and 0 <= row['days_to_expiry'] <= 30)
    return rows


def register(ctx):
    Crud(ctx, 'contract-types', 'contract_types',
         {'name': 'text', 'default_months': 'int', 'renewal_limit': 'int', 'alert_days': 'int', 'active': 'bool'},
         required=('name',), view='contract.view', edit='settings.edit', global_table=True,
         label='نوع قرارداد').register()

    @ctx.route('contracts', permission='contract.view', confidential='contract')
    def hrm_contracts_list():
        cid = g.hrm_cid
        sql = ('SELECT c.*,e.name employee_name,e.code employee_code,t.name type_name '
               'FROM contracts c JOIN employees e ON e.id=c.employee_id '
               'LEFT JOIN contract_types t ON t.id=c.contract_type_id WHERE c.company_id=?')
        params = [cid]
        if request.args.get('status'):
            sql += ' AND c.status=?'
            params.append(request.args['status'])
        if request.args.get('employee_id'):
            sql += ' AND c.employee_id=?'
            params.append(positive_id(request.args['employee_id']))
        sql += ' ORDER BY c.start_date DESC,c.id DESC'
        rows = ctx.rows(sql, params)
        for row in rows:
            end = row.get('end_date')
            row['days_to_expiry'] = dates.days_between(dates.today_iso(), end) if end else None
        return jsonify(rows)

    @ctx.route('contracts', methods=('POST',), permission='contract.edit', confidential='contract')
    def hrm_contracts_create():
        cid = g.hrm_cid
        body = ctx.body()
        eid = positive_id(body.get('employee_id'))
        ctx.employee(eid, cid)
        start = ctx.required_iso(body.get('start_date'), 'تاریخ شروع')
        end = ctx.required_iso(body.get('end_date'), 'تاریخ پایان')
        if end < start:
            raise ValueError('تاریخ پایان نمی‌تواند پیش از تاریخ شروع باشد')
        type_id = None
        if body.get('contract_type_id'):
            type_id = ctx.reference('contract_types', body['contract_type_id'], cid, global_ok=True)
        status = body.get('status') or 'active'
        if status not in ('draft', 'active', 'expired', 'terminated', 'renewed'):
            raise ValueError('وضعیت قرارداد معتبر نیست')
        kind = ctx.text(body, 'type', maximum=80) or 'permanent'
        number_value = ctx.text(body, 'number', maximum=80)
        note = ctx.text(body, 'note', maximum=2000)
        cur = ctx.c.execute(
            'INSERT INTO contracts(company_id,employee_id,contract_type_id,type,number,start_date,end_date,'
            'status,renewal_count,wage,note,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
            (cid, eid, type_id, kind, number_value, start, end, status, 0,
             ctx.amount(body, 'wage'), note, g.user['id']))
        ctx.history(eid, 'contract', cur.lastrowid, [('created', '', number_value or kind)])
        ctx.audit_log('create_contract', str(cur.lastrowid))
        ctx.c.commit()
        return jsonify(id=cur.lastrowid), 201

    @ctx.route('contracts/<int:rid>', methods=('PUT',), permission='contract.edit', confidential='contract')
    def hrm_contracts_update(rid):
        cid = g.hrm_cid
        row = ctx.c.execute('SELECT * FROM contracts WHERE id=? AND company_id=?',
                            (positive_id(rid), cid)).fetchone()
        if not row:
            return ctx.error('قرارداد یافت نشد', 404)
        ctx.employee(row['employee_id'], cid)
        body = ctx.body()
        changes = {}
        if 'start_date' in body:
            changes['start_date'] = ctx.required_iso(body['start_date'], 'تاریخ شروع')
        if 'end_date' in body:
            changes['end_date'] = ctx.required_iso(body['end_date'], 'تاریخ پایان')
        if changes.get('end_date', row['end_date']) < changes.get('start_date', row['start_date']):
            raise ValueError('تاریخ پایان نمی‌تواند پیش از تاریخ شروع باشد')
        for key in ('number', 'type', 'note', 'status'):
            if key in body:
                changes[key] = ctx.text(body, key, maximum=2000 if key == 'note' else 100)
        if 'wage' in body:
            changes['wage'] = ctx.amount(body, 'wage')
        if 'contract_type_id' in body:
            changes['contract_type_id'] = (None if not body['contract_type_id'] else
                                           ctx.reference('contract_types', body['contract_type_id'], cid, global_ok=True))
        if not changes:
            raise ValueError('تغییری ارسال نشده است')
        ctx.c.execute('UPDATE contracts SET ' + ','.join(k + '=?' for k in changes) + ' WHERE id=?',
                      list(changes.values()) + [rid])
        ctx.history(row['employee_id'], 'contract', rid,
                    [(k, row[k], v) for k, v in changes.items() if row[k] != v])
        ctx.audit_log('update_contract', str(rid))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('contracts/<int:rid>/renew', methods=('POST',), permission='contract.edit', confidential='contract')
    def hrm_contracts_renew(rid):
        cid = g.hrm_cid
        row = ctx.c.execute('SELECT * FROM contracts WHERE id=? AND company_id=?',
                            (positive_id(rid), cid)).fetchone()
        if not row:
            return ctx.error('قرارداد یافت نشد', 404)
        ctx.employee(row['employee_id'], cid)
        body = ctx.body()
        new_end = ctx.required_iso(body.get('end_date'), 'تاریخ پایان تمدید')
        start = ctx.iso(body.get('start_date'), 'تاریخ شروع تمدید') or row['end_date']
        if new_end <= start or new_end <= row['end_date']:
            raise ValueError('پایان تمدید باید پس از پایان قرارداد فعلی باشد')
        limit = setting_number(ctx.c, cid, 'contract.renewal_limit', 3)
        type_row = ctx.c.execute('SELECT renewal_limit FROM contract_types WHERE id=?',
                                 (row['contract_type_id'],)).fetchone() if row['contract_type_id'] else None
        if type_row and type_row['renewal_limit']:
            limit = type_row['renewal_limit']
        if row['renewal_count'] >= limit:
            raise ValueError('سقف دفعات تمدید تعیین‌شده در ضوابط سازمان تکمیل شده است')
        with ctx.c:
            ctx.c.execute('BEGIN IMMEDIATE')
            cur = ctx.c.execute(
                'INSERT INTO contracts(company_id,employee_id,contract_type_id,type,number,start_date,end_date,'
                'status,renewal_of,renewal_count,wage,note,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (cid, row['employee_id'], row['contract_type_id'], row['type'],
                 ctx.text(body, 'number', maximum=80) or row['number'], start, new_end, 'active',
                 rid, row['renewal_count'] + 1, row['wage'], ctx.text(body, 'note', maximum=2000), g.user['id']))
            new_id = cur.lastrowid
            ctx.c.execute(
                'INSERT INTO contract_renewals(company_id,contract_id,previous_end,new_end,renewal_date,number,note,created_by) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (cid, rid, row['end_date'], new_end, dates.today_iso(),
                 ctx.text(body, 'number', maximum=80), ctx.text(body, 'note', maximum=2000), g.user['id']))
            ctx.c.execute("UPDATE contracts SET status='renewed' WHERE id=?", (rid,))
            ctx.history(row['employee_id'], 'contract', new_id,
                        [('renewed_from', rid, new_id), ('end_date', row['end_date'], new_end)])
            ctx.audit_log('renew_contract', f'{rid}->{new_id}')
        return jsonify(id=new_id), 201

    @ctx.route('contract-alerts', permission='contract.view', confidential='contract')
    def hrm_contract_alerts():
        cid = g.hrm_cid
        days = setting_number(ctx.c, cid, 'contract.alert_days', 30)
        today = dates.today_iso()
        until = dates.add_days(today, days)
        rows = ctx.rows(
            'SELECT c.*,e.name employee_name,e.code employee_code,t.name type_name '
            'FROM contracts c JOIN employees e ON e.id=c.employee_id '
            'LEFT JOIN contract_types t ON t.id=c.contract_type_id '
            "WHERE c.company_id=? AND c.status='active' AND c.end_date BETWEEN ? AND ? ORDER BY c.end_date",
            (cid, today, until))
        for row in rows:
            row['days_to_expiry'] = dates.days_between(today, row['end_date'])
        return jsonify(rows)

    @ctx.route('personnel-actions', permission='contract.view', confidential='contract')
    def hrm_actions_list():
        return jsonify(ctx.rows(
            'SELECT a.*,e.name employee_name,e.code employee_code FROM personnel_actions a '
            'JOIN employees e ON e.id=a.employee_id WHERE a.company_id=? ORDER BY a.effective_date DESC,a.id DESC',
            (g.hrm_cid,)))

    @ctx.route('personnel-actions', methods=('POST',), permission='contract.edit', confidential='contract')
    def hrm_actions_create():
        cid = g.hrm_cid
        body = ctx.body()
        eid = positive_id(body.get('employee_id'))
        ctx.employee(eid, cid)
        kind = ctx.text(body, 'kind', required=True, maximum=40)
        if kind not in ACTION_KINDS:
            raise ValueError('نوع اقدام کارگزینی معتبر نیست')
        effective = ctx.required_iso(body.get('effective_date'), 'تاریخ اجرا')
        fields = parse_values(ctx, body, {
            'from_value': 'text', 'to_value': 'text', 'reason': 'text',
            'decision_no': 'text', 'settlement_amount': 'float', 'settlement_date': 'date',
        })
        cur = ctx.c.execute(
            'INSERT INTO personnel_actions(company_id,employee_id,kind,effective_date,from_value,to_value,'
            'reason,decision_no,settlement_amount,settlement_date,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (cid, eid, kind, effective, fields.get('from_value', ''), fields.get('to_value', ''),
             fields.get('reason', ''), fields.get('decision_no', ''), fields.get('settlement_amount') or 0,
             fields.get('settlement_date'), g.user['id']))
        if kind == 'termination':
            ctx.c.execute("UPDATE personnel_profiles SET employment_status='terminated',end_date=? WHERE employee_id=?",
                          (effective, eid))
            ctx.c.execute('UPDATE employees SET active=0 WHERE id=?', (eid,))
        elif kind in ('transfer', 'promotion') and fields.get('to_value'):
            ctx.c.execute('UPDATE employees SET job=?,department=? WHERE id=?',
                          (fields['to_value'], ctx.c.execute(
                              'SELECT department FROM employees WHERE id=?', (eid,)).fetchone()['department'], eid))
        ctx.history(eid, 'action', cur.lastrowid, [(k, '', fields.get(k, '')) for k in fields])
        ctx.audit_log('create_personnel_action', f'{kind}:{eid}')
        ctx.c.commit()
        return jsonify(id=cur.lastrowid), 201

    @ctx.route('personnel-actions/<int:rid>', methods=('DELETE',), permission='contract.edit', confidential='contract')
    def hrm_actions_delete(rid):
        row = ctx.c.execute('SELECT * FROM personnel_actions WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رکورد یافت نشد', 404)
        ctx.c.execute('DELETE FROM personnel_actions WHERE id=?', (rid,))
        ctx.history(row['employee_id'], 'action', rid, [('deleted', row['kind'], '')])
        ctx.audit_log('delete_personnel_action', str(rid))
        ctx.c.commit()
        return ctx.ok()
