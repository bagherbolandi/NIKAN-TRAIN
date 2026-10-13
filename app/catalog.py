"""Canonical holding competencies; legacy company IDs remain stable links."""
SCHEMA='''CREATE TABLE IF NOT EXISTS competency_links(skill_id INTEGER PRIMARY KEY REFERENCES skills(id),code TEXT NOT NULL REFERENCES holding_competencies(code));'''
def ensure_links(db,allow_new=True):
 for s in db.execute('SELECT s.* FROM skills s LEFT JOIN competency_links l ON l.skill_id=s.id WHERE l.skill_id IS NULL ORDER BY s.id').fetchall():
  code=s['code'];bank=db.execute('SELECT * FROM holding_competencies WHERE code=?',(code,)).fetchone()
  if bank and bank['title'].strip()!=s['title'].strip():
   # Same old code with a different meaning must never be silently merged.
   code=f"LEGACY-{s['company_id']}-{s['id']}";bank=db.execute('SELECT * FROM holding_competencies WHERE code=?',(code,)).fetchone()
  if not bank:
   if not allow_new:raise ValueError('شایستگی جدید باید ابتدا توسط مدیر هلدینگ در بانک مشترک تعریف شود')
   db.execute('INSERT INTO holding_competencies(code,title,category,importance,reference) VALUES (?,?,?,?,?)',(code,s['title'],s['category'],s['importance'],('انتقال از کد قبلی '+s['code']) if code!=s['code'] else ''))
  db.execute('INSERT INTO competency_links VALUES (?,?)',(s['id'],code))
def rows(db,cid):
 return [dict(r) for r in db.execute('SELECT s.id,s.company_id,b.code,b.title,b.category,b.importance,s.code legacy_code FROM skills s JOIN competency_links l ON l.skill_id=s.id JOIN holding_competencies b ON b.code=l.code WHERE s.company_id=? ORDER BY b.category,b.code',(cid,))]
def attach(db,cid,code):
 b=db.execute('SELECT * FROM holding_competencies WHERE code=?',(code,)).fetchone()
 if not b:raise ValueError('شایستگی در بانک هلدینگ یافت نشد')
 existing=db.execute('SELECT s.id FROM skills s JOIN competency_links l ON l.skill_id=s.id WHERE s.company_id=? AND l.code=?',(cid,code)).fetchone()
 if existing:return existing['id']
 # Avoid collision with an unrelated preserved legacy skill code.
 local=code
 if db.execute('SELECT 1 FROM skills WHERE company_id=? AND code=?',(cid,local)).fetchone():local='BANK-'+code
 cur=db.execute('INSERT INTO skills(company_id,code,title,category,importance) VALUES (?,?,?,?,?)',(cid,local,b['title'],b['category'],b['importance']))
 db.execute('INSERT INTO competency_links VALUES (?,?)',(cur.lastrowid,code));return cur.lastrowid

