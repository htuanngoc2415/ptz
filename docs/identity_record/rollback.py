"""Restore pre-record source only; checksum-guarded, dry-run by default."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(apply=False):
    manifest = json.loads((ROOT/'docs/identity_record/changes.json').read_text())
    before = ROOT.parent/'validation/identity_record_before'
    for relative,h in manifest['after'].items():
        path = ROOT/relative
        if not path.resolve().is_relative_to(ROOT) or not path.is_file() or sha(path)!=h:
            raise RuntimeError('Subsequent edits/missing source; manual review required: '+relative)
    for relative,h in manifest['before'].items():
        if sha(before/relative)!=h:
            raise RuntimeError('Backup checksum mismatch: '+relative)
    plan = dict(apply=apply,restore=list(manifest['before']),quarantine=manifest['created'],
                preserve='captures, annotations, docs and previous Phase 1/user changes')
    if apply:
        archive = ROOT.parent/'validation'/('identity_record_quarantine_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        archive.mkdir()
        for relative in manifest['after']:
            dest=archive/relative;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(ROOT/relative,dest)
        for relative in manifest['before']:
            shutil.copy2(before/relative,ROOT/relative)
        for relative in manifest['created']:
            (ROOT/relative).unlink()  # exact checksum already archived above
        plan['archive']=str(archive)
    return plan


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true')
    print(json.dumps(run(p.parse_args().apply),indent=2))
