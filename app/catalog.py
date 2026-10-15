"""Canonical holding competencies; legacy company IDs remain stable links."""
SCHEMA='''CREATE TABLE IF NOT EXISTS competency_links(skill_id INTEGER PRIMARY KEY REFERENCES skills(id),code TEXT NOT NULL REFERENCES holding_competencies(code));'''
def bank_values(data,creating=False):
 """Validate both catalog writers without coercing malformed values into records."""
 if not isinstance(data,dict):raise ValueError('اطلاعات شایستگی معتبر نیست')
 fields=['title','category','importance','method','reference']
 if creating:fields.insert(0,'code')
 result={k:v for k,v in data.items() if k in fields}
 required=['code','title'] if creating else []
 for field in fields:
  if field=='importance' or field not in result:continue
  value=result[field]
  if not isinstance(value,str):raise ValueError('اطلاعات متنی شایستگی معتبر نیست')
  result[field]=value.strip()
 if any(not result.get(k) for k in required) or ('title' in result and not result['title']):raise ValueError('کد و عنوان شایستگی الزامی است')
 if creating and any(c in result['code'] for c in '/\\\r\n\t'):raise ValueError('کد شایستگی معتبر نیست')
 if 'importance' in result:
  value=result['importance']
  try:
   number=int(value)
   if isinstance(value,bool) or number not in (1,2,3) or (isinstance(value,float) and value!=number):raise ValueError()
  except (ValueError,TypeError,OverflowError):raise ValueError('اهمیت باید ۱، ۲ یا ۳ باشد')
  result['importance']=number
 if not result:raise ValueError('تغییری ارسال نشده است')
 return result

def bank_in_use(conn,code):
 """Check every declared reference, including references with cascading deletes."""
 # Company selections alone can be removed; requirements and history cannot.
 quote=lambda name:'"'+name.replace('"','""')+'"'
 for table in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
  name=table[0]
  if name=='competency_links':continue
  for ref in conn.execute('PRAGMA foreign_key_list('+quote(name)+')').fetchall():
   target,field=ref[2],ref[3]
   if target=='skills':
    used=conn.execute('SELECT 1 FROM '+quote(name)+' WHERE '+quote(field)+' IN (SELECT skill_id FROM competency_links WHERE code=?) LIMIT 1',(code,)).fetchone()
   elif target=='holding_competencies':
    used=conn.execute('SELECT 1 FROM '+quote(name)+' WHERE '+quote(field)+'=? LIMIT 1',(code,)).fetchone()
   else:continue
   if used:return True
 return False

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
  data=request.get_json() or {}
  if isinstance(data,dict) and 'code' in data and data['code']!=code:raise ValueError('کد شایستگی مشترک قابل تغییر نیست')
  d=bank_values(data)
  db().execute(f"UPDATE holding_competencies SET {','.join(k+'=?' for k in d)} WHERE code=?",list(d.values())+[code])
  # Legacy columns remain readable to old reports, but have one authoritative source.
  db().execute('UPDATE skills SET title=(SELECT title FROM holding_competencies WHERE code=?),category=(SELECT category FROM holding_competencies WHERE code=?),importance=(SELECT importance FROM holding_competencies WHERE code=?) WHERE id IN (SELECT skill_id FROM competency_links WHERE code=?)',(code,code,code,code))
  audit('update_holding_competency',detail=code);db().commit();return jsonify(ok=True)
 @app.delete('/api/competency-bank/<code>')
 @authenticated
 def delete_bank(code):
  if g.user['role']!='admin':raise PermissionError()
  conn=db()
  # Acquire the write lock before checking references so use cannot race deletion.
  with conn:
   conn.execute('BEGIN IMMEDIATE')
   if not conn.execute('SELECT 1 FROM holding_competencies WHERE code=?',(code,)).fetchone():return error('شایستگی یافت نشد',404)
   if bank_in_use(conn,code):return error('این شایستگی در الزامات، ارزیابی‌ها، دوره‌ها یا سوابق شرکت‌ها استفاده شده و قابل حذف نیست؛ برای حفظ سوابق، عنوان و مشخصات آن را ویرایش کنید.',409)
   ids=[r[0] for r in conn.execute('SELECT skill_id FROM competency_links WHERE code=?',(code,))]
   conn.execute('DELETE FROM competency_links WHERE code=?',(code,))
   conn.executemany('DELETE FROM skills WHERE id=?',[(sid,) for sid in ids])
   conn.execute('DELETE FROM holding_competencies WHERE code=?',(code,))
   audit('delete_holding_competency',detail=code)
  return jsonify(ok=True)
 @app.post('/api/competency-bank/<code>/select')
 @authenticated
 def select_bank(code):
  cid=company()
  if g.user['role']=='viewer':raise PermissionError()
  if not db().execute('SELECT 1 FROM holding_competencies WHERE code=?',(code,)).fetchone():return error('شایستگی یافت نشد',404)
  sid=attach(db(),cid,code)
  audit('select_holding_competency',cid,code);db().commit();return jsonify(ok=True,id=sid)
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
  matrix=[dict(r) for r in db().execute('SELECT b.*,s.id skill_id,COALESCE(p.level,r.level,0) required,COALESCE(a.level,0) current FROM holding_competencies b LEFT JOIN competency_links l ON l.code=b.code AND l.skill_id IN (SELECT id FROM skills WHERE company_id=?) LEFT JOIN skills s ON s.id=l.skill_id LEFT JOIN requirements r ON r.skill_id=s.id AND r.company_id=? AND r.job=? LEFT JOIN personal_requirements p ON p.skill_id=s.id AND p.employee_id=? LEFT JOIN assessments a ON a.skill_id=s.id AND a.employee_id=? AND a.company_id=? ORDER BY b.category,b.code',(cid,cid,e['job'],eid,eid,cid))]
  return jsonify(employee=dict(e),matrix=matrix)
 @app.get('/api/needs-summary')
 @authenticated
 def needs_summary():
  cid=company();data={};catalog={r['id']:r for r in rows(db(),cid)}
  for r in gaps(cid):
   c=catalog[r['skill_id']];d=data.setdefault(c['code'],{'code':c['code'],'title':c['title'],'category':c['category'],'importance':c['importance'],'employees':[],'gap_total':0})
   d['employees'].append(r);d['gap_total']+=r['gap']
  return jsonify(sorted([dict(r,people=len(r['employees'])) for r in data.values()],key=lambda r:(-r['importance'],-r['people'],r['code'])))
