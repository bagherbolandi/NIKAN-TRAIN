import os, sqlite3, secrets, time, json, io, csv, re, math
from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, session, send_from_directory, g, Response
from werkzeug.security import generate_password_hash, check_password_hash

COMPANIES=['نور ایستا پلاستیک','نور ایستا خراسان','برنا کامپوزیت ایرانیان','پایا پلاست ایرانیان','پلیمری تدبیر نیکان','ایمن ایستا الکتریک']
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
FIELDS={'employees':['code','name','department','job'],'skills':['code','title','category','importance'],'requirements':['job','skill_id','level'],'assessments':['employee_id','skill_id','level'],'courses':['skill_id','title','date','instructor','hours','cost','status'],'records':['employee_id','course_id','level','score','reaction','behavior','result'],'job_profiles':['job','department','purpose','supervisor','duties','authority','education','experience','legal','hse','equipment','revision','approved_by','definition','commitments','conditions','performance','contract_notes','effective_date','review_date'],'instructors':['name','specialty','organization','qualification','rate','contact'],'needs':['skill_id','department','reason','priority','year','status','decision']}
REQUIRED={'employees':['code','name'],'skills':['code','title'],'requirements':['job','skill_id','level'],'assessments':['employee_id','skill_id','level'],'courses':['skill_id','title','date','hours'],'records':['employee_id','course_id','level'],'job_profiles':['job'],'instructors':['name'],'needs':['skill_id','reason','year']}

