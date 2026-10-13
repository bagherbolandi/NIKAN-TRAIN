"""Build a real offline Windows installer on Linux or Windows with NSIS."""
import argparse,base64,csv,hashlib,io,json,os,shutil,subprocess,sys,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PYTHON_URL='https://api.nuget.org/v3-flatcontainer/python/3.13.16/python.3.13.16.nupkg'
PYTHON_SHA512='TGEhuRTpWtRifDR4r/GEhmg/cGMqtwB4kOCkt1QhXnCmdDE3Dict3iLJI1xrsNSRbXGyCwLEhe6qa5Rd2s376w=='
def verify_runtime(path):
 if base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()!=PYTHON_SHA512:raise RuntimeError('Official CPython package checksum mismatch')
def stage(cache,payload):
 runtime=cache/'python.3.13.16.nupkg'
 if not runtime.exists():
  with urllib.request.urlopen(PYTHON_URL,timeout=90) as response:runtime.write_bytes(response.read())
 verify_runtime(runtime)
 wheels=cache/'wheels-cp313';wheels.mkdir(exist_ok=True)
 subprocess.run([sys.executable,'-m','pip','download','-r',str(ROOT/'requirements.txt'),'--dest',str(wheels),'--platform','win_amd64','--python-version','313','--implementation','cp','--abi','cp313','--only-binary=:all:','colorama==0.4.6'],check=True)
 if payload.exists():shutil.rmtree(payload)
 (payload/'runtime').mkdir(parents=True)
 with zipfile.ZipFile(runtime) as z:
  for name in z.namelist():
   if name.startswith('tools/') and not name.endswith('/'):
    target=payload/'runtime'/name[6:];target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
 site=payload/'runtime'/'Lib'/'site-packages';site.mkdir(parents=True,exist_ok=True)
 for wheel in wheels.glob('*.whl'):
  with zipfile.ZipFile(wheel) as z:
   record=next(n for n in z.namelist() if n.endswith('.dist-info/RECORD'))
   for name,digest,size in csv.reader(io.StringIO(z.read(record).decode())):
    if digest:
     algorithm,expected=digest.split('=',1)
     actual=base64.urlsafe_b64encode(hashlib.new(algorithm,z.read(name)).digest()).decode().rstrip('=')
     if actual!=expected:raise RuntimeError('Wheel RECORD integrity failed: '+wheel.name)
   for name in z.namelist():
    if name.endswith('/'):continue
    if '..' in Path(name).parts or Path(name).is_absolute():raise RuntimeError('Unsafe wheel path')
    target=site/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(name))
 shutil.copytree(ROOT/'app',payload/'app',ignore=shutil.ignore_patterns('__pycache__'))
 for name in ['desktop.py','run.py','README.md','requirements.txt']:shutil.copy2(ROOT/name,payload/name)
 shutil.copy2(ROOT/'packaging'/'nikan.ico',payload/'nikan.ico')
 (payload/'resources').mkdir();shutil.copy2(next(ROOT.glob('*.xlsx')),payload/'resources'/'workbook.xlsx')
 manifest={'python':{'url':PYTHON_URL,'sha512_base64':PYTHON_SHA512},'wheels':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(wheels.glob('*.whl'))},'files':{str(p.relative_to(payload)):hashlib.sha256(p.read_bytes()).hexdigest() for p in payload.rglob('*') if p.is_file()}}
 (payload/'build-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 return manifest
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--cache',type=Path,default=ROOT.parent/'nikan-build-tools');p.add_argument('--makensis',default=os.environ.get('MAKENSIS','makensis'));args=p.parse_args();args.cache.mkdir(exist_ok=True,parents=True)
 payload=args.cache/'payload';manifest=stage(args.cache,payload);output=ROOT/'distribution'/'NikanTraining-Setup.exe';output.parent.mkdir(exist_ok=True)
 subprocess.run([args.makensis,('-D' if os.name!='nt' else '/D')+'PAYLOAD='+str(payload),('-D' if os.name!='nt' else '/D')+'OUTPUT='+str(output),str(ROOT/'packaging'/'installer.nsi')],cwd=ROOT/'packaging',check=True)
 if output.read_bytes()[:2]!=b'MZ':raise RuntimeError('Output is not a Windows executable')
 (output.parent/'NikanTraining-Setup.exe.sha256').write_text(hashlib.sha256(output.read_bytes()).hexdigest()+'  NikanTraining-Setup.exe\n')
 (output.parent/'build-manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
 print('Built installer:',output,output.stat().st_size,'bytes')
