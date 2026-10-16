"""Holding-only planning, recorded attendance, and company cost allocation."""

import json
from decimal import Decimal, ROUND_HALF_UP

from flask import g, jsonify, request
from .validation import json_object, positive_id


SCHEMA = '''
CREATE TABLE IF NOT EXISTS course_participants(
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    attended INTEGER NOT NULL DEFAULT 0 CHECK(attended IN (0,1)),
    PRIMARY KEY(course_id,employee_id)
);
CREATE INDEX IF NOT EXISTS course_participants_employee
    ON course_participants(employee_id);
'''

PERSONNEL_FIELDS = '''e.id,e.code,e.name,e.job,e.department,e.company_id,
                      co.name company_name'''
PERSONNEL_ORDER = ' ORDER BY e.name,co.name,e.id'
MAX_ALLOCATION_RIALS = 9_007_199_254_740_991


def register(app, db, authenticated, audit, error):
    with app.app_context():
        db().executescript(SCHEMA)
        # Existing selections express a plan, never evidence of actual presence.
        # An additive column also preserves row counts in archived databases.
        columns = {row['name'] for row in db().execute('PRAGMA table_info(course_participants)')}
        if 'attended' not in columns:
            db().execute('ALTER TABLE course_participants ADD COLUMN attended '
                         'INTEGER NOT NULL DEFAULT 0 CHECK(attended IN (0,1))')
        db().commit()

    def require_admin():
        if g.user['role'] != 'admin':
            raise PermissionError()

    def course_for_planning(connection, course_id):
        course = connection.execute(
            'SELECT id,title,date,company_id,calendar_scope,cost,status FROM courses WHERE id=?',
            (course_id,),
        ).fetchone()
        if course is None:
            return None
        if course['calendar_scope'] != 'holding':
            raise ValueError('انتخاب شرکت‌کنندگان مشترک فقط برای دوره‌های تقویم هلدینگ مجاز است')
        return dict(course)

    def roster(connection, course):
        participants = connection.execute(
            'SELECT ' + PERSONNEL_FIELDS + ''',p.attended
             FROM course_participants p
             JOIN employees e ON e.id=p.employee_id
             JOIN companies co ON co.id=e.company_id
             WHERE p.course_id=?''' + PERSONNEL_ORDER,
            (course['id'],),
        )
        rows = []
        for row in participants:
            person = dict(row)
            person['attended'] = bool(person['attended'])
            rows.append(person)
        return {'course': course, 'participants': rows}

    def employee_ids_from_request():
        body = json_object()
        if set(body) != {'employee_ids'} or not isinstance(body.get('employee_ids'), list):
            raise ValueError('فهرست شناسه کارکنان را ارسال کنید')
        employee_ids = body['employee_ids']
        if len(employee_ids) > 5000:
            raise ValueError('حداکثر ۵۰۰۰ نفر را برای هر دوره انتخاب کنید')
        if any(type(eid) is not int or not 1 <= eid <= 9223372036854775807 for eid in employee_ids):
            raise ValueError('شناسه کارکنان باید عدد صحیح مثبت باشد')
        if len(set(employee_ids)) != len(employee_ids):
            raise ValueError('انتخاب تکراری کارکنان مجاز نیست')
        return employee_ids

    def allocation(connection, course):
        groups = [dict(row) for row in connection.execute('''
            SELECT co.id company_id,co.name company_name,
                   count(*) planned_count,sum(p.attended) attendee_count
            FROM course_participants p
            JOIN employees e ON e.id=p.employee_id
            JOIN companies co ON co.id=e.company_id
            WHERE p.course_id=?
            GROUP BY co.id,co.name ORDER BY co.id
        ''', (course['id'],))]
        count = sum(row['attendee_count'] for row in groups)
        # Course costs are stored in millions of rials. Round the source once
        # to the nearest rial; distribute whole rials with largest remainders.
        total = int((Decimal(str(course['cost'])) * 1_000_000).to_integral_value(
            rounding=ROUND_HALF_UP))
        # JSON numbers must remain exact in the browser. Legacy course data is
        # never changed when its cost exceeds the supported accounting range.
        if total > MAX_ALLOCATION_RIALS:
            raise ValueError('مبلغ دوره برای تسهیم دقیق بیش از محدوده مجاز است؛ '
                             'حداکثر هزینه قابل تسهیم ۹٬۰۰۷٬۱۹۹٬۲۵۴ میلیون ریال است')
        if count:
            for row in groups:
                row['cost_rials'], row['_remainder'] = divmod(total * row['attendee_count'], count)
            remainder = total - sum(row['cost_rials'] for row in groups)
            ranked = sorted(groups, key=lambda row: (-row['_remainder'], row['company_id']))
            for row in ranked[:remainder]:
                row['cost_rials'] += 1
        for row in groups:
            row.pop('_remainder', None)
            row.setdefault('cost_rials', 0)
            row['cost_million_rials'] = row['cost_rials'] / 1_000_000
        return {
            'course': course,
            'currency': 'IRR', 'source_cost_unit': 'million_rials',
            'allocation_basis': 'actual_attendance',
            'maximum_total_cost_rials': MAX_ALLOCATION_RIALS,
            'rounding': 'nearest_rial_largest_remainder_company_id_tiebreak',
            'planned_count': sum(row['planned_count'] for row in groups),
            'attendee_count': count,
            'total_cost_rials': total,
            'total_cost_million_rials': total / 1_000_000,
            'allocated_cost_rials': total if count else 0,
            'unallocated_cost_rials': 0 if count else total,
            'companies': groups,
        }

    @app.get('/api/holding/personnel')
    @authenticated
    def holding_personnel():
        require_admin()
        rows = db().execute(
            'SELECT ' + PERSONNEL_FIELDS + '''
             FROM employees e JOIN companies co ON co.id=e.company_id'''
            + PERSONNEL_ORDER
        )
        return jsonify([dict(row) for row in rows])

    @app.route('/api/courses/<int:course_id>/participants', methods=['GET', 'PUT'])
    @authenticated
    def course_participants(course_id):
        require_admin()
        course_id = positive_id(course_id)
        connection = db()
        if request.method == 'GET':
            course = course_for_planning(connection, course_id)
            if course is None:
                return error('دوره یافت نشد', 404)
            return jsonify(roster(connection, course))

        employee_ids = employee_ids_from_request()

        # Lock before validating the course and employees. Concurrent course
        # changes cannot turn this into an internal event halfway through a save.
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            course = course_for_planning(connection, course_id)
            if course is None:
                return error('دوره یافت نشد', 404)
            # Chunk placeholders for supported SQLite builds with lower limits.
            for start in range(0, len(employee_ids), 500):
                batch = employee_ids[start:start + 500]
                found = connection.execute(
                    'SELECT count(*) FROM employees WHERE id IN ('
                    + ','.join('?' for _ in batch) + ')', batch,
                ).fetchone()[0]
                if found != len(batch):
                    raise ValueError('یک یا چند نفر از کارکنان یافت نشدند')
            attended = {row[0] for row in connection.execute(
                'SELECT employee_id FROM course_participants WHERE course_id=? AND attended=1',
                (course_id,),
            )}
            if not attended.issubset(employee_ids):
                raise ValueError('برای حذف فرد حاضر، ابتدا حضور ثبت‌شده او را در بخش حضور و تسهیم هزینه بردارید')
            connection.execute('DELETE FROM course_participants WHERE course_id=?', (course_id,))
            connection.executemany(
                'INSERT INTO course_participants(course_id,employee_id,attended) VALUES (?,?,?)',
                [(course_id, eid, int(eid in attended)) for eid in employee_ids],
            )
            audit(
                'update_course_participants', course['company_id'],
                json.dumps({'course_id': course_id, 'participants': len(employee_ids)}),
            )
        return jsonify(roster(connection, course))

    @app.route('/api/courses/<int:course_id>/attendance', methods=['GET', 'PUT'])
    @authenticated
    def course_attendance(course_id):
        require_admin()
        course_id = positive_id(course_id)
        connection = db()
        if request.method == 'GET':
            course = course_for_planning(connection, course_id)
            if course is None:
                return error('دوره یافت نشد', 404)
            return jsonify(roster(connection, course))
        employee_ids = employee_ids_from_request()
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            course = course_for_planning(connection, course_id)
            if course is None:
                return error('دوره یافت نشد', 404)
            planned = {row['employee_id']: bool(row['attended']) for row in connection.execute(
                'SELECT employee_id,attended FROM course_participants WHERE course_id=?', (course_id,),
            )}
            if not set(employee_ids).issubset(planned):
                raise ValueError('حضور فقط برای کارکنان انتخاب‌شده در برنامه این دوره قابل ثبت است')
            if course['status'] == 'cancelled' and any(not planned[eid] for eid in employee_ids):
                raise ValueError('افزودن حضور جدید برای دوره لغوشده مجاز نیست؛ اصلاح یا حذف حضور قبلی امکان‌پذیر است')
            connection.execute('UPDATE course_participants SET attended=0 WHERE course_id=?', (course_id,))
            connection.executemany(
                'UPDATE course_participants SET attended=1 WHERE course_id=? AND employee_id=?',
                [(course_id, eid) for eid in employee_ids],
            )
            audit('update_course_attendance', course['company_id'],
                  json.dumps({'course_id': course_id, 'attendees': len(employee_ids)}))
        return jsonify(roster(connection, course))

    @app.get('/api/courses/<int:course_id>/cost-allocation')
    @authenticated
    def course_cost_allocation(course_id):
        require_admin()
        course_id = positive_id(course_id)
        connection = db()
        # Keep the source cost and grouped attendance in the same read snapshot.
        with connection:
            connection.execute('BEGIN')
            course = course_for_planning(connection, course_id)
            if course is None:
                return error('دوره یافت نشد', 404)
            result = allocation(connection, course)
        return jsonify(result)
