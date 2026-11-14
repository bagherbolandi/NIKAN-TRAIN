import shutil,sqlite3,tempfile,unittest,io
from contextlib import closing
from pathlib import Path
from app import create_app
from app.excel_forms import write_row

class GovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed=tempfile.TemporaryDirectory();create_app(cls.seed.name,testing=True)
    @classmethod
    def tearDownClass(cls):cls.seed.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();shutil.copytree(self.seed.name,self.temp.name,dirs_exist_ok=True)
        self.app=create_app(self.temp.name,testing=True);self.admin=self.app.test_client()
        self.admin.post('/api/setup',json=dict(username='admin',password='test-password-12345'))
        self.admin.post('/api/login',json=dict(username='admin',password='test-password-12345'))
        self.csrf=self.admin.get('/api/me').json['csrf']
        self.post('users',dict(username='manager',password='test-password-12345',role='manager',company_id=1))
        self.post('users',dict(username='viewer',password='test-password-12345',role='viewer',company_id=1))
        self.manager=self.app.test_client();self.manager.post('/api/login',json=dict(username='manager',password='test-password-12345'))
        self.mcsrf=self.manager.get('/api/me').json['csrf']
        self.sid=self.post('data/skills?company_id=1',dict(code='TEST-X',title='Test competence',importance=3)).json['id']
        self.need=self.post('data/needs?company_id=1',dict(skill_id=self.sid,reason='Gap',year=1405)).json['id']
    def tearDown(self):self.temp.cleanup()
    def post(self,path,data,client=None,method='POST'):
        client=client or self.admin
        return client.open('/api/'+path,method=method,json=data,headers={'X-CSRF-Token':self.mcsrf if client is getattr(self,"manager",None) else self.csrf})
    def decide(self,status='approved',version=1):return self.post(f'holding/needs/{self.need}/decision',dict(status=status,decision='Committee review',version=version))
    def test_company_isolation_and_holding_access(self):
        self.assertEqual(self.manager.get('/api/data/employees?company_id=2').status_code,403)
        self.assertEqual(self.manager.get('/api/holding/needs?year=1405').status_code,403)
        self.assertEqual(self.manager.get('/api/holding/gaps').status_code,403)
        self.assertEqual(self.manager.get('/api/calendar?scope=holding').status_code,403)
    def test_company_cannot_approve_by_any_generic_write(self):
        data=dict(skill_id=self.sid,reason='Bypass',year=1405,status='approved')
        self.assertEqual(self.post('data/needs?company_id=1',data,self.manager).status_code,400)
        self.assertEqual(self.post(f'data/needs/{self.need}?company_id=1',dict(status='approved'),self.manager,'PUT').status_code,400)
        self.assertEqual(self.post(f'holding/needs/{self.need}/decision',dict(status='approved',decision='No',version=1),self.manager).status_code,403)
    def test_decision_history_lock_reopen_and_conflict(self):
        self.assertEqual(self.decide().status_code,200)
        self.assertEqual(self.decide('rejected').status_code,409)
        self.assertEqual(self.post(f'data/needs/{self.need}?company_id=1',dict(reason='Changed'),self.manager,'PUT').status_code,409)
        self.assertEqual(self.decide('submitted',2).status_code,200)
        self.assertEqual(self.post(f'data/needs/{self.need}?company_id=1',dict(reason='Revised'),self.manager,'PUT').status_code,200)
        self.assertEqual(self.decide('approved',3).status_code,409)
        self.assertEqual(self.decide('approved',4).status_code,200)
        history=self.admin.get(f'/api/development-plans/{self.need}?company_id=1').json['history']
        self.assertEqual(len(history),3)
    def test_internal_training_and_cross_company_reference(self):
        data=dict(skill_id=self.sid,title='Internal',date='1405/08/20',hours=2)
        response=self.post('data/courses?company_id=1',data,self.manager)
        self.assertEqual(response.status_code,201)
        self.assertEqual(self.manager.get('/api/calendar?company_id=1&scope=internal').json[0]['calendar_scope'],'internal')
        self.assertEqual(self.post('data/courses?company_id=1',dict(data,calendar_scope='holding'),self.manager).status_code,403)
        self.assertEqual(self.post('data/courses?company_id=2',data).status_code,400)
    def test_plan_requires_approval_evidence_and_version(self):
        path=f'development-plans/{self.need}?company_id=1'
        data=dict(objective='Improve skill',method='Coaching',owner='HR',due_date='1405/12/01',success_criterion='score >= 80',baseline=30,target=80,version=0)
        self.assertEqual(self.post(path,data,method='PUT').status_code,409)
        self.decide()
        self.assertEqual(self.post(path,dict(data,actual=90),method='PUT').status_code,400)
        response=self.post(path,data,method='PUT');self.assertEqual(response.status_code,200,response.json)
        self.assertEqual(self.post(path,data,method='PUT').status_code,409)
        self.assertEqual(self.post(path,dict(data,version=1,actual=90,evidence='Observed assessment',review_date='1405/12/02'),method='PUT').status_code,200)
        self.assertEqual(self.manager.get(f'/api/development-plans/{self.need}?company_id=2').status_code,403)
        self.assertEqual(self.post(path,data,self.manager,'PUT').status_code,403)
    def test_csrf_and_anonymous_access(self):
        self.assertEqual(self.app.test_client().get('/api/holding/gaps').status_code,401)
        self.assertEqual(self.admin.post(f'/api/holding/needs/{self.need}/decision',json={}).status_code,403)
    def test_viewer_cannot_write(self):
        viewer=self.app.test_client();viewer.post('/api/login',json=dict(username='viewer',password='test-password-12345'))
        csrf=viewer.get('/api/me').json['csrf']
        response=viewer.post('/api/data/needs?company_id=1',json=dict(skill_id=self.sid,reason='No',year=1405),headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status_code,403)
    def test_excel_cannot_approve_or_overwrite_reviewed_need(self):
        import openpyxl
        from app.excel_forms import DATA_SHEET
        payload=self.manager.get('/api/excel/templates/needs?company_id=1').data
        workbook=openpyxl.load_workbook(io.BytesIO(payload))
        sheet=workbook[DATA_SHEET]
        def upload(values):
            # Reload the branded file: openpyxl consumes embedded-image streams
            # when saving, while each real upload is a separate completed file.
            filled=openpyxl.load_workbook(io.BytesIO(payload));data_sheet=filled[DATA_SHEET]
            for col,key in enumerate([cell.value for cell in data_sheet[5]],1):data_sheet.cell(7,col,values.get(key,''))
            stream=io.BytesIO();filled.save(stream);filled.close();stream.seek(0)
            return self.manager.post('/api/excel/import/needs?company_id=1',data={'file':(stream,'needs.xlsx'),'mode':'commit','policy':'upsert'},headers={'X-CSRF-Token':self.mcsrf})
        values=dict(skill_code='TEST-X',department='',reason='Gap',priority=2,year=1405,status='approved')
        self.assertEqual(upload(values).status_code,400)
        self.decide()
        values['status']='submitted'
        self.assertEqual(upload(values).status_code,400)
        self.assertEqual(self.admin.get('/api/holding/needs?year=1405').json[0]['status'],'approved')
        workbook.close()
    def test_latest_assessment_overrides_old_training_peak(self):
        eid=self.post('data/employees?company_id=1',dict(code='01',name='Employee',job='Engineer')).json['id']
        self.post('data/requirements?company_id=1',dict(job='Engineer',skill_id=self.sid,level=4))
        course=self.post('data/courses?company_id=1',dict(skill_id=self.sid,title='Course',date='1405/01/01',hours=2,status='completed')).json['id']
        self.post('data/records?company_id=1',dict(employee_id=eid,course_id=course,level=4))
        self.post('data/assessments?company_id=1',dict(employee_id=eid,skill_id=self.sid,level=1))
        gaps=self.admin.get('/api/gaps?company_id=1').json
        self.assertEqual(gaps[0]['gap'],3)
        passport=self.admin.get(f'/api/passports/{eid}?company_id=1');self.assertEqual(passport.status_code,200)
        self.assertEqual(self.admin.get('/api/holding/gaps').json[0]['gap_total'],3)
    def test_upgrade_idempotent_and_database_integrity(self):
        create_app(self.temp.name,testing=True)
        with closing(sqlite3.connect(Path(self.temp.name)/'nikan.sqlite3')) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(),[])
            self.assertEqual(c.execute('SELECT COUNT(*) FROM needs WHERE id=?',(self.need,)).fetchone()[0],1)

if __name__=='__main__':unittest.main(verbosity=2)
