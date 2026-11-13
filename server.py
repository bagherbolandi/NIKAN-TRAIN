"""Windows Server entry point. Initialization is loopback-only; no default account."""
from contextlib import closing
import argparse
import json
import logging
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from app import create_app
from waitress import serve

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--initialize',action='store_true')
    parser.add_argument('--backup',action='store_true')
    parser.add_argument('--check-ready',action='store_true')
    parser.add_argument('--data-dir',default=str(Path(os.environ.get('PROGRAMDATA',str(Path.home())))/'NikanTrainingServer'))
    args=parser.parse_args()
    root=Path(args.data_dir);root.mkdir(parents=True,exist_ok=True)
    logging.basicConfig(filename=root/'server.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    config_path=root/'server.json'
    config=json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.exists() else dict(host='0.0.0.0',port=8766,https=False)
    app=create_app(root)
    app.config['DEPLOYMENT_MODE']='server'
    app.config['SESSION_COOKIE_SECURE']=bool(config.get('https',False)) and not args.initialize
    app.config['ALLOW_SETUP']=args.initialize
    if config.get('https') and not args.initialize:
        if config.get('host') not in ('127.0.0.1','::1'):
            raise SystemExit('HTTPS proxy mode requires a loopback backend host')
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app=ProxyFix(app.wsgi_app,x_for=0,x_proto=1,x_host=0,x_port=0,x_prefix=0)
    if args.backup:
        backup_dir=root/'backups';backup_dir.mkdir(exist_ok=True)
        target=backup_dir/('nikan-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
        with closing(sqlite3.connect(app.config['DB'])) as source,closing(sqlite3.connect(target)) as destination:source.backup(destination)
        print(target);return
    if not args.initialize:
        with closing(sqlite3.connect(app.config['DB'])) as connection:
            if not connection.execute('SELECT 1 FROM users WHERE role="admin" AND active=1').fetchone():
                raise SystemExit('Initialize an administrator locally first: server.py --initialize')
    if args.check_ready:
        print('Administrator and database are ready.');return
    if not config_path.exists():config_path.write_text(json.dumps(config,indent=2),encoding='utf-8')
    port=int(config.get('port',8766))
    if not 1024<=port<=65535:raise SystemExit('Port must be between 1024 and 65535')
    host='127.0.0.1' if args.initialize else config.get('host','0.0.0.0')
    if args.initialize:threading.Timer(1,lambda:webbrowser.open(f'http://127.0.0.1:{port}')).start()
    print(f'Nikan Training 0.11.0: {host}:{port}',flush=True)
    serve(app,host=host,port=port,threads=8,channel_timeout=60,max_request_body_size=16*1024*1024)

if __name__=='__main__':
    try:main()
    except Exception:
        logging.exception('Server startup or runtime failure')
        raise
