import zipfile
import openpyxl
LEVELS={'I':1,'L':2,'U':3,'O':4}
def import_workbook(db,file,cid,personnel=False,allow_new=True,skip_missing=False):
 # Reject oversized expanded ZIPs before XML processing.
 with zipfile.ZipFile(file) as z:
  if sum(x.file_size for x in z.infolist())>100*1024*1024:raise ValueError('حجم بازشده فایل بیش از حد مجاز است')
 file.seek(0)
 w=openpyxl.load_workbook(file,read_only=True,data_only=True)
 result={'skills':0,'requirements':0,'employees':0,'assessments':0,'job_profiles':0,'employee_metadata':0}
 try:
  db.execute('SAVEPOINT excel_import')
  from .catalog import ensure_links,attach
  ensure_links(db,allow_new)
  skills={}
  for row in w['کاتالوگ_مهارت'].iter_rows(min_row=6,values_only=True):
   if not row[1] or not row[2]:continue
   code=str(row[1]);bank=db.execute('SELECT 1 FROM holding_competencies WHERE code=?',(code,)).fetchone()
   if not bank:
    # Applying the packaged template must honor the admin's current bank,
    # including intentional deletions, without recreating deleted entries.
    if skip_missing:continue
    if not allow_new:raise ValueError('شایستگی جدید باید توسط مدیر هلدینگ در بانک مشترک تعریف شود')
    db.execute('INSERT INTO holding_competencies(code,title,category,importance,method,reference) VALUES (?,?,?,?,?,?)',(code,str(row[2]),str(row[3] or ''),int(row[4] or 1),str(row[5] or ''),str(row[6] or '')))
   before=db.execute('SELECT count(*) FROM skills WHERE company_id=?',(cid,)).fetchone()[0]
   skills[code]=attach(db,cid,code)
   result['skills']+=db.execute('SELECT count(*) FROM skills WHERE company_id=?',(cid,)).fetchone()[0]-before
  s=w['شناسنامه_شغل'];jobs=next(s.iter_rows(min_row=4,max_row=4,values_only=True))
  for row in s.iter_rows(min_row=16,values_only=True):
   sid=skills.get(str(row[1]))
   if not sid:continue
   for i,value in enumerate(row[4:],4):
    level=LEVELS.get(str(value).strip())
    if level and jobs[i]:
     c=db.execute('INSERT OR IGNORE INTO requirements(company_id,job,skill_id,level) VALUES (?,?,?,?)',(cid,str(jobs[i]),sid,level));result['requirements']+=c.rowcount
  if 'مشخصات_سمت' in w.sheetnames:
   for row in w['مشخصات_سمت'].iter_rows(min_row=6,values_only=True):
    if not row[1]:continue
    values=[str(row[i] or '') for i in [1,2,6,7,9,10,11,12,13,14,15,16,18]]
    c=db.execute('INSERT OR IGNORE INTO job_profiles(company_id,job,department,purpose,supervisor,duties,authority,education,experience,legal,hse,equipment,revision,approved_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',[cid]+values);result['job_profiles']+=c.rowcount
  if personnel:
   from .personnel_identity import find_current_employee,write_legacy_employee
   s=w['ماتریس_مهارت'];codes=next(s.iter_rows(min_row=4,max_row=4,values_only=True))
   for row in s.iter_rows(min_row=6,values_only=True):
    if not row[1] or not row[2]:continue
    eid=find_current_employee(db,cid,str(row[1]))
    if eid is None:
     eid,outcome=write_legacy_employee(db,cid,dict(code=str(row[1]),name=str(row[2]),department=str(row[4] or ''),job=str(row[3] or '')),policy='create')
     result['employees']+=int(outcome=='created')
    for i,v in enumerate(row[12:],12):
     level=LEVELS.get(str(v).strip());sid=skills.get(str(codes[i]))
     if level and sid:
      if db.execute('SELECT 1 FROM assessments a JOIN hrm_training_assessment_context x ON x.assessment_id=a.id WHERE a.company_id=? AND a.employee_id=? AND a.skill_id=?',(cid,eid,sid)).fetchone():
       raise ValueError('ارزیابی نسخه‌دار از فایل قدیمی تغییر نمی‌کند؛ از مدیریت آموزش یا قالب یکپارچه ارزیابی استفاده کنید')
      c=db.execute('INSERT OR IGNORE INTO assessments(company_id,employee_id,skill_id,level) VALUES (?,?,?,?)',(cid,eid,sid,level));result['assessments']+=c.rowcount
  if personnel and 'پرونده_پرسنلی' in w.sheetnames:
   for row in w['پرونده_پرسنلی'].iter_rows(min_row=6,values_only=True):
    if not row[1]:continue
    eid=find_current_employee(db,cid,str(row[1]))
    if eid is None:continue
    c=db.execute('INSERT OR IGNORE INTO employee_meta(employee_id,company_id,source_job,mapping,assessment_status,assessor,assessment_date,evidence,hr_approved) VALUES (?,?,?,?,?,?,?,?,?)',(eid,cid,str(row[4] or ''),str(row[6] or ''),str(row[13] or ''),str(row[15] or ''),str(row[16] or ''),str(row[17] or ''),int(bool(row[10]))));result['employee_metadata']+=c.rowcount
  db.execute('RELEASE excel_import')
 except Exception:
  db.execute('ROLLBACK TO excel_import');db.execute('RELEASE excel_import');raise
 finally:w.close()
 return result
