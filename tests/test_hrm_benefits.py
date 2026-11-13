"""Management benefits use real scoped APIs and preserve monetary/booking history."""
from concurrent.futures import ThreadPoolExecutor
import io
import sqlite3
import threading

import openpyxl
import pytest

from app import create_app


PASSWORD = 'Secure-benefits-password-123'
MODULES = ('loans', 'welfare', 'accommodation')


def login(app, name):
    client = app.test_client()
    assert client.post('/api/login', json={'username': name, 'password': PASSWORD}).status_code == 200
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


@pytest.fixture
def ready(tmp_path):
    app = create_app(tmp_path, testing=True)
    admin = app.test_client()
    assert admin.post('/api/setup', json={'username': 'admin', 'password': PASSWORD}).status_code == 200
    admin, ah = login(app, 'admin')
    people = []
    for cid, code, unit in ((1, 'BEN-1001', 'A'), (1, 'BEN-1002', 'B'), (2, 'BEN-2001', 'A')):
        response = admin.post(f'/api/data/employees?company_id={cid}', headers=ah,
                              json={'code': code, 'name': code, 'department': unit, 'job': 'Engineer'})
        assert response.status_code == 201, response.json
        people.append(response.json['id'])

    def account(name, scopes, role='company_hr', write=True, modules=MODULES):
        result = admin.post('/api/hrm/access/users', headers=ah, json={
            'username': name, 'password': PASSWORD, 'role': role,
            'scopes': [{'company_id': cid, 'department': unit} for cid, unit in scopes],
            'permissions': {key: {'read': True, 'write': write} for key in modules},
            'training_access': False,
        })
        assert result.status_code == 201, result.json
        return login(app, name)

    hr, hh = account('benefits-hr', [(1, ''), (2, '')], 'holding_hr')
    one, oh = account('benefits-company', [(1, '')])
    two, th = account('benefits-other-company', [(2, '')])
    unit, uh = account('benefits-unit', [(1, 'A')], 'unit_manager')
    reader, rh = account('benefits-reader', [(1, '')], 'executive', False)
    plain, ph = account('benefits-no-loans', [(1, '')], modules=('welfare', 'accommodation'))
    return dict(app=app, directory=tmp_path, admin=admin, ah=ah, hr=hr, hh=hh,
                one=one, oh=oh, two=two, th=th, unit=unit, uh=uh, reader=reader, rh=rh,
                plain=plain, ph=ph, people=people)


def post(data, resource, body, client='hr', status=201):
    headers = {'hr': 'hh', 'one': 'oh', 'two': 'th', 'unit': 'uh', 'reader': 'rh', 'plain': 'ph', 'admin': 'ah'}
    response = data[client].post('/api/hrm/' + resource, headers=data[headers[client]], json=body)
    assert response.status_code == status, (resource, response.status_code, response.json)
    return response.json


def loan(data, employee=None, **extras):
    return post(data, 'loans', {'employee_id': employee or data['people'][0], 'title': 'وام داخلی آزمایشی',
                              'amount_rials': 1001, 'installment_count': 3,
                              'issued_date': '1405/08/21', 'status': 'disbursed', **extras})['id']


def property_record(data, cid=1, **extras):
    return post(data, 'properties', {'company_id': cid, 'name': 'واحد آزمایشی محمودآباد',
                                     'location': 'مازندران', 'capacity': 4, **extras})['id']


def reservation(data, place, employee=None, **extras):
    return {'employee_id': employee or data['people'][0], 'property_id': place,
            'start_date': '2026-12-01', 'end_date': '2026-12-04', 'guests': 2,
            'status': 'approved', **extras}


def test_explicit_confidential_grants_and_company_unit_read_write_scope(ready):
    first = loan(ready)
    second = loan(ready, ready['people'][1])
    third = loan(ready, ready['people'][2])
    assert {row['id'] for row in ready['hr'].get('/api/hrm/loans').json} == {first, second, third}
    assert {row['id'] for row in ready['one'].get('/api/hrm/loans').json} == {first, second}
    assert [row['id'] for row in ready['unit'].get('/api/hrm/loans').json] == [first]
    assert ready['admin'].get('/api/hrm/loans').status_code == 200
    assert ready['plain'].get('/api/hrm/loans').status_code == 403
    assert ready['one'].get('/api/hrm/loans?company_id=2').status_code == 403
    post(ready, 'loans', {'employee_id': ready['people'][1], 'title': 'Not in unit', 'amount_rials': 5}, 'unit', 403)
    post(ready, 'loans', {'employee_id': ready['people'][0], 'title': 'Read only', 'amount_rials': 5}, 'reader', 403)
    assert ready['unit'].get('/api/data/employees?company_id=1').status_code == 403


