"""Training facade uses canonical data and preserves actual-attendance ownership."""
import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import threading

import openpyxl
import pytest

from app import create_app
from app.hrm_common import jalali_display


PASSWORD = 'Secure-HRM-training-password-123'
CODE = 'HRM-TRAINING-ONLY-TEST'
ALL = dict(read=True, create=True, edit=True, delete=True, export=True)


def login(app, username):
    client = app.test_client()
    response = client.post('/api/login', json={'username': username, 'password': PASSWORD})
    assert response.status_code == 200, response.json
    return client, {'X-CSRF-Token': client.get('/api/me').json['csrf']}


def create(client, headers, resource, body, cid=None):
    response = client.post('/api/hrm/' + resource + (f'?company_id={cid}' if cid else ''), headers=headers, json=body)
    assert response.status_code == 201, (resource, response.status_code, response.json)
    return response.json


@pytest.fixture
def training(tmp_path):
    app = create_app(tmp_path, testing=True)
    first = app.test_client()
    assert first.post('/api/setup', json={'username': 'training-admin', 'password': PASSWORD}).status_code == 200
    admin, ah = login(app, 'training-admin')
    sites = [create(admin, ah, 'sites', {'name': title, 'kind': 'factory'}, cid)
             for cid, title in ((1, 'Training site A'), (1, 'Training site B'), (2, 'Training site C'))]
    people = []
    for number, (site, department) in enumerate(zip(sites, ('A', 'B', 'C')), 1):
        people.append(create(admin, ah, 'people', {'code': f'TRAIN-{number}', 'name': f'Learner {number}',
                      'site_id': site['id'], 'department': department, 'job': 'Engineer',
                      'location': 'Hall A', 'start_date': '2026-01-01'}, site['company_id']))
    assert admin.post('/api/competency-bank', headers=ah, json={'code': CODE, 'title': 'Synthetic shared competency'}).status_code == 201

    def account(name, scopes, flags=ALL):
        result = admin.post('/api/hrm/access/users', headers=ah, json={
            'username': name, 'password': PASSWORD, 'role': 'training_officer',
            'scopes': scopes, 'permissions': {'training': flags}, 'training_access': False,
        })
        assert result.status_code == 201, result.json
        return login(app, name)
    a, ha = account('trainer-a', [{'company_id': 1, 'site_id': sites[0]['id']}])
    b, hb = account('trainer-b', [{'company_id': 1, 'site_id': sites[1]['id']}])
    c, hc = account('trainer-c', [{'company_id': 2, 'site_id': sites[2]['id']}])
    return dict(app=app, root=tmp_path, admin=admin, ah=ah, sites=sites, people=people,
                a=a, ha=ha, b=b, hb=hb, c=c, hc=hc, account=account)


def course(data, client='a', site_index=0, **extras):
    site = data['sites'][site_index]
    return create(data[client], data['h' + client], 'training_courses', {
        'company_id': site['company_id'], 'site_id': site['id'], 'department': chr(65 + site_index),
        'competency_code': CODE, 'title': f'Class at site {site_index}', 'date': '2026-10-01',
        'hours': 2, 'cost': .000101, 'status': 'completed', 'calendar_scope': 'internal', **extras,
    })


def row(client, resource, rid):
    return next(value for value in client.get('/api/hrm/' + resource).json if value['id'] == rid)


def test_site_scope_uses_existing_canonical_course_and_no_global_bank_mutation(training):
    d = training
    before_bank = d['admin'].get('/api/competency-bank').json
    created = course(d)
    visible = d['a'].get('/api/hrm/training_courses?company_id=1&site_id=' + str(d['sites'][0]['id']))
    assert visible.status_code == 200 and [value['id'] for value in visible.json] == [created['id']]
    assert visible.json[0]['date'] == '2026-10-01'
    assert d['b'].get('/api/hrm/training_courses?company_id=1').json == []
    assert d['a'].get('/api/hrm/training_courses?site_id=' + str(d['sites'][1]['id'])).status_code == 403
    assert d['a'].get('/api/hrm/training_records?company_id=2').status_code == 403
    assert d['a'].get('/api/data/courses?company_id=1').status_code == 403
    assert d['b'].put('/api/hrm/training_courses/' + str(created['id']), headers=d['hb'], json={'version': 1, 'title': 'Other site edit'}).status_code == 403
    with sqlite3.connect(d['app'].config['DB']) as conn:
        canonical = conn.execute('SELECT id,company_id,date,skill_id FROM courses WHERE id=?', (created['id'],)).fetchone()
        context = conn.execute('SELECT company_id,site_id,department,date_iso FROM hrm_training_course_context WHERE course_id=?', (created['id'],)).fetchone()
        assert canonical[0:3] == (created['id'], 1, jalali_display('2026-10-01'))
        assert context == (1, d['sites'][0]['id'], 'A', '2026-10-01')
        assert conn.execute('SELECT code FROM competency_links WHERE skill_id=?', (canonical[3],)).fetchone()[0] == CODE
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
    assert d['admin'].get('/api/competency-bank').json == before_bank
    invalid = d['a'].post('/api/hrm/training_courses', headers=d['ha'], json={
        'company_id': 1, 'site_id': d['sites'][0]['id'], 'title': 'Unknown code',
        'competency_code': 'DO-NOT-CREATE-BANK-ENTRY', 'date': '2026-10-01', 'hours': 2,
    })
    assert invalid.status_code == 400
    assert d['admin'].get('/api/competency-bank').json == before_bank


