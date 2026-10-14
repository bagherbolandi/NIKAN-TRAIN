"""Central HTTPS server. Run on one Windows host; clients use their browsers."""
import argparse,logging,os,sys,signal,threading
from pathlib import Path
BASE=Path(__file__).resolve().parent
# The Windows bundled interpreter is launched with -I.
if str(BASE) not in sys.path:sys.path.insert(0,str(BASE))
from logging.handlers import RotatingFileHandler
from network.storage import FileLock
from network.config import initialize,load

def run(root):
 from app import create_app
 from cheroot.wsgi import Server
 from cheroot.ssl.builtin import BuiltinSSLAdapter
 root=Path(root)
 with FileLock(root/'server.lock'):
  config=load(root);app=create_app(root)
  # Register after the core routes, reusing their authentication and audit functions.
  app.extensions['register_network'](root,config)
  manager=app.extensions['network_backup']
  log=RotatingFileHandler(root/'server.log',maxBytes=2*1024*1024,backupCount=5,encoding='utf-8')
  log.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'));logging.getLogger().addHandler(log);logging.getLogger().setLevel(logging.INFO)
  server=Server((config['host'],config['port']),app,numthreads=config.get('threads',16),request_queue_size=100,timeout=30)
  adapter=BuiltinSSLAdapter(config['certificate'],config['private_key'])
  import ssl
  adapter.context.minimum_version=ssl.TLSVersion.TLSv1_2
  server.ssl_adapter=adapter
  def stop(*_):threading.Thread(target=server.stop,daemon=True).start()
  signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
  # A scheduled task can be stopped gracefully by writing this local control marker.
  marker=root/'stop.request';marker.unlink(missing_ok=True)
  ended=threading.Event()
  def control():
   while not ended.wait(1):
    if marker.exists():stop();return
  threading.Thread(target=control,daemon=True).start()
  manager.start()
  try:server.start()
  finally:ended.set();manager.close();log.close();logging.getLogger().removeHandler(log)

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('PROGRAMDATA',str(Path.home()))) / 'NikanTrainingServer');parser.add_argument('--initialize',action='store_true');parser.add_argument('--hostname');parser.add_argument('--port',type=int,default=8766);parser.add_argument('--mirror',default='');parser.add_argument('--stop',action='store_true');args=parser.parse_args()
 if args.initialize:initialize(args.data_dir,args.hostname,args.port,args.mirror)
 elif args.stop:
  from network.storage import atomic_write
  atomic_write(args.data_dir/'stop.request',b'stop')
 else:run(args.data_dir)