def test_exact_rial_loan_repayments_version_and_void_history(ready):
    lid = loan(ready)
    original = ready['hr'].get('/api/hrm/loans').json[0]
    assert original['issued_date'] == '2026-11-12'
    assert original['paid_rials'] == 0 and original['outstanding_rials'] == 1001
    payment = post(ready, 'loan_payments', {'loan_id': lid, 'amount_rials': 334, 'paid_date': '1405/08/22',
                                           'reference': 'PAY-1', 'installment_no': 1})['id']
    post(ready, 'loan_payments', {'loan_id': lid, 'amount_rials': 668, 'paid_date': '2026-11-14', 'reference': 'PAY-2'}, status=400)
    post(ready, 'loan_payments', {'loan_id': lid, 'amount_rials': 334, 'paid_date': '2026-11-14', 'reference': 'PAY-1'}, status=400)
    post(ready, 'loan_payments', {'loan_id': lid, 'amount_rials': 10, 'paid_date': '2026-11-11', 'reference': 'EARLY'}, status=400)
    row = ready['hr'].get('/api/hrm/loans').json[0]
    assert (row['paid_rials'], row['outstanding_rials'], row['repayment_status']) == (334, 667, 'partial')
    before = ready['hr'].get('/api/hrm/loan_payments').json
    assert ready['hr'].put(f'/api/hrm/loan_payments/{payment}', headers=ready['hh'], json={'version': 0, 'amount_rials': 1}).status_code == 409
    assert ready['hr'].get('/api/hrm/loan_payments').json == before
    assert ready['hr'].put(f'/api/hrm/loans/{lid}', headers=ready['hh'], json={'version': 1, 'amount_rials': 333}).status_code == 400
    assert ready['hr'].put(f'/api/hrm/loans/{lid}', headers=ready['hh'], json={'version': 1, 'status': 'cancelled'}).status_code == 400
    voided = ready['hr'].put(f'/api/hrm/loan_payments/{payment}', headers=ready['hh'], json={'version': 1, 'status': 'voided'})
    assert voided.status_code == 200 and voided.json['version'] == 2
    assert ready['hr'].get('/api/hrm/loan_payments').json[0]['status'] == 'voided'
    post(ready, 'loan_payments', {'loan_id': lid, 'amount_rials': 1001, 'paid_date': '2026-11-14', 'reference': 'PAY-FINAL'})
    row = ready['hr'].get('/api/hrm/loans').json[0]
    assert (row['paid_rials'], row['outstanding_rials'], row['repayment_status']) == (1001, 0, 'paid')
    assert ready['hr'].delete(f'/api/hrm/loan_payments/{payment}', headers=ready['hh']).status_code == 400


def test_bank_referral_and_repeated_loan_history_are_persistent(ready):
    loan(ready, loan_kind='bank', bank_name='بانک آزمایشی', referral_number='LETTER-001', referral_status='issued')
    loan(ready, title='وام دوم')
    rows = ready['hr'].get('/api/hrm/loans?employee_id=' + str(ready['people'][0])).json
    assert len(rows) == 2
    bank = next(row for row in rows if row['loan_kind'] == 'bank')
    assert bank['referral_number'] == 'LETTER-001' and bank['referral_status'] == 'issued'
    post(ready, 'loans', {'employee_id': ready['people'][0], 'title': 'Bank without name', 'loan_kind': 'bank', 'amount_rials': 5}, status=400)


@pytest.mark.parametrize('amount', [True, False, 1.5, -1, 0, 9_007_199_254_740_992, 'NaN', 'Infinity', {}, []])
def test_financial_invalid_amounts_are_controlled_and_atomic(ready, amount):
    before = ready['hr'].get('/api/hrm/loans').json
    post(ready, 'loans', {'employee_id': ready['people'][0], 'title': 'Invalid amount', 'amount_rials': amount}, status=400)
    assert ready['hr'].get('/api/hrm/loans').json == before


