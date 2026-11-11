"""Management dashboard, filtered reports and authorised Excel exports."""
import io
import json

from flask import g, jsonify, request, Response

from ..validation import positive_id
from . import access, dates
from .settings import setting_number

REPORTS = {
    'personnel': {
        'label': 'پرونده پرسنلی', 'confidential': None,
        'columns': ['code', 'name', 'department', 'job', 'unit_name', 'position_name',
                    'employment_status', 'start_date', 'phone', 'national_id'],
        'sql': '''SELECT e.code,e.name,e.department,e.job,u.name unit_name,pos.title position_name,
            COALESCE(p.employment_status,'active') employment_status,p.start_date,p.phone,p.national_id
            FROM employees e LEFT JOIN personnel_profiles p ON p.employee_id=e.id
            LEFT JOIN org_units u ON u.id=p.unit_id LEFT JOIN positions pos ON pos.id=p.position_id
            WHERE e.company_id=? ORDER BY e.name''',
    },
    'contracts': {
        'label': 'قراردادها', 'confidential': 'contract',
        'columns': ['employee_code', 'employee_name', 'number', 'type', 'start_date', 'end_date',
                    'status', 'renewal_count', 'wage'],
        'sql': '''SELECT e.code employee_code,e.name employee_name,c.number,c.type,c.start_date,c.end_date,
            c.status,c.renewal_count,c.wage FROM contracts c JOIN employees e ON e.id=c.employee_id
            WHERE c.company_id=? AND c.start_date>=? AND c.start_date<=? ORDER BY c.end_date''',
    },
    'attendance': {
        'label': 'حضور، غیاب و مرخصی', 'confidential': None,
        'columns': ['employee_code', 'employee_name', 'unit_name', 'kind', 'date', 'end_date',
                    'minutes', 'hours', 'status', 'source', 'request_id', 'note'],
        'sql': '''SELECT e.code employee_code,e.name employee_name,u.name unit_name,a.kind,a.date,a.end_date,
            a.minutes,a.hours,a.status,a.source,a.request_id,a.note FROM attendance_records a
            JOIN employees e ON e.id=a.employee_id LEFT JOIN personnel_profiles p ON p.employee_id=e.id
            LEFT JOIN org_units u ON u.id=p.unit_id
            WHERE a.company_id=? AND a.date BETWEEN ? AND ? ORDER BY a.date DESC''',
    },
    'performance': {
        'label': 'ارزیابی عملکرد', 'confidential': None,
        'columns': ['period_title', 'employee_code', 'employee_name', 'department',
                    'weighted_average', 'indicators', 'start_date', 'end_date'],
        'sql': '''SELECT p.title period_title,e.code employee_code,e.name employee_name,e.department,
            ROUND(SUM(s.score*i.weight)/NULLIF(SUM(i.weight),0),2) weighted_average,
            COUNT(s.id) indicators,p.start_date,p.end_date FROM performance_scores s
            JOIN performance_periods p ON p.id=s.period_id JOIN employees e ON e.id=s.employee_id
            JOIN performance_indicators i ON i.id=s.indicator_id
            WHERE s.company_id=? AND p.start_date BETWEEN ? AND ? GROUP BY p.id,s.employee_id
            ORDER BY p.start_date DESC,weighted_average DESC''',
    },
    'disciplinary': {
        'label': 'سوابق تشویقی و انضباطی', 'confidential': 'discipline',
        'columns': ['employee_code', 'employee_name', 'kind', 'date', 'reason', 'decision', 'decision_no'],
        'sql': '''SELECT e.code employee_code,e.name employee_name,d.kind,d.date,d.reason,d.decision,d.decision_no
            FROM disciplinary_records d JOIN employees e ON e.id=d.employee_id
            WHERE d.company_id=? AND d.date BETWEEN ? AND ? ORDER BY d.date DESC''',
    },
    'loans': {
        'label': 'وام‌ها و تسهیلات', 'confidential': 'loan',
        'columns': ['employee_code', 'employee_name', 'kind', 'amount', 'request_date',
                    'status', 'installments', 'paid_amount', 'bank_name', 'letter_no', 'letter_status'],
        'sql': '''SELECT e.code employee_code,e.name employee_name,l.kind,l.amount,l.request_date,l.status,
            l.installments,l.paid_amount,l.bank_name,l.letter_no,l.letter_status FROM loans l
            JOIN employees e ON e.id=l.employee_id
            WHERE l.company_id=? AND l.request_date BETWEEN ? AND ? ORDER BY l.request_date DESC''',
    },
    'welfare': {
        'label': 'توزیع خدمات رفاهی', 'confidential': None,
        'columns': ['employee_code', 'employee_name', 'unit_name', 'service_name',
                    'date', 'quantity', 'value', 'note'],
        'sql': '''SELECT e.code employee_code,e.name employee_name,u.name unit_name,s.name service_name,
            w.date,w.quantity,w.value,w.note FROM welfare_usages w JOIN employees e ON e.id=w.employee_id
            JOIN welfare_services s ON s.id=w.service_id LEFT JOIN personnel_profiles p ON p.employee_id=e.id
            LEFT JOIN org_units u ON u.id=p.unit_id
            WHERE w.company_id=? AND w.date BETWEEN ? AND ? ORDER BY w.date DESC''',
    },
    'reservations': {
        'label': 'رزرو اقامتگاه‌ها', 'confidential': None,
        'columns': ['employee_code', 'employee_name', 'accommodation_name', 'location',
                    'from_date', 'to_date', 'guests', 'status', 'note'],
        'sql': '''SELECT e.code employee_code,e.name employee_name,a.name accommodation_name,a.location,
            r.from_date,r.to_date,r.guests,r.status,r.note FROM reservations r
            JOIN employees e ON e.id=r.employee_id JOIN accommodations a ON a.id=r.accommodation_id
            WHERE r.company_id=? AND r.from_date BETWEEN ? AND ? ORDER BY r.from_date DESC''',
    },
    'requests': {
        'label': 'درخواست‌ها و گردش تأیید', 'confidential': None,
        'columns': ['employee_code', 'employee_name', 'type_name', 'title', 'status',
                    'created_at', 'decided_at', 'decision_note'],
        'sql': '''SELECT e.code employee_code,e.name employee_name,t.name type_name,r.title,r.status,
            r.created_at,r.decided_at,r.decision_note FROM requests r
            JOIN employees e ON e.id=r.employee_id JOIN request_types t ON t.id=r.type_id
            WHERE r.company_id=? AND substr(r.created_at,1,10) BETWEEN ? AND ? ORDER BY r.created_at DESC''',
    },
}
HEADERS = {
    'code': 'کد پرسنلی', 'name': 'نام و نام خانوادگی', 'department': 'واحد سازمانی',
    'job': 'سمت', 'unit_name': 'واحد', 'position_name': 'سمت سازمانی',
    'employment_status': 'وضعیت اشتغال', 'start_date': 'تاریخ شروع', 'phone': 'تلفن',
    'national_id': 'کد ملی', 'employee_code': 'کد پرسنلی', 'employee_name': 'نام',
    'number': 'شماره قرارداد', 'type': 'نوع', 'end_date': 'تاریخ پایان',
    'status': 'وضعیت', 'renewal_count': 'دفعات تمدید', 'wage': 'مبلغ',
    'kind': 'نوع', 'date': 'تاریخ', 'minutes': 'دقیقه', 'hours': 'ساعت',
    'source': 'منبع', 'request_id': 'شماره درخواست', 'note': 'توضیح', 'period_title': 'دوره ارزیابی',
    'weighted_average': 'میانگین وزنی', 'indicators': 'تعداد شاخص',
    'reason': 'دلیل', 'decision': 'تصمیم', 'decision_no': 'شماره تصمیم',
    'amount': 'مبلغ', 'request_date': 'تاریخ درخواست', 'installments': 'تعداد اقساط',
    'paid_amount': 'پرداخت‌شده', 'bank_name': 'بانک', 'letter_no': 'شماره معرفی‌نامه',
    'letter_status': 'وضعیت معرفی‌نامه', 'service_name': 'خدمت', 'quantity': 'تعداد',
    'value': 'ارزش', 'accommodation_name': 'اقامتگاه', 'location': 'موقعیت',
    'from_date': 'از تاریخ', 'to_date': 'تا تاریخ', 'guests': 'نفرات',
    'type_name': 'نوع درخواست', 'title': 'عنوان', 'created_at': 'زمان ثبت',
    'decided_at': 'زمان تصمیم', 'decision_note': 'توضیح تصمیم',
}


