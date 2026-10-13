"""Windows desktop entry point with an isolated bundled interpreter."""
import ctypes,os,sys,threading,webbrowser,subprocess,logging,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from app import create_app
from waitress import create_server

def open_window(url,data):
 paths=[Path(os.environ.get(k,''))/'Microsoft'/'Edge'/'Application'/'msedge.exe' for k in ['ProgramFiles(x86)','ProgramFiles','LOCALAPPDATA']]
 edge=next((p for p in paths if p.exists()),None)
 if edge:return subprocess.Popen([str(edge),'--app='+url,'--user-data-dir='+str(data/'browser-profile'),'--no-first-run'])
 webbrowser.open(url);return None

def main():
 data=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'NikanTraining';data.mkdir(parents=True,exist_ok=True)
 logging.basicConfig(filename=data/'desktop.log',level=logging.WARNING)
 endpoint=data/'desktop-endpoint.json';mutex=None
 if os.name=='nt':
  kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.restype=ctypes.c_void_p;kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
  mutex=kernel.CreateMutexW(None,False,'Local\\NikanTrainingDesktop')
  if ctypes.get_last_error()==183:
   try:
    url=json.loads(endpoint.read_text())['url']
    if not url.startswith('http://127.0.0.1:'):raise ValueError('Invalid endpoint')
    open_window(url,data)
   except Exception:ctypes.windll.user32.MessageBoxW(None,'برنامه نیکان در حال اجرا یا راه‌اندازی است. لحظه‌ای بعد دوباره کلیک کنید.','نیکان',0)
   kernel.CloseHandle(ctypes.c_void_p(mutex));return
 os.environ['NIKAN_DATA_DIR']=str(data)
 app=create_app();server=create_server(app,host='127.0.0.1',port=0,threads=6)
 url=f'http://127.0.0.1:{server.effective_port}';stop=threading.Event();app.config['DESKTOP_STOP']=stop.set
 thread=threading.Thread(target=server.run,daemon=True);thread.start()
 endpoint.write_text(json.dumps({'url':url}),encoding='utf-8')
 try:
  if not os.environ.get('NIKAN_HEADLESS_DESKTOP'):
   process=open_window(url,data)
   if process:
    def watch():process.wait();stop.set()
    threading.Thread(target=watch,daemon=True).start()
  stop.wait()
 finally:
  server.task_dispatcher.shutdown();server.close();endpoint.unlink(missing_ok=True)
  if mutex:ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(mutex))
if __name__=='__main__':
 try:main()
 except Exception:
  logging.exception('Desktop startup failed')
  if os.name=='nt':ctypes.windll.user32.MessageBoxW(None,'اجرای برنامه ناموفق بود. فایل desktop.log در پوشه داده را بررسی کنید.','نیکان',0x10)
  else:raise