def test_welfare_quota_counts_quantities_and_backdated_uses(ready):
    service = post(ready, 'welfare_services', {'company_id': 1, 'name': 'بسته رفاهی',
                                              'max_uses': 2, 'max_quantity': 3, 'period_days': 30})['id']
    def use(day, quantity=1, **extra):
        return {'employee_id': ready['people'][0], 'service_id': service, 'used_date': day, 'quantity': quantity, **extra}
    first = post(ready, 'welfare_uses', use('2026-12-20', 2))['id']
    post(ready, 'welfare_uses', use('2026-12-25'))
    # Inserting an earlier day must not bypass an already-recorded future window.
    post(ready, 'welfare_uses', use('2026-12-01'), status=400)
    post(ready, 'welfare_uses', use('2027-01-25', 3))
    assert ready['hr'].put(f'/api/hrm/welfare_uses/{first}', headers=ready['hh'], json={'version': 1, 'status': 'cancelled'}).status_code == 200
    post(ready, 'welfare_uses', use('2026-12-01'))
    assert len(ready['hr'].get('/api/hrm/welfare_uses').json) == 4


def test_catalog_policy_requires_company_wide_scope_and_shared_services_are_explicit(ready):
    post(ready, 'welfare_services', {'company_id': 1, 'name': 'Unit cannot change company policy'}, 'unit', 403)
    service = post(ready, 'welfare_services', {'company_id': 1, 'name': 'مشترک', 'shared': 1})['id']
    private = post(ready, 'welfare_services', {'company_id': 1, 'name': 'اختصاصی'})['id']
    body = {'employee_id': ready['people'][2], 'used_date': '2026-12-01', 'quantity': 1}
    post(ready, 'welfare_uses', {**body, 'service_id': private}, status=400)
    post(ready, 'welfare_uses', {**body, 'service_id': service})
    assert len(ready['one'].get('/api/hrm/welfare_uses').json) == 0
    # A scope-limited manager can record actual unit use, not alter the company catalog.
    post(ready, 'welfare_uses', {**body, 'employee_id': ready['people'][0], 'service_id': private}, 'unit')


def test_shared_property_overlap_is_global_but_checkout_day_is_available(ready):
    place = property_record(ready, shared=1)
    first = post(ready, 'reservations', reservation(ready, place))['id']
    post(ready, 'reservations', reservation(ready, place, ready['people'][2]), status=409)
    assert ready['one'].get('/api/hrm/reservations').json[0]['id'] == first
    post(ready, 'reservations', reservation(ready, place, ready['people'][2], start_date='2026-12-04', end_date='2026-12-06'))
    post(ready, 'reservations', reservation(ready, place, end_date='2026-12-01'), status=400)
    post(ready, 'reservations', reservation(ready, place, start_date='2026-12-07', end_date='2026-12-08', guests=5), status=400)
    assert ready['hr'].put(f'/api/hrm/reservations/{first}', headers=ready['hh'], json={'version': 1, 'status': 'cancelled'}).status_code == 200
    post(ready, 'reservations', reservation(ready, place))
    assert len(ready['hr'].get('/api/hrm/reservations').json) == 3


def test_booking_limits_cooldown_and_edit_revalidation(ready):
    place = property_record(ready, min_nights=2, max_nights=4, cooldown_days=5, max_bookings=2, period_days=30)
    row = post(ready, 'reservations', reservation(ready, place))['id']
    post(ready, 'reservations', reservation(ready, place, start_date='2026-12-06', end_date='2026-12-08'), status=400)
    post(ready, 'reservations', reservation(ready, place, start_date='2026-12-09', end_date='2026-12-11'))
    post(ready, 'reservations', reservation(ready, place, start_date='2026-12-20', end_date='2026-12-22'), status=400)
    before = ready['hr'].get('/api/hrm/reservations').json
    response = ready['hr'].put(f'/api/hrm/reservations/{row}', headers=ready['hh'], json={'version': 1, 'start_date': '2026-12-08', 'end_date': '2026-12-10'})
    assert response.status_code == 409
    assert ready['hr'].get('/api/hrm/reservations').json == before


