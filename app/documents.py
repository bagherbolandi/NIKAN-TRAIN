"""Stable contract document references, independent personal amendments and XLSX reports."""
import io,json,re
from flask import request,jsonify,g,Response
from .validation import SQLITE_MAX_INTEGER,json_object,positive_id
FIELDS=['purpose','definition','supervisor','duties','commitments','authority','education','experience','legal','hse','equipment','conditions','performance','contract_notes','effective_date','review_date','revision','approved_by']

def short_serial(reference):
 """Extract a supported short reference's serial without lossy/large coercion."""
 match=re.fullmatch(r'NP-[0-9]+-([0-9]+)',reference)
 if not match:return 0
 digits=match[1].lstrip('0') or '0'
 if len(digits)>19:return 0
 value=int(digits)
 return value if value<=SQLITE_MAX_INTEGER else 0

def serial_sql(value):
 """SQLite equivalent used by triggers when explicit references are imported."""
 separator=f"instr(substr({value},4),'-')"
 company=f'substr({value},4,{separator}-1)'
 serial=f'substr({value},{separator}+4)'
 digits=f"ltrim({serial},'0')"
 return f"""CASE WHEN substr({value},1,3)='NP-' AND {separator}>1
 AND {company} NOT GLOB '*[^0-9]*' AND {serial}!='' AND {serial} NOT GLOB '*[^0-9]*'
 AND (length({digits})<19 OR (length({digits})=19 AND {digits}<='9223372036854775807'))
 THEN CAST({serial} AS INTEGER) ELSE 0 END"""

