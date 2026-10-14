"""Stable contract document references, independent personal amendments and XLSX reports."""
import io,json
from flask import request,jsonify,g,Response
FIELDS=['purpose','definition','supervisor','duties','commitments','authority','education','experience','legal','hse','equipment','conditions','performance','contract_notes','effective_date','review_date','revision','approved_by']

def document_view(conn,cid,eid,master):
 row=conn.execute('SELECT * FROM personal_documents WHERE employee_id=? AND company_id=?',(eid,cid)).fetchone()
 overrides=json.loads(row['overrides']) if row else {}
 effective={**(master or {}),**overrides} if master or overrides else None
 return dict(number=conn.execute('SELECT passport_number FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()[0],master_number=master.get('document_number') if master else None,master=master,effective=effective,overrides=overrides,revision=row['revision'] if row else 0)

def register(app,db,authenticated,company,audit,error,gaps):
 with app.app_context():
  conn=db();columns={r['name'] for r in conn.execute('PRAGMA table_info(job_profiles)')}
  for field in ['document_number']+FIELDS:
   if field not in columns:conn.execute(f"ALTER TABLE job_profiles ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
  if 'passport_number' not in {r['name'] for r in conn.execute('PRAGMA table_info(employees)')}:conn.execute("ALTER TABLE employees ADD COLUMN passport_number TEXT NOT NULL DEFAULT ''")
  conn.execute("UPDATE employees SET passport_number='NT-P-'||upper(hex(randomblob(12))) WHERE passport_number=''")
  conn.execute("UPDATE job_profiles SET document_number=printf('NT-J-%02d-%06d',company_id,id) WHERE document_number=''")
  conn.executescript("""CREATE UNIQUE INDEX IF NOT EXISTS job_document_number ON job_profiles(document_number) WHERE document_number!='';
DROP TRIGGER IF EXISTS job_document_created;
CREATE UNIQUE INDEX IF NOT EXISTS employee_passport_number ON employees(passport_number) WHERE passport_number!='';
CREATE TRIGGER IF NOT EXISTS employee_passport_created AFTER INSERT ON employees BEGIN UPDATE employees SET passport_number='NT-P-'||upper(hex(randomblob(12))) WHERE id=NEW.id; END;
CREATE TRIGGER job_document_created AFTER INSERT ON job_profiles BEGIN UPDATE job_profiles SET document_number='NT-J-'||upper(hex(randomblob(12))) WHERE id=NEW.id; END;
CREATE TABLE IF NOT EXISTS personal_requirements(employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,skill_id INTEGER NOT NULL REFERENCES skills(id),level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 4),PRIMARY KEY(employee_id,skill_id));
CREATE TABLE IF NOT EXISTS personal_documents(employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,company_id INTEGER NOT NULL REFERENCES companies(id),overrides TEXT NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 0,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);""")
  conn.commit()
 @app.route('/api/passports/<int:eid>/document',methods=['GET','PUT','DELETE'])
 @authenticated
 def personal(eid):
  cid=company();employee=db().execute('SELECT * FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
  if not employee:return error('فرد یافت نشد',404)
  master=db().execute('SELECT * FROM job_profiles WHERE company_id=? AND job=?',(cid,employee['job'])).fetchone()
  if request.method!='GET':
   if g.user['role']=='viewer':raise PermissionError()
   if request.method=='DELETE':db().execute('DELETE FROM personal_documents WHERE employee_id=? AND company_id=?',(eid,cid))
   else:
    data=request.get_json() or {};values=data.get('overrides',{})
    if not isinstance(values,dict) or any(k not in FIELDS or not isinstance(v,str) or len(v)>10000 for k,v in values.items()):raise ValueError('اطلاعات اختصاصی شناسنامه معتبر نیست')
    db().execute("INSERT INTO personal_documents(employee_id,company_id,overrides,revision) VALUES (?,?,?,1) ON CONFLICT(employee_id) DO UPDATE SET overrides=excluded.overrides,revision=personal_documents.revision+1,updated=CURRENT_TIMESTAMP",(eid,cid,json.dumps(values,ensure_ascii=False)))
   audit('personal_document_'+request.method,cid,str(eid));db().commit()
  return jsonify(document_view(db(),cid,eid,dict(master) if master else None))
 @app.route('/api/passports/<int:eid>/requirements',methods=['GET','PUT','DELETE'])
 @authenticated
 def personal_requirements(eid):
  cid=company();e=db().execute('SELECT * FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
  if not e:return error('فرد یافت نشد',404)
  if request.method!='GET':
   if g.user['role']=='viewer':raise PermissionError()
   if request.method=='DELETE':db().execute('DELETE FROM personal_requirements WHERE employee_id=?',(eid,))
   else:
    items=(request.get_json() or {}).get('items')
    if not isinstance(items,list) or len(items)>2000:raise ValueError('فهرست الزامات معتبر نیست')
    seen=set()
    for item in items:
     if not isinstance(item,dict) or not isinstance(item.get('code'),str) or type(item.get('level')) is not int or item['level'] not in range(5) or item['code'] in seen:raise ValueError('سطح یا کد شایستگی معتبر نیست')
     seen.add(item['code'])
     if not db().execute('SELECT 1 FROM holding_competencies WHERE code=?',(item['code'],)).fetchone():raise ValueError('شایستگی در بانک هلدینگ وجود ندارد')
    from .catalog import attach
    for item in items:
     sid=attach(db(),cid,item['code']);db().execute('INSERT INTO personal_requirements VALUES (?,?,?) ON CONFLICT(employee_id,skill_id) DO UPDATE SET level=excluded.level',(eid,sid,item['level']))
   audit('personal_requirements_'+request.method,cid,str(eid));db().commit()
  matrix=[dict(r) for r in db().execute('SELECT b.*,COALESCE(p.level,r.level,0) level,COALESCE(r.level,0) master_level FROM holding_competencies b LEFT JOIN competency_links l ON l.code=b.code AND l.skill_id IN (SELECT id FROM skills WHERE company_id=?) LEFT JOIN requirements r ON r.skill_id=l.skill_id AND r.company_id=? AND r.job=? LEFT JOIN personal_requirements p ON p.skill_id=l.skill_id AND p.employee_id=? ORDER BY b.category,b.code',(cid,cid,e['job'],eid))]
  return jsonify(matrix=matrix)
 @app.get('/api/reference-options')
 @authenticated
 def references():
  cid=company()
  return jsonify(jobs=[r[0] for r in db().execute('SELECT job FROM job_profiles WHERE company_id=? ORDER BY job',(cid,))],departments=[r[0] for r in db().execute("SELECT department FROM employees WHERE company_id=? AND department!='' UNION SELECT department FROM job_profiles WHERE company_id=? AND department!='' ORDER BY department",(cid,cid))],instructors=[r[0] for r in db().execute('SELECT DISTINCT name FROM instructors WHERE company_id=? ORDER BY name',(cid,))],categories=[r[0] for r in db().execute("SELECT DISTINCT category FROM holding_competencies WHERE category!='' ORDER BY category")])
 @app.get('/api/export.xlsx')
 @authenticated
 def export_excel():
  import openpyxl
  from openpyxl.styles import Font,PatternFill,Alignment
  from openpyxl.utils import get_column_letter
  cid=company();rows=sorted(gaps(cid),key=lambda r:(r['department'],r['name'],r['code'],r['title'],r['skill_id']))
  people={};skills={}
  for r in rows:
   people.setdefault(r['employee_id'],[]).append(r);skills.setdefault(r['skill_id'],[]).append(r)
  wb=openpyxl.Workbook();wb.remove(wb.active)
  def sheet(name,headers,records):
   ws=wb.create_sheet(name);ws.sheet_view.rightToLeft=True;ws.append(headers)
   for record in records:ws.append([("'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v) for v in record])
   ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
   for c in ws[1]:c.font=Font(bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='15304F')
   for row in ws.iter_rows(min_row=2):
    for c in row:c.alignment=Alignment(vertical='top',wrap_text=True)
   for i in range(1,len(headers)+1):ws.column_dimensions[get_column_letter(i)].width=24 if i<5 else 48
  sheet('افراد', ['کد پرسنلی','نام فرد','واحد','شغل','تعداد نیاز','نیازهای آموزشی و فاصله سطح'],[[rs[0]['code'],rs[0]['name'],rs[0]['department'],rs[0]['job'],len(rs),'\n'.join(f"{r['title']} | لازم {r['required']}، فعلی {r['current']}، گپ {r['gap']}" for r in rs)] for rs in people.values()])
  grouped=sorted(skills.values(),key=lambda rs:(-rs[0]['importance'],rs[0]['title']))
  sheet('شایستگی‌ها',['شایستگی','اهمیت','تعداد افراد','جمع گپ','افراد نیازمند'],[[rs[0]['title'],rs[0]['importance'],len(rs),sum(r['gap'] for r in rs),'\n'.join(f"{r['name']} ({r['code']}) — {r['department']}" for r in rs)] for rs in grouped])
  # The normalized detail sheet retains explicit keys so sorting/filtering never loses identity.
  sheet('جزئیات قابل فیلتر',['کد پرسنلی','نام فرد','واحد','شغل','شایستگی','سطح لازم','سطح فعلی','گپ','اهمیت'],[[r[k] for k in ['code','name','department','job','title','required','current','gap','importance']] for r in rows])
  out=io.BytesIO();wb.save(out)
  return Response(out.getvalue(),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename=Nikan-Training-Needs.xlsx'})