def test_concurrent_cross_company_booking_has_one_winner(ready):
    place = property_record(ready, shared=1)
    barrier = threading.Barrier(2)
    def save(eid):
        client, headers = login(ready['app'], 'benefits-hr')
        barrier.wait(timeout=10)
        response = client.post('/api/hrm/reservations', headers=headers,
                               json=reservation(ready, place, eid))
        return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save, eid) for eid in (ready['people'][0], ready['people'][2])]
        assert sorted(future.result(timeout=20) for future in futures) == [201, 409]
    rows = ready['hr'].get('/api/hrm/reservations').json
    assert len(rows) == 1
    with sqlite3.connect(ready['app'].config['DB']) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_concurrent_repayments_cannot_exceed_principal(ready):
    lid = loan(ready)
    barrier = threading.Barrier(2)
    def pay(reference):
        client, headers = login(ready['app'], 'benefits-hr')
        barrier.wait(timeout=10)
        response = client.post('/api/hrm/loan_payments', headers=headers, json={
            'loan_id': lid, 'amount_rials': 600, 'paid_date': '2026-11-13', 'reference': reference,
        })
        return response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(pay, reference) for reference in ('CONCURRENT-A', 'CONCURRENT-B')]
        assert sorted(future.result(timeout=20) for future in futures) == [201, 400]
    result = ready['hr'].get('/api/hrm/loans').json[0]
    assert (result['paid_rials'], result['outstanding_rials']) == (600, 401)
    assert len(ready['hr'].get('/api/hrm/loan_payments').json) == 1


def test_history_owner_and_reference_cannot_be_reassigned(ready):
    first, other = loan(ready), loan(ready, ready['people'][2])
    payment = post(ready, 'loan_payments', {'loan_id': first, 'amount_rials': 1, 'paid_date': '2026-11-13', 'reference': 'IMMUTABLE'})['id']
    assert ready['hr'].put(f'/api/hrm/loan_payments/{payment}', headers=ready['hh'], json={'version': 1, 'loan_id': other}).status_code == 409
    assert ready['hr'].put(f'/api/hrm/loans/{first}', headers=ready['hh'], json={'version': 1, 'employee_id': ready['people'][1]}).status_code == 409
    # Moving a worker cannot grant a new-company manager access to the old loan.
    with sqlite3.connect(ready['app'].config['DB']) as conn:
        columns = {row[1] for row in conn.execute('PRAGMA table_info(hrm_employee_details)')}
        assert columns
        conn.execute("INSERT INTO hrm_employee_details(employee_id,company_id,department,job) VALUES (?,2,'A','Engineer') ON CONFLICT(employee_id) DO UPDATE SET company_id=2", (ready['people'][0],))
    assert first in {row['id'] for row in ready['one'].get('/api/hrm/loans').json}
    assert first not in {row['id'] for row in ready['two'].get('/api/hrm/loans').json}
    assert first in {row['id'] for row in ready['hr'].get('/api/hrm/loans').json}
    assert ready['one'].put(f'/api/hrm/loans/{other}', headers=ready['oh'], json={'version': 1, 'amount_rials': 2}).status_code == 403


def test_data_and_audit_survive_restart_without_confidential_audit_payloads(ready):
    lid = loan(ready, notes='CONFIDENTIAL-LOAN-NOTE')
    place = property_record(ready, shared=1)
    post(ready, 'reservations', reservation(ready, place))
    before = ready['hr'].get('/api/hrm/loans').json
    with sqlite3.connect(ready['app'].config['DB']) as conn:
        details = conn.execute("SELECT detail FROM audit WHERE action GLOB 'hrm_*'").fetchall()
        assert all('CONFIDENTIAL' not in value[0] for value in details)
    restored_app = create_app(ready['directory'], testing=True)
    hr, _ = login(restored_app, 'benefits-hr')
    assert hr.get('/api/hrm/loans').json == before
    assert hr.get('/api/hrm/loans').json[0]['id'] == lid
    assert len(hr.get('/api/hrm/reservations').json) == 1
    backup = ready['admin'].post('/api/backup', headers=ready['ah'])
    assert backup.status_code == 200 and backup.data.startswith(b'SQLite format 3')


