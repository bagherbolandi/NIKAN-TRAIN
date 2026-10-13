"""Run with the bundled Windows interpreter after installation."""
import sys,tempfile,threading,urllib.request,json
from pathlib import Path
base=Path(__file__).resolve().parent
sys.path.insert(0,str(base if (base/'app').exists() else base.parent))
from app import create_app
from waitress import create_server
with tempfile.TemporaryDirectory() as root:
 app=create_app(root,True)
 c=app.test_client();assert c.get('/api/status').json['setup_required']
 assert len(c.post('/api/setup',json={'username':'validation-admin','password':'Test-only-password-123'}).json)>0
 assert c.post('/api/login',json={'username':'validation-admin','password':'Test-only-password-123'}).status_code==200
 me=c.get('/api/me').json;assert len(me['companies'])==6
 assert len(c.get('/api/competency-bank').json)==281
 h={'X-CSRF-Token':me['csrf']}
 assert c.post('/api/competency-bank/adopt?company_id=1',headers=h).status_code==200
 assert c.get('/api/data/job_profiles?company_id=1').json
 assert c.get('/api/dashboard?company_id=2').json['employees']==0
 server=create_server(app,host='127.0.0.1',port=0);thread=threading.Thread(target=server.run,daemon=True);thread.start()
 try:
  with urllib.request.urlopen(f'http://127.0.0.1:{server.effective_port}/api/status') as response:assert response.status==200
 finally:server.task_dispatcher.shutdown();server.close();thread.join(timeout=3)
print('Payload validation passed:',sys.platform,sys.version.split()[0])
