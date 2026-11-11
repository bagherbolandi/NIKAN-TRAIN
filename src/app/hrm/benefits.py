"""Loans, bank introductions, welfare services, accommodations and reservations."""
import json

from flask import g, jsonify, request

from ..validation import positive_id, number
from . import access, dates
from .crud import Crud, parse_values
from .settings import setting_number

LOAN_KINDS = ('internal', 'bank')
LOAN_STATUSES = ('submitted', 'approved', 'rejected', 'active', 'settled', 'cancelled')


def _service(ctx, cid, service_id):
    row = ctx.c.execute('SELECT * FROM welfare_services WHERE id=? AND (company_id=0 OR company_id=?)',
                        (positive_id(service_id), cid)).fetchone()
    if not row:
        raise ValueError('خدمت رفاهی معتبر نیست')
    return row


def create_loan(ctx, cid, body, request_id=None):
    eid = positive_id(body.get('employee_id'))
    ctx.employee(eid, cid)
    kind = body.get('kind') or 'internal'
    if kind not in LOAN_KINDS:
        raise ValueError('نوع وام معتبر نیست')
    amount = number(body.get('amount'), minimum=1, maximum=10 ** 12)
    maximum = setting_number(ctx.c, cid, 'loan.max_amount', 500000000)
    if amount > maximum:
        raise ValueError(f'مبلغ وام بیش از سقف تعیین‌شده ({maximum}) است')
    if kind == 'internal':
        active = ctx.c.execute(
            "SELECT COUNT(*) FROM loans WHERE employee_id=? AND kind='internal' AND status IN ('submitted','approved','active')",
            (eid,)).fetchone()[0]
        if active >= setting_number(ctx.c, cid, 'loan.max_active', 1):
            raise ValueError('سقف تعداد وام‌های فعال هم‌زمان تکمیل شده است')
    values = parse_values(ctx, body, {
        'request_date': 'date', 'approval_date': 'date', 'installments': 'int',
        'bank_name': 'text', 'letter_no': 'text', 'letter_status': 'text', 'note': 'text',
    }, required=('request_date',))
    cur = ctx.c.execute(
        'INSERT INTO loans(company_id,employee_id,kind,amount,request_date,approval_date,status,installments,'
        'bank_name,letter_no,letter_status,note,request_id,created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (cid, eid, kind, amount, values['request_date'], values.get('approval_date'), 'submitted',
         values.get('installments') or 0, values.get('bank_name', ''), values.get('letter_no', ''),
         values.get('letter_status', ''), values.get('note', ''), request_id, g.user['id']))
    ctx.history(eid, 'loan', cur.lastrowid, [('created', '', str(amount))])
    ctx.audit_log('create_loan', str(cur.lastrowid))
    return cur.lastrowid


def create_welfare_usage(ctx, cid, body, request_id=None):
    eid = positive_id(body.get('employee_id'))
    ctx.employee(eid, cid)
    service = _service(ctx, cid, body.get('service_id'))
    values = parse_values(ctx, body, {
        'date': 'date', 'quantity': 'int', 'value': 'float', 'note': 'text',
    }, required=('date',))
    quantity = values.get('quantity') or 1
    if quantity < 1:
        raise ValueError('تعداد باید حداقل ۱ باشد')
    limit = service['max_per_period'] or setting_number(ctx.c, cid, 'welfare.max_per_month', 2)
    if limit:
        period = service['period']
        if period == 'month':
            key = values['date'][:7]
        elif period == 'year':
            key = values['date'][:4]
        else:
            key = ''
        used = 0
        for row in ctx.c.execute(
            'SELECT date,quantity FROM welfare_usages WHERE employee_id=? AND service_id=?',
            (eid, service['id'])):
            row_key = row['date'][:7] if period == 'month' else row['date'][:4] if period == 'year' else ''
            if row_key == key:
                used += row['quantity']
        if used + quantity > limit:
            raise ValueError(f'سقف استفاده از این خدمت ({limit}) در دوره تعیین‌شده تکمیل شده است')
    cur = ctx.c.execute(
        'INSERT INTO welfare_usages(company_id,employee_id,service_id,date,quantity,value,note,request_id,created_by) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (cid, eid, service['id'], values['date'], quantity, values.get('value') or 0,
         values.get('note', ''), request_id, g.user['id']))
    ctx.history(eid, 'welfare', cur.lastrowid, [('created', '', service['name'])])
    ctx.audit_log('create_welfare_usage', str(cur.lastrowid))
    return cur.lastrowid


