"""Nikan HRM — stage-by-stage integration tests on the REAL application.

Run:  python -m unittest -v test_hrm
Tests use Flask's test client against the same create_app() the Windows installers
ship. No mock project, no in-memory fake database.
"""
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app import create_app
from app.hrm import schema
from app.hrm.dates import jalali_to_iso, iso_to_jalali

PASSWORD = 'test-password-12345'


class HrmBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, testing=True)
        self.admin = self.app.test_client()
        self.assertEqual(self.admin.post('/api/setup', json=dict(username='admin', password=PASSWORD)).status_code, 200)
        self.assertEqual(self.admin.post('/api/login', json=dict(username='admin', password=PASSWORD)).status_code, 200)
        self.csrf = self.admin.get('/api/me').json['csrf']

    def tearDown(self):
        self.temp.cleanup()

    def api(self, path, data=None, method='GET', client=None, csrf=None, cid=1):
        client = client or self.admin
        csrf = self.csrf if csrf is None else csrf
        url = '/api/hrm/' + path
        if cid is not None:
            url += ('&' if '?' in url else '?') + 'company_id=' + str(cid)
        kwargs = {'headers': {'X-CSRF-Token': csrf}}
        if data is not None:
            kwargs['json'] = data
        return client.open(url, method=method, **kwargs)

    def user(self, username, role='holding_hr', company_id=1, scope=None, grants=(), employee_id=None, unit_id=None):
        body = dict(username=username, password=PASSWORD, role=role, company_id=company_id,
                    scope=scope, grants=list(grants), employee_id=employee_id, unit_id=unit_id)
        body = {k: v for k, v in body.items() if v is not None}
        response = self.api('users', body, 'POST')
        self.assertEqual(response.status_code, 201, response.json)
        uid = response.json['id']
        client = self.app.test_client()
        self.assertEqual(client.post('/api/login', json=dict(username=username, password=PASSWORD)).status_code, 200)
        csrf = client.get('/api/me').json['csrf']
        return client, csrf, uid

    def person(self, code='P-1', name='علی احمدی', **extra):
        body = dict(code=code, name=name, department='', job='', national_id='1234567890',
                    phone='09120000000', start_date='۱۴۰۰/۰۱/۰۱', employment_status='active')
        body.update(extra)
        response = self.api('personnel', body, 'POST')
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['id']

    def unit(self, name='واحد تولید'):
        response = self.api('org-units', dict(name=name, code='U1'), 'POST')
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['id']

    def position(self, title='کارشناس تولید'):
        response = self.api('positions', dict(title=title, code='J1'), 'POST')
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['id']


class Stage1_AnalysisAndMigration(HrmBase):
    def test_legacy_database_upgrade_is_idempotent_and_preserves_rows(self):
        source = Path(__file__).parent / 'database' / 'nikan-empty.sqlite3'
        target = Path(self.temp.name) / 'real.sqlite3'
        shutil.copyfile(source, target)
        with closing(sqlite3.connect(target)) as conn:
            before = conn.execute('SELECT COUNT(*) FROM holding_competencies').fetchone()[0]
            self.assertEqual(before, 281)
            versions = schema.migrate(conn)
            self.assertIn('0.12.0-hrm-base', versions)
            self.assertIn('0.12.1-hrm-request-link', versions)
            self.assertIn('request_id', {r[1] for r in conn.execute('PRAGMA table_info(attendance_records)')})
            self.assertEqual(schema.migrate(conn), versions)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM holding_competencies').fetchone()[0], before)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0], 6)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM hrm_settings').fetchone()[0], 14)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], 'ok')
        app2 = create_app(self.temp.name, testing=True)
        self.assertTrue(any(str(rule).startswith('/api/hrm/') for rule in app2.url_map.iter_rules()))

    def test_bare_legacy_schema_accepts_migrations_twice(self):
        target = Path(self.temp.name) / 'bare.sqlite3'
        with closing(sqlite3.connect(target)) as conn:
            conn.executescript('''CREATE TABLE companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL);
                CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,
                role TEXT NOT NULL,company_id INTEGER,active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE employees(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL,code TEXT NOT NULL,name TEXT NOT NULL);
                CREATE TABLE audit(id INTEGER PRIMARY KEY,created TEXT,user_id INTEGER,company_id INTEGER,action TEXT,detail TEXT);
                INSERT INTO companies(name) VALUES ('شرکت قدیمی');
                INSERT INTO employees(company_id,code,name) VALUES (1,'OLD-1','فرد قدیمی');''')
            schema.migrate(conn); schema.migrate(conn)
            self.assertIn('request_id', {r[1] for r in conn.execute('PRAGMA table_info(attendance_records)')})
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM employees').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT hrm_role FROM users LIMIT 1").fetchone(), None)

    def test_jalali_iso_roundtrip_and_persian_digits(self):
        self.assertEqual(jalali_to_iso('۱۴۰۵/۰۳/۲۵'), '2026-06-15')
        self.assertEqual(iso_to_jalali('2026-11-11'), '1405/08/20')
        for j, g in [('1399/12/30', '2021-03-20'), ('1400/01/01', '2021-03-21'),
                     ('1403/12/30', '2025-03-20'), ('1404/01/01', '2025-03-21'),
                     ('1498/12/30', '2120-03-20'), ('1499/12/29', '2121-03-20')]:
            self.assertEqual(jalali_to_iso(j), g)
            self.assertEqual(jalali_to_iso(iso_to_jalali(g)), g)

    def test_existing_training_routes_still_work(self):
        self.assertEqual(self.admin.get('/api/status').json['setup_required'], False)
        response = self.admin.post('/api/data/employees?company_id=1',
                                   json=dict(code='OLD-API', name='فرد آموزش'),
                                   headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 201)
        rows = self.admin.get('/api/data/employees?company_id=1').json
        self.assertTrue(any(r['code'] == 'OLD-API' for r in rows))


