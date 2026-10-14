"""Read NSIS options without interpolating user-provided paths into shell commands."""
import argparse,configparser,sys
from pathlib import Path
BASE=Path(__file__).resolve().parent
if str(BASE) not in sys.path:sys.path.insert(0,str(BASE))
from network.config import initialize
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--ini',type=Path);p.add_argument('--data-dir',type=Path,required=True);p.add_argument('--wait',action='store_true');a=p.parse_args()
 if a.wait:
  import ssl,urllib.request,time
  from network.config import load
  config=load(a.data_dir);context=ssl.create_default_context(cafile=config['certificate']);opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=context));deadline=time.monotonic()+45
  while True:
   try:
    with opener.open(f"https://localhost:{config['port']}/api/status",timeout=3) as response:
     if response.status==200:break
   except OSError:
    if time.monotonic()>deadline:raise RuntimeError('HTTPS server did not become ready; inspect server.log and Task Scheduler')
    time.sleep(.2)
  sys.exit(0)
 if not a.ini:p.error('--ini is required when initializing')
 ini=configparser.ConfigParser(interpolation=None);ini.read(a.ini,encoding='utf-16');c=ini['server']
 config=initialize(a.data_dir,c.get('hostname'),int(c.get('port','8766')),c.get('mirror',''))
 out=configparser.ConfigParser(interpolation=None);out['server']={'hostname':config['hostname'],'port':str(config['port'])}
 with (BASE/'verified-install.ini').open('w',encoding='utf-16') as f:out.write(f)