def create_reservation(ctx, cid, body, request_id=None):
    eid = positive_id(body.get('employee_id'))
    ctx.employee(eid, cid)
    accommodation_id = positive_id(body.get('accommodation_id'))
    place = ctx.c.execute(
        'SELECT * FROM accommodations WHERE id=? AND (company_id IS NULL OR company_id=0 OR company_id=?)',
        (accommodation_id, cid)).fetchone()
    if not place or not place['active']:
        raise ValueError('اقامتگاه معتبر یا فعال نیست')
    values = parse_values(ctx, body, {
        'from_date': 'date', 'to_date': 'date', 'guests': 'int', 'note': 'text',
    }, required=('from_date', 'to_date'))
    start, end = values['from_date'], values['to_date']
    if end < start:
        raise ValueError('تاریخ پایان رزرو نمی‌تواند پیش از شروع باشد')
    nights = dates.days_between(start, end) + 1
    max_nights = place['max_nights'] or setting_number(ctx.c, cid, 'reservation.max_nights', 3)
    if nights > max_nights:
        raise ValueError(f'تعداد شب‌ها بیش از سقف تعیین‌شده ({max_nights}) است')
    if dates.days_between(dates.today_iso(), start) < setting_number(ctx.c, cid, 'reservation.min_lead_days', 2):
        raise ValueError('رزرو باید با حداقل فاصله تعیین‌شده در ضوابط ثبت شود')
    guests = values.get('guests') or 1
    if guests > place['capacity']:
        raise ValueError('تعداد نفرات بیش از ظرفیت اقامتگاه است')
    year = dates.iso_to_jalali(start)[:4]
    used = 0
    for row in ctx.c.execute(
        "SELECT from_date FROM reservations WHERE employee_id=? AND status IN ('submitted','approved','completed')",
        (eid,)):
        if dates.iso_to_jalali(row['from_date'])[:4] == year:
            used += 1
    if used >= setting_number(ctx.c, cid, 'reservation.max_per_year', 4):
        raise ValueError('سقف تعداد رزرو سالانه این فرد تکمیل شده است')
    with ctx.c:
        ctx.c.execute('BEGIN IMMEDIATE')
        clash = ctx.c.execute(
            "SELECT id,from_date,to_date FROM reservations WHERE accommodation_id=? "
            "AND status IN ('submitted','approved','completed') AND NOT (to_date < ? OR from_date > ?) LIMIT 1",
            (accommodation_id, start, end)).fetchone()
        if clash:
            raise ValueError(f'تداخل رزرو: این اقامتگاه از {dates.iso_to_jalali(clash["from_date"])} تا '
                             f'{dates.iso_to_jalali(clash["to_date"])} رزرو شده است')
        cur = ctx.c.execute(
            'INSERT INTO reservations(company_id,accommodation_id,employee_id,from_date,to_date,guests,'
            'status,note,request_id,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (cid, accommodation_id, eid, start, end, guests, 'submitted',
             values.get('note', ''), request_id, g.user['id']))
        rid = cur.lastrowid
    ctx.history(eid, 'reservation', rid, [('created', '', f'{place["name"]} {start}..{end}')])
    ctx.audit_log('create_reservation', str(rid))
    return rid


