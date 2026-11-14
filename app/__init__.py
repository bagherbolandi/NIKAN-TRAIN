import os, sqlite3, secrets, time, json, io, csv
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory, g, Response
from werkzeug.security import generate_password_hash, check_password_hash
from .validation import json_object, positive_id, number, jalali_date

COMPANIES=['نور ایستا پلاستیک','نور ایستا خراسان','برنا کامپوزیت ایرانیان','پایا پلاست ایرانیان','پلیمری تدبیر نیکان','ایمن ایستا الکتریک','موج نیکان']
SCHEMA='''
CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY,name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,username TEXT UNIQUE NOT NULL,password TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('admin','manager','viewer')),company_id INTEGER REFERENCES companies(id),active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),code TEXT NOT NULL,name TEXT NOT NULL,department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',UNIQUE(company_id,code));
CREATE TABLE IF NOT EXISTS skills(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),code TEXT NOT NULL,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',importance INTEGER NOT NULL DEFAULT 1 CHECK(importance BETWEEN 1 AND 3),UNIQUE(company_id,code));
CREATE TABLE IF NOT EXISTS requirements(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),job TEXT NOT NULL,skill_id INTEGER NOT NULL REFERENCES skills(id),level INTEGER NOT NULL CHECK(level BETWEEN 1 AND 4),UNIQUE(company_id,job,skill_id));
CREATE TABLE IF NOT EXISTS assessments(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),employee_id INTEGER NOT NULL REFERENCES employees(id),skill_id INTEGER NOT NULL REFERENCES skills(id),level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 4),UNIQUE(company_id,employee_id,skill_id));
CREATE TABLE IF NOT EXISTS courses(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),skill_id INTEGER NOT NULL REFERENCES skills(id),title TEXT NOT NULL,date TEXT NOT NULL,instructor TEXT NOT NULL DEFAULT '',hours REAL NOT NULL CHECK(hours>0),cost REAL NOT NULL DEFAULT 0 CHECK(cost>=0),status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','approved','completed','cancelled')));
CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),employee_id INTEGER NOT NULL REFERENCES employees(id),course_id INTEGER NOT NULL REFERENCES courses(id),level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 4),score REAL CHECK(score BETWEEN 0 AND 100),reaction REAL CHECK(reaction BETWEEN 1 AND 5),behavior REAL CHECK(behavior BETWEEN 1 AND 5),result REAL CHECK(result BETWEEN 0 AND 100),UNIQUE(company_id,employee_id,course_id));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,user_id INTEGER,company_id INTEGER,action TEXT NOT NULL,detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(username TEXT PRIMARY KEY,count INTEGER NOT NULL,until REAL NOT NULL);
'''
FIELDS={'employees':['code','name','department','job'],'skills':['code','title','category','importance'],'requirements':['job','skill_id','level'],'assessments':['employee_id','skill_id','level'],'courses':['skill_id','title','date','instructor','hours','cost','status','calendar_scope'],'records':['employee_id','course_id','level','score','reaction','behavior','result'],'job_profiles':['job','department','purpose','supervisor','duties','authority','education','experience','legal','hse','equipment','revision','approved_by','definition','commitments','conditions','performance','contract_notes','effective_date','review_date'],'instructors':['name','specialty','organization','qualification','rate','contact'],'needs':['skill_id','department','reason','priority','year','status','decision']}
REQUIRED={'employees':['code','name'],'skills':['code','title'],'requirements':['job','skill_id','level'],'assessments':['employee_id','skill_id','level'],'courses':['skill_id','title','date','hours'],'records':['employee_id','course_id','level'],'job_profiles':['job'],'instructors':['name'],'needs':['skill_id','reason','year']}

