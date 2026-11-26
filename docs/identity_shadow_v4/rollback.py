"""Guarded v4 rollback; dry-run unless --apply. Preserve earlier dirty changes."""
import argparse,json,hashlib,shutil,tempfile
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]
BACKUP=ROOT.parent/'validation/identity_shadow_v4_before'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def rollback(apply=False):
 manifest=json.loads((ROOT/'docs/identity_shadow_v4/changes.json').read_text())
 for name,state in manifest.items():
  p=ROOT/name
  if not p.resolve().is_relative_to(ROOT) or not p.is_file() or sha(p)!=state['after']:
   raise RuntimeError('Source changed after v4; review manually: '+name)
  if state['before'] is not None and sha(BACKUP/name)!=state['before']:
   raise RuntimeError('Backup checksum mismatch: '+name)
 result=dict(mode='apply' if apply else 'dry_run',restore=[n for n,s in manifest.items() if s['before'] is not None],quarantine=[n for n,s in manifest.items() if s['before'] is None])
 if apply:
  dest=ROOT.parent/'validation'/('identity_shadow_v4_quarantine_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));dest.mkdir()
  for name,state in manifest.items():
   p=ROOT/name;copy=dest/name;copy.parent.mkdir(parents=True,exist_ok=True)
   if state['before'] is None:shutil.move(str(p),str(copy))
   else:
    shutil.copy2(p,copy)
    with tempfile.NamedTemporaryFile(dir=p.parent,delete=False) as f:
     tmp=Path(f.name);f.write((BACKUP/name).read_bytes())
    shutil.copymode(p,tmp);tmp.replace(p)
  result['quarantine']=str(dest)
 return result
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');args=p.parse_args()
 print(json.dumps(rollback(args.apply),indent=2))
