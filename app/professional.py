"""Holding competency catalog, personnel passports and certificate repository."""
import io,zipfile
from pathlib import Path
from flask import request,jsonify,Response,session,g
SCHEMA='''
CREATE TABLE IF NOT EXISTS holding_competencies(code TEXT PRIMARY KEY,title TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',importance INTEGER NOT NULL DEFAULT 1,method TEXT NOT NULL DEFAULT '',reference TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS holding_catalog_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job_profiles(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),job TEXT NOT NULL,department TEXT NOT NULL DEFAULT '',purpose TEXT NOT NULL DEFAULT '',supervisor TEXT NOT NULL DEFAULT '',duties TEXT NOT NULL DEFAULT '',authority TEXT NOT NULL DEFAULT '',education TEXT NOT NULL DEFAULT '',experience TEXT NOT NULL DEFAULT '',legal TEXT NOT NULL DEFAULT '',hse TEXT NOT NULL DEFAULT '',equipment TEXT NOT NULL DEFAULT '',revision TEXT NOT NULL DEFAULT '',approved_by TEXT NOT NULL DEFAULT '',UNIQUE(company_id,job));
CREATE TABLE IF NOT EXISTS employee_meta(employee_id INTEGER PRIMARY KEY REFERENCES employees(id),company_id INTEGER NOT NULL REFERENCES companies(id),source_job TEXT NOT NULL DEFAULT '',mapping TEXT NOT NULL DEFAULT '',assessment_status TEXT NOT NULL DEFAULT '',assessor TEXT NOT NULL DEFAULT '',assessment_date TEXT NOT NULL DEFAULT '',evidence TEXT NOT NULL DEFAULT '',hr_approved INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS certificates(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),employee_id INTEGER NOT NULL REFERENCES employees(id),course_id INTEGER REFERENCES courses(id),title TEXT NOT NULL,issuer TEXT NOT NULL DEFAULT '',issued TEXT NOT NULL DEFAULT '',expires TEXT NOT NULL DEFAULT '',filename TEXT NOT NULL,mime TEXT NOT NULL,content BLOB NOT NULL,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS instructors(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),name TEXT NOT NULL,specialty TEXT NOT NULL DEFAULT '',organization TEXT NOT NULL DEFAULT '',qualification TEXT NOT NULL DEFAULT '',rate REAL NOT NULL DEFAULT 0 CHECK(rate>=0),contact TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS needs(id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),skill_id INTEGER NOT NULL REFERENCES skills(id),department TEXT NOT NULL DEFAULT '',reason TEXT NOT NULL,priority INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 1 AND 3),year INTEGER NOT NULL DEFAULT 1405,status TEXT NOT NULL DEFAULT 'submitted' CHECK(status IN ('submitted','approved','rejected')),decision TEXT NOT NULL DEFAULT '');
'''

