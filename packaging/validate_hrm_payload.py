"""Exercise the installed HRM application with its runtime dependencies only.

Run with the bundled interpreter: python -I validate_hrm_payload.py --install DIR.
All records, accounts, keys and backups are synthetic and isolated in a temporary
directory. This complements the legacy training/network payload validators.
"""
import argparse
from contextlib import closing
from datetime import date, timedelta
import hashlib
import io
import json
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import zipfile


def validate(installation):
    installation = installation.resolve()
    sys.path.insert(0, str(installation))
    import app as application_module
    assert Path(application_module.__file__).resolve().parent.parent == installation, 'Application was not imported from the installed payload'
    from app.documents import FIELDS
    from app.hrm_common import jalali_display

    checks = []
    password = secrets.token_urlsafe(24)
    today = date.today()
    iso = today.isoformat()
    pdf = b'%PDF-1.4\n% Synthetic HRM payload attachment\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n'
    with tempfile.TemporaryDirectory(prefix='nikan-hrm-runtime-validation-') as temporary:
        folder = Path(temporary)
        app = application_module.create_app(folder / 'data', testing=True)
        admin = app.test_client()
        assert admin.post('/api/setup', json={'username': 'hrm-payload-admin', 'password': password}).status_code == 200
        assert admin.post('/api/login', json={'username': 'hrm-payload-admin', 'password': password}).status_code == 200
        me = admin.get('/api/me').json
        headers = {'X-CSRF-Token': me['csrf']}
        assert me['hrm']['is_primary_admin'] and me['hrm']['all_companies']
        assert len(me['companies']) == 7 and 'موج نیکان' in {row['name'] for row in me['companies']}
        assert all(me['hrm']['permissions']['health'][operation] for operation in ('read', 'create', 'edit', 'delete', 'export'))
        checks.append('primary administrator has all five operations across all seven companies')

        def write(path, body, method='post', expected=(200, 201), client=admin, token=headers):
            response = getattr(client, method)('/api/' + path, headers=token, json=body)
            assert response.status_code in expected, (path, response.status_code, response.get_json())
            return response.get_json()

        first_site = write('hrm/sites?company_id=1', {'name': 'Synthetic runtime plant A', 'kind': 'factory'})['id']
        second_site = write('hrm/sites?company_id=1', {'name': 'Synthetic runtime plant B', 'kind': 'factory'})['id']
        other_site = write('hrm/sites?company_id=2', {'name': 'Synthetic other company plant', 'kind': 'factory'})['id']
        staff = []
        for cid, site, code in ((1, first_site, 'RUNTIME-1234'), (2, other_site, 'RUNTIME-9876'), (1, first_site, 'RUNTIME-2345')):
            staff.append(write('hrm/people', {'company_id': cid, 'site_id': site, 'code': code,
                                             'name': 'Synthetic ' + code, 'job': 'Synthetic operator',
                                             'department': 'Synthetic production', 'location': 'Synthetic hall',
                                             'start_date': iso}))
        first = staff[0]['id']
        assert staff[0]['passport_number'] == 'NP-01-1234'
        contract = write('hrm/contracts?company_id=1', {'employee_id': first, 'number': 'SYNTHETIC-CONTRACT-1',
                         'contract_type': 'Synthetic fixed term', 'start_date': iso,
                         'end_date': (today + timedelta(days=180)).isoformat(), 'status': 'active',
                         'notes': 'SYNTHETIC-CONTRACT-PRIVATE'})
        uploaded = admin.post('/api/hrm/documents?company_id=1', headers=headers, data={
            'employee_id': str(first), 'contract_id': str(contract['id']), 'permission_group': 'contracts',
            'title': 'SYNTHETIC-PRIVATE-FILE', 'file': (io.BytesIO(pdf), 'synthetic.pdf')})
        assert uploaded.status_code == 201, uploaded.get_json()
        document_id = uploaded.json['id']
        downloaded = admin.get(f'/api/hrm/documents/{document_id}/file')
        assert downloaded.status_code == 200 and downloaded.data == pdf
        assert downloaded.headers['Content-Disposition'].startswith('attachment;')
        attendance = write('hrm/attendance?company_id=1', {'employee_id': first, 'work_date': iso,
                             'kind': 'work', 'minutes': 480, 'note': 'Synthetic work record'})
        assert attendance['site_id'] == first_site
        checks.append('central personnel identity, company/site assignment, contract, private file roundtrip and attendance')

        loan = write('hrm/loans?company_id=1', {'employee_id': first, 'title': 'Synthetic internal loan',
                        'loan_kind': 'internal', 'amount_rials': 1000, 'issued_date': iso,
                        'due_date': (today + timedelta(days=30)).isoformat(), 'status': 'disbursed'})
        payment = write('hrm/loan_payments?company_id=1', {'loan_id': loan['id'], 'paid_date': iso,
                           'amount_rials': 200, 'installment_no': 1, 'reference': 'SYNTHETIC-REPAYMENT-1', 'status': 'posted'})
        assert payment['id'] > 0
        current_loan = next(row for row in admin.get('/api/hrm/loans?company_id=1').json if row['id'] == loan['id'])
        assert current_loan['paid_rials'] == 200 and current_loan['outstanding_rials'] == 800
        residence = write('hrm/properties?company_id=1', {'company_id': 1, 'site_id': first_site,
                              'name': 'Synthetic Mahmoudabad accommodation', 'capacity': 4, 'shared': 1, 'active': 1})
        stay = {'employee_id': first, 'property_id': residence['id'], 'start_date': (today + timedelta(days=10)).isoformat(),
                'end_date': (today + timedelta(days=12)).isoformat(), 'guests': 2, 'status': 'approved'}
        write('hrm/reservations?company_id=1', stay)
        conflict = admin.post('/api/hrm/reservations?company_id=2', headers=headers,
                              json={**stay, 'employee_id': staff[1]['id']})
        assert conflict.status_code == 409 and 'Synthetic RUNTIME' not in conflict.get_data(as_text=True)
        checks.append('loan repayment balance and globally conflicting shared accommodation reservations')

        equipment = write('hrm/ppe_catalog?company_id=1', {'site_id': first_site, 'name': 'Synthetic helmet',
                              'category': 'Synthetic PPE', 'lifetime_days': 30, 'active': 1})
        issue = write('hrm/ppe_issues?company_id=1', {'employee_id': first, 'ppe_id': equipment['id'],
                          'issue_date': iso, 'quantity': 1, 'status': 'delivered', 'receiver_reference': 'SYNTHETIC-RECEIPT'})
        assert issue['site_id'] == first_site
        write('hrm/health_records?company_id=1', {'employee_id': first, 'exam_date': iso,
              'exam_type': 'Synthetic occupational examination', 'fitness': 'fit',
              'restrictions': 'SYNTHETIC-CLINICAL-PRIVATE', 'physician': 'Synthetic physician'})

        def new_user(name, permissions, site=first_site, **extra):
            body = {'username': name, 'password': password, 'role': 'holding_manager',
                    'scopes': [{'company_id': 1, 'site_id': site}], 'permissions': permissions,
                    'training_access': False, **extra}
            created = write('hrm/access/users', body)
            client = app.test_client()
            assert client.post('/api/login', json={'username': name, 'password': password}).status_code == 200
            token = {'X-CSRF-Token': client.get('/api/me').json['csrf']}
            return created, client, token

        _, safety, _ = new_user('synthetic-hse', {'hse': {'read': True}, 'health_environment': {'read': True}})
        assert safety.get('/api/hrm/ppe_issues?company_id=1').status_code == 200
        assert safety.get('/api/hrm/health_records?company_id=1').status_code == 403
        combined = safety.get('/api/hrm/dashboard?section=health_environment&company_id=1')
        assert combined.status_code == 200 and 'SYNTHETIC-CLINICAL-PRIVATE' not in combined.get_data(as_text=True)
        assert combined.json['medical_alerts'] == []
        assert safety.get('/api/hrm/access/users').status_code == 403
        assert safety.get('/api/calendar').status_code == 403
        _, export_only, _ = new_user('synthetic-export-only', {'loans': {'export': True}})
        assert export_only.get('/api/hrm/loans?company_id=1').status_code == 403
        report = export_only.get('/api/hrm/reports/loans.xlsx?company_id=1')
        assert report.status_code == 200 and report.data.startswith(b'PK')
        _, read_only, _ = new_user('synthetic-read-only', {'loans': {'read': True}})
        assert read_only.get('/api/hrm/loans?company_id=1').status_code == 200
        assert read_only.get('/api/hrm/reports/loans.xlsx?company_id=1').status_code == 403
        _, creator, _ = new_user('synthetic-create-only', {'loans': {'create': True}})
        assert creator.get('/api/hrm/loans?company_id=1').status_code == 403
        options = creator.get('/api/hrm/people?module=loans&operation=create&company_id=1')
        assert options.status_code == 200 and any(row['id'] == first for row in options.json)
        assert all('national_id' not in row and 'notes' not in row for row in options.json)
        checks.append('PPE, separately confidential medical records, no employee portal and five independent operation grants')

        _, site_b, _ = new_user('synthetic-site-b', {'contracts': {'read': True}, 'loans': {'read': True}}, site=second_site)
        changed = write(f'hrm/people/{first}', {'site_id': second_site, 'department': 'Synthetic other unit',
                        'effective_date': iso, 'version': staff[0]['version']}, method='put')
        assert changed['id'] == first and changed['passport_number'] == staff[0]['passport_number']
        assert site_b.get('/api/hrm/contracts?company_id=1').json == []
        assert site_b.get(f'/api/hrm/contracts/{contract["id"]}?company_id=1').status_code == 403
        assert site_b.get(f'/api/hrm/documents/{document_id}/file').status_code == 403
        assert site_b.get('/api/hrm/loans?company_id=1').json == []
        checks.append('site transfer keeps person/reference identity and does not reveal former-site contract, attachment or loan')

        skill = admin.post('/api/competency-bank/S001/select?company_id=1', headers=headers)
        assert skill.status_code == 200, skill.json
        course = write('data/courses?company_id=1', {'skill_id': skill.json['id'], 'title': 'Synthetic shared legacy training',
                       'date': jalali_display(iso),
                       'hours': 4, 'cost': 1.000001, 'status': 'completed'})['id']
        members = [row['id'] for row in staff]
        write(f'courses/{course}/participants', {'employee_ids': members}, method='put')
        assert admin.get(f'/api/courses/{course}/cost-allocation').json['attendee_count'] == 0
        write(f'courses/{course}/attendance', {'employee_ids': members}, method='put')
        allocation = admin.get(f'/api/courses/{course}/cost-allocation').json
        assert allocation['attendee_count'] == 3 and allocation['total_cost_rials'] == 1_000_001
        assert {row['company_id']: row['cost_rials'] for row in allocation['companies']} == {1: 666667, 2: 333334}
        actual = {field: 'Synthetic complete personal profile ' + field + ' END' for field in FIELDS}
        write(f'passports/{first}/document?company_id=1', {'overrides': actual}, method='put')
        for export in ('profile.docx', 'commitment.docx'):
            word = admin.get(f'/api/passports/{first}/{export}?company_id=1')
            assert word.status_code == 200 and word.data.startswith(b'PK')
            with zipfile.ZipFile(io.BytesIO(word.data)) as package:
                assert package.testzip() is None
                if export == 'profile.docx':
                    content = package.read('word/document.xml').decode()
                    assert all(value in content for value in actual.values())
        checks.append('legacy shared training, actual-headcount exact-rial cost allocation and full/annex Word runtime dependencies')

        backup = admin.post('/api/backup', headers=headers)
        assert backup.status_code == 200 and backup.data.startswith(b'SQLite format 3')
        restored = folder / 'restored';restored.mkdir()
        (restored / 'nikan.sqlite3').write_bytes(backup.data)
        restored_app = application_module.create_app(restored, testing=True)
        restored_admin = restored_app.test_client()
        assert restored_admin.post('/api/login', json={'username': 'hrm-payload-admin', 'password': password}).status_code == 200
        assert restored_admin.get(f'/api/hrm/documents/{document_id}/file').data == pdf
        assert restored_admin.get(f'/api/courses/{course}/cost-allocation').json == allocation
        with closing(sqlite3.connect(restored_app.config['DB'])) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            assert connection.execute('SELECT count(*) FROM employees').fetchone()[0] == len(staff)
            assert connection.execute('SELECT count(*) FROM hrm_employee_details').fetchone()[0] == len(staff)
            assert connection.execute('SELECT sum(attended) FROM course_participants').fetchone()[0] == len(staff)
        checks.append('raw backup/restart preserves central identities, original confidential BLOB and actual attendance/cost')
    return dict(passed=True, installation=str(installation), platform=sys.platform,
                python=sys.version.split()[0], checks=checks,
                synthetic_attachment_sha256=hashlib.sha256(pdf).hexdigest(),
                native_windows_execution=sys.platform == 'win32')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--install', type=Path, required=True)
    parser.add_argument('--report', type=Path)
    arguments = parser.parse_args()
    result = validate(arguments.install)
    if arguments.report:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('HRM runtime payload passed: ' + '; '.join(result['checks']) + '.')
    print(json.dumps({'passed': True, 'platform': result['platform'], 'python': result['python'],
                      'native_windows_execution': result['native_windows_execution']}))
