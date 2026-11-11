"""Leave, mission, absence, delay, overtime, Excel import and attendance reports."""
import io
import json

from flask import g, jsonify, request, Response

from ..validation import positive_id, number
from . import access, dates
from .crud import Crud
from .settings import setting_number

KINDS = ('leave', 'mission', 'absence', 'delay', 'overtime')
KIND_LABELS = {'leave': 'مرخصی', 'mission': 'مأموریت', 'absence': 'غیبت',
               'delay': 'تأخیر', 'overtime': 'اضافه‌کاری'}
KIND_ALIASES = {
    'مرخصی': 'leave', 'ماموریت': 'mission', 'مأموریت': 'mission', 'غیبت': 'absence',
    'تاخیر': 'delay', 'تأخیر': 'delay', 'اضافه کاری': 'overtime', 'اضافه‌کاری': 'overtime',
    'اضافهکاری': 'overtime', 'leave': 'leave', 'mission': 'mission',
    'absence': 'absence', 'delay': 'delay', 'overtime': 'overtime',
}


def _filtered(ctx, rows):
    return [row for row in rows if access.employee_allowed(ctx.c, g.user, {
        'id': row['employee_id'], 'unit_id': row.get('unit_id')})]


def _validate_record(ctx, body, cid, *, import_mode=False):
    eid = positive_id(body.get('employee_id'))
    employee = ctx.employee(eid, cid)
    kind = body.get('kind')
    if kind not in KINDS:
        # Older exported workbooks may still contain the HTML entity instead of U+200C.
        normalized = str(kind).strip().replace('&zwnj;', '\u200c')
        kind = KIND_ALIASES.get(normalized, None)
    if kind not in KINDS:
        raise ValueError('نوع حضور و غیاب معتبر نیست')
    date = ctx.required_iso(body.get('date'), 'تاریخ')
    end = ctx.iso(body.get('end_date'), 'تاریخ پایان') or date
    if end < date:
        raise ValueError('تاریخ پایان نمی‌تواند پیش از تاریخ شروع باشد')
    minutes = ctx.integer(body, 'minutes', minimum=0, maximum=100000, default=0)
    hours = ctx.amount(body, 'hours', minimum=0, maximum=1000)
    leave_type_id = None
    if kind == 'leave' and body.get('leave_type_id'):
        leave_type_id = ctx.reference('leave_types', body['leave_type_id'], cid, global_ok=True)
    days = dates.days_between(date, end) + 1
    if kind == 'leave':
        limit = setting_number(ctx.c, cid, 'leave.max_per_request', 10)
        row = ctx.c.execute('SELECT max_per_request,annual_limit FROM leave_types WHERE id=?',
                            (leave_type_id,)).fetchone() if leave_type_id else None
        if row and row['max_per_request']:
            limit = row['max_per_request']
        if days > limit:
            raise ValueError(f'طول مرخصی بیش از سقف تعیین‌شده ({limit} روز) است')
        annual = row['annual_limit'] if row and row['annual_limit'] else setting_number(ctx.c, cid, 'leave.annual_limit', 30)
        if annual:
            used = 0
            for rec in ctx.c.execute(
                "SELECT date,end_date FROM attendance_records WHERE employee_id=? AND kind='leave' AND status IN ('recorded','approved') AND leave_type_id IS ?",
                (eid, leave_type_id)):
                if dates.iso_to_jalali(rec['date'])[:4] == dates.iso_to_jalali(date)[:4]:
                    used += dates.days_between(rec['date'], rec['end_date']) + 1
            if used + days > annual:
                raise ValueError(f'سقف سالانه مرخصی ({annual} روز) تکمیل شده است')
    note = ctx.text(body, 'note', maximum=1000)
    duplicate = ctx.c.execute(
        'SELECT 1 FROM attendance_records WHERE company_id=? AND employee_id=? AND kind=? AND date=? '
        'AND leave_type_id IS ? AND status!=? LIMIT 1',
        (cid, eid, kind, date, leave_type_id, 'rejected')).fetchone()
    if duplicate:
        raise ValueError('رکورد تکراری برای همین فرد، تاریخ و نوع وجود دارد')
    leave_row = ctx.c.execute('SELECT requires_approval FROM leave_types WHERE id=?',
                              (leave_type_id,)).fetchone() if leave_type_id else None
    approval = setting_number(ctx.c, cid, 'request.approval_enabled', 1)
    status = 'pending' if (approval and (not leave_row or leave_row['requires_approval'])) else 'recorded'
    return dict(company_id=cid, employee_id=eid, kind=kind, leave_type_id=leave_type_id,
                date=date, end_date=end, minutes=minutes, hours=hours, status=status,
                source='excel' if import_mode else 'manual', note=note,
                created_by=g.user['id'])