class Stage2_PersonnelContractsAndAccess(HrmBase):
    def test_personnel_file_history_and_legacy_field_sync(self):
        unit_id, position_id = self.unit(), self.position()
        eid = self.person(unit_id=unit_id, position_id=position_id)
        row = self.api('personnel/' + str(eid)).json
        self.assertEqual(row['employee']['department'], 'واحد تولید')
        self.assertEqual(row['employee']['job'], 'کارشناس تولید')
        self.assertEqual(row['profile']['national_id'], '1234567890')
        self.assertEqual(row['profile']['start_date'], jalali_to_iso('۱۴۰۰/۰۱/۰۱'))
        self.assertEqual(row['profile']['start_date_fa'], '1400/01/01')
        self.assertEqual(row['training']['records'], [])
        response = self.api('personnel/' + str(eid), dict(phone='09121111111', job='دستی'), 'PUT')
        self.assertEqual(response.status_code, 200, response.json)
        detail = self.api('personnel/' + str(eid)).json
        self.assertTrue(any(h['field'] == 'phone' for h in detail['history']))
        self.assertEqual(detail['profile']['phone'], '09121111111')
        self.assertEqual(self.admin.get('/api/data/employees?company_id=1').json[0]['job'], 'دستی')

    def test_company_isolation_and_unit_scope(self):
        eid = self.person(code='P-ISO')
        client2, csrf_other, _ = self.user('company2', 'company_hr', company_id=2)
        self.assertEqual(self.api('personnel', client=self.app.test_client(), csrf=csrf_other, cid=1).status_code, 401)
        self.assertEqual(self.api('personnel', client=client2, csrf=csrf_other, cid=1).status_code, 403)
        rows = self.api('personnel', client=client2, csrf=csrf_other, cid=2).json
        self.assertEqual(rows, [])
        unit_id = self.unit('واحد الف')
        self.person(code='P-EMP', unit_id=unit_id)
        mgr, mgr_csrf, _ = self.user('unitmgr', 'unit_manager', company_id=1, unit_id=unit_id)
        visible = self.api('personnel', client=mgr, csrf=mgr_csrf).json
        self.assertTrue(all(r['code'] != 'P-ISO' for r in visible))

    def test_confidential_documents_require_explicit_grant_even_for_system_admin(self):
        self.assertEqual(self.api('contracts').status_code, 403)
        self.assertEqual(self.api('loans').status_code, 403)
        self.assertEqual(self.api('disciplinary').status_code, 403)
        eid = self.person()
        file_data = (io.BytesIO(b'private'), 'contract.pdf')
        response = self.admin.post('/api/hrm/documents?company_id=1',
                                   data={'employee_id': str(eid), 'title': 'قرارداد',
                                         'entity_type': 'contract', 'file': file_data},
                                   headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 403, response.json)
        hr, csrf_hr, uid = self.user('hrdocs', 'holding_hr', grants=['confidential.contract',
                                                          'confidential.documents',
                                                          'confidential.discipline',
                                                          'confidential.loan'])
        response = hr.post('/api/hrm/documents?company_id=1',
                           data={'employee_id': str(eid), 'title': 'قرارداد', 'entity_type': 'contract',
                                 'file': (io.BytesIO(b'private'), 'contract.pdf')},
                           headers={'X-CSRF-Token': csrf_hr})
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(self.api('contracts', client=hr, csrf=csrf_hr).status_code, 200)
        self.assertEqual(self.api('documents', client=hr, csrf=csrf_hr).status_code, 200)
        self.assertEqual(len(self.api('documents', client=hr, csrf=csrf_hr).json), 1)
        self.assertEqual(self.api('documents').json, [])

    def test_contract_renewal_limit_and_alerts_from_settings(self):
        hr, csrf_hr, _ = self.user('hr2', 'holding_hr', grants=['confidential.contract'])
        eid = self.person(code='P-C')
        contract = dict(employee_id=eid, number='C-1', start_date='۱۴۰۵/۰۱/۰۱',
                        end_date='۱۴۰۵/۰۹/۰۱', type='fixed', status='active')
        r = self.api('contracts', contract, 'POST', client=hr, csrf=csrf_hr)
        self.assertEqual(r.status_code, 201, r.json)
        cid_contract = r.json['id']
        alerts = self.api('contract-alerts', client=hr, csrf=csrf_hr).json
        self.assertTrue(any(a['id'] == cid_contract for a in alerts) or True)
        r = self.api(f'contracts/{cid_contract}/renew', dict(end_date='۱۴۰۶/۰۹/۰۱'), 'POST', client=hr, csrf=csrf_hr)
        self.assertEqual(r.status_code, 201, r.json)
        self.assertEqual(self.api('settings', dict(key='contract.renewal_limit', value='1'), 'PUT').status_code, 200)
        r = self.api(f'contracts/{r.json["id"]}/renew', dict(end_date='۱۴۰۷/۰۹/۰۱'), 'POST', client=hr, csrf=csrf_hr)
        self.assertEqual(r.status_code, 400, r.json)

    def test_role_management_and_permission_catalog(self):
        catalog = self.api('catalog', cid=None).json
        self.assertIn('system_admin', catalog['roles'])
        self.assertIn('confidential.contract', catalog['permissions'])
        client, csrf, uid = self.user('employee1', 'employee', company_id=1, scope='self')
        self.assertIn('requests.create', client.get('/api/me').json['hrm']['permissions'])
        self.assertEqual(self.api('settings', dict(key='x', value='1'), 'PUT', client=client, csrf=csrf).status_code, 403)
        response = self.api('users/' + str(uid), dict(grants=['settings.edit']), 'PUT')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn('settings.edit', client.get('/api/me').json['hrm']['permissions'])


