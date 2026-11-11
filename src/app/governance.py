"""Holding decisions and evidence-based development follow-up, additive migration."""
import json
from flask import g, request, jsonify
from .validation import json_object, positive_id, jalali_date, number

SCHEMA = '''
CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY,applied_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS need_reviews(id INTEGER PRIMARY KEY,need_id INTEGER NOT NULL REFERENCES needs(id),company_id INTEGER NOT NULL REFERENCES companies(id),user_id INTEGER NOT NULL REFERENCES users(id),previous_status TEXT NOT NULL,status TEXT NOT NULL,decision TEXT NOT NULL,created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS development_plans(need_id INTEGER PRIMARY KEY REFERENCES needs(id),company_id INTEGER NOT NULL REFERENCES companies(id),objective TEXT NOT NULL,method TEXT NOT NULL,owner TEXT NOT NULL,due_date TEXT NOT NULL,success_criterion TEXT NOT NULL,baseline REAL NOT NULL,target REAL NOT NULL,actual REAL,evidence TEXT NOT NULL DEFAULT '',review_date TEXT NOT NULL DEFAULT '',course_id INTEGER REFERENCES courses(id),version INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS needs_holding_queue ON needs(year,status,company_id);
CREATE INDEX IF NOT EXISTS review_need ON need_reviews(need_id,id);
CREATE INDEX IF NOT EXISTS employee_company_job ON employees(company_id,job);
INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0.11.0');
'''

