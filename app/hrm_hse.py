"""Management records for HSE, PPE and independently confidential occupational health.

Existing employee IDs and training evidence are reused. No employee portal,
clinical inference, legal training interval or PPE entitlement is invented here.
"""
from datetime import date, timedelta
import json

from flask import jsonify, request

from .validation import json_object, positive_id
from . import hrm_access as access
from .hrm_access import require, employee, scope_sql, company_scope, ctx, effective_column
from .hrm_common import (date_value, text_value, integer_value, today_iso,
                         HrmConflict, begin_write, check_version)

SCHEMA = '''
CREATE TABLE IF NOT EXISTS hrm_ppe_catalog(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 name TEXT NOT NULL,category TEXT NOT NULL DEFAULT '',standard TEXT NOT NULL DEFAULT '',
 lifetime_days INTEGER CHECK(lifetime_days IS NULL OR lifetime_days>0),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(company_id,name));
CREATE TABLE IF NOT EXISTS hrm_ppe_requirements(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 ppe_id INTEGER NOT NULL REFERENCES hrm_ppe_catalog(id),
 quantity INTEGER NOT NULL CHECK(quantity>0),
 renewal_days INTEGER CHECK(renewal_days IS NULL OR renewal_days>0),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(company_id,job,activity_location,ppe_id));
CREATE TABLE IF NOT EXISTS hrm_ppe_issues(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 employee_id INTEGER NOT NULL REFERENCES employees(id),ppe_id INTEGER NOT NULL REFERENCES hrm_ppe_catalog(id),
 issue_date TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity>0),size TEXT NOT NULL DEFAULT '',
 next_due_date TEXT NOT NULL DEFAULT '',expiry_basis TEXT NOT NULL DEFAULT '',
 receiver_reference TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'delivered' CHECK(status IN ('delivered','returned','retired')),
 notes TEXT NOT NULL DEFAULT '',document_id INTEGER REFERENCES hrm_documents(id),
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_ppe_issues_employee ON hrm_ppe_issues(employee_id,ppe_id,issue_date);
CREATE TABLE IF NOT EXISTS hrm_hse_training_requirements(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 domain TEXT NOT NULL CHECK(domain IN ('safety','health','environment')),
 competency_code TEXT NOT NULL REFERENCES holding_competencies(code),
 required_level INTEGER NOT NULL CHECK(required_level BETWEEN 1 AND 4),
 renewal_days INTEGER CHECK(renewal_days IS NULL OR renewal_days>0),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),notes TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(company_id,job,activity_location,domain,competency_code));
CREATE TABLE IF NOT EXISTS hrm_health_records(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 employee_id INTEGER NOT NULL REFERENCES employees(id),exam_date TEXT NOT NULL,
 exam_type TEXT NOT NULL,physician TEXT NOT NULL DEFAULT '',
 fitness TEXT NOT NULL CHECK(fitness IN ('fit','restricted','temporarily_unfit','unfit','awaiting_result')),
 restrictions TEXT NOT NULL DEFAULT '',clinical_notes TEXT NOT NULL DEFAULT '',
 next_review_date TEXT NOT NULL DEFAULT '',document_id INTEGER REFERENCES hrm_documents(id),
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS hrm_health_records_employee ON hrm_health_records(employee_id,exam_date);
CREATE TABLE IF NOT EXISTS hrm_hse_incidents(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),
 employee_id INTEGER NOT NULL REFERENCES employees(id),event_date TEXT NOT NULL,
 event_type TEXT NOT NULL CHECK(event_type IN ('injury','near_miss','environment','unsafe_condition')),
 title TEXT NOT NULL,description TEXT NOT NULL DEFAULT '',corrective_action TEXT NOT NULL DEFAULT '',
 followup_date TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
 document_id INTEGER REFERENCES hrm_documents(id),
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
'''
RECOVERY_ADDITIONS = {name: 0 for name in (
    'hrm_ppe_catalog', 'hrm_ppe_requirements', 'hrm_ppe_issues',
    'hrm_hse_training_requirements', 'hrm_health_records', 'hrm_hse_incidents')}


def field(key, label, type='text', **extra):
    return dict(key=key, label=label, type=type, **extra)