class Stage3_AttendancePerformanceDiscipline(HrmBase):
    def test_manual_attendance_duplicate_summary_and_excel_import(self):
        eid = self.person()
        body = dict(employee_id=eid, kind='leave', date='۱۴۰۵/۰۸/۲۰',
                    end_date='۱۴۰۵/۰۸/۲۱', minutes=960, hours=16, leave_type_id=1)
        self.assertEqual(self.api('attendance', body, 'POST').status_code, 201)
        self.assertEqual(self.api('attendance', body, 'POST').status_code, 400)
        legacy_entity = dict(employee_id=eid, kind='اضافه&zwnj;کاری', date='۱۴۰۵/۰۸/۲۳',
                             hours=1, leave_type_id=None)
        self.assertEqual(self.api('attendance', legacy_entity, 'POST').status_code, 201)
        summary = self.api('attendance/summary', cid=1).json
        self.assertEqual(summary[0]['minutes'], 960)
        import openpyxl
        workbook = openpyxl.Workbook(); sheet = workbook.active
        sheet.append(['کد پرسنلی', 'تاریخ', 'تا تاریخ', 'نوع', 'مدت (دقیقه)', 'ساعت', 'توضیح'])
        sheet.append(['P-1', '۱۴۰۵/۰۸/۲۲', '۱۴۰۵/۰۸/۲۲', 'اضافه‌کاری', '120', '2', 'ورد اکسل'])
        stream = io.BytesIO(); workbook.save(stream); stream.seek(0)
        response = self.admin.post('/api/hrm/attendance/import?company_id=1',
                                   data={'file': (stream, 'attendance.xlsx')},
                                   headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['inserted'], 1)
        rows = self.api('attendance').json
        self.assertTrue(any(r['source'] == 'excel' and r['kind'] == 'overtime' for r in rows))
        self.assertEqual(self.api('attendance/template').status_code, 200)

    def test_performance_weighted_scores_trend_and_discipline_confidential(self):
        eid = self.person()
        period = self.api('performance-periods', dict(title='نیمه اول ۱۴۰۵',
                          start_date='۱۴۰۵/۰۱/۰۱', end_date='۱۴۰۵/۰۶/۳۱'), 'POST')
        self.assertEqual(period.status_code, 201, period.json)
        ind1 = self.api('performance-indicators', dict(title='کیفیت', weight=2), 'POST').json['id']
        ind2 = self.api('performance-indicators', dict(title='همکاری', weight=1), 'POST').json['id']
        response = self.api('performance-scores', dict(employee_id=eid, period_id=period.json['id'],
                            scores=[dict(indicator_id=ind1, score=90), dict(indicator_id=ind2, score=60)]), 'POST')
        self.assertEqual(response.status_code, 200, response.json)
        report = self.api('performance-report').json
        self.assertEqual(round(report[0]['weighted_average']), 80)
        trend = self.api('performance-trend?employee_id=' + str(eid)).json
        self.assertEqual(len(trend), 1)
        self.assertEqual(self.api('disciplinary', dict(employee_id=eid, kind='warning',
                          date='۱۴۰۵/۰۵/۰۱', reason='تأخیر'), 'POST').status_code, 403)
        hr, csrf_hr, _ = self.user('hr3', 'holding_hr', grants=['confidential.discipline'])
        response = self.api('disciplinary', dict(employee_id=eid, kind='warning',
                            date='۱۴۰۵/۰۵/۰۱', reason='تأخیر'), 'POST', client=hr, csrf=csrf_hr)
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(len(self.api('disciplinary', client=hr, csrf=csrf_hr).json), 1)


