"""Holding training calendar and explicitly separate internal company events."""

from flask import g, jsonify, request


def migrate(db):
    """Extend existing courses in place so attendance and legacy IDs stay intact."""
    columns = {row['name'] for row in db.execute('PRAGMA table_info(courses)')}
    if 'calendar_scope' not in columns:
        db.execute(
            "ALTER TABLE courses ADD COLUMN calendar_scope TEXT NOT NULL "
            "DEFAULT 'holding' CHECK(calendar_scope IN ('holding','internal'))"
        )
    db.execute(
        'CREATE INDEX IF NOT EXISTS courses_calendar_scope '
        'ON courses(calendar_scope,company_id,date)'
    )
    db.commit()


def register(app, db, authenticated, company):
    with app.app_context():
        migrate(db())

    @app.get('/api/calendar')
    @authenticated
    def training_calendar():
        scope = request.args.get('scope', 'holding')
        if scope not in ('holding', 'internal'):
            raise ValueError('نوع تقویم باید هلدینگ یا داخلی شرکت باشد')

        if scope == 'holding' and g.user['role'] != 'admin':
            raise PermissionError()

        # Consolidated planning belongs to the holding administrator. Company
        # users can view their authorized internal calendar independently.
        where = 'c.calendar_scope=?'
        parameters = [scope]
        if scope == 'internal':
            where += ' AND c.company_id=?'
            parameters.append(company())

        # A calendar is a schedule, never an employee or attendance report.
        # Keep this projection explicit when more private course fields are added.
        rows = db().execute(
            '''SELECT c.id,c.company_id,co.name company_name,c.skill_id,
                      COALESCE(b.title,s.title) skill_title,c.title,c.date,
                      c.instructor,c.hours,c.status,c.calendar_scope
               FROM courses c
               JOIN companies co ON co.id=c.company_id
               JOIN skills s ON s.id=c.skill_id AND s.company_id=c.company_id
               LEFT JOIN competency_links l ON l.skill_id=s.id
               LEFT JOIN holding_competencies b ON b.code=l.code
               WHERE ''' + where + ' ORDER BY c.date,c.id',
            parameters,
        )
        return jsonify([dict(row) for row in rows])