ACTIVE = [[1, 'فعال'], [0, 'غیرفعال']]
STAFF = field('employee_id', 'فرد', 'employee', required=True)
PPE = field('ppe_id', 'تجهیز حفاظت فردی', 'resource', resource='ppe_catalog', required=True)
MATRIX = [field('job', 'شغل (خالی: همه شغل‌ها)'), field('activity_location', 'محل فعالیت (خالی: همه محل‌ها)')]
OPTIONAL_RENEWAL = field('renewal_days', 'فاصله تجدید بر حسب روز (اختیاری)', 'number', min=1)
DOC = field('document_id', 'شناسه مدرک پیوست مجاز (اختیاری)', 'number', min=1)
CATALOG = {
 'ppe_catalog': dict(title='بانک تجهیزات حفاظت فردی',module='hse',
   fields=[field('name','نام تجهیز',required=True),field('category','نوع / گروه'),field('standard','استاندارد / مشخصات'),
           field('lifetime_days','عمر مصرف بر حسب روز (اختیاری)','number',min=1),field('active','وضعیت','select',choices=ACTIVE),field('notes','توضیحات','textarea')],
   columns=['name','category','standard','lifetime_days','active']),
 'ppe_requirements': dict(title='ماتریس توزیع حفاظت فردی بر اساس شغل و محل',module='hse',
   fields=[*MATRIX,PPE,field('quantity','تعداد لازم','number',required=True,min=1),OPTIONAL_RENEWAL,
           field('active','وضعیت','select',choices=ACTIVE),field('notes','خطر مربوط / دلیل انتخاب','textarea')],
   columns=['job','activity_location','ppe_name','quantity','renewal_days','active']),
 'ppe_issues': dict(title='سوابق تحویل تجهیزات حفاظت فردی',module='hse',
   fields=[STAFF,PPE,field('issue_date','تاریخ تحویل','date',required=True),field('quantity','تعداد','number',required=True,min=1),
           field('size','اندازه / مشخصات'),field('next_due_date','موعد تعویض (خالی: ضابطه ثبت‌شده)','date'),
           field('receiver_reference','شماره رسید / تأیید تحویل'),field('status','وضعیت','select',choices=[['delivered','تحویل‌شده'],['returned','برگشت‌شده'],['retired','از رده خارج']]),DOC,field('notes','توضیحات','textarea')],
   columns=['employee_name','ppe_name','issue_date','quantity','next_due_date','status','receiver_reference']),
 'hse_training_requirements': dict(title='الزامات آموزش ایمنی، بهداشت و محیط‌زیست',module='hse',
   fields=[*MATRIX,field('domain','حوزه HSE','select',required=True,choices=[['safety','ایمنی'],['health','بهداشت'],['environment','محیط‌زیست']]),
           field('competency_code','شایستگی موجود در بانک هلدینگ','resource',resource='hse_training_options',value_key='code',label_key='title',required=True),
           field('required_level','سطح لازم (۱ تا ۴)','number',required=True,min=1,max=4),OPTIONAL_RENEWAL,
           field('active','وضعیت','select',choices=ACTIVE),field('notes','دلیل الزام / ضابطه داخلی','textarea')],
   columns=['job','activity_location','domain','competency_code','competency_title','required_level','renewal_days','active']),
 'health_records': dict(title='پرونده سلامت و معاینات شاغلین',module='health',confidential=True,
   fields=[STAFF,field('exam_date','تاریخ معاینه','date',required=True),field('exam_type','نوع معاینه (مطابق عنوان الزام ثبت‌شده)',required=True),
           field('physician','پزشک / مرکز معاینه'),field('fitness','نظر ثبت‌شده پزشک','select',required=True,choices=[['fit','مناسب'],['restricted','مناسب با محدودیت'],['temporarily_unfit','نامناسب موقت'],['unfit','نامناسب'],['awaiting_result','در انتظار نتیجه']]),
           field('restrictions','محدودیت‌های اعلام‌شده','textarea'),field('clinical_notes','یادداشت محرمانه سلامت','textarea'),
           field('next_review_date','معاینه / بازبینی بعدی','date'),DOC],
   columns=['employee_name','exam_date','exam_type','fitness','next_review_date','physician']),
 'hse_incidents': dict(title='حوادث و اقدامات اصلاحی HSE',module='hse',
   fields=[STAFF,field('event_date','تاریخ رویداد','date',required=True),field('event_type','نوع رویداد','select',required=True,choices=[['injury','حادثه'],['near_miss','شبه‌حادثه'],['environment','رویداد محیط‌زیستی'],['unsafe_condition','شرایط ناایمن']]),
           field('title','عنوان',required=True),field('description','شرح رویداد (بدون اطلاعات بالینی)','textarea'),
           field('corrective_action','اقدام اصلاحی / پیگیری','textarea'),field('followup_date','تاریخ پیگیری','date'),
           field('status','وضعیت','select',choices=[['open','باز'],['closed','بسته']]),DOC],
   columns=['employee_name','event_date','event_type','title','followup_date','status']),
}
EMPLOYEE_RESOURCES = {'ppe_issues','health_records','hse_incidents'}
TABLES = {key:'hrm_'+key for key in CATALOG}



# Operational Health & Environment shares one organizational permission. Medical
# narrative and attachments retain the narrower, explicit ``health`` grant.
EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS hrm_job_risks(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 hazard TEXT NOT NULL,risk_description TEXT NOT NULL DEFAULT '',control_measures TEXT NOT NULL DEFAULT '',
 review_date TEXT NOT NULL DEFAULT '',active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 notes TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS hrm_harmful_exposures(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),employee_id INTEGER NOT NULL REFERENCES employees(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 exposure_date TEXT NOT NULL,factor TEXT NOT NULL,measurement TEXT NOT NULL DEFAULT '',measurement_unit TEXT NOT NULL DEFAULT '',
 controls TEXT NOT NULL DEFAULT '',next_review_date TEXT NOT NULL DEFAULT '',document_id INTEGER REFERENCES hrm_documents(id),
 notes TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS hrm_environment_participation(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),employee_id INTEGER NOT NULL REFERENCES employees(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 activity_date TEXT NOT NULL,program TEXT NOT NULL,contribution TEXT NOT NULL DEFAULT '',document_id INTEGER REFERENCES hrm_documents(id),
 notes TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS hrm_safety_qualifications(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),employee_id INTEGER NOT NULL REFERENCES employees(id),
 site_id INTEGER REFERENCES hrm_sites(id),department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 title TEXT NOT NULL,certificate_number TEXT NOT NULL DEFAULT '',issuer TEXT NOT NULL DEFAULT '',issued_date TEXT NOT NULL,
 valid_until TEXT NOT NULL DEFAULT '',certificate_id INTEGER REFERENCES certificates(id),document_id INTEGER REFERENCES hrm_documents(id),
 notes TEXT NOT NULL DEFAULT '',is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1,created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS hrm_medical_requirements(
 id INTEGER PRIMARY KEY,company_id INTEGER NOT NULL REFERENCES companies(id),site_id INTEGER REFERENCES hrm_sites(id),
 department TEXT NOT NULL DEFAULT '',job TEXT NOT NULL DEFAULT '',activity_location TEXT NOT NULL DEFAULT '',
 exam_type TEXT NOT NULL,frequency_days INTEGER CHECK(frequency_days IS NULL OR frequency_days>0),
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),notes TEXT NOT NULL DEFAULT '',
 is_deleted INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1)),version INTEGER NOT NULL DEFAULT 1,
 created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(company_id,site_id,department,job,activity_location,exam_type));
