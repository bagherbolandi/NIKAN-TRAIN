"""Admin-only participant planning for training shared across the holding."""

import json

from flask import g, jsonify, request
from .validation import json_object, positive_id


SCHEMA = '''
CREATE TABLE IF NOT EXISTS course_participants(
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    PRIMARY KEY(course_id,employee_id)
);
CREATE INDEX IF NOT EXISTS course_participants_employee
    ON course_participants(employee_id);
'''

PERSONNEL_FIELDS = '''e.id,e.code,e.name,e.job,e.department,e.company_id,
                      co.name company_name'''
PERSONNEL_ORDER = ' ORDER BY e.name,co.name,e.id'


def register(app, db, authenticated, audit, error):
    with app.app_context():
        db().executescript(SCHEMA)
        db().commit()

    def require_admin():
        if g.user['role'] != 'admin':
            raise PermissionError()

    def course_for_planning(connection, course_id):
        course = connection.execute(
            'SELECT id,title,date,company_id,calendar_scope FROM courses WHERE id=?',
            (course_id,),
        ).fetchone()
        if course is None:
            return None
        if course['calendar_scope'] != 'holding':
            raise ValueError('انتخاب شرکت‌کنندگان مشترک فقط برای دوره‌های تقویم هلدینگ مجاز است')
        return dict(course)

    def roster(connection, course):
        participants = connection.execute(
            'SELECT ' + PERSONNEL_FIELDS + '''
             FROM course_participants p
             JOIN employees e ON e.id=p.employee_id
             JOIN companies co ON co.id=e.company_id
             WHERE p.course_id=?''' + PERSONNEL_ORDER,
            (course['id'],),
        )
        return {'course': course, 'participants': [dict(row) for row in participants]}

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

        body = json_object()
        if not isinstance(body.get('employee_ids'), list):
            raise ValueError('فهرست شناسه کارکنان را ارسال کنید')
        employee_ids = body['employee_ids']
        if len(employee_ids) > 5000:
            raise ValueError('حداکثر ۵۰۰۰ نفر را برای هر دوره انتخاب کنید')
        if any(type(eid) is not int or not 1 <= eid <= 9223372036854775807 for eid in employee_ids):
            raise ValueError('شناسه کارکنان باید عدد صحیح مثبت باشد')
        if len(set(employee_ids)) != len(employee_ids):
            raise ValueError('انتخاب تکراری کارکنان مجاز نیست')

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
            connection.execute('DELETE FROM course_participants WHERE course_id=?', (course_id,))
            connection.executemany(
                'INSERT INTO course_participants(course_id,employee_id) VALUES (?,?)',
                [(course_id, eid) for eid in employee_ids],
            )
            audit(
                'update_course_participants', course['company_id'],
                json.dumps({'course_id': course_id, 'participants': len(employee_ids)}),
            )
        return jsonify(roster(connection, course))
