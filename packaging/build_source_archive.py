"""Package current source and the verified installer without local databases."""
from pathlib import Path
import zipfile,hashlib
ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
 files=[ROOT/p for p in ['README.md','VALIDATION.md','requirements.txt','requirements-dev.txt','requirements-network.txt','NETWORK-README.md','network_server.py','server_setup.py','recovery.py','run.py','desktop.py','stop_desktop.py','Install-Windows.cmd','Start-Nikan.cmd','.gitignore']]
 files+=list(ROOT.glob('*.xlsx'))
 files += [p for folder in ['app','network','tests','packaging','docs','.github'] for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
 installer=ROOT/'distribution'/'NikanTraining-Setup.exe';checksum=ROOT/'distribution'/'NikanTraining-Setup.exe.sha256'
 assert hashlib.sha256(installer.read_bytes()).hexdigest()==checksum.read_text().split()[0]
 out=ROOT/'distribution'/'NikanTraining-Windows.zip'
 with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
  for p in files:z.write(p,Path('NikanTraining')/p.relative_to(ROOT))
  for p in [installer,checksum]+list((ROOT/'distribution').glob('*Network*Setup.exe*')):
   if p.suffix=='.exe':assert hashlib.sha256(p.read_bytes()).hexdigest()==p.with_suffix('.exe.sha256').read_text().split()[0]
   z.write(p,'NikanTraining/'+p.name)
 with zipfile.ZipFile(out) as z:
  assert z.testzip() is None
  assert not any(n.endswith(('.sqlite3','.key')) for n in z.namelist())
 (out.parent/'NikanTraining-Windows.sha256').write_text(hashlib.sha256(out.read_bytes()).hexdigest()+'  '+out.name+'\n')
 print('Archive verified:',out.stat().st_size,'bytes')