def test_grade_assessment_and_actual_presence_are_independent(training):
    d = training; cid = course(d)['id']; eid = d['people'][0]['id']
    record = create(d['a'], d['ha'], 'training_records', {'employee_id': eid, 'course_id': cid, 'level': 4, 'score': 99})
    policy = create(d['a'], d['ha'], 'training_requirements', {
        'company_id': 1, 'site_id': d['sites'][0]['id'], 'department': 'A', 'job': 'Engineer',
        'competency_code': CODE, 'required_level': 3, 'renewal_days': 30, 'onboarding': 1,
    })
    assessment = create(d['a'], d['ha'], 'training_assessments', {
        'employee_id': eid, 'competency_code': CODE, 'level': 1, 'assessment_date': '2026-10-02',
    })
    report = d['a'].get('/api/hrm/training_report').json
    assert report['metrics']['actual_attendances'] == 0
    assert report['metrics']['actual_person_hours'] == 0
    evidence = report['requirements'][0]
    assert (evidence['current_level'], evidence['gap'], evidence['level_source']) == (1, 2, 'current_assessment')
    assert evidence['last_training_date'] == '' and evidence['renewal_due'] is None
    assert evidence['onboarding_missing'] is True
    with sqlite3.connect(d['app'].config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM course_participants WHERE course_id=?', (cid,)).fetchone()[0] == 0
        assert conn.execute('SELECT employee_id,company_id,level FROM records WHERE id=?', (record['id'],)).fetchone() == (eid, 1, 4)
        assert conn.execute('SELECT level FROM assessments WHERE id=?', (assessment['id'],)).fetchone()[0] == 1
    changed = d['a'].put('/api/hrm/training_records/' + str(record['id']), headers=d['ha'], json={'version': 1, 'attended': 1})
    assert changed.status_code == 200 and changed.json['version'] == 2
    report = d['a'].get('/api/hrm/training_report').json
    assert report['metrics']['actual_attendances'] == 1 and report['metrics']['actual_headcount'] == 1
    assert report['metrics']['actual_person_hours'] == 2 and report['metrics']['hours_per_employee'] == 2
    assert report['metrics']['achievement_percent'] == 100
    evidence = report['requirements'][0]
    assert evidence['renewal_due'] is True and evidence['onboarding_missing'] is False
    assert evidence['current_level'] == 1  # A higher course grade never displaces the current assessment.
    assert d['a'].put('/api/hrm/training_records/' + str(record['id']), headers=d['ha'], json={'version': 1, 'score': 1}).status_code == 409
    assert d['a'].put('/api/hrm/training_records/' + str(record['id']), headers=d['ha'], json={'version': 2, 'score': 50}).status_code == 200
    assert row(d['a'], 'training_records', record['id'])['attended'] == 1
    assert row(d['a'], 'training_requirements', policy['id'])['onboarding'] == 1


def test_shared_course_requires_planned_cross_company_learner_and_keeps_exact_costs(training):
    d = training; site = d['sites'][0]
    held = create(d['admin'], d['ah'], 'training_courses', {
        'company_id': 1, 'site_id': site['id'], 'competency_code': CODE, 'title': 'Holding class',
        'date': '2026-10-01', 'hours': 3, 'cost': .000101, 'status': 'completed', 'calendar_scope': 'holding',
    })['id']
    outside = d['people'][2]['id']; own = d['people'][0]['id']
    assert d['c'].post('/api/hrm/training_records', headers=d['hc'], json={
        'employee_id': outside, 'course_id': held, 'level': 2, 'attended': 1,
    }).status_code == 403
    planned = d['admin'].put(f'/api/courses/{held}/participants', headers=d['ah'], json={'employee_ids': [own, outside]})
    assert planned.status_code == 200
    # A course hosted by company1 still appears among company2's authorized learner options.
    listed = d['c'].get('/api/hrm/training_courses?company_id=2&site_id=' + str(d['sites'][2]['id']))
    assert listed.status_code == 200 and [r['id'] for r in listed.json] == [held]
    create(d['a'], d['ha'], 'training_records', {'employee_id': own, 'course_id': held, 'level': 2, 'attended': 1})
    create(d['c'], d['hc'], 'training_records', {'employee_id': outside, 'course_id': held, 'level': 3, 'attended': 1})
    allocation = d['admin'].get(f'/api/courses/{held}/cost-allocation').json
    assert allocation['attendee_count'] == 2
    assert [(r['company_id'], r['cost_rials']) for r in allocation['companies']] == [(1, 51), (2, 50)]
    assert d['a'].get(f'/api/courses/{held}/cost-allocation').status_code == 403
    assert len(d['c'].get('/api/hrm/training_records').json) == 1
    assert d['c'].get('/api/hrm/training_records').json[0]['company_id'] == 2


def test_actual_cost_owner_survives_transfer_plan_resave_and_restart(training):
    d = training
    sid = d['admin'].post('/api/competency-bank/' + CODE + '/select?company_id=1', headers=d['ah']).json['id']
    held = d['admin'].post('/api/data/courses?company_id=1', headers=d['ah'], json={
        'skill_id': sid, 'title': 'Legacy shared course', 'date': '1405/07/09', 'hours': 2, 'cost': .000101,
    }).json['id']
    eid = d['people'][0]['id']; other = d['people'][2]['id']
    assert d['admin'].put(f'/api/courses/{held}/participants', headers=d['ah'], json={'employee_ids': [eid, other]}).status_code == 200
    assert d['admin'].put(f'/api/courses/{held}/attendance', headers=d['ah'], json={'employee_ids': [eid, other]}).status_code == 200
    before = d['admin'].get(f'/api/courses/{held}/cost-allocation').json
    current = d['admin'].get('/api/hrm/people/' + str(eid)).json
    moved = d['admin'].put('/api/hrm/people/' + str(eid), headers=d['ah'], json={
        'version': current['version'], 'company_id': 2, 'site_id': d['sites'][2]['id'], 'department': 'C',
    })
    assert moved.status_code == 200, moved.json
    directory = d['admin'].get('/api/holding/personnel').json
    assert next(r for r in directory if r['id'] == eid)['company_id'] == 2
    assert d['admin'].put(f'/api/courses/{held}/participants', headers=d['ah'], json={'employee_ids': [eid, other]}).status_code == 200
    assert d['admin'].put(f'/api/courses/{held}/attendance', headers=d['ah'], json={'employee_ids': [eid, other]}).status_code == 200
    assert d['admin'].get(f'/api/courses/{held}/cost-allocation').json == before
    roster = d['admin'].get(f'/api/courses/{held}/participants').json['participants']
    assert next(r for r in roster if r['id'] == eid)['company_id'] == 1
    restarted = create_app(d['root'], testing=True)
    admin, ah = login(restarted, 'training-admin')
    assert admin.get(f'/api/courses/{held}/cost-allocation').json == before
    # Explicitly clear and reconfirm: this is a new confirmation with a new owner.
    assert admin.put(f'/api/courses/{held}/attendance', headers=ah, json={'employee_ids': [other]}).status_code == 200
    assert admin.put(f'/api/courses/{held}/attendance', headers=ah, json={'employee_ids': [eid, other]}).status_code == 200
    after = admin.get(f'/api/courses/{held}/cost-allocation').json
    assert [(r['company_id'], r['attendee_count'], r['cost_rials']) for r in after['companies']] == [(2, 2, 101)]


def test_migration_does_not_rewrite_existing_training_data_or_invent_presence(training):
    d = training; eid = d['people'][0]['id']
    sid = d['admin'].post('/api/competency-bank/' + CODE + '/select?company_id=1', headers=d['ah']).json['id']
    held = d['admin'].post('/api/data/courses?company_id=1', headers=d['ah'], json={
        'skill_id': sid, 'title': 'Untouched archived class', 'date': '1405/07/09', 'hours': 2, 'status': 'completed',
    }).json['id']
    grade = d['admin'].post('/api/data/records?company_id=1', headers=d['ah'], json={'employee_id': eid, 'course_id': held, 'level': 4}).json['id']
    assessment = d['admin'].post('/api/data/assessments?company_id=1', headers=d['ah'], json={'employee_id': eid, 'skill_id': sid, 'level': 2}).json['id']
    def snapshot():
        with sqlite3.connect(d['app'].config['DB']) as conn:
            return {name: conn.execute('SELECT * FROM '+name+' ORDER BY id').fetchall() for name in ('employees','courses','records','assessments')}
    original = snapshot()
    restarted = create_app(d['root'], testing=True)
    assert snapshot() == original
    client, h = login(restarted, 'trainer-a')
    assert row(client, 'training_records', grade)['level'] == 4
    assert row(client, 'training_assessments', assessment)['level'] == 2
    assert client.put('/api/hrm/training_records/' + str(grade), headers=h, json={'version': 0, 'level': 3}).status_code == 403
    assert client.get('/api/hrm/training_report').json['metrics']['actual_attendances'] == 0
    with sqlite3.connect(d['app'].config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM hrm_training_course_context').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM hrm_training_record_context').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM hrm_training_assessment_context').fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM course_participants').fetchone()[0] == 0


