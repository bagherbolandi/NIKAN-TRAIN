"""Run the actual isolated test entry point, leaving an old installation intact."""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request


def test_actual_test_desktop_does_not_open_or_modify_legacy_data(tmp_path):
    repository=Path(__file__).resolve().parents[1]
    local=tmp_path/'local';old=local/'NikanTraining';old.mkdir(parents=True)
    original=old/'nikan.sqlite3'
    with sqlite3.connect(original) as conn:
        conn.execute('CREATE TABLE original_private_facts(value TEXT)')
        conn.execute("INSERT INTO original_private_facts VALUES ('Original deployment must stay unchanged')")
    before=hashlib.sha256(original.read_bytes()).hexdigest()
    endpoint=local/'NikanHRM-Test/desktop-endpoint.json'
    with (tmp_path/'desktop.log').open('w+') as log:
        process=subprocess.Popen([sys.executable,'-I',str(repository/'desktop_test.py')],
            cwd=repository,env={**os.environ,'LOCALAPPDATA':str(local),'NIKAN_HEADLESS_DESKTOP':'1'},
            stdout=log,stderr=subprocess.STDOUT)
        try:
            deadline=time.monotonic()+25
            while not endpoint.exists():
                if process.poll() is not None or time.monotonic()>deadline:
                    log.seek(0)
                    raise AssertionError(log.read()[-5000:])
                time.sleep(.05)
            base=json.loads(endpoint.read_text())['url']
            assert base.startswith('http://127.0.0.1:')
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),
                       urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
            def get(path):
                with opener.open(base+'/api/'+path,timeout=5) as response:return json.load(response)
            def post(path,body,csrf=''):
                request=urllib.request.Request(base+'/api/'+path,data=json.dumps(body).encode(),
                    headers={'Content-Type':'application/json','X-CSRF-Token':csrf})
                with opener.open(request,timeout=5) as response:return json.load(response)
            assert get('status')['setup_required']
            credentials={'username':'isolated-admin','password':'Isolated-test-password-123'}
            post('setup',credentials);post('login',credentials)
            me=get('me')
            assert me['deployment_mode']=='test' and not me['network']
            assert me['hrm']['is_primary_admin']
            assert len(me['companies'])==7
            assert hashlib.sha256(original.read_bytes()).hexdigest()==before
            post('desktop-exit',{},me['csrf'])
            assert process.wait(timeout=10)==0
            assert not endpoint.exists()
            assert hashlib.sha256(original.read_bytes()).hexdigest()==before
            assert (local/'NikanHRM-Test/nikan.sqlite3').exists()
        finally:
            if process.poll() is None:
                process.terminate();process.wait(timeout=5)