def _range(ctx):
    start = ctx.iso(request.args.get('from'), 'تاریخ شروع') or '1900-01-01'
    end = ctx.iso(request.args.get('to'), 'تاریخ پایان') or '2999-12-31'
    if end < start:
        raise ValueError('بازه تاریخ معتبر نیست')
    return start, end


def _report_rows(ctx, name, cid):
    definition = REPORTS[name]
    if definition['confidential']:
        access.require(ctx.c, g.user, cid, confidential=definition['confidential'])
    start, end = _range(ctx)
    binds = (cid,) if definition['sql'].count('?') == 1 else (cid, start, end)
    rows = ctx.rows(definition['sql'], binds)
    if g.user['hrm_scope'] in ('self', 'unit'):
        people = {r['code']: (r['id'], r['unit_id']) for r in ctx.c.execute(
            'SELECT e.id,e.code,p.unit_id FROM employees e LEFT JOIN personnel_profiles p ON p.employee_id=e.id '
            'WHERE e.company_id=?', (cid,))}
        key = 'code' if 'code' in definition['columns'] else 'employee_code'
        filtered = []
        for row in rows:
            found = people.get(row.get(key))
            if found and access.employee_allowed(ctx.c, g.user, {'id': found[0], 'unit_id': found[1]}):
                filtered.append(row)
        return definition, filtered
    return definition, rows