def register(app,db,authenticated,company,audit,error,validate_refs,gaps):
 with app.app_context():
  db().executescript(SCHEMA)
  initialized=db().execute("SELECT 1 FROM holding_catalog_state WHERE key='initial_seed_complete'").fetchone()
  if not initialized and not db().execute('SELECT 1 FROM holding_competencies').fetchone():
   base=Path(__file__).resolve().parents[1]
   candidates=list(base.glob('*.xlsx'))+[base/'resources'/'workbook.xlsx']
   path=next((p for p in candidates if p.exists()),None)
   if path:
    import openpyxl
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
     for r in w['کاتالوگ_مهارت'].iter_rows(min_row=6,values_only=True):
      if r[1] and r[2]:db().execute('INSERT OR IGNORE INTO holding_competencies VALUES (?,?,?,?,?,?)',(str(r[1]),str(r[2]),str(r[3] or ''),int(r[4] or 1),str(r[5] or ''),str(r[6] or '')))
     db().commit()
    finally:w.close()
  # Existing databases are initialized too; an intentionally emptied bank
  # must remain empty on subsequent application starts.
  db().execute("INSERT OR IGNORE INTO holding_catalog_state VALUES ('initial_seed_complete','1')");db().commit()
 @app.route('/api/competency-bank',methods=['GET','POST'])
 @authenticated
 def bank():
  if request.method=='GET':return jsonify([dict(r) for r in db().execute('SELECT * FROM holding_competencies ORDER BY code')])
  if g.user['role']!='admin':raise PermissionError()
  from .catalog import bank_values
  d=bank_values(request.get_json() or {},creating=True)
  db().execute('INSERT INTO holding_competencies VALUES (?,?,?,?,?,?)',(d['code'],d['title'],d.get('category',''),int(d.get('importance',1)),d.get('method',''),d.get('reference','')));audit('holding_competency_created',detail=d['code']);db().commit();return jsonify(ok=True),201
 @app.post('/api/competency-bank/adopt')
 @authenticated
 def adopt():
  cid=company()
  if g.user['role']=='viewer':raise PermissionError()
  from .catalog import ensure_links,attach
  ensure_links(db(),False)
  for bank in db().execute('SELECT code FROM holding_competencies').fetchall():attach(db(),cid,bank['code'])
  base=Path(__file__).resolve().parents[1];paths=list(base.glob('*.xlsx'))+[base/'resources'/'workbook.xlsx'];path=next((p for p in paths if p.exists()),None)
  result={}
  if path:
   from .importer import import_workbook
   with path.open('rb') as f:result=import_workbook(db(),f,cid,False,False,skip_missing=True)
  from .catalog import ensure_links
  ensure_links(db(),False)
  audit('adopt_holding_catalog',cid);db().commit();return jsonify(ok=True,imported=result)
 @app.get('/api/passports/<int:eid>')
 @authenticated
 def passport(eid):
  cid=company();e=db().execute('SELECT * FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
  if not e:return error('فرد یافت نشد',404)
  job=db().execute('SELECT * FROM job_profiles WHERE company_id=? AND job=?',(cid,e['job'])).fetchone();meta=db().execute('SELECT * FROM employee_meta WHERE employee_id=? AND company_id=?',(eid,cid)).fetchone()
  matrix=db().execute('''SELECT s.id,b.code,b.title,b.category,COALESCE(p.level,r.level,0) required,MAX(COALESCE(a.level,0),COALESCE((SELECT MAX(t.level) FROM records t JOIN courses c ON c.id=t.course_id WHERE t.employee_id=? AND t.company_id=? AND c.skill_id=s.id AND c.status='completed'),0)) current FROM skills s JOIN competency_links l ON l.skill_id=s.id JOIN holding_competencies b ON b.code=l.code LEFT JOIN requirements r ON r.skill_id=s.id AND r.company_id=s.company_id AND r.job=? LEFT JOIN personal_requirements p ON p.skill_id=s.id AND p.employee_id=? LEFT JOIN assessments a ON a.skill_id=s.id AND a.employee_id=? AND a.company_id=s.company_id WHERE s.company_id=? AND (r.id IS NOT NULL OR a.id IS NOT NULL OR p.employee_id IS NOT NULL) ORDER BY b.category,b.code''',(eid,cid,e['job'],eid,eid,cid)).fetchall()
  records=db().execute('SELECT t.*,c.title,c.date,c.status FROM records t JOIN courses c ON c.id=t.course_id WHERE t.employee_id=? AND t.company_id=?',(eid,cid)).fetchall()
  certs=db().execute('SELECT id,title,issuer,issued,expires,filename FROM certificates WHERE employee_id=? AND company_id=?',(eid,cid)).fetchall()
  related=[r['id'] for r in matrix if r['required']>r['current']]
  plans=[dict(r) for r in db().execute("SELECT * FROM courses WHERE company_id=? AND status IN ('planned','approved')",(cid,)) if r['skill_id'] in related]
  from .documents import document_view
  document=document_view(db(),cid,eid,dict(job) if job else None)
  return jsonify(document=document,employee=dict(e),job=document['effective'],meta=dict(meta) if meta else None,matrix=[dict(r,gap=max(0,r['required']-r['current'])) for r in matrix],records=[dict(r) for r in records],certificates=[dict(r) for r in certs],idp=plans)
 @app.put('/api/passports/<int:eid>/approval')
 @authenticated
 def approval(eid):
  cid=company()
  if g.user['role']=='viewer':raise PermissionError()
  validate_refs('employee_meta',{'employee_id':eid},cid)
  d=request.get_json() or {};job=str(d.get('job','')).strip()
  if not db().execute('SELECT 1 FROM job_profiles WHERE company_id=? AND job=?',(cid,job)).fetchone():raise ValueError('ابتدا شناسنامه این سمت را تعریف کنید')
  db().execute('UPDATE employees SET job=? WHERE id=? AND company_id=?',(job,eid,cid))
  db().execute('INSERT INTO employee_meta(employee_id,company_id,hr_approved) VALUES (?,?,1) ON CONFLICT(employee_id) DO UPDATE SET hr_approved=1',(eid,cid));audit('hr_job_approved',cid,str(eid));db().commit();return jsonify(ok=True)
 @app.route('/api/certificates',methods=['GET','POST'])
 @authenticated
 def certificates():
  cid=company()
  if request.method=='GET':return jsonify([dict(r) for r in db().execute('SELECT c.id,c.employee_id,c.course_id,c.title,c.issuer,c.issued,c.expires,c.filename,c.created,e.name employee_name FROM certificates c JOIN employees e ON e.id=c.employee_id WHERE c.company_id=? ORDER BY c.id DESC',(cid,))])
  if g.user['role']=='viewer':raise PermissionError()
  eid=request.form.get('employee_id',type=int);course=request.form.get('course_id',type=int)
  validate_refs('certificates',{'employee_id':eid,**({'course_id':course} if course else {})},cid)
  title=request.form.get('title','').strip();f=request.files.get('file')
  if not title or not f:raise ValueError('فرد، عنوان و فایل گواهینامه الزامی است')
  content=f.read(8*1024*1024+1)
  if len(content)>8*1024*1024:raise ValueError('حداکثر حجم فایل ۸ مگابایت است')
  types=[(b'%PDF-','application/pdf','.pdf'),(b'\x89PNG\r\n\x1a\n','image/png','.png'),(b'\xff\xd8\xff','image/jpeg','.jpg')]
  found=next(((mime,ext) for sig,mime,ext in types if content.startswith(sig)),None)
  if not found:raise ValueError('فقط فایل PDF، PNG و JPEG معتبر پذیرفته می‌شود')
  cur=db().execute('INSERT INTO certificates(company_id,employee_id,course_id,title,issuer,issued,expires,filename,mime,content) VALUES (?,?,?,?,?,?,?,?,?,?)',(cid,eid,course,title,request.form.get('issuer',''),request.form.get('issued',''),request.form.get('expires',''),'certificate'+found[1],found[0],content));audit('certificate_uploaded',cid,str(cur.lastrowid));db().commit();return jsonify(id=cur.lastrowid),201
 @app.route('/api/certificates/<int:rid>',methods=['GET','DELETE'])
 @authenticated
 def certificate(rid):
  cid=company();r=db().execute('SELECT * FROM certificates WHERE id=? AND company_id=?',(rid,cid)).fetchone()
  if not r:return error('گواهینامه یافت نشد',404)
  if request.method=='DELETE':
   if g.user['role']=='viewer':raise PermissionError()
   db().execute('DELETE FROM certificates WHERE id=? AND company_id=?',(rid,cid));audit('certificate_deleted',cid,str(rid));db().commit();return jsonify(ok=True)
  return Response(r['content'],mimetype='application/octet-stream',headers={'Content-Disposition':f'attachment; filename="certificate-{rid}{Path(r["filename"]).suffix}"'})
 @app.get('/api/management-report')
 @authenticated
 def report():
  cid=company();gs=gaps(cid);departments={}
  for r in gs:
   v=departments.setdefault(r['department'] or 'بدون واحد',{'gaps':0,'employees':set(),'critical':0});v['gaps']+=1;v['employees'].add(r['employee_id']);v['critical']+=int(r['importance']==3)
  rows=[dict(department=k,gaps=v['gaps'],employees=len(v['employees']),critical=v['critical']) for k,v in departments.items()]
  return jsonify(departments=sorted(rows,key=lambda r:-r['gaps']),unmapped=db().execute("SELECT COUNT(*) FROM employees WHERE company_id=? AND job=''",(cid,)).fetchone()[0],unapproved=db().execute('SELECT COUNT(*) FROM employees e LEFT JOIN employee_meta m ON m.employee_id=e.id WHERE e.company_id=? AND COALESCE(m.hr_approved,0)=0',(cid,)).fetchone()[0],certificate_count=db().execute('SELECT COUNT(*) FROM certificates WHERE company_id=?',(cid,)).fetchone()[0])