def test_invalid_choice_scope_and_csrf_do_not_write(ready):
    employee = ready['people'][0]
    for key, value in [('status', []), ('loan_kind', {}), ('issued_date', False), ('bank_name', False)]:
        post(ready, 'loans', {'employee_id': employee, 'title': 'Invalid choice', 'amount_rials': 5, key: value}, status=400)
    assert ready['hr'].post('/api/hrm/loans', json={'employee_id': employee, 'title': 'No token', 'amount_rials': 5}).status_code == 403
    assert ready['hr'].get('/api/hrm/loans').json == []


def access_account(ready, name, scopes, flags):
    response = ready['admin'].post('/api/hrm/access/users', headers=ready['ah'], json={
        'username': name, 'password': PASSWORD, 'role': 'holding_hr',
        'scopes': scopes, 'training_access': False,
        'permissions': {module: flags for module in MODULES},
    })
    assert response.status_code == 201, response.json
    return login(ready['app'], name)


def assign_site(ready, employee_id, site_id):
    path = '/api/hrm/people/' + str(employee_id)
    current = ready['admin'].get(path).json
    changed = ready['admin'].put(path, headers=ready['ah'], json={
        'version': current['version'], 'site_id': site_id,
    })
    assert changed.status_code == 200, changed.json


def test_site_and_department_snapshots_remain_owned_after_transfer(ready):
    site_a = post(ready, 'sites', {'company_id': 1, 'name': 'سایت اول'}, 'admin')['id']
    site_b = post(ready, 'sites', {'company_id': 1, 'name': 'سایت دوم'}, 'admin')['id']
    assign_site(ready, ready['people'][0], site_a)
    assign_site(ready, ready['people'][1], site_b)
    flags = {'read': True, 'create': True, 'edit': True, 'delete': True, 'export': True}
    a, ah = access_account(ready, 'site-a', [{'company_id': 1, 'site_id': site_a}], flags)
    b, bh = access_account(ready, 'site-b', [{'company_id': 1, 'site_id': site_b}], flags)
    created = a.post('/api/hrm/loans', headers=ah, json={
        'employee_id': ready['people'][0], 'title': 'تسهیلات سایت اول', 'amount_rials': 1001,
        'issued_date': '2026-11-12', 'status': 'disbursed',
    })
    assert created.status_code == 201, created.json
    lid = created.json['id']
    old = a.get('/api/hrm/loans?company_id=1').json[0]
    assert (old['site_id'], old['department']) == (site_a, 'A')
    assert a.get('/api/hrm/loans?site_id=' + str(site_b)).status_code == 403
    assert b.get('/api/hrm/loans?company_id=1').json == []
    assert a.post('/api/hrm/loans', headers=ah, json={
        'employee_id': ready['people'][1], 'title': 'Other site', 'amount_rials': 1,
    }).status_code == 403
    assign_site(ready, ready['people'][0], site_b)
    assert [row['id'] for row in a.get('/api/hrm/loans?site_id=' + str(site_a)).json] == [lid]
    assert b.get('/api/hrm/loans?company_id=1').json == []
    repayment = a.post('/api/hrm/loan_payments', headers=ah, json={
        'loan_id': lid, 'amount_rials': 1, 'paid_date': '2026-11-13', 'reference': 'OLD-SITE-REPAYMENT',
    })
    assert repayment.status_code == 201, repayment.json
    rows = a.get('/api/hrm/loan_payments?site_id=' + str(site_a)).json
    assert rows[0]['site_id'] == site_a and rows[0]['department'] == 'A'
    assert b.put('/api/hrm/loans/' + str(lid), headers=bh, json={'version': 1, 'notes': 'attempt'}).status_code == 403
    # The historical unit is also retained, independently of the current unit.
    with sqlite3.connect(ready['app'].config['DB']) as conn:
        conn.execute("UPDATE hrm_employee_details SET department='B' WHERE employee_id=?", (ready['people'][0],))
    assert lid in {row['id'] for row in ready['unit'].get('/api/hrm/loans').json}