def register(app,db,authenticated,company,audit,error,validate_refs,gaps):
 from flask import request,jsonify,g
 with app.app_context():
  db().executescript(SCHEMA);ensure_links(db());db().commit()
 @app.get('/api/search')
 @authenticated
 def search():
  cid=company();q=request.args.get('q','').strip()[:100]
  if not q:return jsonify([])
  q=q.replace('ي','ی').replace('ك','ک').replace('\u200c',' ')
  q=q.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹','0123456789'))
  term='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%';result=[]
  for table,kind,title,subtitle,columns in [('holding_competencies','bank','title','category',['code','title','category']),('employees','person','name','code',['name','code','job','department']),('job_profiles','job','job','department',['job','department']),('courses','course','title','date',['title','instructor','date'])]:
   expressions=[f"replace(replace(replace({c},'ي','ی'),'ك','ک'),char(8204),' ') LIKE ? ESCAPE '\\'" for c in columns]
   scope='' if table=='holding_competencies' else 'company_id=? AND '
   params=([cid] if scope else [])+[term]*len(columns)
   for row in db().execute(f"SELECT * FROM {table} WHERE {scope}({' OR '.join(expressions)}) LIMIT 8",params):
    result.append({'kind':kind,'id':row['code'] if kind=='bank' else row['id'],'title':row[title],'subtitle':row[subtitle]})
  return jsonify(result)
 @app.put('/api/competency-bank/<code>')
 @authenticated
 def edit_bank(code):
  if g.user['role']!='admin':raise PermissionError()
  old=db().execute('SELECT * FROM holding_competencies WHERE code=?',(code,)).fetchone()
  if not old:return error('شایستگی یافت نشد',404)
  d={k:v for k,v in (request.get_json() or {}).items() if k in ['title','category','importance','method','reference']}
  if not d or ('title' in d and not str(d['title']).strip()):raise ValueError('عنوان الزامی است')
  if 'importance' in d and int(d['importance']) not in (1,2,3):raise ValueError('اهمیت نامعتبر است')
  db().execute(f"UPDATE holding_competencies SET {','.join(k+'=?' for k in d)} WHERE code=?",list(d.values())+[code])
  # Legacy columns remain readable to old reports, but have one authoritative source.
  db().execute('UPDATE skills SET title=(SELECT title FROM holding_competencies WHERE code=?),category=(SELECT category FROM holding_competencies WHERE code=?),importance=(SELECT importance FROM holding_competencies WHERE code=?) WHERE id IN (SELECT skill_id FROM competency_links WHERE code=?)',(code,code,code,code))
  audit('update_holding_competency',detail=code);db().commit();return jsonify(ok=True)
 @app.route('/api/job-workspace',methods=['GET','PUT'])
 @authenticated
 def job_workspace():
  cid=company();job=request.args.get('job','').strip()
  if not job:raise ValueError('شغل را انتخاب کنید')
  if request.method=='PUT':
   if g.user['role']=='viewer':raise PermissionError()
   items=(request.get_json() or {}).get('items',[])
   if not isinstance(items,list) or len(items)>2000:raise ValueError('تعداد اقلام نامعتبر است')
   validated=[];seen=set()
   for item in items:
    if not isinstance(item,dict) or not isinstance(item.get('code'),str):raise ValueError('قالب اقلام نامعتبر است')
    code=item.get('code');level=item.get('level')
    if not isinstance(level,int) or isinstance(level,bool) or not 0<=level<=4 or code in seen:raise ValueError('سطح یا شایستگی تکراری نامعتبر است')
    if not db().execute('SELECT 1 FROM holding_competencies WHERE code=?',(code,)).fetchone():raise ValueError('شایستگی یافت نشد')
    seen.add(code);validated.append((code,level))
   for code,level in validated:
    sid=attach(db(),cid,code)
    if level:db().execute('INSERT INTO requirements(company_id,job,skill_id,level) VALUES (?,?,?,?) ON CONFLICT(company_id,job,skill_id) DO UPDATE SET level=excluded.level',(cid,job,sid,level))
    else:db().execute('DELETE FROM requirements WHERE company_id=? AND job=? AND skill_id=?',(cid,job,sid))
   audit('batch_job_requirements',cid,job);db().commit()
  profile=db().execute('SELECT * FROM job_profiles WHERE company_id=? AND job=?',(cid,job)).fetchone()
  matrix=[dict(r) for r in db().execute('SELECT b.*,s.id skill_id,r.id requirement_id,COALESCE(r.level,0) level FROM holding_competencies b LEFT JOIN competency_links l ON l.code=b.code AND l.skill_id IN (SELECT id FROM skills WHERE company_id=?) LEFT JOIN skills s ON s.id=l.skill_id LEFT JOIN requirements r ON r.skill_id=s.id AND r.company_id=? AND r.job=? ORDER BY b.category,b.code',(cid,cid,job))]
  return jsonify(profile=dict(profile) if profile else None,matrix=matrix)
 @app.get('/api/job-summary')
 @authenticated
 def job_summary():
  cid=company();names=set(r[0] for r in db().execute('SELECT job FROM job_profiles WHERE company_id=? UNION SELECT job FROM requirements WHERE company_id=? UNION SELECT job FROM employees WHERE company_id=? AND job!=?',(cid,cid,cid,'')))
  result=[]
  for name in sorted(names):
   p=db().execute('SELECT * FROM job_profiles WHERE company_id=? AND job=?',(cid,name)).fetchone()
   result.append({'job':name,'department':p['department'] if p else '', 'profile_id':p['id'] if p else None,'employees':db().execute('SELECT count(*) FROM employees WHERE company_id=? AND job=?',(cid,name)).fetchone()[0],'skills':db().execute('SELECT count(*) FROM requirements WHERE company_id=? AND job=?',(cid,name)).fetchone()[0]})
  return jsonify(result)
 @app.route('/api/assessment-workspace/<int:eid>',methods=['GET','PUT'])
 @authenticated
 def assessment_workspace(eid):
  cid=company();e=db().execute('SELECT * FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
  if not e:return error('فرد یافت نشد',404)
  if request.method=='PUT':
   if g.user['role']=='viewer':raise PermissionError()
   items=(request.get_json() or {}).get('items',[])
   if not isinstance(items,list) or len(items)>2000:raise ValueError('تعداد اقلام نامعتبر است')
   checked=[];seen=set()
   for item in items:
    if not isinstance(item,dict) or not isinstance(item.get('code'),str):raise ValueError('قالب اقلام نامعتبر است')
    code=item.get('code');level=item.get('level')
    if code in seen or not isinstance(level,int) or isinstance(level,bool) or not 0<=level<=4:raise ValueError('سطح یا شایستگی تکراری نامعتبر است')
    if not db().execute('SELECT 1 FROM holding_competencies WHERE code=?',(code,)).fetchone():raise ValueError('شایستگی یافت نشد')
    seen.add(code);checked.append((code,level))
   for code,level in checked:
    sid=attach(db(),cid,code);db().execute('INSERT INTO assessments(company_id,employee_id,skill_id,level) VALUES (?,?,?,?) ON CONFLICT(company_id,employee_id,skill_id) DO UPDATE SET level=excluded.level',(cid,eid,sid,level))
   audit('batch_employee_assessment',cid,str(eid));db().commit()
  matrix=[dict(r) for r in db().execute('SELECT b.*,s.id skill_id,COALESCE(r.level,0) required,COALESCE(a.level,0) current FROM holding_competencies b LEFT JOIN competency_links l ON l.code=b.code AND l.skill_id IN (SELECT id FROM skills WHERE company_id=?) LEFT JOIN skills s ON s.id=l.skill_id LEFT JOIN requirements r ON r.skill_id=s.id AND r.company_id=? AND r.job=? LEFT JOIN assessments a ON a.skill_id=s.id AND a.employee_id=? AND a.company_id=? ORDER BY b.category,b.code',(cid,cid,e['job'],eid,cid))]
  return jsonify(employee=dict(e),matrix=matrix)
 @app.get('/api/needs-summary')
 @authenticated
 def needs_summary():
  cid=company();data={};catalog={r['id']:r for r in rows(db(),cid)}
  for r in gaps(cid):
   c=catalog[r['skill_id']];d=data.setdefault(c['code'],{'code':c['code'],'title':c['title'],'category':c['category'],'importance':c['importance'],'employees':[],'gap_total':0})
   d['employees'].append(r);d['gap_total']+=r['gap']
  return jsonify(sorted([dict(r,people=len(r['employees'])) for r in data.values()],key=lambda r:(-r['importance'],-r['people'],r['code'])))