"""
SCHEMA += EXTRA_SCHEMA
RECOVERY_ADDITIONS.update({name:0 for name in ('hrm_job_risks','hrm_harmful_exposures','hrm_environment_participation','hrm_safety_qualifications','hrm_medical_requirements')})
SITE=field('site_id','سایت (خالی: سایت جاری فرد / سیاست عمومی)','resource',resource='sites')
DEPARTMENT=field('department','واحد سازمانی (خالی: همه واحدها)')
RISK=field('risk_id','مخاطره ثبت‌شده مرتبط (اختیاری)','resource',resource='job_risks')
CATALOG['hse_training_requirements']['fields'][2]['choices']=[['safety','ایمنی']]
CATALOG['hse_training_requirements']['title']='الزامات آموزش ایمنی شغل و محل'
CATALOG['hse_incidents']['fields'][2]['choices']=[['injury','حادثه'],['near_miss','شبه‌حادثه'],['unsafe_condition','شرایط ناایمن']]
CATALOG['health_environment_training_requirements']=dict(
 title='الزامات آموزش بهداشت و محیط زیست',module='health_environment',
 fields=[*MATRIX,field('domain','حوزه تخصصی','select',required=True,choices=[['health','بهداشت حرفه‌ای'],['environment','محیط زیست']]),
  field('competency_code','شایستگی موجود در بانک هلدینگ','resource',resource='hse_training_options',required=True),
  field('required_level','سطح لازم (۱ تا ۴)','number',required=True,min=1,max=4),OPTIONAL_RENEWAL,
  field('active','وضعیت','select',choices=ACTIVE),field('notes','ضابطه داخلی / دلیل الزام','textarea')],
 columns=['job','activity_location','domain','competency_code','competency_title','required_level','renewal_days','active'])
CATALOG.update({
 'job_risks':dict(title='مخاطرات و الزامات ایمنی شغل و محل',module='hse',
  fields=[*MATRIX,field('hazard','عامل خطر / مخاطره',required=True),field('risk_description','شرح مخاطره','textarea'),
   field('control_measures','اقدامات کنترلی / الزام ایمنی','textarea'),field('review_date','موعد بازنگری','date'),
   field('active','وضعیت','select',choices=ACTIVE),field('notes','توضیحات','textarea')],
  columns=['job','activity_location','hazard','control_measures','review_date','active']),
 'harmful_exposures':dict(title='سوابق مواجهه با عوامل زیان‌آور',module='health_environment',
  fields=[STAFF,field('exposure_date','تاریخ مواجهه / پایش','date',required=True),field('factor','عامل زیان‌آور',required=True),
   field('measurement','نتیجه اندازه‌گیری ثبت‌شده'),field('measurement_unit','واحد اندازه‌گیری'),
   field('controls','کنترل‌ها / اقدام پیگیری','textarea'),field('next_review_date','موعد پایش بعدی','date'),DOC,field('notes','توضیحات','textarea')],
  columns=['employee_name','exposure_date','factor','measurement','measurement_unit','next_review_date']),
 'environment_participation':dict(title='مشارکت کارکنان در برنامه‌های محیط زیستی',module='health_environment',
  fields=[STAFF,field('activity_date','تاریخ فعالیت','date',required=True),field('program','برنامه محیط زیستی',required=True),
   field('contribution','نقش و مشارکت فرد','textarea'),DOC,field('notes','توضیحات','textarea')],
  columns=['employee_name','activity_date','program','contribution']),
 'safety_qualifications':dict(title='صلاحیت‌ها و گواهی‌های ایمنی شغلی',module='hse',
  fields=[STAFF,field('title','عنوان صلاحیت / مجوز',required=True),field('certificate_number','شماره گواهی / مجوز'),
   field('issuer','مرجع صادرکننده'),field('issued_date','تاریخ صدور','date',required=True),field('valid_until','اعتبار تا','date'),
   field('certificate_id','شناسه گواهی آموزشی موجود (اختیاری)','number',min=1),DOC,field('notes','توضیحات','textarea')],
  columns=['employee_name','title','certificate_number','issued_date','valid_until','issuer']),
 'medical_requirements':dict(title='الزامات معاینه شغلی بر اساس شغل و محل',module='health_environment',
  fields=[*MATRIX,field('exam_type','نوع معاینه الزامی',required=True),
   field('frequency_days','فاصله معاینه بر حسب روز (خالی: فقط ثبت الزام)','number',min=1),
   field('active','وضعیت','select',choices=ACTIVE),field('notes','ضابطه مصوب سازمان','textarea')],
  columns=['job','activity_location','exam_type','frequency_days','active']),
})
EMPLOYEE_RESOURCES.update({'harmful_exposures','environment_participation','safety_qualifications'})
for key,meta in CATALOG.items():
 meta['fields']=[SITE,*([DEPARTMENT] if key not in EMPLOYEE_RESOURCES else []),*meta['fields']]
 meta['columns']=['site_name',*meta['columns']]
 meta['soft_delete']=True
 if key in ('ppe_requirements','hse_training_requirements'):
  meta['fields'].append(RISK)
TABLES.update({key:'hrm_'+key for key in CATALOG})
TABLES['health_environment_training_requirements']='hrm_hse_training_requirements'
TRAINING_RESOURCES={'hse_training_requirements','health_environment_training_requirements'}
DATE_KEYS={'ppe_issues':'issue_date','health_records':'exam_date','hse_incidents':'event_date','harmful_exposures':'exposure_date',
           'environment_participation':'activity_date','safety_qualifications':'issued_date'}


def _migrate(conn):
    conn.executescript(SCHEMA)
    for table in set(TABLES.values()):
        columns={r['name'] for r in conn.execute('PRAGMA table_info('+table+')')}
        additions={'site_id':'INTEGER REFERENCES hrm_sites(id)', 'department':"TEXT NOT NULL DEFAULT ''",
                   'is_deleted':'INTEGER NOT NULL DEFAULT 0 CHECK(is_deleted IN (0,1))'}
        if table in {TABLES[k] for k in EMPLOYEE_RESOURCES}:
            additions.update(job="TEXT NOT NULL DEFAULT ''",activity_location="TEXT NOT NULL DEFAULT ''")
        if table in ('hrm_ppe_requirements','hrm_hse_training_requirements'):
            additions['risk_id']='INTEGER REFERENCES hrm_job_risks(id)'
        for column,ddl in additions.items():
            if column not in columns:conn.execute('ALTER TABLE '+table+' ADD COLUMN '+column+' '+ddl)
    # The initial PPE schema predates site separation. Replace only the two
    # additive HRM tables whose old unique keys omitted site and department.
    # Their rows/IDs remain exact, and incoming external FK extensions abort.
    import re
    for table,tail in [('hrm_ppe_requirements','ppe_id'),('hrm_hse_training_requirements','domain,competency_code')]:
        sql=conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()[0]
        pattern=r'UNIQUE\s*\(company_id,job,activity_location,'+re.escape(tail)+r'\)'
        if re.search(pattern,sql,re.I):
            for other in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                if any(fk['table']==table for fk in conn.execute('PRAGMA foreign_key_list("'+other['name'].replace('"','""')+'")')):
                    raise ValueError('مهاجرت کلید سایت به بررسی ارتباط سفارشی جدول '+table+' نیاز دارد')
            replacement=table+'__site_upgrade'
            ddl=re.sub(pattern,'CHECK(1)',sql,flags=re.I).replace('CREATE TABLE '+table+'(', 'CREATE TABLE '+replacement+'(',1)
            # SQLite may quote a restored table name in sqlite_master.
            if replacement not in ddl:ddl=ddl.replace('CREATE TABLE "'+table+'"','CREATE TABLE "'+replacement+'"',1)
            conn.execute(ddl)
            columns=','.join(r['name'] for r in conn.execute('PRAGMA table_info('+table+')'))
            conn.execute('INSERT INTO '+replacement+'('+columns+') SELECT '+columns+' FROM '+table)
            conn.execute('DROP TABLE '+table)
            conn.execute('ALTER TABLE '+replacement+' RENAME TO '+table)
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS '+table+'_site_policy ON '+table+'(company_id,COALESCE(site_id,0),department,job,activity_location,'+tail+') WHERE is_deleted=0')
    conn.commit()


def _domain_clause(resource,alias='r'):
    if resource=='hse_training_requirements':return alias+".domain='safety'"
    if resource=='health_environment_training_requirements':return alias+".domain IN ('health','environment')"
    if resource=='hse_incidents':return alias+".event_type!='environment'"
    return '1'


def _optional_id(value):
    return None if value in (None,'') else positive_id(value)


def _site(conn,cid,sid,module,operation=None,department=None):
    sid=_optional_id(sid)
    access.site_scope(conn,cid,sid,module,operation=operation,department=department)
    if sid is not None:
        row=conn.execute('SELECT company_id,active FROM hrm_sites WHERE id=?',(sid,)).fetchone()
        if not row or row['company_id']!=cid:raise ValueError('سایت باید متعلق به همین شرکت باشد')
        if operation=='create' and not row['active']:raise ValueError('سایت غیرفعال قابل استفاده برای ثبت جدید نیست')
    return sid


def _filter_company(conn,cid,module,operation=None):
    actor=require(conn,module,operation=operation)
    cid=positive_id(cid)
    if not actor.get('is_primary_admin') and cid not in actor['company_ids']:
        raise PermissionError()
    if not conn.execute('SELECT 1 FROM companies WHERE id=?',(cid,)).fetchone():
        raise ValueError('شرکت یافت نشد')
    return cid


def _record_access(conn,row,module,operation=None):
    access.record_scope(conn,row,module,operation=operation)


def _assignment(conn,eid,module='hse',operation=None):
    person=access.employee(conn,eid,module,operation=operation)
    detail=conn.execute('SELECT location,site_id FROM hrm_employee_details WHERE employee_id=?',(person['id'],)).fetchone()
    person['location']=detail['location'] if detail else ''
    person['site_id']=person.get('site_id',detail['site_id'] if detail else None)
    return person


def _matches(rule,person):
    return (rule['site_id'] is None or rule['site_id']==person['site_id']) and (not rule['department'] or rule['department']==person['department']) and (not rule['job'] or rule['job']==person['job']) and (not rule['activity_location'] or rule['activity_location']==person['location'])


def _document(conn,document_id,eid,cid,group,operation=None):
    if document_id in (None,''):return None
    rid=positive_id(document_id)
    row=conn.execute('SELECT * FROM hrm_documents WHERE id=?',(rid,)).fetchone()
    if not row or row['employee_id']!=eid or row['company_id']!=cid or row['permission_group']!=group:
        raise ValueError('مدرک باید متعلق به همین فرد، شرکت و گروه دسترسی باشد')
    _record_access(conn,row,group,operation)
    return rid


def _ppe(conn,rid,cid,active=False,site_id=None):
    rid=positive_id(rid)
    row=conn.execute('SELECT * FROM hrm_ppe_catalog WHERE id=? AND company_id=? AND is_deleted=0',(rid,cid)).fetchone()
    if not row or (active and not row['active']) or (row['site_id'] is not None and row['site_id']!=site_id):
        raise ValueError('تجهیز فعال متعلق به همین شرکت و سایت را انتخاب کنید')
    return dict(row)


def _optional_days(value):
    return None if value in (None,'') else integer_value(value,'فاصله زمانی',minimum=1,maximum=36500)


def _choice(data,key,choices,default=None):
    value=data.get(key,default)
    if value not in choices:raise ValueError('گزینه '+key+' معتبر نیست')
    return value


def _risk(conn,rid,cid,sid,operation=None):
    if rid in (None,''):return None
    rid=positive_id(rid)
    row=conn.execute('SELECT * FROM hrm_job_risks WHERE id=? AND is_deleted=0',(rid,)).fetchone()
    if not row or row['company_id']!=cid or (row['site_id'] is not None and row['site_id']!=sid):
        raise ValueError('مخاطره باید متعلق به همین شرکت و سایت باشد')
    _record_access(conn,row,'hse',operation)
    return rid


def _issue_expiry(conn,person,equipment,issued):
    requirements=conn.execute('SELECT * FROM hrm_ppe_requirements WHERE company_id=? AND ppe_id=? AND active=1 AND is_deleted=0',(person['company_id'],equipment['id'])).fetchall()
    intervals=[r['renewal_days'] for r in requirements if r['renewal_days'] and _matches(r,person)]
    if equipment['lifetime_days']:intervals.append(equipment['lifetime_days'])
    if not intervals:return '',''
    return (date.fromisoformat(issued)+timedelta(days=min(intervals))).isoformat(),'configured_interval'


def _validate(conn,resource,data,cid,previous=None):
    allowed={f['key'] for f in CATALOG[resource]['fields']}
    if set(data)-allowed-{'version'}:raise ValueError('فیلد ناشناخته در اطلاعات ایمنی و بهداشت')
    combined={**(dict(previous) if previous else {}),**data};result={}
    module=CATALOG[resource]['module'];operation='edit' if previous else 'create'
    person=None
    if resource in EMPLOYEE_RESOURCES:
        eid=positive_id(combined.get('employee_id'))
        if previous:
            if eid!=previous['employee_id']:raise ValueError('فرد یک سابقه ثبت‌شده قابل جایگزینی نیست')
            raw=conn.execute('SELECT * FROM employees WHERE id=?',(eid,)).fetchone()
            person=dict(raw);person.update(company_id=cid,site_id=previous['site_id'],department=previous['department'],job=previous['job'],location=previous['activity_location'])
        else:
            person=_assignment(conn,eid,module,operation)
            if person['company_id']!=cid:raise ValueError('فرد متعلق به شرکت جاری نیست')
        result['employee_id']=eid
    sid=_optional_id(combined.get('site_id')) if 'site_id' in data or previous else (person['site_id'] if person else None)
    if person and not previous and sid is None:sid=person['site_id']
    department=person['department'] if person else text_value(combined.get('department'),'واحد سازمانی',max_length=200)
    result.update(site_id=_site(conn,cid,sid,module,operation,department),department=department)
    for f in CATALOG[resource]['fields']:
        key=f['key'];value=combined.get(key)
        if key in ('site_id','department'):continue
        if f['type'] in ('text','textarea'):
            result[key]=text_value(value,f['label'],required=f.get('required',False),max_length=8000 if f['type']=='textarea' else 500)
        elif f['type']=='date':result[key]=date_value(value,required=f.get('required',False))
    if person:
        result.update(job=person['job'],activity_location=person['location'])
        group='health' if resource=='health_records' else ('health_environment' if module=='health_environment' else 'hse')
        result['document_id']=_document(conn,combined.get('document_id'),person['id'],cid,group,operation)
    if resource in ('ppe_catalog','ppe_requirements','job_risks','medical_requirements') or resource in TRAINING_RESOURCES:
        result['active']=integer_value(combined.get('active',1),'وضعیت',maximum=1)
    if resource=='ppe_catalog':result['lifetime_days']=_optional_days(combined.get('lifetime_days'))
    elif resource=='ppe_requirements':
        result['ppe_id']=_ppe(conn,combined.get('ppe_id'),cid,site_id=sid)['id']
        result['quantity']=integer_value(combined.get('quantity'),'تعداد',minimum=1,maximum=10000)
        result['renewal_days']=_optional_days(combined.get('renewal_days'));result['risk_id']=_risk(conn,combined.get('risk_id'),cid,sid,operation)
    elif resource in TRAINING_RESOURCES:
        result['domain']=_choice(combined,'domain',('safety',) if resource=='hse_training_requirements' else ('health','environment'),'safety' if resource=='hse_training_requirements' else None)
        code=text_value(combined.get('competency_code'),'شایستگی',required=True,max_length=128)
        if not conn.execute('SELECT 1 FROM holding_competencies WHERE code=?',(code,)).fetchone():raise ValueError('شایستگی را از بانک موجود هلدینگ انتخاب کنید')
        result.update(competency_code=code,required_level=integer_value(combined.get('required_level'),'سطح لازم',minimum=1,maximum=4),renewal_days=_optional_days(combined.get('renewal_days')))
        result['risk_id']=_risk(conn,combined.get('risk_id'),cid,sid,operation)
    elif resource=='ppe_issues':
        equipment=_ppe(conn,combined.get('ppe_id'),cid,active=not previous,site_id=sid)
        result.update(ppe_id=equipment['id'],quantity=integer_value(combined.get('quantity'),'تعداد',minimum=1,maximum=10000),status=_choice(combined,'status',('delivered','returned','retired'),'delivered'))
        if result['next_due_date']:result['expiry_basis']='manual' if not previous or 'next_due_date' in data else previous['expiry_basis']
        else:result['next_due_date'],result['expiry_basis']=_issue_expiry(conn,person,equipment,result['issue_date'])
        if result['next_due_date'] and result['next_due_date']<result['issue_date']:raise ValueError('موعد تعویض پیش از تاریخ تحویل نیست')
    elif resource=='health_records':
        result['fitness']=_choice(combined,'fitness',('fit','restricted','temporarily_unfit','unfit','awaiting_result'))
        if result['next_review_date'] and result['next_review_date']<result['exam_date']:raise ValueError('بازبینی سلامت پیش از تاریخ معاینه نیست')
        if result['exam_date']>today_iso() and result['fitness']!='awaiting_result':raise ValueError('نتیجه معاینه آینده قابل ثبت نیست')
    elif resource=='hse_incidents':
        result.update(event_type=_choice(combined,'event_type',('injury','near_miss','unsafe_condition')),status=_choice(combined,'status',('open','closed'),'open'))
        if result['followup_date'] and result['followup_date']<result['event_date']:raise ValueError('تاریخ پیگیری پیش از رویداد نیست')
        if result['status']=='closed' and not result['corrective_action']:raise ValueError('برای بستن رویداد، اقدام اصلاحی را ثبت کنید')
    elif resource=='medical_requirements':result['frequency_days']=_optional_days(combined.get('frequency_days'))
    elif resource=='harmful_exposures':
        if result['next_review_date'] and result['next_review_date']<result['exposure_date']:raise ValueError('موعد پایش قبل از مواجهه نیست')
    elif resource=='safety_qualifications':
        result['certificate_id']=_optional_id(combined.get('certificate_id'))
        if result['certificate_id']:
            cert=conn.execute('SELECT employee_id,company_id FROM certificates WHERE id=?',(result['certificate_id'],)).fetchone()
            if not cert or cert['employee_id']!=person['id'] or cert['company_id']!=person.get('training_company_id',person['company_id']):raise ValueError('گواهی آموزشی باید متعلق به همین فرد و سابقه آموزشی او باشد')
        if result['valid_until'] and result['valid_until']<result['issued_date']:raise ValueError('پایان اعتبار پیش از صدور نیست')
    if resource in DATE_KEYS and resource!='health_records' and result[DATE_KEYS[resource]]>today_iso():raise ValueError('سابقه انجام‌شده نمی‌تواند تاریخ آینده داشته باشد')
    return result


def _include_deleted():
    value=request.args.get('include_deleted','0')
    if value not in ('0','1','true','false',''):
        raise ValueError('گزینه نمایش سوابق حذف‌شده معتبر نیست')
    return value in ('1','true')


def _list(conn,resource,cid=None):
    module=CATALOG[resource]['module'];require(conn,module)
    predicate,params=access.record_scope_sql(conn,module,alias='r',department_column='r.department')
    where=[predicate,_domain_clause(resource)];params=list(params)
    if not _include_deleted():where.append('r.is_deleted=0')
    table=TABLES[resource]
    sql='SELECT r.*,s.name site_name'+(',e.name employee_name,e.code employee_code' if resource in EMPLOYEE_RESOURCES else '')+' FROM '+table+' r'
    if resource in EMPLOYEE_RESOURCES:sql+=' JOIN employees e ON e.id=r.employee_id'
    sql+=' LEFT JOIN hrm_sites s ON s.id=r.site_id'
    if resource in ('ppe_requirements','ppe_issues'):
        sql=sql.replace(' FROM ',' ,p.name ppe_name FROM ',1)+' JOIN hrm_ppe_catalog p ON p.id=r.ppe_id'
    elif resource in TRAINING_RESOURCES:
        sql=sql.replace(' FROM ',' ,b.title competency_title FROM ',1)+' JOIN holding_competencies b ON b.code=r.competency_code'
    if resource=='ppe_catalog':
        actor=ctx(conn);ids=actor['company_ids']
        if not actor.get('is_primary_admin') and ids:
            where[0]='(('+where[0]+') OR (r.site_id IS NULL AND r.company_id IN ('+','.join('?' for _ in ids)+')))';params=[*params,*ids]
    if cid is not None:where.append('r.company_id=?');params.append(cid)
    for key in ('site_id','department'):
        if request.args.get(key) not in (None,''):
            value=positive_id(request.args[key]) if key=='site_id' else text_value(request.args[key],max_length=200)
            where.append('r.'+key+'=?');params.append(value)
    if resource in EMPLOYEE_RESOURCES:
        if request.args.get('employee_id') not in (None,''):
            where.append('r.employee_id=?');params.append(positive_id(request.args['employee_id']))
        for key,operator in [('start_date','>='),('end_date','<=')]:
            if request.args.get(key):where.append('r.'+DATE_KEYS[resource]+operator+'?');params.append(date_value(request.args[key],True))
    return [dict(r) for r in conn.execute(sql+' WHERE '+' AND '.join('('+x+')' for x in where)+' ORDER BY r.id DESC LIMIT 5000',params)]


def _training_evidence(conn,person,code,module='hse'):
    from .hrm_training_evidence import scoped_training_evidence
    return scoped_training_evidence(conn,person,code,module,as_of=today_iso())


def _people(conn,module,cid=None):
    predicate,params=access.scope_sql(conn,module,'e')
    if cid is not None:
        _filter_company(conn,cid,module)
        predicate+=' AND '+effective_column(conn,'company_id','e')+'=?';params=[*params,cid]
    people=[_assignment(conn,r['id'],module) for r in conn.execute('SELECT e.id FROM employees e WHERE '+predicate+' ORDER BY e.id LIMIT 5000',params)]
    sid=_optional_id(request.args.get('site_id'))
    department=request.args.get('department');eid=_optional_id(request.args.get('employee_id'))
    return [p for p in people if (sid is None or p['site_id']==sid) and (not department or p['department']==department) and (eid is None or p['id']==eid)]


def _rules(conn,table,module,cid=None):
    predicate,params=access.record_scope_sql(conn,module,'r',department_column='r.department')
    if cid is not None:predicate+=' AND r.company_id=?';params=[*params,cid]
    return [dict(r) for r in conn.execute('SELECT r.* FROM '+table+' r WHERE r.is_deleted=0 AND r.active=1 AND ('+predicate+')',params)]


def _training_report(conn,people,domains):
    rows=[];today=today_iso()
    from .hrm_training_evidence import applicable_hse_training_rules
    module='hse' if domains=={'safety'} else 'health_environment'
    for person in people:
        rules=applicable_hse_training_rules(conn,person,module)
        for rule in rules:
            if rule['domain'] not in domains:continue
            evidence=_training_evidence(conn,person,rule['competency_code'],module)
            from .hrm_training_evidence import renewal_evidence
            renewal=renewal_evidence(evidence['last_training_date'],rule['renewal_days'],today)
            level=evidence['current_level']
            gap=max(0,rule['required_level']-level) if level is not None else None
            review=level is None or renewal['renewal_review_required']
            rows.append(dict(employee_id=person['id'],employee_name=person['name'],employee_code=person['code'],company_id=person['company_id'],site_id=person['site_id'],
                department=person['department'],job=person['job'],activity_location=person['location'],requirement_id=rule['id'],domain=rule['domain'],
                competency_code=rule['competency_code'],competency_title=rule['competency_title'],required_level=rule['required_level'],
                **evidence,**renewal,gap=gap,evidence_review_required=review,
                training_due=bool((gap is not None and gap>0) or renewal['renewal_due'] is True or review)))

    return rows


def hse_report(conn,cid=None):
    people=_people(conn,'hse',cid);today=today_iso();ppe_rows=[]
    for person in people:
        rules=conn.execute('SELECT r.*,p.name ppe_name FROM hrm_ppe_requirements r JOIN hrm_ppe_catalog p ON p.id=r.ppe_id WHERE r.company_id=? AND r.active=1 AND r.is_deleted=0 AND p.active=1 AND p.is_deleted=0',(person['company_id'],)).fetchall()
        for rule in rules:
            if not _matches(rule,person):continue
            owned,owners=access.record_scope_sql(conn,'hse','r',department_column='r.department')
            issues=conn.execute("SELECT r.* FROM hrm_ppe_issues r WHERE employee_id=? AND company_id=? AND ppe_id=? AND site_id IS ? AND status='delivered' AND is_deleted=0 AND issue_date<=? AND ("+owned+")",[person['id'],person['company_id'],rule['ppe_id'],person['site_id'],today,*owners]).fetchall()
            valid=[r for r in issues if not r['next_due_date'] or r['next_due_date']>=today];available=sum(r['quantity'] for r in valid);missing=max(0,rule['quantity']-available)
            due_dates=[r['next_due_date'] for r in valid if r['next_due_date']]
            ppe_rows.append(dict(employee_id=person['id'],employee_name=person['name'],employee_code=person['code'],company_id=person['company_id'],site_id=person['site_id'],
                department=person['department'],job=person['job'],activity_location=person['location'],requirement_id=rule['id'],ppe_id=rule['ppe_id'],ppe_name=rule['ppe_name'],
                required_quantity=rule['quantity'],valid_delivered_quantity=available,missing_quantity=missing,next_due_date=min(due_dates) if due_dates else '',
                overdue=bool(missing and any(r['next_due_date'] and r['next_due_date']<today for r in issues))))
    training=_training_report(conn,people,{'safety'});incidents=_list(conn,'hse_incidents',cid);qualifications=_list(conn,'safety_qualifications',cid)
    reviews=_temporary_reviews(conn,people,'hse')
    reviewed={r['employee_id'] for r in reviews}
    for item in [*ppe_rows,*training]:item['review_required']=item['employee_id'] in reviewed
    return dict(as_of=today,assignment_reviews=reviews,ppe=ppe_rows,training=training,risks=_list(conn,'job_risks',cid),incidents=incidents,qualifications=qualifications,
                metrics=dict(temporary_assignment_reviews=len(reviews),ppe_requirements=len(ppe_rows),ppe_missing=sum(r['missing_quantity']>0 for r in ppe_rows),ppe_overdue=sum(r['overdue'] for r in ppe_rows),
                             training_requirements=len(training),training_due=sum(r['training_due'] for r in training),incidents_open=sum(r['status']=='open' for r in incidents),
                             qualifications_expired=sum(bool(r['valid_until'] and r['valid_until']<today) for r in qualifications)),
                data_status='no_scoped_employees' if not people else ('assignment_review_required' if reviews else ('no_configured_requirements' if not ppe_rows and not training else 'recorded_data')))


def _medical_status(conn,people):
    today=today_iso();requirements=[];missing=[];due=[];completed=[]
    for person in people:
        rules=conn.execute('SELECT * FROM hrm_medical_requirements WHERE company_id=? AND active=1 AND is_deleted=0',(person['company_id'],)).fetchall()
        for rule in rules:
            if not _matches(rule,person):continue
            owned,owners=access.record_scope_sql(conn,'health','r',department_column='r.department')
            exam=conn.execute("SELECT r.exam_date,r.next_review_date FROM hrm_health_records r WHERE r.employee_id=? AND r.company_id=? AND r.site_id IS ? AND r.exam_type=? AND r.fitness!='awaiting_result' AND r.is_deleted=0 AND ("+owned+") ORDER BY r.exam_date DESC,r.id DESC LIMIT 1",[person['id'],person['company_id'],person['site_id'],rule['exam_type'],*owners]).fetchone()
            row=dict(employee_id=person['id'],employee_name=person['name'],company_id=person['company_id'],site_id=person['site_id'],job=person['job'],activity_location=person['location'],
                     requirement_id=rule['id'],requirement_source='job_site_policy',exam_type=rule['exam_type'],last_exam_date=exam['exam_date'] if exam else '',next_review_date='',status='missing' if not exam else 'recorded')
            if not exam:missing.append(row)
            else:
                dates=[]
                if rule['frequency_days']:dates.append((date.fromisoformat(exam['exam_date'])+timedelta(days=rule['frequency_days'])).isoformat())
                if exam['next_review_date']:dates.append(exam['next_review_date'])
                row['next_review_date']=min(dates) if dates else ''
                if row['next_review_date'] and row['next_review_date']<=today:row['status']='due';due.append(row)
                else:completed.append(row)
            requirements.append(row)
        # A physician's explicit next-review date is also real follow-up data,
        # even when no organization-wide job/site interval has been configured.
        # Reuse the same historical-record permission predicate as above.
        owned,owners=access.record_scope_sql(conn,'health','r',department_column='r.department')
        examinations=conn.execute("SELECT r.id,r.exam_type,r.exam_date,r.next_review_date FROM hrm_health_records r WHERE r.employee_id=? AND r.company_id=? AND r.site_id IS ? AND r.fitness!='awaiting_result' AND r.is_deleted=0 AND ("+owned+") ORDER BY r.exam_date DESC,r.id DESC",[person['id'],person['company_id'],person['site_id'],*owners]).fetchall()
        latest={}
        covered={r['exam_type'] for r in rules if _matches(r,person)}
        for exam in examinations:
            if exam['exam_type'] not in latest:latest[exam['exam_type']]=exam
        for exam_type,exam in latest.items():
            if exam_type in covered or not exam['next_review_date']:continue
            row=dict(employee_id=person['id'],employee_name=person['name'],company_id=person['company_id'],site_id=person['site_id'],job=person['job'],activity_location=person['location'],
                     requirement_id=None,requirement_source='recorded_review',exam_type=exam_type,last_exam_date=exam['exam_date'],next_review_date=exam['next_review_date'],
                     status='due' if exam['next_review_date']<=today else 'recorded')
            requirements.append(row)
            (due if row['status']=='due' else completed).append(row)
    return dict(authorized=True,requirements=requirements,missing=missing,due=due,completed=completed,
                metrics=dict(required=len(requirements),missing=len(missing),due=len(due),recorded=len(completed)),
                data_status='no_configured_requirements' if not requirements else 'configured_policy')


def _temporary_reviews(conn,people,module):
    today=today_iso();reviews=[]
    for person in people:
        rows=conn.execute("SELECT * FROM hrm_temporary_assignments WHERE employee_id=? AND status='active' AND start_date<=? AND end_date>=? ORDER BY id",(person['id'],today,today)).fetchall()
        for assignment in rows:
            if assignment['target_site_id']==person['site_id'] and (not assignment['activity_location'] or assignment['activity_location']==person['location']):
                continue
            review=dict(employee_id=person['id'],employee_name=person['name'],company_id=person['company_id'],site_id=person['site_id'],
                        assignment_id=assignment['id'],review_required=True,
                        reason='فعالیت موقت در محل دیگر؛ بازنگری الزامات شغل، محل و مخاطرات لازم است')
            try:
                access.site_scope(conn,assignment['target_company_id'],assignment['target_site_id'],module,department=person['department'])
                review.update(target_company_id=assignment['target_company_id'],target_site_id=assignment['target_site_id'],activity_location=assignment['activity_location'])
            except PermissionError:
                review['target_scope_authorized']=False
            reviews.append(review)
    return reviews


def health_report(conn,cid=None):
    people=_people(conn,'health',cid);rows=_list(conn,'health_records',cid);latest={}
    for row in rows:
        key=(row['company_id'],row['site_id'],row['employee_id'],row['exam_type'])
        if key not in latest or (row['exam_date'],row['id'])>(latest[key]['exam_date'],latest[key]['id']):latest[key]=row
    today=today_iso()
    return dict(as_of=today,latest_examinations=list(latest.values()),due_reviews=[r for r in latest.values() if r['next_review_date'] and r['next_review_date']<=today],medical=_medical_status(conn,people))


def health_environment_report(conn,cid=None):
    people=_people(conn,'health_environment',cid);training=_training_report(conn,people,{'health','environment'})
    exposures=_list(conn,'harmful_exposures',cid);participation=_list(conn,'environment_participation',cid)
    if ctx(conn)['permissions'].get('health',{}).get('read'):
        health_ids={p['id'] for p in _people(conn,'health',cid)}
        medical=_medical_status(conn,[p for p in people if p['id'] in health_ids])
    else:medical=dict(authorized=False,requirements=None,missing=None,due=None,metrics=dict(required=None,missing=None,due=None,recorded=None),data_status='not_authorized')
    reviews=_temporary_reviews(conn,people,'health_environment')
    return dict(as_of=today_iso(),assignment_reviews=reviews,training=training,exposures=exposures,participation=participation,medical=medical,
                metrics=dict(temporary_assignment_reviews=len(reviews),training_requirements=len(training),training_due=sum(r['training_due'] for r in training),exposures=len(exposures),environment_participations=len(participation),
                             medical_required=medical['metrics']['required'],medical_missing=medical['metrics']['missing'],medical_due=medical['metrics']['due']),
                data_status='no_scoped_employees' if not people else 'recorded_data')


def register(app,db,authenticated,audit,error):
    with app.app_context():_migrate(db())
    app.extensions.setdefault('hrm_catalog',{}).update(CATALOG)

    def route(resource,rid=None):
        module=CATALOG[resource]['module'];conn=db()
        if request.method=='GET' and rid is None:
            require(conn,module)
            raw=request.args.get('company_id');cid=positive_id(raw) if raw not in (None,'') else None
            if cid is not None:_filter_company(conn,cid,module)
            return jsonify(_list(conn,resource,cid))
        if rid is not None:rid=positive_id(rid)
        if request.method=='GET':
            require(conn,module);row=conn.execute('SELECT * FROM '+TABLES[resource]+' r WHERE id=? AND '+('1' if _include_deleted() else 'is_deleted=0')+' AND '+_domain_clause(resource),(rid,)).fetchone()
            if not row:return error('سابقه یافت نشد',404)
            if resource=='ppe_catalog' and row['site_id'] is None:_filter_company(conn,row['company_id'],module)
            else:_record_access(conn,row,module)
            return jsonify(dict(row))
        operation='delete' if request.method=='DELETE' else ('edit' if rid else 'create')
        require(conn,module,operation=operation);body=json_object()
        with begin_write(conn):
            old=conn.execute('SELECT * FROM '+TABLES[resource]+' r WHERE id=? AND is_deleted=0 AND '+_domain_clause(resource),(rid,)).fetchone() if rid else None
            if rid and old is None:return error('سابقه یافت نشد',404)
            if old:
                _record_access(conn,old,module,operation);check_version(body,old);cid=old['company_id']
            else:
                cid=positive_id(request.args.get('company_id'))
                _filter_company(conn,cid,module,operation)
            if operation=='delete':
                if set(body)!={'version'}:raise ValueError('برای حذف، نسخه جاری سابقه را ارسال کنید')
                if resource=='ppe_catalog' and (conn.execute('SELECT 1 FROM hrm_ppe_requirements WHERE ppe_id=? LIMIT 1',(rid,)).fetchone() or conn.execute('SELECT 1 FROM hrm_ppe_issues WHERE ppe_id=? LIMIT 1',(rid,)).fetchone()):raise HrmConflict('تجهیز در ماتریس یا سوابق تحویل استفاده شده است؛ آن را غیرفعال کنید')
                if resource=='job_risks' and any(conn.execute('SELECT 1 FROM '+table+' WHERE risk_id=? LIMIT 1',(rid,)).fetchone() for table in ('hrm_ppe_requirements','hrm_hse_training_requirements')):raise HrmConflict('مخاطره به الزامات ارجاع دارد؛ آن را غیرفعال کنید')
                conn.execute('UPDATE '+TABLES[resource]+' SET is_deleted=1,version=version+1 WHERE id=?',(rid,));audit('hrm_delete_'+resource,cid,str(rid));return jsonify(ok=True,id=rid)
            values=_validate(conn,resource,body,cid,old)
            if old:conn.execute('UPDATE '+TABLES[resource]+' SET '+','.join(k+'=?' for k in values)+',version=version+1 WHERE id=?',[*values.values(),rid])
            else:
                keys=['company_id',*values];rid=conn.execute('INSERT INTO '+TABLES[resource]+'('+','.join(keys)+') VALUES ('+','.join('?' for _ in keys)+')',[cid,*values.values()]).lastrowid
            audit('hrm_'+('update_' if old else 'create_')+resource,cid,str(rid))
        return jsonify(dict(conn.execute('SELECT * FROM '+TABLES[resource]+' WHERE id=?',(rid,)).fetchone())),200 if old else 201

    for resource in CATALOG:
        def listing(resource=resource):return route(resource)
        def detail(rid,resource=resource):return route(resource,rid)
        app.add_url_rule('/api/hrm/'+resource,'hrm_hse_'+resource,authenticated(listing),methods=['GET','POST'])
        app.add_url_rule('/api/hrm/'+resource+'/<int:rid>','hrm_hse_'+resource+'_detail',authenticated(detail),methods=['GET','PUT','DELETE'])

    @app.get('/api/hrm/hse_training_options')
    @authenticated
    def hrm_hse_training_options():
        actor=ctx(db())
        if not any(actor['permissions'].get(module,{}).get(op) for module in ('hse','health_environment') for op in ('read','create','edit','export')):raise PermissionError()
        return jsonify([dict(id=r['code'],code=r['code'],title=r['title'],name=r['title']) for r in db().execute('SELECT code,title FROM holding_competencies ORDER BY category,code')])

    @app.get('/api/hrm/hse_report')
    @authenticated
    def hrm_hse_report():
        raw=request.args.get('company_id');return jsonify(hse_report(db(),positive_id(raw) if raw not in (None,'') else None))

    @app.get('/api/hrm/health_report')
    @authenticated
    def hrm_health_report():
        raw=request.args.get('company_id');return jsonify(health_report(db(),positive_id(raw) if raw not in (None,'') else None))

    @app.get('/api/hrm/health_environment_report')
    @authenticated
    def hrm_health_environment_report():
        raw=request.args.get('company_id');return jsonify(health_environment_report(db(),positive_id(raw) if raw not in (None,'') else None))