def register(ctx):
    @ctx.route('dashboard', permission='reports.view')
    def hrm_dashboard():
        cid = g.hrm_cid
        conn = ctx.c
        confidential = access.granting_roles(conn, g.user)
        def count(sql, params=()):
            return conn.execute(sql, params).fetchone()[0]
        alert_days = setting_number(conn, cid, 'contract.alert_days', 30)
        today = dates.today_iso()
        expiring = count(
            "SELECT COUNT(*) FROM contracts WHERE company_id=? AND status='active' AND end_date BETWEEN ? AND ?",
            (cid, today, dates.add_days(today, alert_days)))
        dashboard = {
            'company_id': cid,
            'generated_at': ctx.now(),
            'confidential': confidential,
            'personnel': {
                'total': count('SELECT COUNT(*) FROM employees WHERE company_id=?', (cid,)),
                'active': count('SELECT COUNT(*) FROM employees WHERE company_id=? AND active=1', (cid,)),
                'units': count('SELECT COUNT(*) FROM org_units WHERE company_id=? AND active=1', (cid,)),
            },
            'contracts': {
                'active': count("SELECT COUNT(*) FROM contracts WHERE company_id=? AND status='active'", (cid,)),
                'expiring': expiring,
                'alert_days': alert_days,
                'details': [],
            },
            'attendance': {
                'absence': count("SELECT COUNT(*) FROM attendance_records WHERE company_id=? AND kind='absence' AND status!='rejected'", (cid,)),
                'delay': count("SELECT COUNT(*) FROM attendance_records WHERE company_id=? AND kind='delay' AND status!='rejected'", (cid,)),
                'leave': count("SELECT COUNT(*) FROM attendance_records WHERE company_id=? AND kind='leave' AND status IN ('approved','recorded')", (cid,)),
                'overtime_hours': count("SELECT COALESCE(SUM(hours),0) FROM attendance_records WHERE company_id=? AND kind='overtime'", (cid,)),
            },
            'performance': {
                'average': count('SELECT COALESCE(ROUND(AVG(score),2),0) FROM performance_scores WHERE company_id=?', (cid,)),
                'periods': count('SELECT COUNT(*) FROM performance_periods WHERE company_id=?', (cid,)),
                'records': count('SELECT COUNT(*) FROM performance_scores WHERE company_id=?', (cid,)),
            },
            'training': {
                'courses': count('SELECT COUNT(*) FROM courses WHERE company_id=?', (cid,)),
                'records': count('SELECT COUNT(*) FROM records WHERE company_id=?', (cid,)),
                'certificates': count('SELECT COUNT(*) FROM certificates WHERE company_id=?', (cid,)),
                'assessments': count('SELECT COUNT(*) FROM assessments WHERE company_id=?', (cid,)),
            },
            'benefits': {
                'loans': count("SELECT COUNT(*) FROM loans WHERE company_id=? AND status!='rejected'", (cid,)) if 'loan' in confidential else None,
                'loan_amount': count('SELECT COALESCE(SUM(amount),0) FROM loans WHERE company_id=?', (cid,)) if 'loan' in confidential else None,
                'loans_restricted': 'loan' not in confidential,
                'welfare_usages': count('SELECT COUNT(*) FROM welfare_usages WHERE company_id=?', (cid,)),
                'reservations': count("SELECT COUNT(*) FROM reservations WHERE company_id=? AND status IN ('submitted','approved')", (cid,)),
                'villas_mahmoudabad': count(
                    "SELECT COUNT(*) FROM reservations r JOIN accommodations a ON a.id=r.accommodation_id "
                    "WHERE r.company_id=? AND a.location LIKE ? AND r.status IN ('submitted','approved')",
                    (cid, '%محمودآباد%')),
            },
            'requests': {
                'pending': count("SELECT COUNT(*) FROM requests WHERE company_id=? AND status='submitted'", (cid,)),
                'approved': count("SELECT COUNT(*) FROM requests WHERE company_id=? AND status='approved'", (cid,)),
            },
            'companies': [],
        }
        if 'contract' in confidential:
            dashboard['contracts']['details'] = ctx.rows(
                'SELECT c.id,c.end_date,c.status,e.name employee_name FROM contracts c '
                'JOIN employees e ON e.id=c.employee_id WHERE c.company_id=? AND c.status=? '
                'AND c.end_date BETWEEN ? AND ? ORDER BY c.end_date LIMIT 20',
                (cid, 'active', today, dates.add_days(today, alert_days)))
        for company in access.visible_companies(conn, g.user):
            dashboard['companies'].append({
                'id': company['id'], 'name': company['name'],
                'employees': count('SELECT COUNT(*) FROM employees WHERE company_id=?', (company['id'],)),
                'active_contracts': count("SELECT COUNT(*) FROM contracts WHERE company_id=? AND status='active'", (company['id'],)),
                'pending_requests': count("SELECT COUNT(*) FROM requests WHERE company_id=? AND status='submitted'", (company['id'],)),
            })
        return jsonify(dashboard)

    @ctx.route('reports', permission='reports.view')
    def hrm_reports_list():
        granted = access.granting_roles(ctx.c, g.user)
        return jsonify([{'name': name, 'label': item['label'],
                         'available': item['confidential'] is None or item['confidential'] in granted,
                         'confidential': item['confidential']}
                        for name, item in REPORTS.items()])

    @ctx.route('reports/<name>', permission='reports.view')
    def hrm_report_json(name):
        if name not in REPORTS:
            return ctx.error('گزارش یافت نشد', 404)
        definition, rows = _report_rows(ctx, name, g.hrm_cid)
        return jsonify(name=name, label=definition['label'],
                       columns=definition['columns'], headers=[HEADERS.get(c, c) for c in definition['columns']],
                       rows=rows)

    @ctx.route('reports/<name>.xlsx', permission='reports.export')
    def hrm_report_xlsx(name):
        import openpyxl
        if name not in REPORTS:
            return ctx.error('گزارش یافت نشد', 404)
        definition, rows = _report_rows(ctx, name, g.hrm_cid)
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = definition['label'][:31]
        columns = definition['columns']
        sheet.append([HEADERS.get(c, c) for c in columns])
        for cell in sheet[1]:
            cell.font = openpyxl.styles.Font(bold=True)
        for row in rows:
            values = []
            for column in columns:
                value = row.get(column)
                if isinstance(value, str) and len(value) >= 10 and value[:4].isdigit() and value[4:5] == '-':
                    value = dates.iso_to_jalali(value[:10]) + value[10:]
                if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
                    value = "'" + value
                values.append(value if value is not None else '')
            sheet.append(values)
        for index, column in enumerate(columns, 1):
            sheet.column_dimensions[openpyxl.utils.get_column_letter(index)].width = 22
        stream = io.BytesIO(); workbook.save(stream); stream.seek(0)
        ctx.audit_log('export_report', name)
        ctx.c.commit()
        return Response(stream.getvalue(),
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename="Nikan-HRM-{name}.xlsx"'})
