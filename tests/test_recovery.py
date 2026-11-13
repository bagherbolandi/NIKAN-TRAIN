import hashlib,sqlite3,time,unittest
from contextlib import closing
from pathlib import Path
import test_governance as fixtures
from app.recovery import issue

class RecoveryTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.GovernanceTests.setUpClass.__func__)
    tearDownClass=classmethod(fixtures.GovernanceTests.tearDownClass.__func__)
    setUp=fixtures.GovernanceTests.setUp
    tearDown=fixtures.GovernanceTests.tearDown
    post=fixtures.GovernanceTests.post
    def code(self,invalidate=False):
        with closing(sqlite3.connect(Path(self.temp.name)/'nikan.sqlite3')) as c:return issue(c,'admin',invalidate)
    def reset(self,code,password='new-password-12345',username='admin'):
        return self.app.test_client().post('/api/password-recovery',json=dict(username=username,code=code,password=password))
    def test_reset_one_use_and_session_revocation(self):
        code=self.code();self.assertEqual(self.reset(code).status_code,200)
        self.assertEqual(self.reset(code).status_code,400)
        self.assertEqual(self.admin.get('/api/me').status_code,401)
        client=self.app.test_client()
        self.assertEqual(client.post('/api/login',json=dict(username='admin',password='test-password-12345')).status_code,401)
        self.assertEqual(client.post('/api/login',json=dict(username='admin',password='new-password-12345')).status_code,200)
    def test_secret_is_hashed_and_account_bound(self):
        code=self.code()
        with closing(sqlite3.connect(Path(self.temp.name)/'nikan.sqlite3')) as c:
            self.assertEqual(c.execute('SELECT digest FROM password_recovery').fetchone()[0],hashlib.sha256(code.encode()).hexdigest())
        self.assertEqual(self.reset(code,username='manager').status_code,400)
        self.assertEqual(self.reset(code).status_code,200)
    def test_expiry_and_reissue(self):
        code=self.code()
        with closing(sqlite3.connect(Path(self.temp.name)/'nikan.sqlite3')) as c:
            c.execute('UPDATE password_recovery SET expires=?',(time.time()-1,));c.commit()
        self.assertEqual(self.reset(code).status_code,400)
        newer=self.code();self.assertEqual(self.reset(code).status_code,400);self.assertEqual(self.reset(newer).status_code,200)
    def test_wrong_code_limit(self):
        code=self.code()
        for _ in range(5):self.assertEqual(self.reset('wrong').status_code,400)
        self.assertEqual(self.reset(code).status_code,400)
    def test_explicit_reset_invalidates_existing_password(self):
        code=self.code(True)
        self.assertEqual(self.admin.get('/api/me').status_code,401)
        self.assertEqual(self.app.test_client().post('/api/login',json=dict(username='admin',password='test-password-12345')).status_code,401)
        self.assertEqual(self.reset(code).status_code,200)
    def test_weak_password_does_not_consume_code(self):
        code=self.code();self.assertEqual(self.reset(code,password='short').status_code,400)
        self.assertEqual(self.reset(code).status_code,200)
    def test_company_admin_reset_revokes_session_and_recovery(self):
        with closing(sqlite3.connect(Path(self.temp.name)/'nikan.sqlite3')) as c:code=issue(c,'manager')
        users=self.admin.get('/api/users').json;uid=next(u['id'] for u in users if u['username']=='manager')
        self.assertEqual(self.post(f'users/{uid}',dict(password='managed-password-123'),method='PUT').status_code,200)
        self.assertEqual(self.manager.get('/api/me').status_code,401)
        self.assertEqual(self.reset(code,username='manager').status_code,400)

if __name__=='__main__':unittest.main(verbosity=2)
