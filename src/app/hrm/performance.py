"""Performance periods, indicators, weighted scores, trends and discipline records."""
import json

from flask import g, jsonify, request

from ..validation import positive_id, number
from . import access
from .crud import Crud, parse_values

DISCIPLINE_KINDS = ('commendation', 'notice', 'warning', 'reprimand')
DISCIPLINE_LABELS = {'commendation': 'تشویق', 'notice': 'تذکر', 'warning': 'اخطار', 'reprimand': 'توبیخ'}


def register(ctx):
    Crud(ctx, 'performance-periods', 'performance_periods',
         {'title': 'text', 'start_date': 'date', 'end_date': 'date', 'status': 'text'},
         required=('title', 'start_date', 'end_date'), view='performance.view',
         edit='performance.edit', label='دوره ارزیابی').register()
    Crud(ctx, 'performance-indicators', 'performance_indicators',
         {'title': 'text', 'weight': 'float', 'target': 'float', 'description': 'text', 'active': 'bool'},
         required=('title',), view='performance.view', edit='performance.edit',
         label='شاخص عملکرد').register()

    @ctx.route('performance-scores', permission='performance.view')
    def hrm_scores_list():
        sql = ('SELECT s.*,e.name employee_name,e.code employee_code,p.title period_title,'
               'i.title indicator_title,i.weight,pp.unit_id FROM performance_scores s '
               'JOIN employees e ON e.id=s.employee_id '
               'JOIN performance_periods p ON p.id=s.period_id '
               'JOIN performance_indicators i ON i.id=s.indicator_id '
               'LEFT JOIN personnel_profiles pp ON pp.employee_id=e.id WHERE s.company_id=?')
        params = [g.hrm_cid]
        if request.args.get('period_id'):
            sql += ' AND s.period_id=?'
            params.append(positive_id(request.args['period_id']))
        if request.args.get('employee_id'):
            sql += ' AND s.employee_id=?'
            params.append(positive_id(request.args['employee_id']))
        rows = [r for r in ctx.rows(sql + ' ORDER BY s.period_id DESC,s.employee_id,s.indicator_id', params)
                if access.employee_allowed(ctx.c, g.user, {'id': r['employee_id'], 'unit_id': r.get('unit_id')})]
        return jsonify(rows)

    @ctx.route('performance-scores', methods=('POST',), permission='performance.edit')
    def hrm_scores_save():
        cid = g.hrm_cid
        body = ctx.body()
        eid = positive_id(body.get('employee_id'))
        ctx.employee(eid, cid)
        period_id = ctx.reference('performance_periods', body.get('period_id'), cid)
        scores = body.get('scores')
        if not isinstance(scores, list) or not scores:
            raise ValueError('امتیاز شاخص‌ها ارسال نشده است')
        with ctx.c:
            ctx.c.execute('BEGIN IMMEDIATE')
            for item in scores:
                if not isinstance(item, dict):
                    raise ValueError('ساختار امتیاز معتبر نیست')
                indicator_id = ctx.reference('performance_indicators', item.get('indicator_id'), cid)
                score = number(item.get('score'), minimum=0, maximum=100)
                comment = ctx.text(item, 'comment', maximum=1000)
                ctx.c.execute(
                    'INSERT INTO performance_scores(company_id,period_id,employee_id,indicator_id,score,comment,evaluator_id) '
                    'VALUES (?,?,?,?,?,?,?) ON CONFLICT(period_id,employee_id,indicator_id) '
                    'DO UPDATE SET score=excluded.score,comment=excluded.comment,evaluator_id=excluded.evaluator_id,created_at=CURRENT_TIMESTAMP',
                    (cid, period_id, eid, indicator_id, score, comment, g.user['id']))
            ctx.history(eid, 'performance', period_id, [('scores', '', json.dumps(scores, ensure_ascii=False)[:500])])
            ctx.audit_log('save_performance_scores', f'{eid}/{period_id}')
        return ctx.ok()

    @ctx.route('performance-report', permission='performance.view')
    def hrm_performance_report():
        cid = g.hrm_cid
        rows = ctx.rows(
            'SELECT s.period_id,p.title period_title,p.start_date,p.end_date,s.employee_id,'
            'e.name employee_name,e.code employee_code,e.department,pp.unit_id,'
            'ROUND(SUM(s.score*i.weight)/NULLIF(SUM(i.weight),0),2) weighted_average,'
            'COUNT(s.id) indicators FROM performance_scores s '
            'JOIN employees e ON e.id=s.employee_id JOIN performance_periods p ON p.id=s.period_id '
            'JOIN performance_indicators i ON i.id=s.indicator_id '
            'LEFT JOIN personnel_profiles pp ON pp.employee_id=e.id WHERE s.company_id=? '
            'GROUP BY s.period_id,s.employee_id ORDER BY p.start_date DESC,weighted_average DESC', (cid,))
        rows = [r for r in rows if access.employee_allowed(ctx.c, g.user, {'id': r['employee_id'], 'unit_id': r.get('unit_id')})]
        return jsonify(rows)

    @ctx.route('performance-trend', permission='performance.view')
    def hrm_performance_trend():
        eid = positive_id(request.args.get('employee_id'))
        ctx.employee(eid, g.hrm_cid)
        return jsonify(ctx.rows(
            'SELECT p.id period_id,p.title period_title,p.start_date,p.end_date,'
            'ROUND(SUM(s.score*i.weight)/NULLIF(SUM(i.weight),0),2) weighted_average '
            'FROM performance_periods p JOIN performance_scores s ON s.period_id=p.id '
            'JOIN performance_indicators i ON i.id=s.indicator_id '
            'WHERE s.employee_id=? AND p.company_id=? GROUP BY p.id ORDER BY p.start_date',
            (eid, g.hrm_cid)))

    @ctx.route('disciplinary', permission='performance.view', confidential='discipline')
    def hrm_disciplinary_list():
        rows = ctx.rows(
            'SELECT d.*,e.name employee_name,e.code employee_code FROM disciplinary_records d '
            'JOIN employees e ON e.id=d.employee_id WHERE d.company_id=? ORDER BY d.date DESC,d.id DESC',
            (g.hrm_cid,))
        for row in rows:
            row['kind_label'] = DISCIPLINE_LABELS.get(row['kind'], row['kind'])
        return jsonify(rows)

    @ctx.route('disciplinary', methods=('POST',), permission='performance.edit', confidential='discipline')
    def hrm_disciplinary_create():
        cid = g.hrm_cid
        body = ctx.body()
        eid = positive_id(body.get('employee_id'))
        ctx.employee(eid, cid)
        kind = ctx.text(body, 'kind', required=True, maximum=40)
        if kind not in DISCIPLINE_KINDS:
            raise ValueError('نوع سابقه انضباطی معتبر نیست')
        values = parse_values(ctx, body, {
            'date': 'date', 'reason': 'text', 'decision': 'text', 'decision_no': 'text',
            'confidential': 'bool',
        }, required=('date', 'reason'))
        cur = ctx.c.execute(
            'INSERT INTO disciplinary_records(company_id,employee_id,kind,date,reason,decision,decision_no,confidential,created_by) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (cid, eid, kind, values['date'], values['reason'], values.get('decision', ''),
             values.get('decision_no', ''), values.get('confidential', 1), g.user['id']))
        ctx.history(eid, 'disciplinary', cur.lastrowid, [(k, '', v) for k, v in values.items()])
        ctx.audit_log('create_disciplinary', str(cur.lastrowid))
        ctx.c.commit()
        return jsonify(id=cur.lastrowid), 201

    @ctx.route('disciplinary/<int:rid>', methods=('PUT',), permission='performance.edit', confidential='discipline')
    def hrm_disciplinary_update(rid):
        row = ctx.c.execute('SELECT * FROM disciplinary_records WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رکورد یافت نشد', 404)
        ctx.employee(row['employee_id'], g.hrm_cid)
        values = parse_values(ctx, ctx.body(), {
            'date': 'date', 'reason': 'text', 'decision': 'text', 'decision_no': 'text',
            'confidential': 'bool', 'kind': 'text'})
        if values.get('kind') and values['kind'] not in DISCIPLINE_KINDS:
            raise ValueError('نوع سابقه انضباطی معتبر نیست')
        if not values:
            raise ValueError('تغییری ارسال نشده است')
        ctx.c.execute('UPDATE disciplinary_records SET ' + ','.join(k + '=?' for k in values) + ' WHERE id=?',
                      list(values.values()) + [rid])
        ctx.history(row['employee_id'], 'disciplinary', rid, [(k, row[k], v) for k, v in values.items()])
        ctx.audit_log('update_disciplinary', str(rid))
        ctx.c.commit()
        return ctx.ok()

    @ctx.route('disciplinary/<int:rid>', methods=('DELETE',), permission='performance.edit', confidential='discipline')
    def hrm_disciplinary_delete(rid):
        row = ctx.c.execute('SELECT * FROM disciplinary_records WHERE id=? AND company_id=?',
                            (positive_id(rid), g.hrm_cid)).fetchone()
        if not row:
            return ctx.error('رکورد یافت نشد', 404)
        ctx.c.execute('DELETE FROM disciplinary_records WHERE id=?', (rid,))
        ctx.history(row['employee_id'], 'disciplinary', rid, [('deleted', row['kind'], '')])
        ctx.audit_log('delete_disciplinary', str(rid))
        ctx.c.commit()
        return ctx.ok()