def create_app(data_dir=None,testing=False):
 app=Flask(__name__,static_folder='static'); app.config.update(TESTING=testing,MAX_CONTENT_LENGTH=16*1024*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',PERMANENT_SESSION_LIFETIME=1800)
 from .runtime_errors import register as register_runtime_errors
 register_runtime_errors(app)
 root=Path(data_dir or os.environ.get('NIKAN_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'NikanTraining'));root.mkdir(parents=True,exist_ok=True)
 key=root/'session.key'
 if not key.exists():
  with key.open('x') as f:f.write(secrets.token_hex(32))
  key.chmod(0o600)
 app.secret_key=key.read_text(); app.config['DB']=str(root/'nikan.sqlite3')
 from .hrm_migrations import prepare_upgrade_backup
 app.extensions['hrm_upgrade_backup']=prepare_upgrade_backup(root)
 def db():
  if 'db' not in g:
   g.db=sqlite3.connect(app.config['DB'],timeout=15);g.db.row_factory=sqlite3.Row;g.db.execute('PRAGMA foreign_keys=ON');g.db.execute('PRAGMA journal_mode=WAL')
  return g.db
 @app.teardown_appcontext
 def close(_):
  c=g.pop('db',None)
  if c:c.close()
 with app.app_context():
  existing_companies=db().execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='companies'").fetchone() is not None
  db().executescript(SCHEMA)
  from .companies import initialize as initialize_companies
  initialize_companies(db(),COMPANIES,seed_defaults=not existing_companies)
 def error(message,status=400):return jsonify(error=message),status
 @app.before_request
 def same_origin():
  if request.method not in ('GET','HEAD','OPTIONS'):
   origin=request.headers.get('Origin')
   if origin and origin != request.host_url.rstrip('/'):
    return error('مبدأ درخواست مجاز نیست',403)
 def audit(action,cid=None,detail=''):
  db().execute('INSERT INTO audit(user_id,company_id,action,detail) VALUES (?,?,?,?)',(session.get('uid'),cid,action,detail))
 def authenticated(f):
  @wraps(f)
  def inner(*args,**kwargs):
   u=db().execute('SELECT * FROM users WHERE id=? AND active=1',(session.get('uid'),)).fetchone()
   if not u or session.get('auth_version',0)!=u['auth_version']:return error('ابتدا وارد شوید',401)
   g.user=u
   if request.method!='GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('csrf','!')):return error('درخواست نامعتبر',403)
   from .hrm_access import legacy_guard
   legacy_guard(db(),request.endpoint)
   return f(*args,**kwargs)
  return inner
 def company():
  cid=positive_id(request.args.get('company_id'))
  if not db().execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone():raise ValueError('شرکت را انتخاب کنید')
  if g.user['role']!='admin' and g.user['company_id']!=cid:raise PermissionError()
  return cid
 @app.errorhandler(ValueError)
 def invalid(e):return error(str(e))
 @app.errorhandler(PermissionError)
 def denied(e):return error('دسترسی به این عملیات یا شرکت مجاز نیست',403)
 @app.errorhandler(sqlite3.IntegrityError)
 def integrity(e):return error('اطلاعات تکراری یا نامعتبر است؛ وابستگی‌ها و محدوده مقادیر را بررسی کنید')
 @app.after_request
 def headers(r):
  r.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Cache-Control':'no-store','Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'"});return r
 @app.get('/')
 def index():return send_from_directory(app.static_folder,'index.html')
 @app.get('/api/status')
 def status():return jsonify(setup_required=db().execute('SELECT count(*) FROM users').fetchone()[0]==0)
 @app.post('/api/setup')
 def setup():
  if not app.config.get('ALLOW_SETUP',True):return error('راه‌اندازی فقط در حالت محلی سرور مجاز است',403)
  if db().execute('SELECT 1 FROM users').fetchone():return error('راه‌اندازی قبلاً انجام شده است',403)
  if request.remote_addr not in ('127.0.0.1','::1'):return error('راه‌اندازی فقط روی رایانه میزبان مجاز است',403)
  d=json_object();u=d.get('username','');p=d.get('password','')
  if not isinstance(u,str) or not isinstance(p,str):raise ValueError('نام کاربری و رمز عبور باید متنی باشند')
  u=u.strip()
  if len(u)<3 or len(p)<12:return error('نام کاربری حداقل ۳ و رمز عبور حداقل ۱۲ نویسه باشد')
  db().execute("INSERT INTO users(username,password,role) VALUES (?,?,'admin')",(u,generate_password_hash(p)));db().commit();return jsonify(ok=True)
 @app.post('/api/login')
 def login():
  d=json_object();name=d.get('username','');password=d.get('password','')
  if not isinstance(name,str) or not isinstance(password,str):raise ValueError('نام کاربری و رمز عبور باید متنی باشند')
  name=name[:100];a=db().execute('SELECT * FROM login_attempts WHERE username=?',(name,)).fetchone()
  if a and a['until']>time.time() and a['count']>=5:return error('تلاش‌های زیاد؛ ۱۵ دقیقه بعد دوباره امتحان کنید',429)
  u=db().execute('SELECT * FROM users WHERE username=? AND active=1',(name,)).fetchone()
  if not u or not check_password_hash(u['password'],password):
   count=(a['count'] if a and a['until']>time.time() else 0)+1
   db().execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)',(name,count,time.time()+900));db().commit();return error('نام کاربری یا رمز عبور نادرست است',401)
  session.clear();session['uid']=u['id'];session['auth_version']=u['auth_version'];session['csrf']=secrets.token_hex(32);session.permanent=True
  db().execute('DELETE FROM login_attempts WHERE username=?',(name,));audit('login');db().commit();return jsonify(ok=True)
 @app.get('/api/me')
 @authenticated
 def me():
  cs=db().execute('SELECT * FROM companies'+(' WHERE id=?' if g.user['role']!='admin' else ''),((g.user['company_id'],) if g.user['role']!='admin' else ())).fetchall()
  from .hrm_access import actor_summary
  hrm=actor_summary(db(),g.user)
  companies=[dict(c) for c in cs] if hrm['training_access'] else hrm['companies'] or hrm.get('configuration_companies',[])
  return jsonify(deployment_mode=app.config.get('DEPLOYMENT_MODE','desktop'),username=g.user['username'],role=g.user['role'],csrf=session['csrf'],desktop=bool(app.config.get('DESKTOP_STOP')),network=bool(app.config.get('NETWORK')),companies=companies,hrm=hrm)
 @app.post('/api/desktop-exit')
 @authenticated
 def desktop_exit():
  stop=app.config.get('DESKTOP_STOP')
  if not stop:return error('اجرا در حالت دسکتاپ نیست',400)
  import threading
  threading.Timer(.5,stop).start();return jsonify(ok=True)
 @app.post('/api/logout')
 @authenticated
 def logout():session.clear();return jsonify(ok=True)
 @app.route('/api/users',methods=['GET','POST'])
 @authenticated
 def users():
  if g.user['role']!='admin':raise PermissionError()
  if request.method=='GET':return jsonify([dict(r) for r in db().execute('SELECT id,username,role,company_id,active FROM users')])
  d=json_object()
  if not isinstance(d.get('password'),str) or not isinstance(d.get('username'),str):raise ValueError('نام کاربری و رمز عبور باید متنی باشند')
  d['username']=d['username'].strip()
  if d.get('role') not in ('manager','viewer') or len(d['password'])<12 or len(d['username'])<3:return error('نقش یا رمز عبور نامعتبر است')
  cid=positive_id(d.get('company_id'))
  if not db().execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone():raise ValueError('شرکت یافت نشد')
  db().execute('INSERT INTO users(username,password,role,company_id) VALUES (?,?,?,?)',(d['username'],generate_password_hash(d['password']),d['role'],cid));audit('create_user',cid,d['username']);db().commit();return jsonify(ok=True),201
 @app.put('/api/users/<int:uid>')
 @authenticated
 def update_user(uid):
  if g.user['role']!='admin':raise PermissionError()
  uid=positive_id(uid)
  target=db().execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
  if not target:return error('کاربر یافت نشد',404)
  d=json_object()
  if not any(k in d for k in ('active','password')):raise ValueError('تغییری ارسال نشده')
  if 'active' in d:
   if uid==g.user['id']:raise ValueError('حساب خود را نمی‌توانید غیرفعال کنید')
   if type(d['active']) is not int or d['active'] not in (0,1):raise ValueError('وضعیت نامعتبر است')
  if 'password' in d:
   if not isinstance(d['password'],str) or len(d['password'])<12:raise ValueError('رمز عبور حداقل ۱۲ نویسه باشد')
  if 'active' in d:db().execute('UPDATE users SET active=? WHERE id=?',(d['active'],uid))
  if 'password' in d:
   db().execute('UPDATE users SET password=?,auth_version=auth_version+1 WHERE id=?',(generate_password_hash(d['password']),uid))
  if 'password' in d:db().execute('DELETE FROM password_recovery WHERE user_id=?',(uid,))
  audit('update_user',target['company_id'],str(uid));db().commit();return jsonify(ok=True)
 def validate_refs(table,d,cid):
  if table=='needs':
   if d.get('status','submitted')!='submitted' or d.get('decision',''):
    raise ValueError('تصویب یا رد نیاز فقط در میز تصمیم‌گیری هلدینگ انجام می‌شود')
  if table=='employees' and 'code' in d and (not isinstance(d['code'],str) or len(d['code'])>128):
   raise ValueError('کد پرسنلی باید متن با حداکثر ۱۲۸ نویسه باشد')
  if table=='courses' and 'calendar_scope' in d and d['calendar_scope'] not in ('holding','internal'):
   raise ValueError('نوع تقویم باید هلدینگ یا داخلی شرکت باشد')
  numeric={'level':dict(integer=True,minimum=1 if table=='requirements' else 0,maximum=4),
           'importance':dict(integer=True,minimum=1,maximum=3),'hours':dict(positive=True),
           'cost':dict(minimum=0),'score':dict(minimum=0,maximum=100,nullable=True),
           'reaction':dict(minimum=1,maximum=5,nullable=True),'behavior':dict(minimum=1,maximum=5,nullable=True),
           'result':dict(minimum=0,maximum=100,nullable=True),'rate':dict(minimum=0),
           'priority':dict(integer=True,minimum=1,maximum=3),'year':dict(integer=True,positive=True)}
  for field,limits in numeric.items():
   if field in d:d[field]=number(d[field],**limits)
  references={'employee_id':'employees','skill_id':'skills','course_id':'courses'}
  for field in FIELDS.get(table,[]):
   if field in d and field not in numeric and field not in references:
    if not isinstance(d[field],str):raise ValueError('مقدار فیلد متنی معتبر نیست')
    if field in REQUIRED.get(table,[]) and not d[field].strip():raise ValueError('فیلد الزامی خالی است')
  if 'status' in d:
   choices=('planned','approved','completed','cancelled') if table=='courses' else ('submitted','approved','rejected')
   if d['status'] not in choices:raise ValueError('وضعیت نامعتبر است')
  if 'date' in d:
   d['date']=jalali_date(d['date'])
  for field,target in references.items():
   if field in d:
    d[field]=positive_id(d[field])
    if target=='employees':
     found=db().execute('SELECT 1 FROM employees e LEFT JOIN hrm_employee_details h ON h.employee_id=e.id WHERE e.id=? AND COALESCE(h.company_id,e.company_id)=?',(d[field],cid)).fetchone()
    else:found=db().execute(f'SELECT 1 FROM {target} WHERE id=? AND company_id=?',(d[field],cid)).fetchone()
    if not found:raise ValueError('رکورد مرتبط متعلق به این شرکت نیست')
 @app.route('/api/data/<table>',methods=['GET','POST'])
 @authenticated
 def data(table):
  if table not in FIELDS:return error('بخش یافت نشد',404)
  cid=company()
  if table=='skills' and request.method=='GET':
   from .catalog import rows
   return jsonify(rows(db(),cid))
  if table=='skills' and g.user['role']!='admin':raise PermissionError()
  if request.method=='GET':
   if table=='employees':
    from .personnel_identity import current_employee_rows
    return jsonify(current_employee_rows(db(),cid))
   source='hrm_live_'+table if table in ('assessments','records') else table
   return jsonify([dict(r) for r in db().execute(f'SELECT * FROM {source} WHERE company_id=? ORDER BY id DESC',(cid,))])
  if g.user['role']=='viewer':raise PermissionError()
  d={k:v for k,v in json_object().items() if k in FIELDS[table]}
  if table=='courses' and g.user['role']!='admin':
   if d.get('calendar_scope')=='holding':raise PermissionError()
   d.setdefault('calendar_scope','internal')
  if any(d.get(k) in (None,'') for k in REQUIRED[table]):return error('فیلدهای الزامی را تکمیل کنید')
  validate_refs(table,d,cid)
  if table=='employees':
   from .personnel_identity import write_legacy_employee
   rid,_=write_legacy_employee(db(),cid,d,policy='create')
   audit('create_employees',cid,str(rid));db().commit();return jsonify(id=rid),201
  if table=='skills':
   bank=db().execute('SELECT title FROM holding_competencies WHERE code=?',(d['code'],)).fetchone()
   if bank and bank['title'].strip()!=str(d['title']).strip():raise ValueError('این کد در بانک مشترک وجود دارد؛ عنوان آن را از بانک هلدینگ ویرایش کنید')
  keys=list(d);cols=','.join(['company_id']+keys);params=','.join('?' for _ in range(len(keys)+1))
  cur=db().execute(f'INSERT INTO {table} ({cols}) VALUES ({params})',[cid]+[d[k] for k in keys]);rid=cur.lastrowid
  if table=='skills':
   from .catalog import ensure_links
   ensure_links(db())
  audit('create_'+table,cid,str(rid));db().commit();return jsonify(id=rid),201
 @app.route('/api/data/<table>/<int:rid>',methods=['PUT','DELETE'])
 @authenticated
 def modify(table,rid):
  if table not in FIELDS:return error('بخش یافت نشد',404)
  cid=company()
  if table=='skills' and g.user['role']!='admin':raise PermissionError()
  if table=='skills':return error('شایستگی مشترک را از بانک هلدینگ ویرایش کنید',400)
  if g.user['role']=='viewer':raise PermissionError()
  rid=positive_id(rid)
  if table=='employees':
   from .personnel_identity import write_legacy_employee
   target=db().execute('SELECT e.id FROM employees e LEFT JOIN hrm_employee_details h ON h.employee_id=e.id WHERE e.id=? AND COALESCE(h.company_id,e.company_id)=?',(rid,cid)).fetchone()
   if not target:return error('رکورد یافت نشد',404)
   if request.method=='DELETE':
    if db().execute('SELECT 1 FROM hrm_employee_details WHERE employee_id=?',(rid,)).fetchone():
     return error('پرونده مرکزی دارای انتساب و تاریخچه حذف نمی‌شود؛ وضعیت همکاری را در پرونده پرسنلی ثبت کنید',409)
    db().execute('DELETE FROM employees WHERE id=?',(rid,))
   else:
    body=json_object();d={k:v for k,v in body.items() if k in FIELDS[table]}
    if not d:raise ValueError('تغییری ارسال نشده')
    validate_refs(table,d,cid)
    write_legacy_employee(db(),cid,d,employee_id=rid,version=body.get('version'))
   audit(request.method+'_employees',cid,str(rid));db().commit();return jsonify(ok=True)
  if table=='courses':db().execute('BEGIN IMMEDIATE')
  target=db().execute(f'SELECT * FROM {table} WHERE id=? AND company_id=?',(rid,cid)).fetchone()
  if not target:return error('رکورد یافت نشد',404)
  if table in ('assessments','records'):
   context_table='hrm_training_'+('assessment' if table=='assessments' else 'record')+'_context'
   key='assessment_id' if table=='assessments' else 'record_id'
   historical=db().execute(f'SELECT is_deleted FROM {context_table} WHERE {key}=?',(rid,)).fetchone()
   if historical:
    return error('برای اصلاح، ابطال یا بازیابی این سابقه از بخش مدیریت آموزش HRM استفاده کنید تا تاریخچه حفظ شود',409)
  if table=='needs' and target['status']!='submitted':
   return error('نیاز بررسی‌شده قفل است؛ ابتدا هلدینگ آن را برای اصلاح بازگشایی کند',409)
  if table=='courses' and g.user['role']!='admin' and target['calendar_scope']=='holding':raise PermissionError()
  if request.method=='DELETE':
   if table=='courses' and db().execute('SELECT 1 FROM course_participants WHERE course_id=? AND attended=1 LIMIT 1',(rid,)).fetchone():
    return error('این دوره حضور ثبت‌شده دارد؛ برای حفظ مبنای تسهیم هزینه، ابتدا حضور را صریحاً پاک کنید',409)
   db().execute(f'DELETE FROM {table} WHERE id=? AND company_id=?',(rid,cid))
  else:
   d={k:v for k,v in json_object().items() if k in FIELDS[table]}
   if not d:raise ValueError('تغییری ارسال نشده')
   if table=='courses' and g.user['role']!='admin' and d.get('calendar_scope')=='holding':raise PermissionError()
   if any(k in d and d[k] in ('',None) for k in REQUIRED[table]):raise ValueError('فیلد الزامی خالی است')
   validate_refs(table,d,cid)
   if table=='courses' and d.get('calendar_scope')=='internal' and db().execute('SELECT 1 FROM course_participants WHERE course_id=? LIMIT 1',(rid,)).fetchone():
    raise ValueError('این دوره فهرست برنامه‌ریزی مشترک دارد؛ ادمین هلدینگ باید ابتدا فهرست افراد را خالی کند')
   db().execute(f"UPDATE {table} SET {','.join(k+'=?' for k in d)} WHERE id=? AND company_id=?",list(d.values())+[rid,cid])
  audit(request.method+'_'+table,cid,str(rid));db().commit();return jsonify(ok=True)
 def gaps(cid):
  # The matrix owns level precedence and distinguishes unknown from zero.
  from .hrm_matrix import matrix_report
  report=matrix_report(db(),{'company_id':cid})
  skill_ids={r['code']:r['id'] for r in db().execute('SELECT s.id,l.code FROM skills s JOIN competency_links l ON l.skill_id=s.id WHERE s.company_id=?',(cid,))}
  result=[]
  for row in report['rows']:
   required=[value for value in (row.get('required_level'),row.get('job_required_level')) if value is not None and value>0]
   known=[value for value in (row.get('gap'),row.get('job_gap')) if value is not None]
   unknown=bool(required) and row.get('current_level') is None
   gap=max(known) if known else None
   if not unknown and (gap is None or gap<=0):continue
   result.append(dict(row,skill_id=skill_ids.get(row['competency_code']),code=row['employee_code'],name=row['employee_name'],title=row['competency_title'],
                      required=max(required) if required else None,current=row.get('current_level'),gap=gap,
                      requires_assessment=unknown,gap_basis='job_ideal_and_personal_requirement'))
  return result
 @app.get('/api/gaps')
 @authenticated
 def gap_api():return jsonify(gaps(company()))
 @app.get('/api/dashboard')
 @authenticated
 def dashboard():
  cid=company();counts={t:db().execute(f'SELECT count(*) FROM {t} WHERE company_id=?',(cid,)).fetchone()[0] for t in ['employees','skills','courses','records']}
  counts['gaps']=len(gaps(cid));counts['budget']=db().execute('SELECT COALESCE(SUM(cost),0) FROM courses WHERE company_id=? AND status!=?',(cid,'cancelled')).fetchone()[0]
  from .personnel_identity import current_employee_rows
  counts['employees']=len(current_employee_rows(db(),cid))
  counts['unknown_competencies']=sum(row['requires_assessment'] for row in gaps(cid))
  counts['companies']=[dict(r,employees=len(current_employee_rows(db(),r['id']))) for r in db().execute('SELECT id,name FROM companies WHERE id=? OR ?=1',(cid,int(g.user['role']=='admin')))];return jsonify(counts)
 @app.get('/api/audit')
 @authenticated
 def logs():
  cid=company();return jsonify([dict(r) for r in db().execute('SELECT a.*,u.username FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE a.company_id=? ORDER BY a.id DESC LIMIT 300',(cid,))])
 @app.get('/api/export')
 @authenticated
 def export():
  cid=company();out=io.StringIO();rows=gaps(cid);fields=['code','name','department','job','title','required','current','gap','importance'];writer=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');writer.writeheader()
  for row in rows:
   writer.writerow({k:("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in row.items()})
  return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=training-needs.csv'})
 @app.post('/api/backup')
 @authenticated
 def backup():
  if g.user['role']!='admin':raise PermissionError()
  if app.config.get('NETWORK'):
   manager=app.extensions['network_backup'];manager.background(manager.run);audit('encrypted_backup_requested');db().commit();return jsonify(ok=True),202
  temp=root/('backup-'+secrets.token_hex(8)+'.sqlite3')
  try:
   target=sqlite3.connect(temp)
   try:db().backup(target)
   finally:target.close()
   payload=temp.read_bytes()
  finally:temp.unlink(missing_ok=True)
  audit('backup');db().commit();return Response(payload,mimetype='application/octet-stream',headers={'Content-Disposition':'attachment; filename=nikan-backup.sqlite3'})
 @app.post('/api/import')
 @authenticated
 def import_excel():
  cid=company()
  if g.user['role']=='viewer':raise PermissionError()
  from .importer import import_workbook
  f=request.files.get('file')
  if not f:raise ValueError('فایل اکسل را انتخاب کنید')
  try:result=import_workbook(db(),f,cid,request.form.get('personnel')=='yes',g.user['role']=='admin')
  except (ValueError,sqlite3.IntegrityError):raise
  except Exception:raise ValueError('ساختار فایل اکسل معتبر نیست')
  from .catalog import ensure_links
  ensure_links(db(),g.user['role']=='admin')
  audit('import',cid,json.dumps(result));db().commit();return jsonify(result)
 def register_network(root,config):
  from network.web import register as register_network_web
  return register_network_web(app,db,authenticated,audit,error,root,config)
 app.extensions['register_network']=register_network
 from .professional import register
 register(app,db,authenticated,company,audit,error,validate_refs,gaps)
 from .catalog import register as register_catalog
 register_catalog(app,db,authenticated,company,audit,error,validate_refs,gaps)
 from .branding import register as register_branding
 register_branding(app,db,authenticated,audit,error)
 from .documents import register as register_documents
 register_documents(app,db,authenticated,company,audit,error,gaps)
 from .commitments import register as register_commitments
 register_commitments(app,db,authenticated,company,error)
 from .training_calendar import register as register_training_calendar
 register_training_calendar(app,db,authenticated,company)
 from .holding_planning import register as register_holding_planning
 register_holding_planning(app,db,authenticated,audit,error)
 from .companies import register as register_companies
 register_companies(app,db,authenticated,audit,error)
 from .excel_forms import register as register_excel_forms
 register_excel_forms(app,db,authenticated,company,audit,error,validate_refs)
 from .governance import register as register_governance
 register_governance(app,db,authenticated,company,audit,error,gaps)
 from .recovery import register as register_recovery
 register_recovery(app,db,error)
 from .hrm_common import HrmConflict
 @app.errorhandler(HrmConflict)
 def hrm_conflict(e):return error(str(e),409)
 from .hrm_access import register as register_hrm_access
 register_hrm_access(app,db,authenticated,audit,error)
 from .hrm_people import register as register_hrm_people
 register_hrm_people(app,db,authenticated,audit,error)
 from .hrm_workforce import register as register_hrm_workforce
 register_hrm_workforce(app,db,authenticated,audit,error)
 from .hrm_benefits import register as register_hrm_benefits
 register_hrm_benefits(app,db,authenticated,audit,error)
 from .hrm_hse import register as register_hrm_hse
 register_hrm_hse(app,db,authenticated,audit,error)
 from .hrm_training import register as register_hrm_training
 register_hrm_training(app,db,authenticated,audit,error)
 with app.app_context():
  db().executescript('''BEGIN;
 DROP VIEW IF EXISTS hrm_live_assessments;
 DROP VIEW IF EXISTS hrm_live_records;
 CREATE VIEW hrm_live_assessments AS
 SELECT a.* FROM assessments a LEFT JOIN hrm_training_assessment_context x ON x.assessment_id=a.id
 WHERE COALESCE(x.is_deleted,0)=0;
 CREATE VIEW hrm_live_records AS
 SELECT r.* FROM records r LEFT JOIN hrm_training_record_context x ON x.record_id=r.id
 WHERE COALESCE(x.is_deleted,0)=0;COMMIT;''')
 from .hrm_productivity import register as register_hrm_productivity
 register_hrm_productivity(app,db,authenticated,audit,error)
 from .hrm_matrix import register as register_hrm_matrix
 register_hrm_matrix(app,db,authenticated,audit,error)
 from .hrm_integration import register as register_hrm_integration
 register_hrm_integration(app,db,authenticated,audit,error)
 from .hrm_core import register as register_hrm_core
 register_hrm_core(app,db,authenticated,audit,error)
 from .hrm_management import register as register_hrm_management
 register_hrm_management(app,db,authenticated,audit,error)
 from .hrm_excel import register as register_hrm_excel
 register_hrm_excel(app,db,authenticated,audit,error)
 from .hrm_migrations import complete as complete_hrm_migration
 with app.app_context():complete_hrm_migration(db())
 return app