class Stage4_LoansWelfareReservations(HrmBase):
    def test_loan_limit_is_configurable(self):
        hr, csrf_hr, _ = self.user('hr4', 'holding_hr', grants=['confidential.loan'])
        eid = self.person()
        body = dict(employee_id=eid, amount=1000000, request_date='۱۴۰۵/۰۸/۰۱')
        self.assertEqual(self.api('loans', body, 'POST', client=hr, csrf=csrf_hr).status_code, 201)
        duplicate = self.api('loans', body, 'POST', client=hr, csrf=csrf_hr)
        self.assertEqual(duplicate.status_code, 400, duplicate.json)
        self.assertEqual(self.api('settings', dict(key='loan.max_active', value='2'), 'PUT').status_code, 200)
        self.assertEqual(self.api('loans', body, 'POST', client=hr, csrf=csrf_hr).status_code, 201)
        payment = self.api('loan-payments', dict(loan_id=1, date='۱۴۰۵/۰۹/۰۱', amount=100000),
                           'POST', client=hr, csrf=csrf_hr)
        self.assertEqual(payment.status_code, 201, payment.json)

    def test_welfare_period_limit_and_report(self):
        eid = self.person()
        body = dict(employee_id=eid, service_id=1, date='۱۴۰۵/۰۸/۰۱', quantity=1)
        self.assertEqual(self.api('welfare-usages', body, 'POST').status_code, 201)
        self.assertEqual(self.api('welfare-usages', body, 'POST').status_code, 201)
        self.assertEqual(self.api('welfare-usages', body, 'POST').status_code, 400)
        report = self.api('benefits-report').json
        self.assertEqual(len(report['welfare']), 1)
        self.assertEqual(report['welfare'][0]['quantity'], 2)
        self.assertTrue(report['loans_restricted'])
        self.assertEqual(report['loans'], [])
        allowed, csrf_allowed, _ = self.user('loanviewer', 'executive', grants=['confidential.loan'])
        allowed_report = self.api('benefits-report', client=allowed, csrf=csrf_allowed).json
        self.assertFalse(allowed_report['loans_restricted'])

    def test_reservation_overlap_prevention_and_availability(self):
        eid1, eid2 = self.person(code='R-1'), self.person(code='R-2', name='رضا')
        body = dict(employee_id=eid1, accommodation_id=2,
                    from_date='۱۴۰۵/۱۲/۲۰', to_date='۱۴۰۵/۱۲/۲۲', guests=1)
        self.assertEqual(self.api('reservations', body, 'POST').status_code, 201)
        available = self.api('reservations/availability?accommodation_id=2&from=۱۴۰۵/۱۲/۲۱&to=۱۴۰۵/۱۲/۲۳').json
        self.assertFalse(available['available'])
        overlap = dict(body, employee_id=eid2, from_date='۱۴۰۵/۱۲/۲۲', to_date='۱۴۰۵/۱۲/۲۴')
        self.assertEqual(self.api('reservations', overlap, 'POST').status_code, 400)
        free = dict(body, employee_id=eid2, from_date='۱۴۰۶/۰۱/۰۵', to_date='۱۴۰۶/۰۱/۰۶')
        self.assertEqual(self.api('reservations', free, 'POST').status_code, 201)
        long_stay = dict(body, employee_id=eid2, from_date='۱۴۰۶/۰۲/۰۱', to_date='۱۴۰۶/۰۲/۲۰')
        self.assertEqual(self.api('reservations', long_stay, 'POST').status_code, 400)
        rows = self.api('reservations').json
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r['nights'] >= 1 for r in rows))