def migrate_passport_numbers(conn):
 """Keep issued references as aliases and never reuse retired short serials."""
 with conn:
  conn.execute('BEGIN IMMEDIATE')
  columns={row['name'] for row in conn.execute('PRAGMA table_info(employees)')}
  for field in ('passport_number','passport_legacy_number'):
   if field not in columns:conn.execute(f"ALTER TABLE employees ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
  registry_columns={row['name'] for row in conn.execute('PRAGMA table_info(company_registry)')}
  first_conversion='passport_high_watermark' not in registry_columns
  if first_conversion:conn.execute('ALTER TABLE company_registry ADD COLUMN passport_high_watermark INTEGER NOT NULL DEFAULT 0 CHECK(passport_high_watermark BETWEEN 0 AND 9223372036854775807)')
  for trigger in ('employee_passport_created','employee_passport_updated'):
   conn.execute('DROP TRIGGER IF EXISTS '+trigger)
  employees=conn.execute('SELECT id,company_id,passport_number,passport_legacy_number FROM employees ORDER BY id').fetchall()
  reserved={reference for row in employees for reference in (row['passport_number'],row['passport_legacy_number']) if reference}
  high=conn.execute('SELECT passport_high_watermark FROM company_registry WHERE id=1').fetchone()[0]
  high=max([high,0]+[row['id'] for row in employees]+[short_serial(reference) for reference in reserved])
  for row in employees:
   previous=row['passport_number']
   generated=re.fullmatch(r'NT-P-[A-F0-9]{24}',previous) is not None
   if previous and not generated:continue
   candidate=f"NP-{row['company_id']:02d}-{row['id']:06d}" if first_conversion and row['id']>0 else None
   if candidate is None or candidate in reserved:
    while True:
     if high>=SQLITE_MAX_INTEGER:raise ValueError('ظرفیت شماره شناسنامه‌های فردی تکمیل شده است')
     high+=1;candidate=f"NP-{row['company_id']:02d}-{high:06d}"
     if candidate not in reserved:break
   legacy=row['passport_legacy_number'] or (previous if generated else '')
   conn.execute('UPDATE employees SET passport_number=?,passport_legacy_number=? WHERE id=?',(candidate,legacy,row['id']))
   reserved.add(candidate)
  conn.execute('UPDATE company_registry SET passport_high_watermark=? WHERE id=1',(high,))
  conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS employee_passport_number ON employees(passport_number) WHERE passport_number!=''")
  advance=f"""UPDATE company_registry SET passport_high_watermark=MAX(passport_high_watermark,
  {serial_sql('NEW.passport_number')},{serial_sql('NEW.passport_legacy_number')}) WHERE id=1;"""
  require_registry="SELECT CASE WHEN (SELECT count(*) FROM company_registry WHERE id=1)!=1 THEN RAISE(ABORT,'نشانه شماره‌گذاری شناسنامه موجود نیست') END;"
  conn.execute(f"""CREATE TRIGGER employee_passport_created AFTER INSERT ON employees BEGIN
  {require_registry}
  {advance}
  SELECT CASE WHEN NEW.passport_number='' AND (SELECT passport_high_watermark FROM company_registry WHERE id=1)>=9223372036854775807 THEN RAISE(ABORT,'ظرفیت شماره شناسنامه تکمیل شده است') END;
  UPDATE company_registry SET passport_high_watermark=passport_high_watermark+1 WHERE id=1 AND NEW.passport_number='';
  UPDATE employees SET passport_number=printf('NP-%02d-%06d',NEW.company_id,(SELECT passport_high_watermark FROM company_registry WHERE id=1)) WHERE id=NEW.id AND NEW.passport_number='';
  END""")
  conn.execute(f"""CREATE TRIGGER employee_passport_updated AFTER UPDATE OF passport_number,passport_legacy_number ON employees BEGIN
  {require_registry}
  {advance}
  END""")

def document_view(conn,cid,eid,master):
 row=conn.execute('SELECT * FROM personal_documents WHERE employee_id=? AND company_id=?',(eid,cid)).fetchone()
 overrides=json.loads(row['overrides']) if row else {}
 is_snapshot=bool(row and row['is_snapshot'])
 effective={**(master or {}),**({field:overrides.get(field,'') for field in FIELDS} if is_snapshot else overrides)} if master or overrides or is_snapshot else None
 reference=conn.execute('SELECT passport_number,passport_legacy_number FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
 return dict(number=reference['passport_number'],legacy_number=reference['passport_legacy_number'],master_number=master.get('document_number') if master else None,master=master,effective=effective,overrides=overrides,revision=row['revision'] if row else 0,is_snapshot=is_snapshot,requires_review=not is_snapshot)

def save_personal_document(conn,cid,eid,values):
 """Save a full actual-state snapshot; callers retain transaction/audit ownership."""
 if not isinstance(values,dict) or any(field not in FIELDS or not isinstance(value,str) or len(value)>10000 for field,value in values.items()):
  raise ValueError('اطلاعات اختصاصی شناسنامه معتبر نیست')
 for value in values.values():
  try:value.encode('utf-8')
  except UnicodeEncodeError:raise ValueError('متن شناسنامه دارای نویسه نامعتبر است') from None
 employee=conn.execute('SELECT job FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
 if not employee:raise ValueError('فرد متعلق به این شرکت نیست')
 master=conn.execute('SELECT * FROM job_profiles WHERE company_id=? AND job=?',(cid,employee['job'])).fetchone()
 current=document_view(conn,cid,eid,dict(master) if master else None)['effective'] or {}
 snapshot={field:values.get(field,current.get(field,'')) for field in FIELDS}
 conn.execute("INSERT INTO personal_documents(employee_id,company_id,overrides,revision,is_snapshot) VALUES (?,?,?,1,1) ON CONFLICT(employee_id) DO UPDATE SET overrides=excluded.overrides,revision=personal_documents.revision+1,is_snapshot=1,updated=CURRENT_TIMESTAMP",(eid,cid,json.dumps(snapshot,ensure_ascii=False)))

def register(app,db,authenticated,company,audit,error,gaps):
 with app.app_context():
  conn=db();columns={r['name'] for r in conn.execute('PRAGMA table_info(job_profiles)')}
  for field in ['document_number']+FIELDS:
   if field not in columns:conn.execute(f"ALTER TABLE job_profiles ADD COLUMN {field} TEXT NOT NULL DEFAULT ''")
  migrate_passport_numbers(conn)
  conn.execute("UPDATE job_profiles SET document_number=printf('NT-J-%02d-%06d',company_id,id) WHERE document_number=''")
  conn.executescript("""CREATE UNIQUE INDEX IF NOT EXISTS job_document_number ON job_profiles(document_number) WHERE document_number!='';
DROP TRIGGER IF EXISTS job_document_created;
CREATE TRIGGER job_document_created AFTER INSERT ON job_profiles WHEN NEW.document_number='' BEGIN UPDATE job_profiles SET document_number='NT-J-'||upper(hex(randomblob(12))) WHERE id=NEW.id; END;
CREATE TABLE IF NOT EXISTS personal_requirements(employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,skill_id INTEGER NOT NULL REFERENCES skills(id),level INTEGER NOT NULL CHECK(level BETWEEN 0 AND 4),PRIMARY KEY(employee_id,skill_id));
CREATE TABLE IF NOT EXISTS personal_documents(employee_id INTEGER PRIMARY KEY REFERENCES employees(id) ON DELETE CASCADE,company_id INTEGER NOT NULL REFERENCES companies(id),overrides TEXT NOT NULL DEFAULT '{}',revision INTEGER NOT NULL DEFAULT 0,updated TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,is_snapshot INTEGER NOT NULL DEFAULT 0 CHECK(is_snapshot IN (0,1)));""")
  if 'is_snapshot' not in {row['name'] for row in conn.execute('PRAGMA table_info(personal_documents)')}:conn.execute('ALTER TABLE personal_documents ADD COLUMN is_snapshot INTEGER NOT NULL DEFAULT 0 CHECK(is_snapshot IN (0,1))')
  conn.commit()
 @app.route('/api/passports/<int:eid>/document',methods=['GET','PUT','DELETE'])
 @authenticated
 def personal(eid):
  cid=company();eid=positive_id(eid);employee=db().execute('SELECT * FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
  if not employee:return error('فرد یافت نشد',404)
  master=db().execute('SELECT * FROM job_profiles WHERE company_id=? AND job=?',(cid,employee['job'])).fetchone()
  if request.method!='GET':
   if g.user['role']=='viewer':raise PermissionError()
   if request.method=='DELETE':db().execute('DELETE FROM personal_documents WHERE employee_id=? AND company_id=?',(eid,cid))
   else:
    data=json_object()
    if set(data)!={'overrides'}:raise ValueError('اصلاحات اختصاصی شناسنامه را ارسال کنید')
    save_personal_document(db(),cid,eid,data['overrides'])
   audit('personal_document_'+request.method,cid,str(eid));db().commit()
  return jsonify(document_view(db(),cid,eid,dict(master) if master else None))
 @app.route('/api/passports/<int:eid>/requirements',methods=['GET','PUT','DELETE'])
 @authenticated
 def personal_requirements(eid):
  cid=company();eid=positive_id(eid);e=db().execute('SELECT * FROM employees WHERE id=? AND company_id=?',(eid,cid)).fetchone()
  if not e:return error('فرد یافت نشد',404)
  if request.method!='GET':
   if g.user['role']=='viewer':raise PermissionError()
   if request.method=='DELETE':db().execute('DELETE FROM personal_requirements WHERE employee_id=?',(eid,))
   else:
    data=json_object()
    if set(data)!={'items'}:raise ValueError('فهرست الزامات را ارسال کنید')
    items=data['items']
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