def register(ctx):
    Crud(ctx, 'welfare-services', 'welfare_services',
         {'name': 'text', 'kind': 'text', 'max_per_period': 'int', 'period': 'text',
          'requires_approval': 'bool', 'active': 'bool', 'description': 'text'},
         required=('name',), view='welfare.view', edit='welfare.edit', global_table=True,
         label='خدمت رفاهی').register()
    Crud(ctx, 'accommodations', 'accommodations',
         {'name': 'text', 'kind': 'text', 'location': 'text', 'capacity': 'int',
          'max_nights': 'int', 'rules': 'text', 'active': 'bool'},
         required=('name',), view='welfare.view', edit='welfare.edit', global_table=True,
         label='اقامتگاه').register()

    @ctx.route('loans', permission='benefits.view', confidential='loan')
    def hrm_loans_list():
        sql = ('SELECT l.*,e.name employee_name,e.code employee_code FROM loans l '
               'JOIN employees e ON e.id=l.employee_id WHERE l.company_id=?')
        params = [g.hrm_cid]
        if request.args.get('employee_id'):
            sql += ' AND l.employee_id=?'
            params.append(positive_id(request.args['employee_id']))
        if request.args.get('kind'):
            sql += ' AND l.kind=?'
            params.append(request.args['kind'])
        if request.args.get('status'):
            sql += ' AND l.status=?'
            params.append(request.args['status'])
        return jsonify(ctx.rows(sql + ' ORDER BY l.request_date DESC,l.id DESC', params))

    @ctx.route('loans', methods=('POST',), permission='benefits.edit', confidential='loan')
    def hrm_loans_create():
        rid = create_loan(ctx, g.hrm_cid, ctx.body())
        ctx.c.commit()
        return jsonify(id=rid), 201

    @ctx.route('loans/<int:rid>', methods=('PUT',), permission='benefits.edit', confidential='loan')
    def hrm_loans_update(rid):
        row = ctx.c.execute('SELECT * FROM loans WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('وام یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        values = parse_values(ctx, ctx.body(), {
            'approval_date': 'date', 'status': 'text', 'installments': 'int',
            'bank_name': 'text', 'letter_no': 'text', 'letter_status': 'text', 'note': 'text',
            'paid_amount': 'float'})
        if values.get('status') and values['status'] not in LOAN_STATUSES:
            raise ValueError('وضعیت وام معتبر نیست')
        if not values:
            raise ValueError('تغییری ارسال نشده است')
        ctx.c.execute('UPDATE loans SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                      list(values.values()) + [rid])
        ctx.history(row['employee_id'], 'loan', rid, [(k, row[k], v) for k, v in values.items()])
        ctx.audit_log('update_loan', str(rid))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('loan-payments', permission='benefits.view', confidential='loan')
    def hrm_loan_payments_list():
        sql = ('SELECT p.*,l.amount loan_amount,e.name employee_name FROM loan_payments p '
               'JOIN loans l ON l.id=p.loan_id JOIN employees e ON e.id=l.employee_id WHERE p.company_id=?')
        params = [g.hrm_cid]
        if request.args.get('loan_id'):
            sql += ' AND p.loan_id=?'
            params.append(positive_id(request.args['loan_id']))
        return jsonify(ctx.rows(sql + ' ORDER BY p.date DESC,p.id DESC', params))

    @ctx.route('loan-payments', methods=('POST',), permission='benefits.edit', confidential='loan')
    def hrm_loan_payments_create():
        cid = g.hrm_cid
        body = ctx.body()
        loan_id = positive_id(body.get('loan_id'))
        loan = ctx.c.execute('SELECT * FROM loans WHERE id=? AND company_id=?', (loan_id, cid)).fetchone()
        if not loan:
            return ctx.error('وام یافت نشد', 404)
        ctx.employee(loan['employee_id'], cid)
        values = parse_values(ctx, body, {'date': 'date', 'amount': 'float', 'status': 'text', 'note': 'text'},
                              required=('date', 'amount'))
        if values.get('status', 'paid') not in ('planned', 'paid', 'late'):
            raise ValueError('وضعیت قسط معتبر نیست')
        cur = ctx.c.execute(
            'INSERT INTO loan_payments(company_id,loan_id,amount,date,status,note,created_by) VALUES (?,?,?,?,?,?,?)',
            (cid, loan_id, values['amount'], values['date'], values.get('status', 'paid'),
             values.get('note', ''), g.user['id']))
        if values.get('status', 'paid') == 'paid':
            ctx.c.execute('UPDATE loans SET paid_amount=paid_amount+? WHERE id=?', (values['amount'], loan_id))
        ctx.audit_log('create_loan_payment', str(cur.lastrowid))
        ctx.c.commit()
        return jsonify(id=cur.lastrowid), 201

    @ctx.route('welfare-usages', permission='welfare.view')
    def hrm_welfare_usages_list():
        sql = ('SELECT w.*,e.name employee_name,e.code employee_code,p.unit_id,u.name unit_name,'
               's.name service_name,s.kind service_kind FROM welfare_usages w '
               'JOIN employees e ON e.id=w.employee_id JOIN welfare_services s ON s.id=w.service_id '
               'LEFT JOIN personnel_profiles p ON p.employee_id=e.id LEFT JOIN org_units u ON u.id=p.unit_id '
               'WHERE w.company_id=?')
        params = [g.hrm_cid]
        for key, column in [('from', 'w.date>=?'), ('to', 'w.date<=?')]:
            if request.args.get(key):
                sql += ' AND ' + column
                params.append(ctx.iso(request.args[key], 'تاریخ'))
        if request.args.get('service_id'):
            sql += ' AND w.service_id=?'
            params.append(positive_id(request.args['service_id']))
        if request.args.get('employee_id'):
            sql += ' AND w.employee_id=?'
            params.append(positive_id(request.args['employee_id']))
        if request.args.get('unit_id'):
            sql += ' AND p.unit_id=?'
            params.append(positive_id(request.args['unit_id']))
        rows = [r for r in ctx.rows(sql + ' ORDER BY w.date DESC,w.id DESC', params)
                if access.employee_allowed(ctx.c, g.user, {'id': r['employee_id'], 'unit_id': r.get('unit_id')})]
        return jsonify(rows)

    @ctx.route('welfare-usages', methods=('POST',), permission='welfare.edit')
    def hrm_welfare_usages_create():
        rid = create_welfare_usage(ctx, g.hrm_cid, ctx.body())
        ctx.c.commit()
        return jsonify(id=rid), 201

    @ctx.route('welfare-usages/<int:rid>', methods=('DELETE',), permission='welfare.edit')
    def hrm_welfare_usages_delete(rid):
        row = ctx.c.execute('SELECT * FROM welfare_usages WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رکورد یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        ctx.c.execute('DELETE FROM welfare_usages WHERE id=?', (rid,))
        ctx.audit_log('delete_welfare_usage', str(rid))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('reservations', permission='welfare.view')
    def hrm_reservations_list():
        sql = ('SELECT r.*,e.name employee_name,e.code employee_code,a.name accommodation_name,'
               'a.location,a.kind accommodation_kind FROM reservations r '
               'JOIN employees e ON e.id=r.employee_id JOIN accommodations a ON a.id=r.accommodation_id '
               'WHERE r.company_id=?')
        params = [g.hrm_cid]
        if request.args.get('accommodation_id'):
            sql += ' AND r.accommodation_id=?'
            params.append(positive_id(request.args['accommodation_id']))
        if request.args.get('status'):
            sql += ' AND r.status=?'
            params.append(request.args['status'])
        rows = ctx.rows(sql + ' ORDER BY r.from_date DESC,r.id DESC', params)
        for row in rows:
            row['nights'] = dates.days_between(row['from_date'], row['to_date']) + 1
        return jsonify(rows)

    @ctx.route('reservations', methods=('POST',), permission='welfare.reserve')
    def hrm_reservations_create():
        rid = create_reservation(ctx, g.hrm_cid, ctx.body())
        ctx.c.commit()
        return jsonify(id=rid), 201

    @ctx.route('reservations/availability', permission='welfare.view')
    def hrm_reservations_availability():
        accommodation_id = positive_id(request.args.get('accommodation_id'))
        start = ctx.required_iso(request.args.get('from'), 'تاریخ شروع')
        end = ctx.required_iso(request.args.get('to'), 'تاریخ پایان')
        clash = ctx.c.execute(
            "SELECT id,from_date,to_date FROM reservations WHERE accommodation_id=? AND company_id=? "
            "AND status IN ('submitted','approved','completed') AND NOT (to_date < ? OR from_date > ?) LIMIT 1",
            (accommodation_id, g.hrm_cid, start, end)).fetchone()
        return jsonify(available=clash is None, conflict=None if clash is None else dict(clash))

    @ctx.route('reservations/<int:rid>/decision', methods=('POST',), permission='requests.approve')
    def hrm_reservations_decision(rid):
        row = ctx.c.execute('SELECT * FROM reservations WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رزرو یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        status = ctx.body().get('status')
        if status not in ('approved', 'rejected', 'cancelled', 'completed'):
            raise ValueError('وضعیت رزرو معتبر نیست')
        ctx.c.execute('UPDATE reservations SET status=? WHERE id=?', (status, rid))
        ctx.audit_log('reservation_decision', f'{rid}:{status}')
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('benefits-report', permission='benefits.view')
    def hrm_benefits_report():
        cid = g.hrm_cid
        start = ctx.iso(request.args.get('from'), 'تاریخ شروع') or '1900-01-01'
        end = ctx.iso(request.args.get('to'), 'تاریخ پایان') or '2999-12-31'
        loan_access = 'loan' in access.granting_roles(ctx.c, g.user)
        loans = ctx.rows(
            'SELECT e.id employee_id,e.code,e.name,COUNT(*) count,COALESCE(SUM(l.amount),0) total,'
            'COALESCE(SUM(l.paid_amount),0) paid FROM loans l JOIN employees e ON e.id=l.employee_id '
            'WHERE l.company_id=? AND l.request_date BETWEEN ? AND ? AND l.status!=? '
            'GROUP BY e.id ORDER BY total DESC', (cid, start, end, 'rejected')) if loan_access else []
        welfare = ctx.rows(
            'SELECT e.id employee_id,e.code,e.name,COUNT(*) count,COALESCE(SUM(w.quantity),0) quantity,'
            'COALESCE(SUM(w.value),0) value FROM welfare_usages w JOIN employees e ON e.id=w.employee_id '
            'WHERE w.company_id=? AND w.date BETWEEN ? AND ? GROUP BY e.id ORDER BY count DESC',
            (cid, start, end))
        return jsonify(loans=loans, welfare=welfare, loans_restricted=not loan_access)