def create_app(data_dir=None,testing=False):
 app=Flask(__name__,static_folder='static'); app.config.update(TESTING=testing,MAX_CONTENT_LENGTH=16*1024*1024,SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Strict',PERMANENT_SESSION_LIFETIME=1800)
 root=Path(data_dir or os.environ.get('NIKAN_DATA_DIR') or (Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'NikanTraining'));root.mkdir(parents=True,exist_ok=True)
 key=root/'session.key'
 if not key.exists():
  with key.open('x') as f:f.write(secrets.token_hex(32))
  key.chmod(0o600)
 app.secret_key=key.read_text(); app.config['DB']=str(root/'nikan.sqlite3')
 def db():
  if 'db' not in g:
   g.db=sqlite3.connect(app.config['DB'],timeout=15);g.db.row_factory=sqlite3.Row;g.db.execute('PRAGMA foreign_keys=ON');g.db.execute('PRAGMA journal_mode=WAL')
  return g.db
 @app.teardown_appcontext
 def close(_):
  c=g.pop('db',None)
  if c:c.close()
 with app.app_context():
  db().executescript(SCHEMA)
  db().executemany('INSERT OR IGNORE INTO companies(name) VALUES (?)',[(n,) for n in COMPANIES]);db().commit()
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
   if not u:return error('ابتدا وارد شوید',401)
   g.user=u
   if request.method!='GET' and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''),session.get('csrf','!')):return error('درخواست نامعتبر',403)
   return f(*args,**kwargs)
  return inner
 def company():
  cid=request.args.get('company_id',type=int)
  if not cid or not db().execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone():raise ValueError('شرکت را انتخاب کنید')
  if g.user['role']!='admin' and g.user['company_id']!=cid:raise PermissionError()
  return cid
 @app.errorhandler(ValueError)
 def invalid(e):return error(str(e))
 @app.errorhandler(PermissionError)
 def denied(e):return error('دسترسی به این شرکت مجاز نیست',403)
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
  if db().execute('SELECT 1 FROM users').fetchone():return error('راه‌اندازی قبلاً انجام شده است',403)
  if request.remote_addr not in ('127.0.0.1','::1'):return error('راه‌اندازی فقط روی رایانه میزبان مجاز است',403)
  d=request.get_json() or {};u=str(d.get('username','')).strip();p=d.get('password','')
  if len(u)<3 or len(p)<12:return error('نام کاربری حداقل ۳ و رمز عبور حداقل ۱۲ نویسه باشد')
  db().execute("INSERT INTO users(username,password,role) VALUES (?,?,'admin')",(u,generate_password_hash(p)));db().commit();return jsonify(ok=True)
 @app.post('/api/login')
 def login():
  d=request.get_json() or {};name=str(d.get('username',''))[:100];a=db().execute('SELECT * FROM login_attempts WHERE username=?',(name,)).fetchone()
  if a and a['until']>time.time() and a['count']>=5:return error('تلاش‌های زیاد؛ ۱۵ دقیقه بعد دوباره امتحان کنید',429)
  u=db().execute('SELECT * FROM users WHERE username=? AND active=1',(name,)).fetchone()
  if not u or not check_password_hash(u['password'],d.get('password','')):
   count=(a['count'] if a and a['until']>time.time() else 0)+1
   db().execute('INSERT OR REPLACE INTO login_attempts VALUES (?,?,?)',(name,count,time.time()+900));db().commit();return error('نام کاربری یا رمز عبور نادرست است',401)
  session.clear();session['uid']=u['id'];session['csrf']=secrets.token_hex(32);session.permanent=True
  db().execute('DELETE FROM login_attempts WHERE username=?',(name,));audit('login');db().commit();return jsonify(ok=True)
 @app.get('/api/me')
 @authenticated
 def me():
  cs=db().execute('SELECT * FROM companies'+(' WHERE id=?' if g.user['role']!='admin' else ''),((g.user['company_id'],) if g.user['role']!='admin' else ())).fetchall()
  return jsonify(username=g.user['username'],role=g.user['role'],csrf=session['csrf'],desktop=bool(app.config.get('DESKTOP_STOP')),network=bool(app.config.get('NETWORK')),companies=[dict(c) for c in cs])
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
  d=request.get_json() or {}
  if d.get('role') not in ('manager','viewer') or len(d.get('password',''))<12 or len(d.get('username',''))<3:return error('نقش یا رمز عبور نامعتبر است')
  cid=int(d.get('company_id',0))
  db().execute('INSERT INTO users(username,password,role,company_id) VALUES (?,?,?,?)',(d['username'],generate_password_hash(d['password']),d['role'],cid));audit('create_user',cid,d['username']);db().commit();return jsonify(ok=True),201
 @app.put('/api/users/<int:uid>')
 @authenticated
 def update_user(uid):
  if g.user['role']!='admin':raise PermissionError()
  target=db().execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
  if not target:return error('کاربر یافت نشد',404)
  d=request.get_json() or {}
  if 'active' in d:
   if uid==g.user['id']:raise ValueError('حساب خود را نمی‌توانید غیرفعال کنید')
   if d['active'] not in (0,1):raise ValueError('وضعیت نامعتبر است')
   db().execute('UPDATE users SET active=? WHERE id=?',(d['active'],uid))
  if 'password' in d:
   if len(d['password'])<12:raise ValueError('رمز عبور حداقل ۱۲ نویسه باشد')
   db().execute('UPDATE users SET password=? WHERE id=?',(generate_password_hash(d['password']),uid))
  audit('update_user',target['company_id'],str(uid));db().commit();return jsonify(ok=True)
 def validate_refs(table,d,cid):
  for field in ('level','importance','hours','cost','score','reaction','behavior','result','rate','priority','year'):
   if field in d and d[field] is not None:
    try:
     value=float(d[field])
     if not math.isfinite(value):raise ValueError()
    except (ValueError,TypeError):raise ValueError('مقدار عددی نامعتبر است')
    if field in ('level','importance','priority','year') and value != int(value):raise ValueError('سطح و اهمیت باید عدد صحیح باشند')
  if 'date' in d:
   d['date']=str(d['date']).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789'))
   if not re.fullmatch(r'1[34]\d{2}/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])',d['date']):raise ValueError('تاریخ شمسی با قالب 1405/03/25 وارد شود')
   y,m,day=map(int,d['date'].split('/'))
   if day>(31 if m<=6 else 30):raise ValueError('روز برای ماه انتخاب‌شده نامعتبر است')
  for field,target in [('employee_id','employees'),('skill_id','skills'),('course_id','courses')]:
   if field in d and not db().execute(f'SELECT 1 FROM {target} WHERE id=? AND company_id=?',(d[field],cid)).fetchone():raise ValueError('رکورد مرتبط متعلق به این شرکت نیست')
 @app.route('/api/data/<table>',methods=['GET','POST'])
 @authenticated
 def data(table):
  if table not in FIELDS:return error('بخش یافت نشد',404)
  cid=company()
  if table=='skills' and request.method=='GET':
   from .catalog import rows
   return jsonify(rows(db(),cid))
  if table=='skills' and g.user['role']!='admin':raise PermissionError()
  if request.method=='GET':return jsonify([dict(r) for r in db().execute(f'SELECT * FROM {table} WHERE company_id=? ORDER BY id DESC',(cid,))])
  if g.user['role']=='viewer':raise PermissionError()
  d=request.get_json() or {};d={k:v for k,v in d.items() if k in FIELDS[table]}
  if any(d.get(k) in (None,'') for k in REQUIRED[table]):return error('فیلدهای الزامی را تکمیل کنید')
  validate_refs(table,d,cid)
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
  if table=='skills':return error('شایستگی مشترک را از بانک هلدینگ ویرایش کنید',400)
  if g.user['role']=='viewer':raise PermissionError()
  if not db().execute(f'SELECT 1 FROM {table} WHERE id=? AND company_id=?',(rid,cid)).fetchone():return error('رکورد یافت نشد',404)
  if request.method=='DELETE':db().execute(f'DELETE FROM {table} WHERE id=? AND company_id=?',(rid,cid))
  else:
   d={k:v for k,v in (request.get_json() or {}).items() if k in FIELDS[table]}
   if not d:raise ValueError('تغییری ارسال نشده')
   if any(k in d and d[k] in ('',None) for k in REQUIRED[table]):raise ValueError('فیلد الزامی خالی است')
   validate_refs(table,d,cid);db().execute(f"UPDATE {table} SET {','.join(k+'=?' for k in d)} WHERE id=? AND company_id=?",list(d.values())+[rid,cid])
  audit(request.method+'_'+table,cid,str(rid));db().commit();return jsonify(ok=True)
 def gaps(cid):
  rows=db().execute('''SELECT e.id employee_id,e.code,e.name,e.department,e.job,s.id skill_id,b.title,b.importance,COALESCE(p.level,r.level,0) required,MAX(COALESCE(a.level,0),COALESCE((SELECT MAX(t.level) FROM records t JOIN courses c ON c.id=t.course_id WHERE t.employee_id=e.id AND c.skill_id=s.id AND c.status='completed' AND t.company_id=e.company_id),0)) current FROM employees e JOIN skills s ON s.company_id=e.company_id LEFT JOIN requirements r ON r.company_id=e.company_id AND r.job=e.job AND r.skill_id=s.id LEFT JOIN personal_requirements p ON p.employee_id=e.id AND p.skill_id=s.id JOIN competency_links l ON l.skill_id=s.id JOIN holding_competencies b ON b.code=l.code LEFT JOIN assessments a ON a.employee_id=e.id AND a.skill_id=s.id AND a.company_id=e.company_id WHERE e.company_id=?''',(cid,)).fetchall()
  return [dict(r,gap=r['required']-r['current']) for r in rows if r['required']>r['current']]
 @app.get('/api/gaps')
 @authenticated
 def gap_api():return jsonify(gaps(company()))
 @app.get('/api/dashboard')
 @authenticated
 def dashboard():
  cid=company();counts={t:db().execute(f'SELECT count(*) FROM {t} WHERE company_id=?',(cid,)).fetchone()[0] for t in ['employees','skills','courses','records']}
  counts['gaps']=len(gaps(cid));counts['budget']=db().execute('SELECT COALESCE(SUM(cost),0) FROM courses WHERE company_id=? AND status!=?',(cid,'cancelled')).fetchone()[0]
  counts['companies']=[dict(r) for r in db().execute('SELECT c.id,c.name,(SELECT count(*) FROM employees e WHERE e.company_id=c.id) employees FROM companies c WHERE c.id=? OR ?=1',(cid,int(g.user['role']=='admin')))];return jsonify(counts)
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
 return app