def test_read_create_edit_delete_and_export_are_independent(ready):
    scopes = [{'company_id': 1}]
    creator, ch = access_account(ready, 'only-create', scopes, {'read': False, 'create': True, 'edit': False, 'delete': False, 'export': False})
    response = creator.post('/api/hrm/loans', headers=ch, json={
        'employee_id': ready['people'][0], 'title': 'Created without read access', 'amount_rials': 10,
    })
    assert response.status_code == 201, response.json
    lid = response.json['id']
    assert creator.get('/api/hrm/loans').status_code == 403
    assert creator.put(f'/api/hrm/loans/{lid}', headers=ch, json={'version': 1, 'title': 'No edit'}).status_code == 403
    assert creator.delete(f'/api/hrm/loans/{lid}', headers=ch, json={'version': 1}).status_code == 403
    assert creator.get('/api/hrm/reports/loans.xlsx').status_code == 403
    options = creator.get('/api/hrm/options/loans?module=loans&operation=create')
    assert options.status_code == 200 and options.json[0]['id'] == lid
    editor, eh = access_account(ready, 'only-edit', scopes, {'read': False, 'create': False, 'edit': True, 'delete': False, 'export': False})
    assert editor.get('/api/hrm/loans').status_code == 403
    assert editor.put(f'/api/hrm/loans/{lid}', headers=eh, json={'version': 1, 'title': 'Edited with explicit permission'}).status_code == 200
    exporter, _ = access_account(ready, 'only-export', scopes, {'read': False, 'create': False, 'edit': False, 'delete': False, 'export': True})
    assert exporter.get('/api/hrm/loans').status_code == 403
    export = exporter.get('/api/hrm/reports/loans.xlsx')
    assert export.status_code == 200 and export.data.startswith(b'PK')
    deleter, dh = access_account(ready, 'only-delete', scopes, {'read': False, 'create': False, 'edit': False, 'delete': True, 'export': False})
    assert deleter.get('/api/hrm/loans').status_code == 403
    assert deleter.delete(f'/api/hrm/loans/{lid}', headers=dh, json={'version': 1}).status_code == 409
    deleted = deleter.delete(f'/api/hrm/loans/{lid}', headers=dh, json={'version': 2})
    assert deleted.status_code == 200 and deleted.json['archived']
    assert ready['hr'].get('/api/hrm/loans').json == []
    with sqlite3.connect(ready['app'].config['DB']) as conn:
        assert conn.execute('SELECT deleted,version FROM hrm_loans WHERE id=?', (lid,)).fetchone() == (1, 3)