def test_independent_operations_and_scoped_export(training):
    d = training; scope = [{'company_id': 1, 'site_id': d['sites'][0]['id']}]
    creator, ch = d['account']('training-create-only', scope, dict(read=False, create=True, edit=False, delete=False, export=False))
    created = create(creator, ch, 'training_courses', {
        'company_id': 1, 'site_id': d['sites'][0]['id'], 'competency_code': CODE,
        'title': 'Create-only class', 'date': '2026-10-01', 'hours': 2,
    })
    assert creator.get('/api/hrm/training_courses').status_code == 403
    assert creator.get('/api/hrm/training_competency_options?module=training&operation=create').status_code == 200
    assert creator.get('/api/hrm/options/training_courses?module=training&operation=create').status_code == 200
    assert creator.get('/api/hrm/reports/training_courses.xlsx').status_code == 403
    editor, eh = d['account']('training-edit-only', scope, dict(read=False, create=False, edit=True, delete=False, export=False))
    assert editor.put('/api/hrm/training_courses/' + str(created['id']), headers=eh, json={'version': 1, 'title': 'Explicitly edited'}).status_code == 200
    assert editor.get('/api/hrm/training_courses').status_code == 403
    exporter, _ = d['account']('training-export-only', scope, dict(read=False, create=False, edit=False, delete=False, export=True))
    spreadsheet = exporter.get('/api/hrm/reports/training_courses.xlsx?company_id=1&site_id=' + str(d['sites'][0]['id']))
    assert spreadsheet.status_code == 200, spreadsheet.json
    book = openpyxl.load_workbook(io.BytesIO(spreadsheet.data), read_only=True)
    assert any('Explicitly edited' in str(value) for sheet in book for values in sheet.values for value in values)
    book.close()
    deleter, dh = d['account']('training-delete-only', scope, dict(read=False, create=False, edit=False, delete=True, export=False))
    assert deleter.delete('/api/hrm/training_courses/' + str(created['id']), headers=dh, json={'version': 1}).status_code == 409
    assert deleter.delete('/api/hrm/training_courses/' + str(created['id']), headers=dh, json={'version': 2}).status_code == 200
    with sqlite3.connect(d['app'].config['DB']) as conn:
        assert conn.execute('SELECT status FROM courses WHERE id=?', (created['id'],)).fetchone()[0] == 'cancelled'
        assert conn.execute('SELECT is_deleted FROM hrm_training_course_context WHERE course_id=?', (created['id'],)).fetchone()[0] == 1


