import io
import openpyxl
from test_catalog import ready

def test_master_and_personal_documents_are_independent(ready):
 app,c,h=ready
 jid=c.post('/api/data/job_profiles?company_id=1',headers=h,json={'job':'Engineer','definition':'Master definition','commitments':'Master commitments'}).json['id']
 master=c.get('/api/data/job_profiles?company_id=1').json[0]
 assert master['document_number'].startswith('NT-J-')
 ids=[c.post('/api/data/employees?company_id=1',headers=h,json={'code':str(i),'name':f'Person {i}','job':'Engineer'}).json['id'] for i in range(2)]
 p=f'/api/passports/{ids[0]}/document?company_id=1'
 assert c.put(p,headers=h,json={'overrides':{'duties':'Individual duties','commitments':'Individual commitments'}}).status_code==200
 first=c.get(f'/api/passports/{ids[0]}?company_id=1').json
 assert first['job']['commitments']=='Individual commitments'
 assert c.get(f'/api/passports/{ids[1]}?company_id=1').json['job']['commitments']=='Master commitments'
 assert c.get('/api/data/job_profiles?company_id=1').json[0]['commitments']=='Master commitments'
 assert c.put(f'/api/data/job_profiles/{jid}?company_id=1',headers=h,json={'definition':'Revised'}).status_code==200
 assert c.get(p).json['master']['document_number']==master['document_number']
 assert c.get(p).json['effective']['definition']=='Revised'
 assert c.put(p,headers=h,json={'overrides':{'company_id':'2'}}).status_code==400
 assert c.get(p.replace('company_id=1','company_id=2')).status_code==404
 c.delete(p,headers=h)
 assert c.get(p).json['effective']['commitments']=='Master commitments'

def test_personal_requirements_and_ordered_excel(ready):
 app,c,h=ready
 ids=[c.post('/api/data/employees?company_id=1',headers=h,json={'code':str(i),'name':f'Person {i}','job':'Engineer'}).json['id'] for i in range(2)]
 c.put('/api/job-workspace?company_id=1&job=Engineer',headers=h,json={'items':[{'code':'S001','level':3},{'code':'S002','level':2}]})
 p=f'/api/passports/{ids[0]}/requirements?company_id=1'
 assert c.put(p,headers=h,json={'items':[{'code':'S001','level':0},{'code':'S002','level':1}]}).status_code==200
 gaps=c.get('/api/gaps?company_id=1').json
 assert len([r for r in gaps if r['employee_id']==ids[0]])==1
 assert len([r for r in gaps if r['employee_id']==ids[1]])==2
 assert next(r for r in c.get(f'/api/passports/{ids[0]}?company_id=1').json['matrix'] if r['code']=='S002')['required']==1
 assert c.put(p,headers=h,json={'items':[{'code':'S002','level':4},{'code':'MISSING','level':1}]}).status_code==400
 assert next(r for r in c.get(p).json['matrix'] if r['code']=='S002')['level']==1
 response=c.get('/api/export.xlsx?company_id=1');assert response.status_code==200
 w=openpyxl.load_workbook(io.BytesIO(response.data));assert len(w.sheetnames)==3
 assert w['افراد'].max_row==3;assert w['شایستگی‌ها'].max_row==3
 assert w['افراد'].sheet_view.rightToLeft;assert w['افراد'].freeze_panes=='A2'
 c.delete(p,headers=h);assert len(c.get('/api/gaps?company_id=1').json)==4
