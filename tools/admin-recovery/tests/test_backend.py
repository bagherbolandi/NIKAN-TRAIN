import os, sys, tempfile
sys.path.insert(0,'/tmp/nikan/pylibs')
sys.path.insert(0,'/tmp/nikan/testapp')
os.environ['NIKAN_DATA_DIR']=tempfile.mkdtemp(prefix='nikan_test_')
from app import create_app
app=create_app(testing=True)
c=app.test_client()
def show(label,r):
    try: body=r.get_json()
    except Exception: body=r.data[:200]
    print(f'{label:24s} {r.status_code} {body}')
show('status', c.get('/api/status'))
r=c.post('/api/setup',json={'username':'adminuser','password':'StrongPass12345'},environ_base={'REMOTE_ADDR':'127.0.0.1'})
show('setup',r); key=r.get_json()['recovery_key']
show('login old', c.post('/api/login',json={'username':'adminuser','password':'StrongPass12345'}))
r=c.get('/api/me'); show('me',r); csrf=r.get_json()['csrf']
r=c.post('/api/recovery-key',headers={'X-CSRF-Token':csrf}); show('issue-key',r); key2=r.get_json()['recovery_key']
show('logout', c.post('/api/logout',headers={'X-CSRF-Token':csrf}))
show('recover invalidated', c.post('/api/recover',json={'username':'adminuser','recovery_key':key,'password':'NewPass123456'}))
show('recover bad-pass', c.post('/api/recover',json={'username':'adminuser','recovery_key':key2,'password':'short'}))
show('recover good', c.post('/api/recover',json={'username':'adminuser','recovery_key':key2,'password':'NewPass123456'}))
show('login new', c.post('/api/login',json={'username':'adminuser','password':'NewPass123456'}))
show('reuse key', c.post('/api/recover',json={'username':'adminuser','recovery_key':key2,'password':'AnotherPass123'}))
show('login old again', c.post('/api/login',json={'username':'adminuser','password':'StrongPass12345'}))
