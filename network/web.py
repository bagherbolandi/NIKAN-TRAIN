"""Administrative backup management; no live database overwrite through HTTP."""
from pathlib import Path
from flask import request,jsonify,g,send_file
from cryptography import x509
from .backup import BackupManager

def register(app,db,authenticated,audit,error,root,config):
 manager=BackupManager(root);app.extensions['network_backup']=manager
 app.config.update(NETWORK=True,SESSION_COOKIE_SECURE=True,TRUSTED_HOSTS=config['allowed_hosts'])
 @app.before_request
 def require_tls():
  if not request.is_secure:return error('اتصال شبکه باید از HTTPS استفاده کند',403)
 def admin():
  if g.user['role']!='admin':raise PermissionError()
 @app.route('/api/network/settings',methods=['GET','PUT'])
 @authenticated
 def network_settings():
  admin()
  if request.method=='PUT':manager.configure(request.get_json());audit('backup_configuration_changed');db().commit()
  cert=x509.load_pem_x509_certificate(Path(config['certificate']).read_bytes())
  return jsonify(backup=manager.settings(),url=f"https://{config['hostname']}:{config['port']}",certificate_expires=cert.not_valid_after_utc.isoformat(),encrypted=True)
 @app.get('/api/network/backups')
 @authenticated
 def network_backups():admin();return jsonify(files=manager.inventory(),status=manager.state())
 @app.post('/api/network/backups')
 @authenticated
 def network_backup():
  admin();manager.background(manager.run);audit('encrypted_backup_requested');db().commit();return jsonify(ok=True),202
 @app.post('/api/network/verify')
 @authenticated
 def network_verify():
  admin();name=(request.get_json() or {}).get('name');manager.selected(name);manager.background(manager.verify,name);audit('recovery_test_requested',detail=name);db().commit();return jsonify(ok=True),202
 @app.get('/api/network/backups/<name>')
 @authenticated
 def network_download(name):
  admin();return send_file(manager.selected(name),as_attachment=True,download_name=name,mimetype='application/octet-stream')
 @app.get('/api/network/recovery-key')
 @authenticated
 def network_recovery_key():
  admin();audit('recovery_key_downloaded');db().commit();return send_file(Path(root)/'keys'/'recovery.key',as_attachment=True,download_name='Nikan-Recovery.key',mimetype='application/octet-stream')
 @app.get('/api/network/certificate')
 @authenticated
 def network_certificate():admin();return send_file(config['certificate'],as_attachment=True,download_name='Nikan-Server.pem',mimetype='application/x-pem-file')
 return manager
