from pathlib import Path
import os,stat,hashlib,json,shutil,subprocess,datetime
src=Path('/mnt/files/Project/ptz');stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
out=src.parent/('ptz_async_before_comprehensive_audit_'+stamp);out.mkdir(mode=0o700)
(out/'git_status.txt').write_text(subprocess.check_output(['git','status','--short','--ignored'],cwd=src/'ptz',text=True))
(out/'git_metadata.txt').write_text(subprocess.check_output(['git','log','-5','--format=fuller'],cwd=src/'ptz',text=True)+subprocess.check_output(['git','reflog','-15'],cwd=src/'ptz',text=True))
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
entries=[]
for p in sorted(src.rglob('*')):
 e=dict(path=str(p.relative_to(src)),mode=stat.S_IMODE(p.lstat().st_mode))
 if p.is_symlink():e['link']=os.readlink(p)
 elif p.is_file():e['sha256']=digest(p)
 elif not p.is_dir():raise RuntimeError('Unsupported file '+str(p))
 entries.append(e)
shutil.copytree(src,out/'project',symlinks=True)
for e in entries:
 p=out/'project'/e['path'];assert stat.S_IMODE(p.lstat().st_mode)==e['mode'],p
 if 'sha256' in e:assert digest(p)==e['sha256'],p
 if 'link' in e:assert os.readlink(p)==e['link'],p
(out/'manifest.json').write_text(json.dumps(dict(source=str(src),exclusions=[],verified=True,files=entries),indent=2))
Path('/tmp/ptz_comprehensive_backup_path').write_text(str(out))
print('VERIFIED',out,len(entries),flush=True)