def register(app, db, authenticated, company, audit, error, gaps):
    with app.app_context():
        db().executescript(SCHEMA)
        columns = {r['name'] for r in db().execute('PRAGMA table_info(needs)')}
        if 'version' not in columns:
            db().execute('ALTER TABLE needs ADD COLUMN version INTEGER NOT NULL DEFAULT 1')
        db().execute("CREATE TRIGGER IF NOT EXISTS needs_content_version AFTER UPDATE OF skill_id,department,reason,priority,year ON needs BEGIN UPDATE needs SET version=version+1 WHERE id=NEW.id; END")
        db().commit()

    def admin():
        if g.user['role'] != 'admin':
            raise PermissionError()

    @app.get('/api/holding/needs')
    @authenticated
    def holding_needs():
        admin()
        year = number(request.args.get('year'), integer=True, minimum=1300, maximum=1499)
        rows = db().execute('''SELECT n.*,c.name company_name,s.code skill_code,s.title skill_title,
            p.objective,p.owner,p.due_date,p.success_criterion,p.actual,p.target
            FROM needs n JOIN companies c ON c.id=n.company_id
            JOIN skills s ON s.id=n.skill_id AND s.company_id=n.company_id
            LEFT JOIN development_plans p ON p.need_id=n.id
            WHERE n.year=? ORDER BY n.priority DESC,n.company_id,n.id''', (year,))
        return jsonify([dict(r) for r in rows])

    @app.get('/api/holding/gaps')
    @authenticated
    def holding_gaps():
        admin()
        result = {}
        for co in db().execute('SELECT * FROM companies').fetchall():
            codes = {r['id']:r['code'] for r in db().execute('SELECT id,code FROM skills WHERE company_id=?',(co['id'],))}
            for gap in gaps(co['id']):
                code = codes[gap['skill_id']]
                row = result.setdefault(code, dict(code=code,title=gap['title'],importance=gap['importance'],people=0,gap_total=0,companies={}))
                row['people'] += 1
                row['gap_total'] += gap['gap']
                row['companies'][co['name']] = row['companies'].get(co['name'],0)+1
        return jsonify(sorted(result.values(),key=lambda r:(-r['importance'],-r['gap_total'],r['code'])))

    @app.post('/api/holding/needs/<int:nid>/decision')
    @authenticated
    def decide(nid):
        admin()
        d=json_object()
        status=d.get('status')
        reason=d.get('decision')
        if status not in ('submitted','approved','rejected') or not isinstance(reason,str) or not reason.strip() or len(reason)>4000:
            raise ValueError('وضعیت معتبر و دلیل تصمیم (حداکثر ۴۰۰۰ نویسه) لازم است')
        version=positive_id(d.get('version'))
        with db():
            db().execute('BEGIN IMMEDIATE')
            old=db().execute('SELECT * FROM needs WHERE id=?',(nid,)).fetchone()
            if not old:return error('نیاز یافت نشد',404)
            if old['version']!=version:return error('نیاز توسط کاربر دیگری تغییر کرده؛ صفحه را تازه کنید',409)
            if old['status']==status:return error('این تصمیم قبلاً ثبت شده است',409)
            if old['status']!='submitted' and status!='submitted':return error('ابتدا نیاز را بازگشایی کنید',409)
            db().execute('UPDATE needs SET status=?,decision=?,version=version+1 WHERE id=?',(status,reason.strip(),nid))
            db().execute('INSERT INTO need_reviews(need_id,company_id,user_id,previous_status,status,decision) VALUES (?,?,?,?,?,?)',(nid,old['company_id'],g.user['id'],old['status'],status,reason.strip()))
            audit('holding_need_decision',old['company_id'],json.dumps(dict(id=nid,status=status),ensure_ascii=False))
        return jsonify(ok=True,version=version+1)

    @app.route('/api/development-plans/<int:nid>',methods=['GET','PUT'])
    @authenticated
    def plan(nid):
        cid=company()
        need=db().execute('SELECT * FROM needs WHERE id=? AND company_id=?',(nid,cid)).fetchone()
        if not need:return error('نیاز یافت نشد',404)
        if request.method=='PUT':
            admin()
            d=json_object()
            fields=['objective','method','owner','due_date','success_criterion','baseline','target','actual','evidence','review_date','course_id']
            for key in ['objective','method','owner','success_criterion']:
                if not isinstance(d.get(key),str) or not d[key].strip() or len(d[key])>4000:raise ValueError('هدف، روش، مسئول و معیار موفقیت لازم است')
            d['due_date']=jalali_date(d.get('due_date'))
            d['review_date']=jalali_date(d['review_date']) if d.get('review_date') else ''
            for key in ['baseline','target']:d[key]=number(d.get(key))
            d['actual']=number(d['actual']) if d.get('actual') not in (None,'') else None
            d['evidence']=d.get('evidence','')
            if not isinstance(d['evidence'],str) or len(d['evidence'])>8000:raise ValueError('شاهد ارزیابی نامعتبر است')
            if d['actual'] is not None and (not d['evidence'].strip() or not d['review_date']):raise ValueError('نتیجه واقعی به شاهد و تاریخ ارزیابی نیاز دارد')
            d['course_id']=positive_id(d['course_id']) if d.get('course_id') else None
            with db():
                db().execute('BEGIN IMMEDIATE')
                need=db().execute('SELECT * FROM needs WHERE id=?',(nid,)).fetchone()
                if need['status']!='approved':return error('برنامه فقط برای نیاز مصوب ثبت می‌شود',409)
                if d['course_id']:
                    course=db().execute('SELECT c.*,s.code FROM courses c JOIN skills s ON s.id=c.skill_id WHERE c.id=?',(d['course_id'],)).fetchone()
                    skill=db().execute('SELECT code FROM skills WHERE id=?',(need['skill_id'],)).fetchone()
                    if not course or course['status']=='cancelled' or course['code']!=skill['code'] or (course['calendar_scope']=='internal' and course['company_id']!=cid):raise ValueError('دوره باید مرتبط با شایستگی و در تقویم هلدینگ یا همین شرکت باشد')
                old=db().execute('SELECT version FROM development_plans WHERE need_id=?',(nid,)).fetchone()
                if d.get('version')!=(old['version'] if old else 0):return error('برنامه تغییر کرده؛ دوباره بارگذاری کنید',409)
                if old:
                    db().execute('UPDATE development_plans SET '+','.join(k+'=?' for k in fields)+',version=version+1 WHERE need_id=?',[d[k] for k in fields]+[nid])
                else:
                    db().execute('INSERT INTO development_plans(need_id,company_id,'+','.join(fields)+') VALUES ('+','.join('?' for _ in range(len(fields)+2))+')',[nid,cid]+[d[k] for k in fields])
                audit('development_plan_saved',cid,str(nid))
        current=db().execute('SELECT * FROM development_plans WHERE need_id=?',(nid,)).fetchone()
        history=db().execute('SELECT r.status,r.previous_status,r.decision,r.created,u.username FROM need_reviews r JOIN users u ON u.id=r.user_id WHERE need_id=? ORDER BY r.id DESC',(nid,))
        return jsonify(plan=dict(current) if current else None,history=[dict(r) for r in history])
