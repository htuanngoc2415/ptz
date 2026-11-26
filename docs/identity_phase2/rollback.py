"""Checksum-guarded Phase 2 rollback. Default dry-run; retain all evidence."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT=Path(__file__).resolve().parents[2]
BACKUP=ROOT.parent/'validation/identity_phase2_before'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def rollback(apply=False):
    manifest=json.loads((ROOT/'docs/identity_phase2/changes.json').read_text())
    for name,state in manifest.items():
        p=ROOT/name
        if not p.resolve().is_relative_to(ROOT) or not p.is_file() or sha(p)!=state['after']:
            raise RuntimeError('Refuse to overwrite later edits: '+name)
        if state['before'] is not None and sha(BACKUP/state['backup'])!=state['before']:
            raise RuntimeError('Backup checksum mismatch: '+name)
    result=dict(mode='apply' if apply else 'dry_run',
        restore=[n for n,s in manifest.items() if s['before'] is not None],
        quarantine=[n for n,s in manifest.items() if s['before'] is None])
    if apply:
        dest=ROOT.parent/'validation'/('identity_phase2_quarantine_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        dest.mkdir()
        for name,state in manifest.items():
            p=ROOT/name;copy=dest/name;copy.parent.mkdir(parents=True,exist_ok=True)
            if state['before'] is None:
                shutil.move(str(p),str(copy))
            else:
                shutil.copy2(p,copy)
                with tempfile.NamedTemporaryFile(dir=p.parent,delete=False) as f:
                    temp=Path(f.name);f.write((BACKUP/state['backup']).read_bytes())
                shutil.copymode(p,temp);temp.replace(p)
        result['quarantine_directory']=str(dest)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true')
    print(json.dumps(rollback(p.parse_args().apply),indent=2))
