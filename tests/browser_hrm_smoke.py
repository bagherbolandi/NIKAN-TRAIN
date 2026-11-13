"""Real Chromium checks for management HRM workflows and server-side scope grants.

Uses a temporary data directory and an actual Flask server. Run with
.venv/bin/python tests/browser_hrm_smoke.py (Chromium at /usr/bin/chromium).
"""
from io import BytesIO
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

from openpyxl import load_workbook
from playwright.sync_api import sync_playwright, expect

PASSWORD = 'Browser-HRM-' + secrets.token_urlsafe(18)


def check(base):
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get('NIKAN_CHROMIUM', '/usr/bin/chromium'),
                                     headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(base)
            page.locator('#auth-title').filter(has_text='ساخت حساب').wait_for()
            page.locator('#auth [name=username]').fill('hrm-browser-admin')
            page.locator('#auth [name=password]').fill(PASSWORD)
            page.locator('#auth button').click()
            page.locator('#shell').wait_for(state='visible')
            page.locator('[data-hrm-dashboard="holding"]').wait_for()
            assert page.locator('#company option').count() == 7
            assert page.locator('[data-page=hrm_health_environment]').count() == 1
            assert page.locator('[data-page=hrm_health],[data-page=hrm_environment]').count() == 0
            assert page.locator('[data-hrm-dashboard]').count() == 5
            assert page.locator('[data-hrm-metric]').count() > 0
            initial = page.request.get(base + '/api/hrm/dashboard?section=holding').json()
            assert any(metric.get('missing') or metric.get('value') is None for metric in initial['metrics']), initial
            csrf = page.request.get(base + '/api/me').json()['csrf']

            def api(method, path, data=None, status=200, client=None, token=None):
                response = (client or page.request).fetch(base + '/api/' + path, method=method,
                                                         data=data, headers={'X-CSRF-Token': token or csrf})
                assert response.status == status, (method, path, response.status, response.text())
                return response.json()

            site1 = api('POST', 'hrm/sites?company_id=1', {'name': 'سایت آزمون اول', 'kind': 'factory'}, 201)['id']
            site2 = api('POST', 'hrm/sites?company_id=1', {'name': 'سایت آزمون دوم', 'kind': 'office'}, 201)['id']
            site3 = api('POST', 'hrm/sites?company_id=2', {'name': 'سایت شرکت دیگر', 'kind': 'factory'}, 201)['id']

            # Create a central person in the real management form, including a site.
            page.locator('[data-page=hrm_personnel]').click()
            page.locator('[data-hrm-resource=people]').click()
            page.locator('#hrm-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=code]').fill('HRM-BROWSER-001')
            form.locator('[name=name]').fill('همکار آزمون مرورگر')
            form.locator('[name=company_id]').select_option('1')
            form.locator('[name=site_id]').select_option(str(site1))
            form.locator('[name=department]').fill('تولید')
            form.locator('[name=job]').fill('کارشناس')
            form.locator('[name=status]').select_option('active')
            form.locator('#hrm-save').click()
            page.locator('#hrm-editor').wait_for(state='hidden')
            page.locator('#hrm-table').get_by_text('همکار آزمون مرورگر', exact=True).wait_for()
            person = api('GET', 'hrm/people?company_id=1')[0]
            eid = person['id']
            foreign = api('POST', 'hrm/people?company_id=1', {
                'code': 'HRM-BROWSER-002', 'name': 'همکار سایت دیگر', 'company_id': 1,
                'site_id': site2, 'department': 'تولید', 'job': 'کارشناس', 'status': 'active'}, 201)['id']
            other_company = api('POST', 'hrm/people?company_id=2', {
                'code': 'HRM-BROWSER-003', 'name': 'همکار شرکت دیگر', 'company_id': 2,
                'site_id': site3, 'department': 'تولید', 'job': 'کارشناس', 'status': 'active'}, 201)['id']
            # Form edit respects the optimistic version and keeps the central id.
            page.locator(f'[data-hrm-edit="{eid}"]').click()
            page.locator('#hrm-form [name=phone]').fill('09120000000')
            page.locator('#hrm-save').click()
            page.locator('#hrm-editor').wait_for(state='hidden')
            assert api('GET', f'hrm/people/{eid}?company_id=1')['phone'] == '09120000000'
            page.locator('#hrm-search').fill('not-an-existing-record')
            expect(page.locator('#hrm-table')).to_contain_text('رکوردی پیدا نشد')
            page.locator('#hrm-search').fill('')

            # Company/site/unit/date filters are sent to the server, including exports.
            page.locator('#hrm-filters [name=site_id]').select_option(str(site1))
            page.locator('#hrm-filters [name=department]').fill('تولید')
            page.locator('#hrm-filters button[type=submit]').click()
            expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
            with page.expect_download() as saved:
                page.locator('#hrm-export').click()
            assert Path(saved.value.path()).read_bytes().startswith(b'PK')
            page.locator('#hrm-clear-filters').click()
            expect(page.locator('#hrm-table tbody tr')).to_have_count(2)

            # Renewals append a linked contract; uploads/downloads retain real bytes.
            page.locator('[data-page=hrm_contracts]').click()
            page.locator('#hrm-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=employee_id]').select_option(str(eid))
            form.locator('[name=number]').fill('BR-C-001')
            form.locator('[name=contract_type]').fill('مدت معین')
            form.locator('[name=start_date]').fill('1405/01/01')
            form.locator('[name=end_date]').fill('1405/12/29')
            form.locator('[name=status]').select_option('active')
            form.locator('#hrm-save').click()
            page.locator('#hrm-editor').wait_for(state='hidden')
            page.locator('[data-hrm-renew]').click()
            form = page.locator('#hrm-form')
            form.locator('[name=number]').fill('BR-C-002')
            form.locator('[name=start_date]').fill('1406/01/01')
            form.locator('[name=end_date]').fill('1406/12/29')
            form.locator('#hrm-save').click()
            page.locator('#hrm-editor').wait_for(state='hidden')
            expect(page.locator('#hrm-table tbody tr')).to_have_count(2)
            page.locator('[data-page=hrm_documents]').click()
            page.locator('#hrm-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=employee_id]').select_option(str(eid))
            form.locator('[name=title]').fill('مدرک آزمایشی')
            form.locator('[name=permission_group]').select_option('documents')
            doc = b'%PDF-1.4\nBrowser HRM management document\n%%EOF'
            form.locator('[name=file]').set_input_files({'name': 'record.pdf', 'mimeType': 'application/pdf', 'buffer': doc})
            form.locator('#hrm-save').click()
            page.locator('#hrm-editor').wait_for(state='hidden')
            with page.expect_download() as saved:
                page.locator('[data-hrm-file]').click()
            assert Path(saved.value.path()).read_bytes() == doc
            page.locator('[data-hrm-edit]').click()
            page.locator('#hrm-form [name=title]').fill('مدرک آزمایشی اصلاح‌شده')
            page.locator('#hrm-save').click()
            page.locator('#hrm-editor').wait_for(state='hidden')
            expect(page.locator('#hrm-table')).to_contain_text('مدرک آزمایشی اصلاح‌شده')

            # The actual Excel template is populated, previewed, and then committed.
            page.locator('[data-page=hrm_attendance]').click()
            page.locator('[data-hrm-resource=attendance]').click()
            page.locator('#hrm-attendance-import').click()
            with page.expect_download() as saved:
                page.locator('#hrm-attendance-template').click()
            workbook = load_workbook(BytesIO(Path(saved.value.path()).read_bytes()))
            worksheet = workbook['ورود اطلاعات'] if 'ورود اطلاعات' in workbook.sheetnames else workbook.worksheets[0]
            header = next(row for row in worksheet.iter_rows() if any(cell.value == 'employee_code' for cell in row))
            data_row = header[0].row + 2
            values = {'employee_code': 'HRM-BROWSER-001', 'work_date': '1405/08/01', 'kind': 'work', 'minutes': 480, 'note': 'آزمون مرورگر'}
            for cell in header:
                if cell.value in values:
                    worksheet.cell(data_row, cell.column, values[cell.value])
            buffer = BytesIO(); workbook.save(buffer)
            page.locator('#hrm-import-file').set_input_files({'name': 'attendance.xlsx', 'mimeType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'buffer': buffer.getvalue()})
            page.locator('#hrm-import-check').click()
            expect(page.locator('#hrm-save')).to_be_enabled()
            assert api('GET', 'hrm/attendance?company_id=1') == []
            page.locator('#hrm-save').click()
            page.locator('#hrm-import-preview').get_by_text('نتیجه ثبت').wait_for()
            assert len(api('GET', 'hrm/attendance?company_id=1')) == 1
            page.locator('#hrm-editor [data-hrm-close]').first.click()
            page.locator('[data-hrm-delete]').wait_for()
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator('[data-hrm-delete]').click()
            expect(page.locator('#hrm-table tbody tr')).to_have_count(0)
            page.locator('#hrm-include-archived').check()
            page.locator('[data-hrm-restore]').wait_for()
            expect(page.locator('#hrm-table')).to_contain_text('بایگانی‌شده')
            page.locator('[data-hrm-restore]').click()
            expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
            expect(page.locator('[data-hrm-restore]')).to_have_count(0)
            assert len(api('GET', 'hrm/attendance?company_id=1')) == 1

            # All operational workspaces and specialty dashboards render without JS errors.
            for module in ('performance', 'discipline', 'loans', 'welfare', 'accommodation', 'hse', 'health_environment', 'training', 'reports'):
                page.locator(f'#nav [data-page=hrm_{module}]').click()
                page.locator('#hrm-filters').wait_for()
                assert page.locator('#retry-page').count() == 0, module
            # The site facade writes into the existing training identities and attendance.
            page.locator('#nav [data-page=hrm_training]').click()
            page.locator('[data-hrm-resource=training_courses]').click()
            page.locator('#hrm-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=site_id]').select_option(str(site1))
            form.locator('[name=department]').fill('تولید')
            form.locator('[name=competency_code]').select_option(index=1)
            competency_code = form.locator('[name=competency_code]').input_value()
            form.locator('[name=title]').fill('آموزش مشترک پرونده سایت')
            form.locator('[name=date]').fill('1405/08/01')
            form.locator('[name=hours]').fill('2')
            form.locator('[name=cost]').fill('10')
            form.locator('[name=status]').select_option('completed')
            form.locator('[name=calendar_scope]').select_option('internal')
            form.locator('#hrm-save').click(); page.locator('#hrm-editor').wait_for(state='hidden')
            course = next(row for row in api('GET', 'hrm/training_courses?company_id=1') if row['title'] == 'آموزش مشترک پرونده سایت')
            page.locator('[data-hrm-resource=training_records]').click(); page.locator('#hrm-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=employee_id]').select_option(str(eid))
            form.locator('[name=course_id]').select_option(str(course['id']))
            form.locator('[name=level]').fill('2')
            form.locator('[name=score]').fill('88')
            form.locator('[name=attended]').select_option('1')
            form.locator('#hrm-save').click(); page.locator('#hrm-editor').wait_for(state='hidden')
            expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
            page.locator('[data-hrm-resource=training_assessments]').click(); page.locator('#hrm-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=employee_id]').select_option(str(eid))
            form.locator('[name=competency_code]').select_option(competency_code)
            form.locator('[name=level]').fill('2')
            form.locator('[name=assessment_date]').fill('1405/08/02')
            form.locator('#hrm-save').click(); page.locator('#hrm-editor').wait_for(state='hidden')
            expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
            page.locator('[data-hrm-resource=training_requirements]').click(); page.locator('#hrm-add').click()
            form = page.locator('#hrm-form')
            form.locator('[name=site_id]').select_option(str(site1))
            form.locator('[name=department]').fill('تولید')
            form.locator('[name=job]').fill('کارشناس')
            form.locator('[name=competency_code]').select_option(competency_code)
            form.locator('[name=required_level]').fill('2')
            form.locator('[name=renewal_days]').fill('365')
            form.locator('[name=onboarding]').select_option('1')
            form.locator('#hrm-save').click(); page.locator('#hrm-editor').wait_for(state='hidden')
            expect(page.locator('#hrm-table tbody tr')).to_have_count(1)
            report = api('GET', 'hrm/training_report?company_id=1&site_id=' + str(site1))
            assert report['metrics']['actual_person_hours'] == 2, report
            page.screenshot(path='/tmp/nikan-hrm-training.png', full_page=True)
            foreign_course = api('POST', 'hrm/training_courses?company_id=1', {
                'company_id': 1, 'site_id': site2, 'department': 'تولید', 'competency_code': competency_code,
                'title': 'دوره سایت خارج از محدوده', 'date': '1405/08/01', 'hours': 1, 'status': 'planned', 'calendar_scope': 'internal'}, 201)
            # Active work at another site must visibly queue a safety requirement review.
            api('POST', 'hrm/temporary_assignments?company_id=1', {'employee_id': eid, 'target_site_id': site2,
                'start_date': '1405/01/01', 'end_date': '1406/12/29', 'activity_location': 'خط موقت', 'status': 'active'}, 201)
            ppe = api('POST', 'hrm/ppe_catalog?company_id=1', {'name': 'دستکش آزمون بازنگری', 'active': 1}, 201)
            api('POST', 'hrm/ppe_requirements?company_id=1', {'site_id': site1, 'department': 'تولید', 'job': 'کارشناس',
                'ppe_id': ppe['id'], 'quantity': 1, 'active': 1}, 201)
            safety = api('GET', 'hrm/hse_report?company_id=1')
            assert safety['assignment_reviews'] and any(row['review_required'] for row in safety['ppe']), safety
            page.locator('#nav [data-page=hrm_dashboard]').click()
            for section in ('holding', 'personnel', 'training', 'hse', 'health_environment'):
                page.locator(f'[data-hrm-dashboard={section}]').click()
                page.locator('#hrm-filters').wait_for()
                assert page.locator('#retry-page').count() == 0, section
                if section == 'hse':
                    expect(page.locator('#hrm-alerts')).to_contain_text('بازنگری الزامات پس از تغییر محل فعالیت')
                    expect(page.locator('#hrm-alerts')).to_contain_text('دستکش آزمون بازنگری')
                    expect(page.locator('#hrm-alerts')).to_contain_text('نیازمند بازنگری')
                    page.screenshot(path='/tmp/nikan-hrm-safety.png', full_page=True)
            page.screenshot(path=os.environ.get('NIKAN_HRM_SCREENSHOT', '/tmp/nikan-hrm-dashboard.png'), full_page=True)

            # A site-scoped HR/Safety officer can create but cannot edit/export or read medicine.
            restricted_password = 'Restricted-' + secrets.token_urlsafe(18)
            grants = {module: {'read': True, 'create': True, 'edit': False, 'delete': False, 'export': False}
                      for module in ('personnel', 'hse')}
            created = api('POST', 'hrm/access/users', {'username': 'site-hr-safety', 'password': restricted_password,
                          'role': 'company_hr', 'scopes': [{'company_id': 1, 'site_id': site1, 'department': 'تولید'}],
                          'permissions': grants, 'training_access': False}, 201)
            restricted = browser.new_context(viewport={'width': 1366, 'height': 900})
            local = restricted.new_page(); local.on('pageerror', lambda error: errors.append(str(error)))
            local.goto(base)
            local.locator('#auth [name=username]').fill('site-hr-safety')
            local.locator('#auth [name=password]').fill(restricted_password)
            local.locator('#auth button').click()
            local.locator('#shell').wait_for(state='visible')
            local.locator('[data-page=hrm_personnel]').click()
            local.locator('[data-hrm-resource=people]').click()
            expect(local.locator('#hrm-table tbody tr')).to_have_count(1)
            assert local.locator('#hrm-add').count() == 1
            assert local.locator('[data-hrm-edit],#hrm-export,[data-page=hrm_access],[data-page=hrm_health_environment]').count() == 0
            local_csrf = restricted.request.get(base + '/api/me').json()['csrf']
            for path in (f'hrm/people/{foreign}', f'hrm/people/{other_company}', 'hrm/health_records?company_id=1', 'hrm/reports/people.xlsx?company_id=1'):
                response = restricted.request.get(base + '/api/' + path)
                assert response.status == 403, (path, response.status, response.text())
            response = restricted.request.put(base + f'/api/hrm/people/{eid}?company_id=1', headers={'X-CSRF-Token': local_csrf}, data={'version': person['version'], 'phone': '123'})
            assert response.status == 403, response.text()
            # A create-only grant can open selectors and save without reading full rows.
            api('PUT', 'hrm/access/users/' + str(created['id']), {'role': 'company_hr',
                 'scopes': [{'company_id': 1, 'site_id': site1, 'department': 'تولید'}],
                 'permissions': {'personnel': {'read': False, 'create': True, 'edit': False, 'delete': False, 'export': False}},
                 'training_access': False, 'active': 1})
            local.reload(); local.locator('#login').wait_for(state='visible')
            local.locator('#auth [name=username]').fill('site-hr-safety')
            local.locator('#auth [name=password]').fill(restricted_password)
            local.locator('#auth button').click(); local.locator('#shell').wait_for(state='visible')
            local.locator('[data-page=hrm_personnel]').click()
            local.locator('[data-hrm-resource=people]').click()
            assert local.locator('#hrm-table tbody tr').count() == 0
            local.locator('#hrm-add').click()
            form = local.locator('#hrm-form')
            form.locator('[name=code]').fill('HRM-BROWSER-CREATE')
            form.locator('[name=name]').fill('ثبت با مجوز مستقل')
            form.locator('[name=site_id]').select_option(str(site1))
            form.locator('[name=department]').fill('تولید')
            form.locator('[name=status]').select_option('active')
            form.locator('#hrm-save').click()
            local.locator('#hrm-editor').wait_for(state='hidden')
            assert any(row['code'] == 'HRM-BROWSER-CREATE' for row in api('GET', 'hrm/people?company_id=1'))
            assert restricted.request.get(base + '/api/hrm/people?company_id=1').status == 403
            # Revoke while the account has a live session; stale browser grants cannot bypass the server.
            api('PUT', 'hrm/access/users/' + str(created['id']), {'role': 'company_hr',
                 'scopes': [{'company_id': 1, 'site_id': site1, 'department': 'تولید'}], 'permissions': {}, 'training_access': False, 'active': 1})
            assert restricted.request.get(base + '/api/hrm/people?company_id=1').status in (401, 403)
            local.reload(); local.locator('#login').wait_for(state='visible')
            local.locator('#auth [name=username]').fill('site-hr-safety')
            local.locator('#auth [name=password]').fill(restricted_password)
            local.locator('#auth button').click(); local.locator('#shell').wait_for(state='visible')
            assert local.locator('#nav [data-page=hrm_personnel]').count() == 0

            # A mixed document grant chooses the writable group, while read-only groups stay read-only.
            upload_password = 'Upload-' + secrets.token_urlsafe(18)
            api('POST', 'hrm/access/users', {'username': 'document-uploader', 'password': upload_password,
                'role': 'company_hr', 'scopes': [{'company_id': 1, 'site_id': site1, 'department': 'تولید'}],
                'permissions': {'contracts': {'read': True}, 'documents': {'create': True}}, 'training_access': False}, 201)
            upload_context = browser.new_context(viewport={'width': 1366, 'height': 900})
            upload_page = upload_context.new_page(); upload_page.on('pageerror', lambda error: errors.append(str(error)))
            upload_page.goto(base)
            upload_page.locator('#auth [name=username]').fill('document-uploader')
            upload_page.locator('#auth [name=password]').fill(upload_password)
            upload_page.locator('#auth button').click(); upload_page.locator('#shell').wait_for(state='visible')
            upload_page.locator('#nav [data-page=hrm_documents]').click()
            upload_page.locator('#hrm-add').click()
            form = upload_page.locator('#hrm-form')
            assert form.locator('[name=permission_group]').input_value() == 'documents'
            assert form.locator('[name=permission_group] option[value=contracts]').count() == 0
            form.locator('[name=employee_id]').select_option(str(eid))
            form.locator('[name=title]').fill('ثبت با مجوز مستقل مدرک')
            form.locator('[name=file]').set_input_files({'name': 'restricted.pdf', 'mimeType': 'application/pdf', 'buffer': doc})
            form.locator('#hrm-save').click(); upload_page.locator('#hrm-editor').wait_for(state='hidden')
            created_doc = next(row for row in api('GET', 'hrm/documents?company_id=1') if row['title'] == 'ثبت با مجوز مستقل مدرک')
            assert upload_context.request.get(base + '/api/hrm/documents/' + str(created_doc['id']) + '/file').status == 403
            upload_context.close()

            # A site-only training manager performs CRUD through the protected facade.
            training_password = 'Training-' + secrets.token_urlsafe(18)
            api('POST', 'hrm/access/users', {'username': 'site-training', 'password': training_password,
                'role': 'training_officer', 'scopes': [{'company_id': 1, 'site_id': site1, 'department': 'تولید'}],
                'permissions': {'training': {'read': True, 'create': True, 'edit': True}}, 'training_access': False}, 201)
            training_context = browser.new_context(viewport={'width': 1366, 'height': 900})
            training_page = training_context.new_page(); training_page.on('pageerror', lambda error: errors.append(str(error)))
            training_page.goto(base)
            training_page.locator('#auth [name=username]').fill('site-training')
            training_page.locator('#auth [name=password]').fill(training_password)
            training_page.locator('#auth button').click(); training_page.locator('#shell').wait_for(state='visible')
            assert training_page.locator('#nav [data-page=courses]').count() == 0
            training_page.locator('#nav [data-page=hrm_training]').click()
            training_page.locator('[data-hrm-resource=training_courses]').click()
            expect(training_page.locator('#hrm-table tbody tr')).to_have_count(1)
            assert training_page.locator('#hrm-export').count() == 0
            training_page.locator('#hrm-add').click(); form = training_page.locator('#hrm-form')
            form.locator('[name=site_id]').select_option(str(site1))
            form.locator('[name=department]').fill('تولید')
            form.locator('[name=competency_code]').select_option(competency_code)
            form.locator('[name=title]').fill('دوره ثبت‌شده توسط مسئول سایت')
            form.locator('[name=date]').fill('1405/08/03'); form.locator('[name=hours]').fill('1')
            form.locator('[name=status]').select_option('planned'); form.locator('[name=calendar_scope]').select_option('internal')
            form.locator('#hrm-save').click(); training_page.locator('#hrm-editor').wait_for(state='hidden')
            row = training_page.locator('#hrm-table tbody tr').filter(has_text='دوره ثبت‌شده توسط مسئول سایت')
            row.locator('[data-hrm-edit]').click(); form = training_page.locator('#hrm-form')
            form.locator('[name=title]').fill('دوره اصلاح‌شده توسط مسئول سایت')
            form.locator('#hrm-save').click(); training_page.locator('#hrm-editor').wait_for(state='hidden')
            expect(training_page.locator('#hrm-table')).to_contain_text('دوره اصلاح‌شده توسط مسئول سایت')
            training_csrf = training_context.request.get(base + '/api/me').json()['csrf']
            assert training_context.request.get(base + '/api/data/courses?company_id=1').status == 403
            assert training_context.request.put(base + '/api/hrm/training_courses/' + str(foreign_course['id']),
                headers={'X-CSRF-Token': training_csrf}, data={'version': foreign_course['version'], 'title': 'تغییر غیرمجاز'}).status == 403
            assert training_context.request.post(base + '/api/hrm/training_records?company_id=1',
                headers={'X-CSRF-Token': training_csrf}, data={'employee_id': foreign, 'course_id': course['id'], 'level': 1}).status == 403
            training_page.screenshot(path='/tmp/nikan-hrm-site-training.png', full_page=True)
            training_context.close()

            # Access editor exposes all independent rights and site scope rows.
            page.locator('#nav [data-page=hrm_access]').click()
            page.locator('#hrm-user-add').click()
            assert page.locator('[data-permission-operation=read]').count() > 0
            assert page.locator('[data-permission-operation=create]').count() > 0
            assert page.locator('[data-permission-operation=edit]').count() > 0
            assert page.locator('[data-permission-operation=delete]').count() > 0
            assert page.locator('[data-permission-operation=export]').count() > 0
            assert page.locator('[data-permission="settings"],[data-permission="backup.full_export"]').count() == 0
            assert page.locator('#hrm-form [name=role] option[value=holding_manager]').count() == 1
            assert page.locator('[data-scope-site]').count() == 1
            form = page.locator('#hrm-form')
            form.locator('[name=username]').fill('ui-holding-manager')
            form.locator('[name=password]').fill('Holding-' + secrets.token_urlsafe(18))
            form.locator('[name=role]').select_option('holding_manager')
            form.locator('[name=all_companies]').check()
            form.locator('[data-permission-module=personnel][data-permission-operation=read]').check()
            form.locator('#hrm-save').click()
            page.locator('#hrm-editor').wait_for(state='hidden')
            manager = next(row for row in api('GET', 'hrm/access/users') if row['username'] == 'ui-holding-manager')
            assert manager['role'] == 'holding_manager' and manager['all_companies']
            page.set_viewport_size({'width': 390, 'height': 844})
            page.locator('#nav [data-page=hrm_dashboard]').click()
            page.locator('#hrm-filters').wait_for()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'mobile horizontal overflow'
            page.screenshot(path='/tmp/nikan-hrm-mobile.png', full_page=True)
            assert not errors, errors
            restricted.close(); browser.close()
            print('HRM browser passed: central person CRUD, contract renewal, document bytes/edit/mixed grants, attendance Excel preview/commit and archive/restore, training course/record/assessment/requirement forms and actual attendance, site-only training CRUD, filters/exports, four dashboards plus holding, company/site/operation isolation and revocation, desktop/mobile; no JavaScript errors.')
        except BaseException:
            if not page.is_closed():
                page.screenshot(path='/tmp/nikan-hrm-failure.png', full_page=True)
                Path('/tmp/nikan-hrm-failure.html').write_text(page.content())
            raise


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='nikan-hrm-browser-') as data:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        process = subprocess.Popen([sys.executable, 'run.py', '--no-browser', '--port', str(port)],
                                   cwd=root, env={**os.environ, 'NIKAN_DATA_DIR': data}, stdout=subprocess.DEVNULL)
        try:
            for _ in range(100):
                try:
                    urllib.request.urlopen(base + '/api/status', timeout=1).close(); break
                except OSError:
                    if process.poll() is not None:
                        raise RuntimeError('Server exited')
                    time.sleep(.1)
            else:
                raise RuntimeError('Server did not become ready')
            check(base)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
