import zipfile
import openpyxl
LEVELS={'I':1,'L':2,'U':3,'O':4}
def import_workbook(db,file,cid,personnel=False):
 # Reject oversized expanded ZIPs before XML processing.
 with zipfile.ZipFile(file) as z:
  if sum(x.file_size for x in z.infolist())>100*1024*1024:raise ValueError('حجم بازشده فایل بیش از حد مجاز است')
 file.seek(0)
 w=openpyxl.load_workbook(file,read_only=True,data_only=True)
 result={'skills':0,'requirements':0,'employees':0,'assessments':0}
 try:
  db.execute('SAVEPOINT excel_import')
  for row in w['کاتالوگ_مهارت'].iter_rows(min_row=6,values_only=True):
   if not row[1] or not row[2]:continue
   c=db.execute('INSERT OR IGNORE INTO skills(company_id,code,title,category,importance) VALUES (?,?,?,?,?)',(cid,str(row[1]),str(row[2]),str(row[3] or ''),int(row[4] or 1)));result['skills']+=c.rowcount
  skills={r['code']:r['id'] for r in db.execute('SELECT id,code FROM skills WHERE company_id=?',(cid,))}
  s=w['شناسنامه_شغل'];jobs=next(s.iter_rows(min_row=4,max_row=4,values_only=True))
  for row in s.iter_rows(min_row=16,values_only=True):
   sid=skills.get(str(row[1]))
   if not sid:continue
   for i,value in enumerate(row[4:],4):
    level=LEVELS.get(str(value).strip())
    if level and jobs[i]:
     c=db.execute('INSERT OR IGNORE INTO requirements(company_id,job,skill_id,level) VALUES (?,?,?,?)',(cid,str(jobs[i]),sid,level));result['requirements']+=c.rowcount
  if personnel:
   s=w['ماتریس_مهارت'];codes=next(s.iter_rows(min_row=4,max_row=4,values_only=True))
   for row in s.iter_rows(min_row=6,values_only=True):
    if not row[1] or not row[2]:continue
    c=db.execute('INSERT OR IGNORE INTO employees(company_id,code,name,department,job) VALUES (?,?,?,?,?)',(cid,str(row[1]),str(row[2]),str(row[4] or ''),str(row[3] or '')));result['employees']+=c.rowcount
    eid=db.execute('SELECT id FROM employees WHERE company_id=? AND code=?',(cid,str(row[1]))).fetchone()[0]
    for i,v in enumerate(row[12:],12):
     level=LEVELS.get(str(v).strip());sid=skills.get(str(codes[i]))
     if level and sid:
      c=db.execute('INSERT OR IGNORE INTO assessments(company_id,employee_id,skill_id,level) VALUES (?,?,?,?)',(cid,eid,sid,level));result['assessments']+=c.rowcount
  db.execute('RELEASE excel_import')
 except Exception:
  db.execute('ROLLBACK TO excel_import');db.execute('RELEASE excel_import');raise
 finally:w.close()
 return result
