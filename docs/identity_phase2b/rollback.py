"""Default dry-run. Quarantine ONLY new Phase2B offline tools/tests after hash check."""
import argparse,json,shutil
from pathlib import Path
from datetime import datetime
import hashlib
root=Path(__file__).resolve().parents[2];here=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');a=p.parse_args()
manifest=json.loads((here/'source_integrity.json').read_text())['new_files']
for relative,h in manifest.items():
 f=root/relative
 if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest()!=h:raise SystemExit('Refuse changed/missing file: '+relative)
print(json.dumps(dict(mode='apply' if a.apply else 'dry_run',files=list(manifest),runtime_changes=0),indent=2))
if a.apply:
 dest=here/('quarantine_'+datetime.now().strftime('%Y%m%d_%H%M%S'));dest.mkdir(exist_ok=False)
 for relative in manifest:
  target=dest/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.move(str(root/relative),str(target))
