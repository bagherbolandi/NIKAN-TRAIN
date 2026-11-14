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
    from app.hrm_common import jalali_display, today_iso

    checks = []
    password = secrets.token_urlsafe(24)
    iso = today_iso()
    today = date.fromisoformat(iso)
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

        # Organization changes must retain linked records and retired identifiers.
        empty_site = write('hrm/sites?company_id=1', {'name': 'Synthetic disposable site', 'kind': 'factory'})
        write(f'hrm/sites/{empty_site["id"]}?company_id=1', {'version': empty_site['version']}, method='delete')
        replacement_site = write('hrm/sites?company_id=1', {'name': 'Synthetic replacement site', 'kind': 'factory'})
        assert replacement_site['id'] > empty_site['id']
        write(f'hrm/sites/{replacement_site["id"]}?company_id=1', {'version': replacement_site['version']}, method='delete')
        linked_site = next(row for row in admin.get('/api/hrm/sites?company_id=1').json if row['id'] == second_site)
        assert admin.delete(f'/api/hrm/sites/{second_site}?company_id=1', headers=headers,
                            json={'version': linked_site['version']}).status_code == 409
        roster_a = admin.get(f'/api/hrm/site-roster?company_id=1&site_id={first_site}')
        roster_b = admin.get(f'/api/hrm/site-roster?company_id=1&site_id={second_site}')
        assert roster_a.status_code == roster_b.status_code == 200
        assert {row['id'] for row in roster_a.json['people']} == {staff[2]['id']}
        assert {row['id'] for row in roster_b.json['people']} == {first}
        assert roster_b.json['totals']['employees'] == 1 and roster_b.json['temporary_assignments'] == []
        checks.append('admin site deletion preserves linked history, never reuses retired IDs and shows current site membership after transfer')

        period = write('hrm/evaluation_periods?company_id=1', {
            'company_id': 1, 'site_id': second_site, 'title': 'Synthetic productivity period',
            'start_date': iso, 'end_date': (today + timedelta(days=30)).isoformat(), 'status': 'open'})
        policy = write('hrm/accord_policies?company_id=1', {
            'company_id': 1, 'site_id': second_site, 'period_id': period['id'],
            'title': 'Synthetic management accord', 'method': 'individual_base',
            'minimum_score': 0, 'note': 'Synthetic approved calculation basis'})
        seeded = write(f'hrm/accord_policies/{policy["id"]}/seed-criteria?company_id=1', {'version': policy['version']})
        assert seeded['criteria_count'] == 8
        criteria = [row for row in admin.get('/api/hrm/performance_criteria?company_id=1').json
                    if row['period_id'] == period['id']]
        assert len(criteria) == 8 and sum(row['weight'] for row in criteria) == 100
        for criterion in criteria:
            write('hrm/performance_scores?company_id=1', {
                'employee_id': first, 'period_id': period['id'], 'criterion_id': criterion['id'],
                'score': 80, 'note': 'Synthetic measurable performance evidence'})
        entry = write('hrm/accord_entries?company_id=1', {
            'policy_id': policy['id'], 'employee_id': first, 'base_rials': 1_000_001,
            'scheduled_minutes': 480, 'actual_minutes': 360,
            'note': 'Synthetic confirmed participation; no automatic medical or leave penalty'})
        preview_path = f'/api/hrm/accord_report?company_id=1&policy_id={policy["id"]}'
        preview = admin.get(preview_path)
        assert preview.status_code == 200, preview.json
        assert len(preview.json['rows']) == 1
        calculated = preview.json['rows'][0]
        assert calculated['score_percent'] == 80 and calculated['participation_percent'] == 75
        assert calculated['is_complete'] and calculated['accord_rials'] == 600_001
        assert not preview.json['can_finalize']  # The review period remains open.
        current_period = admin.get(f'/api/hrm/evaluation_periods/{period["id"]}?company_id=1')
        assert current_period.status_code == 200, current_period.json
        write(f'hrm/evaluation_periods/{period["id"]}?company_id=1',
              {'version': current_period.json['version'], 'status': 'closed'}, method='put')
        reviewed = admin.get(preview_path)
        assert reviewed.status_code == 200 and reviewed.json['can_finalize']
        finalized = write(f'hrm/accord_policies/{policy["id"]}/finalize?company_id=1', {
            'version': reviewed.json['policy']['version'], 'calculation_token': reviewed.json['calculation_token']})
        assert finalized['status'] == 'finalized' and len(finalized['rows']) == 1
        accord_result = finalized['rows'][0]
        assert accord_result['employee_id'] == first and accord_result['site_id'] == second_site
        assert accord_result['accord_rials'] == 600_001 and accord_result['locked']
        assert admin.put(f'/api/hrm/accord_entries/{entry["id"]}?company_id=1', headers=headers,
                         json={'version': entry['version'], 'base_rials': 2_000_000}).status_code == 409
        _, performance_viewer, _ = new_user('synthetic-performance-no-money',
                                            {'performance': {'read': True}}, site=second_site)
        assert performance_viewer.get('/api/hrm/accord_results?company_id=1').status_code == 403
        _, accord_viewer, accord_headers = new_user('synthetic-accord-reader',
            {'productivity': {'read': True, 'export': True}}, site=second_site)
        visible_results = accord_viewer.get('/api/hrm/accord_results?company_id=1')
        assert visible_results.status_code == 200 and len(visible_results.json) == 1
        assert visible_results.json[0]['accord_rials'] == 600_001
        assert accord_viewer.put(f'/api/hrm/accord_entries/{entry["id"]}?company_id=1', headers=accord_headers,
                                json={'version': entry['version'], 'actual_minutes': 480}).status_code == 403
        accord_excel = accord_viewer.get('/api/hrm/reports/accord_results.xlsx?company_id=1')
        assert accord_excel.status_code == 200 and accord_excel.data.startswith(b'PK')
        checks.append('canonical eight-item weighted accord, exact-rial participation, explicit financial grants, closed-period finalization and immutable result')

        matrix_path = '/api/hrm/skill-matrix?company_id=1'
        matrix_probe = admin.get(matrix_path)
        assert matrix_probe.status_code == 200, ('Canonical skill matrix endpoint is unavailable', matrix_probe.status_code, matrix_probe.get_json())
        second_skill = admin.post('/api/competency-bank/S002/select?company_id=1', headers=headers)
        assert second_skill.status_code == 200, second_skill.json
        write('data/requirements?company_id=1', {'job': 'Synthetic operator',
              'skill_id': second_skill.json['id'], 'level': 2})
        assessment_metadata = dict(assessor_name='Synthetic named assessor',
            assessment_method='Synthetic work observation', evidence='SYNTHETIC-ASSESSMENT-EVIDENCE-014')
        assessment = write('hrm/training_assessments?company_id=1', {
            'employee_id': first, 'competency_code': 'S002', 'level': 0,
            'assessment_date': iso, **assessment_metadata})

        def matrix_cell(client, employee_id):
            response = client.get(matrix_path)
            assert response.status_code == 200, response.json
            return next(row for row in response.json['rows']
                        if row['employee_id'] == employee_id and row['competency_code'] == 'S002')

        assessed = matrix_cell(admin, first)
        assert assessed['current_level'] == 0 and assessed['required_level'] == 2 and assessed['gap'] == 2
        assert assessed['has_assessment'] and assessed['has_current_level'] and assessed['assessment_id'] == assessment['id']
        assert assessed['assessment_date'] == iso and assessed['assessor_name'] == assessment_metadata['assessor_name']
        assert assessed['assessment_method'] == assessment_metadata['assessment_method']
        assert assessed['assessment_evidence'] == assessment_metadata['evidence']
        assert assessed['recorded_by'] == {'id': me['hrm']['user_id'], 'username': me['username']}
        unknown = matrix_cell(admin, staff[2]['id'])
        assert unknown['current_level'] is None and unknown['gap'] is None and unknown['status'] == 'unassessed'
        overview_path = f'/api/hrm/people/{first}/overview?company_id=1'
        overview = admin.get(overview_path)
        assert overview.status_code == 200, overview.json
        assert overview.json['person']['id'] == first and overview.json['person']['site_id'] == second_site
        assert overview.json['current_assignment_visible'] and 'national_id' not in overview.json['person']
        assert overview.json['sections']['contracts']['resource_counts']['contracts'] == 1
        assert overview.json['sections']['contracts']['resource_counts']['documents'] == 1
        assert 'training_assessments' in overview.json['sections']['training']['resources']
        assert 'SYNTHETIC-CLINICAL-PRIVATE' not in overview.get_data(as_text=True)
        matrix_excel = admin.get('/api/hrm/skill-matrix.xlsx?company_id=1')
        assert matrix_excel.status_code == 200 and matrix_excel.data.startswith(b'PK')
        with zipfile.ZipFile(io.BytesIO(matrix_excel.data)) as package:
            assert package.testzip() is None and 'xl/workbook.xml' in package.namelist()
        checks.append('single-person dossier links, canonical skill matrix, explicit zero versus unknown and dated assessor/recorder/evidence trace')

        forms = admin.get('/api/hrm/excel/forms?company_id=1')
        assert forms.status_code == 200, forms.json
        duplicate_kinds = {'employees', 'assessments', 'courses', 'records'}
        assert not (duplicate_kinds & {form['kind'] for form in forms.json['legacy_forms']})
        assert {'people', 'training_assessments', 'training_courses', 'training_records'}.issubset(
            {form['resource'] for form in forms.json['forms']})
        template_path = f'/api/hrm/excel/templates/people?company_id=1&site_id={second_site}'
        template = admin.get(template_path)
        assert template.status_code == 200 and template.data.startswith(b'PK'), template.get_json()
        with zipfile.ZipFile(io.BytesIO(template.data)) as package:
            assert any(name.startswith('xl/media/') for name in package.namelist()), 'Approved Nikan logo missing from input template'
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(template.data))
        sheet = workbook['ورود اطلاعات']
        technical_keys = [cell.value for cell in sheet[5]]
        preview_person = dict(code='000012345', name='Synthetic preview-only person', company_id=1,
                              site_id=second_site, department='Synthetic other unit', job='Synthetic operator')
        for key, value in preview_person.items():
            cell = sheet.cell(7, technical_keys.index(key) + 1, value)
            if key == 'code':
                cell.number_format = '@'
        stream = io.BytesIO(); workbook.save(stream); workbook.close()
        with closing(sqlite3.connect(app.config['DB'])) as connection:
            before_preview = dict(
                employees=connection.execute('SELECT id,code,name FROM employees ORDER BY id').fetchall(),
                details=connection.execute('SELECT count(*) FROM hrm_employee_details').fetchone()[0],
                history=connection.execute('SELECT count(*) FROM hrm_employment_history').fetchone()[0],
                audit=connection.execute('SELECT count(*) FROM audit').fetchone()[0],
                counters=connection.execute('SELECT * FROM company_registry ORDER BY id').fetchall())
        preview_import = admin.post(f'/api/hrm/excel/import/people?company_id=1&site_id={second_site}',
            headers=headers, data={'mode': 'preview', 'policy': 'insert',
                                   'file': (io.BytesIO(stream.getvalue()), 'Synthetic-HRM-people.xlsx')})
        assert preview_import.status_code == 200, preview_import.json
        assert preview_import.json['ok'] and preview_import.json['can_commit'] and not preview_import.json['applied']
        assert preview_import.json['planned_created'] == 1 and preview_import.json['records'] == []
        with closing(sqlite3.connect(app.config['DB'])) as connection:
            after_preview = dict(
                employees=connection.execute('SELECT id,code,name FROM employees ORDER BY id').fetchall(),
                details=connection.execute('SELECT count(*) FROM hrm_employee_details').fetchone()[0],
                history=connection.execute('SELECT count(*) FROM hrm_employment_history').fetchone()[0],
                audit=connection.execute('SELECT count(*) FROM audit').fetchone()[0],
                counters=connection.execute('SELECT * FROM company_registry ORDER BY id').fetchall())
        assert after_preview == before_preview
        assert admin.get('/api/hrm/accord_results?company_id=1').json == [accord_result]
        checks.append('unified branded Excel hub excludes duplicate training/personnel entry forms and rolls preview back without changing identity counters or financial results')

        management_path = '/api/hrm/management-overview?company_id=1'
        management = admin.get(management_path)
        assert management.status_code == 200, ('Management overview is unavailable in this payload', management.status_code, management.get_json())
        management = management.json
        assert management['as_of'] == iso and management['horizon_days'] == 30
        assert management['due_before'] == (today + timedelta(days=30)).isoformat()
        assert management['metrics']['current_personnel'] == 2
        assert {'training_gaps', 'training_unassessed', 'training_evidence_reviews',
                'medical_follow_up'}.issubset({row['key'] for row in management['categories']})
        assert all(len(category['items']) <= 6 and 'target' in category
                   for category in management['categories'])
        local_management = site_b.get(management_path)
        assert local_management.status_code == 200, local_management.json
        assert {row['module'] for row in local_management.json['categories']} == {'contracts'}
        assert all(row['count'] == 0 and row['items'] == [] for row in local_management.json['categories'])
        safety_management = safety.get(management_path)
        assert safety_management.status_code == 200, safety_management.json
        assert all(row['module'] != 'health' for row in safety_management.json['categories'])
        assert 'medical_follow_up' not in safety_management.json['metrics']
        assert 'SYNTHETIC-CLINICAL-PRIVATE' not in safety_management.get_data(as_text=True)
        assert safety.get('/api/hrm/management-overview?company_id=2').status_code == 403
        assert creator.get(management_path).status_code == 403
        index = admin.get('/')
        assert index.status_code == 200
        for name in ('workspace-shell.js', 'workspace-shell.css'):
            assert ('/static/' + name) in index.get_data(as_text=True)
            asset = admin.get('/static/' + name)
            assert asset.status_code == 200 and len(asset.data) > 100
        with closing(sqlite3.connect(app.config['DB'])) as connection:
            after_management = dict(
                employees=connection.execute('SELECT id,code,name FROM employees ORDER BY id').fetchall(),
                details=connection.execute('SELECT count(*) FROM hrm_employee_details').fetchone()[0],
                history=connection.execute('SELECT count(*) FROM hrm_employment_history').fetchone()[0],
                audit=connection.execute('SELECT count(*) FROM audit').fetchone()[0],
                counters=connection.execute('SELECT * FROM company_registry ORDER BY id').fetchall())
        assert after_management == after_preview
        checks.append('professional shell assets and scoped read-only management follow-up preserve identity, exclude ungranted medical data and never fabricate a zero for unavailable sections')

        backup = admin.post('/api/backup', headers=headers)
        assert backup.status_code == 200 and backup.data.startswith(b'SQLite format 3')
        restored = folder / 'restored';restored.mkdir()
        (restored / 'nikan.sqlite3').write_bytes(backup.data)
        restored_app = application_module.create_app(restored, testing=True)
        restored_admin = restored_app.test_client()
        assert restored_admin.post('/api/login', json={'username': 'hrm-payload-admin', 'password': password}).status_code == 200
        assert restored_admin.get(f'/api/hrm/documents/{document_id}/file').data == pdf
        assert restored_admin.get(f'/api/courses/{course}/cost-allocation').json == allocation
        restored_results = restored_admin.get('/api/hrm/accord_results?company_id=1')
        assert restored_results.status_code == 200 and restored_results.json == [accord_result]
        restored_policy = restored_admin.get(f'/api/hrm/accord_policies/{policy["id"]}?company_id=1')
        assert restored_policy.status_code == 200 and restored_policy.json['status'] == 'finalized'
        restored_assessed = matrix_cell(restored_admin, first)
        assert restored_assessed == assessed
        restored_directory = restored_admin.get('/api/hrm/people-directory?company_id=1')
        assert restored_directory.status_code == 200
        assert sum(row['id'] == first for row in restored_directory.json['people']) == 1
        restored_overview = restored_admin.get(overview_path)
        assert restored_overview.status_code == 200 and restored_overview.json == overview.json
        restored_management = restored_admin.get(management_path)
        assert restored_management.status_code == 200 and restored_management.json == management
        with closing(sqlite3.connect(restored_app.config['DB'])) as connection:
            assert connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
            assert connection.execute('SELECT count(*) FROM employees').fetchone()[0] == len(staff)
            assert connection.execute('SELECT count(*) FROM hrm_employee_details').fetchone()[0] == len(staff)
            assert connection.execute('SELECT sum(attended) FROM course_participants').fetchone()[0] == len(staff)
        checks.append('raw backup/restart preserves one central identity, matrix zero and assessment trace, confidential BLOB, actual attendance/cost and exact finalized accord evidence')
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