def test_archiving_financial_and_booking_records_retains_history(ready):
    lid = loan(ready)
    payment = post(ready, 'loan_payments', {'loan_id': lid, 'amount_rials': 10, 'paid_date': '2026-11-13', 'reference': 'DELETE-RECEIPT'})['id']
    denied = ready['hr'].delete(f'/api/hrm/loans/{lid}', headers=ready['hh'], json={'version': 1})
    assert denied.status_code == 409
    removed = ready['hr'].delete(f'/api/hrm/loan_payments/{payment}', headers=ready['hh'], json={'version': 1})
    assert removed.status_code == 200
    assert ready['hr'].get('/api/hrm/loan_payments').json == []
    assert ready['hr'].get('/api/hrm/loans').json[0]['paid_rials'] == 0
    assert ready['hr'].put(f'/api/hrm/loans/{lid}', headers=ready['hh'], json={'version': 1, 'status': 'cancelled'}).status_code == 200
    assert ready['hr'].delete(f'/api/hrm/loans/{lid}', headers=ready['hh'], json={'version': 2}).status_code == 200
    place = property_record(ready)
    booking = post(ready, 'reservations', reservation(ready, place))['id']
    assert ready['hr'].delete(f'/api/hrm/properties/{place}', headers=ready['hh'], json={'version': 1}).status_code == 409
    assert ready['hr'].delete(f'/api/hrm/reservations/{booking}', headers=ready['hh'], json={'version': 1}).status_code == 200
    # Cancellation/archive releases the interval without physically losing it.
    post(ready, 'reservations', reservation(ready, place))
    with sqlite3.connect(ready['app'].config['DB']) as conn:
        assert conn.execute('SELECT deleted,status FROM hrm_loan_payments WHERE id=?', (payment,)).fetchone() == (1, 'voided')
        assert conn.execute('SELECT deleted,status FROM hrm_reservations WHERE id=?', (booking,)).fetchone() == (1, 'cancelled')
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_archive_toggle_lists_all_benefits_resources_without_reactivating_history(ready):
    lid = loan(ready)
    payment = post(ready, 'loan_payments', {'loan_id': lid, 'amount_rials': 10, 'paid_date': '2026-11-13', 'reference': 'ARCHIVE-ALL'})['id']
    service = post(ready, 'welfare_services', {'company_id': 1, 'name': 'Archived service'})['id']
    use = post(ready, 'welfare_uses', {'employee_id': ready['people'][0], 'service_id': service, 'quantity': 1, 'used_date': '2026-11-13'})['id']
    place = property_record(ready)
    booking = post(ready, 'reservations', reservation(ready, place))['id']
    for resource, rid in (('loan_payments', payment), ('welfare_uses', use), ('welfare_services', service),
                          ('reservations', booking), ('properties', place)):
        response = ready['hr'].delete(f'/api/hrm/{resource}/{rid}', headers=ready['hh'], json={'version': 1})
        assert response.status_code == 200, response.json
    assert ready['hr'].put(f'/api/hrm/loans/{lid}', headers=ready['hh'], json={'version': 1, 'status': 'cancelled'}).status_code == 200
    assert ready['hr'].delete(f'/api/hrm/loans/{lid}', headers=ready['hh'], json={'version': 2}).status_code == 200
    catalog = ready['hr'].get('/api/hrm/catalog').json
    for resource, rid in (('loans', lid), ('loan_payments', payment), ('welfare_uses', use),
                          ('welfare_services', service), ('reservations', booking), ('properties', place)):
        assert ready['hr'].get('/api/hrm/' + resource).json == []
        archived = ready['hr'].get('/api/hrm/' + resource + '?include_deleted=1').json
        assert len(archived) == 1 and archived[0]['id'] == rid
        assert archived[0]['deleted'] == 1 and archived[0]['is_deleted'] == 1
        assert catalog[resource]['deletable'] is True and catalog[resource]['restorable'] is False
    with sqlite3.connect(ready['app'].config['DB']) as conn:
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_archived_financial_rows_remain_scoped_and_do_not_enter_normal_reports(ready):
    archived = loan(ready, title='PRIVATE-ARCHIVED-COMPANY-ONE', status='registered', amount_rials=301)
    active = loan(ready, title='VISIBLE-ACTIVE-COMPANY-ONE', amount_rials=302)
    foreign = loan(ready, ready['people'][2], title='PRIVATE-ARCHIVED-COMPANY-TWO', status='registered', amount_rials=999)
    for rid in (archived, foreign):
        assert ready['hr'].delete(f'/api/hrm/loans/{rid}', headers=ready['hh'], json={'version': 1}).status_code == 200
    visible = ready['one'].get('/api/hrm/loans?include_deleted=1').json
    assert {row['id'] for row in visible} == {archived, active}
    assert foreign not in {row['id'] for row in visible}
    assert ready['one'].get('/api/hrm/loans?include_deleted=1&company_id=2').status_code == 403
    assert ready['plain'].get('/api/hrm/loans?include_deleted=1').status_code == 403
    export = ready['one'].get('/api/hrm/reports/loans.xlsx?company_id=1&include_deleted=1')
    assert export.status_code == 200, export.json
    workbook = openpyxl.load_workbook(io.BytesIO(export.data), read_only=True)
    cells = '\n'.join(str(value) for sheet in workbook for row in sheet.values for value in row)
    workbook.close()
    assert 'VISIBLE-ACTIVE-COMPANY-ONE' in cells
    assert 'PRIVATE-ARCHIVED-COMPANY-ONE' not in cells and 'PRIVATE-ARCHIVED-COMPANY-TWO' not in cells
    normal = ready['one'].get('/api/hrm/dashboard?section=personnel&company_id=1')
    archive_flag = ready['one'].get('/api/hrm/dashboard?section=personnel&company_id=1&include_deleted=1')
    assert normal.status_code == archive_flag.status_code == 200
    assert {key: value for key, value in normal.json.items() if key != 'filters'} == {
        key: value for key, value in archive_flag.json.items() if key != 'filters'}
    assert 'include_deleted' not in normal.json['filters']
    assert archive_flag.json['filters']['include_deleted'] == '1'