def test_cannot_turn_roster_plan_or_grade_into_actual_or_edit_other_learner(training):
    d = training; cid = course(d)['id']; own = d['people'][0]['id']; outside = d['people'][1]['id']
    bad = d['a'].post('/api/hrm/training_records', headers=d['ha'], json={'employee_id': outside, 'course_id': cid, 'level': 4})
    assert bad.status_code == 403
    created = create(d['a'], d['ha'], 'training_records', {'employee_id': own, 'course_id': cid, 'level': 4})
    assert d['b'].put('/api/hrm/training_records/' + str(created['id']), headers=d['hb'], json={'version': 1, 'attended': 1}).status_code == 403
    assert d['a'].post('/api/hrm/training_records', headers=d['ha'], json={'employee_id': own, 'course_id': cid, 'level': 1}).status_code == 409
    assert d['a'].post('/api/hrm/training_assessments', json={'employee_id': own, 'competency_code': CODE, 'level': 3, 'assessment_date': '2026-10-02'}).status_code == 403
    assert d['a'].get('/api/hrm/training_report').json['metrics']['actual_attendances'] == 0


def test_current_assessment_void_and_grade_void_preserve_original_rows(training):
    d = training; cid = course(d)['id']; eid = d['people'][0]['id']
    grade = create(d['a'], d['ha'], 'training_records', {'employee_id': eid, 'course_id': cid, 'level': 4, 'attended': 1})
    assessment = create(d['a'], d['ha'], 'training_assessments', {'employee_id': eid, 'competency_code': CODE, 'level': 1, 'assessment_date': '2026-10-02'})
    assert d['a'].delete('/api/hrm/training_courses/' + str(cid), headers=d['ha'], json={'version': 1}).status_code == 409
    assert d['a'].delete('/api/hrm/training_assessments/' + str(assessment['id']), headers=d['ha'], json={'version': 1}).status_code == 200
    assert d['a'].delete('/api/hrm/training_records/' + str(grade['id']), headers=d['ha'], json={'version': 1}).status_code == 200
    assert d['a'].get('/api/hrm/training_assessments').json == []
    assert d['a'].get('/api/hrm/training_records').json == []
    assert d['a'].get('/api/hrm/training_report').json['metrics']['actual_attendances'] == 1
    with sqlite3.connect(d['app'].config['DB']) as conn:
        assert conn.execute('SELECT level FROM assessments WHERE id=?', (assessment['id'],)).fetchone()[0] == 1
        assert conn.execute('SELECT level FROM records WHERE id=?', (grade['id'],)).fetchone()[0] == 4
        assert conn.execute('SELECT count(*) FROM hrm_live_records WHERE id=?', (grade['id'],)).fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM hrm_live_assessments WHERE id=?', (assessment['id'],)).fetchone()[0] == 0