def insert_attendance(ctx, cid, body, *, request_id=None, approved=False, source='manual'):
    """Shared writer for manual entries, Excel imports and approved requests."""
    values = _validate_record(ctx, body, cid, import_mode=(source == 'excel'))
    values['source'] = source
    values['status'] = 'approved' if approved else values['status']
    if request_id is not None:
        values['request_id'] = request_id
    keys = list(values)
    cur = ctx.c.execute(
        f'INSERT INTO attendance_records ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})',
        [values[k] for k in keys])
    ctx.history(values['employee_id'], 'attendance', cur.lastrowid,
                [('created', '', f'{values["kind"]} {values["date"]}')])
    ctx.audit_log('create_attendance', str(cur.lastrowid))
    return cur.lastrowid


def register(ctx):
    Crud(ctx, 'leave-types', 'leave_types',
         {'code': 'text', 'name': 'text', 'annual_limit': 'int', 'max_per_request': 'int',
          'requires_approval': 'bool', 'paid': 'bool', 'active': 'bool'},
         required=('code', 'name'), view='attendance.view', edit='settings.edit',
         global_table=True, label='نوع مرخصی').register()

    @ctx.route('attendance', permission='attendance.view')
    def hrm_attendance_list():
        cid = g.hrm_cid
        sql = ('SELECT a.*,e.name employee_name,e.code employee_code,p.unit_id,u.name unit_name,'
               'l.name leave_type_name FROM attendance_records a '
               'JOIN employees e ON e.id=a.employee_id '
               'LEFT JOIN personnel_profiles p ON p.employee_id=e.id '
               'LEFT JOIN org_units u ON u.id=p.unit_id '
               'LEFT JOIN leave_types l ON l.id=a.leave_type_id WHERE a.company_id=?')
        params = [cid]
        for key, column in [('from', 'a.date>=?'), ('to', 'a.date<=?'),
                            ('kind', 'a.kind=?'), ('status', 'a.status=?')]:
            if request.args.get(key):
                value = request.args[key]
                if key in ('from', 'to'):
                    value = ctx.iso(value, 'تاریخ')
                sql += ' AND ' + column
                params.append(value)
        if request.args.get('employee_id'):
            sql += ' AND a.employee_id=?'
            params.append(positive_id(request.args['employee_id']))
        if request.args.get('unit_id'):
            sql += ' AND p.unit_id=?'
            params.append(positive_id(request.args['unit_id']))
        rows = ctx.rows(sql + ' ORDER BY a.date DESC,a.id DESC LIMIT 2000', params)
        rows = _filtered(ctx, rows)
        for row in rows:
            row['kind_label'] = KIND_LABELS.get(row['kind'], row['kind'])
        return jsonify(rows)

    @ctx.route('attendance', methods=('POST',), permission='attendance.edit')
    def hrm_attendance_create():
        rid = insert_attendance(ctx, g.hrm_cid, ctx.body())
        ctx.c.commit()
        return jsonify(id=rid), 201

    @ctx.route('attendance/<int:rid>', methods=('PUT',), permission='attendance.edit')
    def hrm_attendance_update(rid):
        row = ctx.c.execute('SELECT * FROM attendance_records WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رکورد یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        body = ctx.body()
        changes = {}
        for key in ('date', 'end_date'):
            if key in body:
                changes[key] = ctx.required_iso(body[key], 'تاریخ')
        if changes.get('end_date', row['end_date']) < changes.get('date', row['date']):
            raise ValueError('تاریخ پایان نمی‌تواند پیش از تاریخ شروع باشد')
        if 'minutes' in body:
            changes['minutes'] = ctx.integer(body, 'minutes', minimum=0, maximum=100000)
        if 'hours' in body:
            changes['hours'] = ctx.amount(body, 'hours', minimum=0, maximum=1000)
        if 'note' in body:
            changes['note'] = ctx.text(body, 'note', maximum=1000)
        if 'status' in body:
            if body['status'] not in ('pending', 'recorded', 'approved', 'rejected'):
                raise ValueError('وضعیت معتبر نیست')
            changes['status'] = body['status']
        if not changes:
            raise ValueError('تغییری ارسال نشده است')
        ctx.c.execute('UPDATE attendance_records SET ' + ','.join(k + '=?' for k in changes) + ' WHERE id=?',
                      list(changes.values()) + [rid])
        ctx.history(row['employee_id'], 'attendance', rid, [(k, row[k], v) for k, v in changes.items()])
        ctx.audit_log('update_attendance', str(rid))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('attendance/<int:rid>/decision', methods=('POST',), permission='requests.approve')
    def hrm_attendance_decision(rid):
        row = ctx.c.execute('SELECT * FROM attendance_records WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رکورد یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        decision = ctx.body().get('status')
        if decision not in ('approved', 'rejected', 'recorded'):
            raise ValueError('تصمیم معتبر نیست')
        ctx.c.execute('UPDATE attendance_records SET status=?,approved_by=? WHERE id=?',
                      (decision, g.user['id'], rid))
        ctx.audit_log('attendance_decision', f'{rid}:{decision}')
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('attendance/<int:rid>', methods=('DELETE',), permission='attendance.edit')
    def hrm_attendance_delete(rid):
        row = ctx.c.execute('SELECT * FROM attendance_records WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رکورد یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        ctx.c.execute('DELETE FROM attendance_records WHERE id=?', (rid,))
        ctx.history(row['employee_id'], 'attendance', rid, [('deleted', row['kind'], '')])
        ctx.audit_log('delete_attendance', str(rid))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('attendance/import', methods=('POST',), permission='attendance.import')
    def hrm_attendance_import():
        import openpyxl
        file = request.files.get('file')
        if not file:
            raise ValueError('فایل اکسل حضور و غیاب را انتخاب کنید')
        payload = file.read()
        try:
            workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
            sheet = workbook.active
            rows = list(sheet.iter_rows(values_only=True))
        except Exception:
            raise ValueError('ساختار فایل اکسل معتبر نیست') from None
        if not rows:
            raise ValueError('فایل اکسل خالی است')
        headers = [str(v or '').strip() for v in rows[0]]
        def column(*names):
            for name in names:
                if name in headers:
                    return headers.index(name)
            return None
        cols = {
            'code': column('کد پرسنلی', 'کد', 'code'),
            'date': column('تاریخ', 'تاریخ شمسی', 'date'),
            'end_date': column('تا تاریخ', 'پایان', 'end_date'),
            'kind': column('نوع', 'kind'),
            'minutes': column('مدت (دقیقه)', 'دقیقه', 'minutes'),
            'hours': column('ساعت', 'hours'),
            'note': column('توضیح', 'note'),
        }
        if cols['code'] is None or cols['date'] is None:
            raise ValueError('ستون‌های «کد پرسنلی» و «تاریخ» در ردیف اول الزامی‌اند')
        people = {r['code']: r['id'] for r in ctx.c.execute(
            'SELECT id,code FROM employees WHERE company_id=?', (g.hrm_cid,))}
        inserted, skipped, errors, new_ids = 0, 0, [], []
        for index, raw in enumerate(rows[1:], start=2):
            if not any(v not in (None, '') for v in raw):
                continue
            def value(key):
                i = cols[key]
                return raw[i] if i is not None and i < len(raw) else None
            code = str(value('code') or '').strip()
            if code not in people:
                errors.append(f'ردیف {index}: کد پرسنلی یافت نشد'); skipped += 1; continue
            body = {
                'employee_id': people[code], 'kind': str(value('kind') or 'leave').strip(),
                'date': str(value('date') or '').strip(),
                'end_date': str(value('end_date') or '').strip() or None,
                'minutes': value('minutes') or 0, 'hours': value('hours') or 0,
                'note': str(value('note') or '').strip(),
            }
            try:
                values = _validate_record(ctx, body, g.hrm_cid, import_mode=True)
                keys = list(values)
                new_ids.append(insert_attendance(ctx, g.hrm_cid, body, source='excel'))
                inserted += 1
            except (ValueError, PermissionError) as exc:
                errors.append(f'ردیف {index}: {exc}'); skipped += 1
        cur = ctx.c.execute(
            'INSERT INTO attendance_imports(company_id,filename,rows_count,created_by,detail) VALUES (?,?,?,?,?)',
            (g.hrm_cid, file.filename[:255], inserted, g.user['id'],
             json.dumps({'skipped': skipped, 'errors': errors[:20]}, ensure_ascii=False)))
        ctx.c.executemany('UPDATE attendance_records SET import_id=? WHERE id=?',
                          [(cur.lastrowid, rid) for rid in new_ids])
        ctx.audit_log('import_attendance', f'{inserted} rows / {skipped} skipped')
        ctx.c.commit()
        return jsonify(inserted=inserted, skipped=skipped, errors=errors[:20], import_id=cur.lastrowid)

    @ctx.route('attendance/template', permission='attendance.view')
    def hrm_attendance_template():
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = 'حضور و غیاب'
        sheet.append(['کد پرسنلی', 'تاریخ', 'تا تاریخ', 'نوع', 'مدت (دقیقه)', 'ساعت', 'توضیح'])
        sheet.append(['P-1001', '۱۴۰۵/۰۸/۲۰', '۱۴۰۵/۰۸/۲۰', 'مرخصی', '480', '8', 'نمونه'])
        for cell in sheet[1]:
            cell.font = openpyxl.styles.Font(bold=True)
        stream = io.BytesIO(); workbook.save(stream); stream.seek(0)
        return Response(stream.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': 'attachment; filename="attendance-template.xlsx"'})

    @ctx.route('attendance/summary', permission='attendance.view')
    def hrm_attendance_summary():
        cid = g.hrm_cid
        start = ctx.iso(request.args.get('from'), 'تاریخ شروع') or '1900-01-01'
        end = ctx.iso(request.args.get('to'), 'تاریخ پایان') or '2999-12-31'
        rows = ctx.rows(
            'SELECT e.id employee_id,e.code,e.name,e.department,p.unit_id,u.name unit_name,'
            'a.kind,COUNT(*) records,COALESCE(SUM(a.minutes),0) minutes,COALESCE(SUM(a.hours),0) hours '
            'FROM attendance_records a JOIN employees e ON e.id=a.employee_id '
            'LEFT JOIN personnel_profiles p ON p.employee_id=e.id '
            'LEFT JOIN org_units u ON u.id=p.unit_id '
            'WHERE a.company_id=? AND a.date BETWEEN ? AND ? AND a.status!=? '
            'GROUP BY e.id,a.kind ORDER BY e.name,a.kind', (cid, start, end, 'rejected'))
        rows = _filtered(ctx, rows)
        for row in rows:
            row['kind_label'] = KIND_LABELS.get(row['kind'], row['kind'])
        return jsonify(rows)