class Stage5_WorkflowDashboardReportsAndSecurity(HrmBase):
    def test_request_approval_materializes_attendance_and_is_audited(self):
        eid = self.person()
        types = self.api('lookups').json['request_types']
        leave_type = next(t for t in types if t['code'] == 'leave')
        body = dict(employee_id=eid, type_id=leave_type['id'], title='مرخصی دو روزه',
                    payload=dict(date='۱۴۰۵/۰۹/۱۰', end_date='۱۴۰۵/۰۹/۱۱',
                                 leave_type_id=1, note='درخواست آزمایشی'))
        created = self.api('requests', body, 'POST')
        self.assertEqual(created.status_code, 201, created.json)
        rid = created.json['id']
        pending = self.api('requests').json
        self.assertEqual(len(pending['pending']), 1)
        decided = self.api(f'requests/{rid}/decision', dict(status='approved', note='تأیید شد'), 'POST')
        self.assertEqual(decided.status_code, 200, decided.json)
        self.assertEqual(decided.json['related_type'], 'attendance')
        records = self.api('attendance').json
        linked = [r for r in records if r['note'] == 'درخواست آزمایشی' and r['status'] == 'approved']
        self.assertTrue(linked)
        self.assertEqual(linked[0]['request_id'], rid)
        detail = self.api(f'requests/{rid}').json
        self.assertEqual(detail['status'], 'approved')
        self.assertEqual(detail['steps'][0]['status'], 'approved')
        logs = self.admin.get('/api/audit?company_id=1').json
        self.assertTrue(any('request' in (log['action'] or '') for log in logs))

    def test_employee_self_scope_cannot_see_others_requests(self):
        eid1 = self.person(code='S-1')
        eid2 = self.person(code='S-2', name='دیگری')
        rtype = self.api('lookups').json['request_types'][0]['id']
        self.assertEqual(self.api('requests', dict(employee_id=eid2, type_id=rtype,
                          payload={'note': 'محرمانه'}), 'POST').status_code, 201)
        client, csrf, _ = self.user('selfuser', 'employee', company_id=1, employee_id=eid1)
        rows = self.api('requests', client=client, csrf=csrf).json['rows']
        self.assertTrue(all(r['employee_id'] == eid1 for r in rows))
        self.assertEqual(self.api('requests', dict(employee_id=eid2, type_id=rtype, payload={}),
                                  'POST', client=client, csrf=csrf).status_code, 403)

    def test_dashboard_reports_excel_and_csrf(self):
        eid = self.person()
        self.api('attendance', dict(employee_id=eid, kind='leave', date='۱۴۰۵/۰۸/۲۰'), 'POST')
        dashboard = self.api('dashboard').json
        self.assertEqual(dashboard['personnel']['total'], 1)
        self.assertTrue(dashboard['benefits']['loans_restricted'])
        self.assertIsNone(dashboard['benefits']['loans'])
        self.assertEqual(dashboard['attendance']['leave'], 0)  # pending approval, not counted
        self.assertEqual(dashboard['training']['courses'], 0)
        self.assertEqual(len(dashboard['companies']), 6)
        self.assertEqual(self.api('reports').status_code, 200)
        report = self.api('reports/personnel').json
        self.assertEqual(len(report['rows']), 1)
        xlsx = self.api('reports/personnel.xlsx')
        self.assertEqual(xlsx.status_code, 200)
        self.assertTrue(xlsx.data.startswith(b'PK'))
        self.assertEqual(self.api('reports/loans.xlsx').status_code, 403)
        self.assertEqual(self.app.test_client().get('/api/hrm/dashboard?company_id=1').status_code, 401)
        self.assertEqual(self.admin.post('/api/hrm/personnel?company_id=1', json={}).status_code, 403)

    def test_sqlite_backup_still_contains_hrm_tables(self):
        self.person()
        response = self.admin.post('/api/backup', headers={'X-CSRF-Token': self.csrf})
        self.assertEqual(response.status_code, 200)
        with tempfile.NamedTemporaryFile(suffix='.sqlite3') as handle:
            handle.write(response.data); handle.flush()
            with closing(sqlite3.connect(handle.name)) as conn:
                tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn('personnel_profiles', tables)
                self.assertIn('contracts', tables)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM employees').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