def test_concurrent_same_employee_course_does_not_duplicate_record(training):
    d = training; cid = course(d)['id']; barrier = threading.Barrier(2)
    def save():
        client, headers = login(d['app'], 'trainer-a'); barrier.wait(timeout=10)
        return client.post('/api/hrm/training_records', headers=headers, json={
            'employee_id': d['people'][0]['id'], 'course_id': cid, 'level': 2,
        }).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(save) for _ in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == [201, 409]
    with sqlite3.connect(d['app'].config['DB']) as conn:
        assert conn.execute('SELECT count(*) FROM records WHERE course_id=?', (cid,)).fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM hrm_training_record_context').fetchone()[0] == 1


def test_legacy_actual_snapshot_migration_uses_original_company_not_transferred_employer(training):
    d = training; sid = d['admin'].post('/api/competency-bank/' + CODE + '/select?company_id=1', headers=d['ah']).json['id']
    held = d['admin'].post('/api/data/courses?company_id=1', headers=d['ah'], json={
        'skill_id': sid, 'title': 'Pre-snapshot attendance', 'date': '1405/07/09', 'hours': 2, 'cost': .000101,
    }).json['id']
    eid = d['people'][0]['id']
    with sqlite3.connect(d['app'].config['DB']) as conn:
        conn.execute('INSERT INTO course_participants(course_id,employee_id,attended) VALUES (?,?,1)', (held, eid))
        conn.execute('UPDATE hrm_employee_details SET company_id=2,site_id=? WHERE employee_id=?', (d['sites'][2]['id'], eid))
        original = conn.execute('SELECT course_id,employee_id,attended FROM course_participants').fetchall()
        for name in ('attendance_company_id', 'attendance_site_id', 'attendance_department'):
            conn.execute('ALTER TABLE course_participants DROP COLUMN ' + name)
    restarted = create_app(d['root'], testing=True)
    admin, _ = login(restarted, 'training-admin')
    allocation = admin.get(f'/api/courses/{held}/cost-allocation').json
    assert [(r['company_id'], r['cost_rials']) for r in allocation['companies']] == [(1, 101)]
    with sqlite3.connect(d['app'].config['DB']) as conn:
        assert conn.execute('SELECT course_id,employee_id,attended FROM course_participants').fetchall() == original
        assert conn.execute('SELECT attendance_company_id,attendance_site_id,attendance_department FROM course_participants').fetchone() == (1, None, '')


def test_activity_date_filter_does_not_erase_prior_completion_for_renewal(training):
    d = training; cid = course(d)['id']; eid = d['people'][0]['id']
    create(d['a'], d['ha'], 'training_records', {'employee_id': eid, 'course_id': cid, 'level': 2, 'attended': 1})
    create(d['a'], d['ha'], 'training_requirements', {
        'company_id': 1, 'site_id': d['sites'][0]['id'], 'competency_code': CODE,
        'required_level': 3, 'renewal_days': 30, 'onboarding': 1,
    })
    report = d['a'].get('/api/hrm/training_report?from_date=2026-11-01&to_date=2026-11-30').json
    assert report['metrics']['actual_attendances'] == 0
    assert report['metrics']['achievement_percent'] is None
    assert report['requirements'][0]['last_training_date'] == '2026-10-01'
    assert report['requirements'][0]['renewal_due'] is True
    assert report['requirements'][0]['onboarding_missing'] is False
    assert d['a'].get('/api/hrm/training_report?company_id=2').status_code == 403
    assert d['a'].get('/api/hrm/training_report?site_id=' + str(d['sites'][1]['id'])).status_code == 403


def test_holding_presence_requires_prior_plan_even_for_owners_company(training):
    d = training; eid = d['people'][0]['id']
    held = create(d['admin'], d['ah'], 'training_courses', {
        'company_id': 1, 'site_id': d['sites'][0]['id'], 'competency_code': CODE,
        'title': 'Owner company shared class', 'date': '2026-10-01', 'hours': 2,
        'calendar_scope': 'holding', 'status': 'completed',
    })['id']
    failed = d['a'].post('/api/hrm/training_records', headers=d['ha'], json={
        'employee_id': eid, 'course_id': held, 'level': 3, 'attended': 1,
    })
    assert failed.status_code == 400
    assert d['a'].get('/api/hrm/training_records').json == []
    grade = create(d['a'], d['ha'], 'training_records', {'employee_id': eid, 'course_id': held, 'level': 3})
    assert d['a'].get('/api/hrm/training_report').json['metrics']['actual_attendances'] == 0
    assert d['admin'].put(f'/api/courses/{held}/participants', headers=d['ah'], json={'employee_ids': [eid]}).status_code == 200
    changed = d['a'].put('/api/hrm/training_records/' + str(grade['id']), headers=d['ha'], json={'version': 1, 'attended': 1})
    assert changed.status_code == 200
    assert d['a'].get('/api/hrm/training_report').json['metrics']['actual_attendances'] == 1


def test_soft_void_is_respected_by_legacy_gap_views(training):
    d = training; eid = d['people'][0]['id']; cid = course(d)['id']
    with sqlite3.connect(d['app'].config['DB']) as conn:
        sid = conn.execute('SELECT skill_id FROM courses WHERE id=?', (cid,)).fetchone()[0]
    assert d['admin'].post('/api/data/requirements?company_id=1', headers=d['ah'], json={'job': 'Engineer', 'skill_id': sid, 'level': 3}).status_code == 201
    grade = create(d['a'], d['ha'], 'training_records', {'employee_id': eid, 'course_id': cid, 'level': 4})
    assessment = create(d['a'], d['ha'], 'training_assessments', {'employee_id': eid, 'competency_code': CODE, 'level': 1, 'assessment_date': '2026-10-02'})
    gaps = d['admin'].get('/api/gaps?company_id=1').json
    assert next(row for row in gaps if row['employee_id'] == eid)['gap'] == 2
    assert d['a'].delete('/api/hrm/training_assessments/' + str(assessment['id']), headers=d['ha'], json={'version': 1}).status_code == 200
    assert not any(row['employee_id'] == eid for row in d['admin'].get('/api/gaps?company_id=1').json)
    assert d['a'].delete('/api/hrm/training_records/' + str(grade['id']), headers=d['ha'], json={'version': 1}).status_code == 200
    gaps = d['admin'].get('/api/gaps?company_id=1').json
    assert next(row for row in gaps if row['employee_id'] == eid)['gap'] == 3
