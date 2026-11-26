"""Checksum-guarded rollback to the captured dirty working tree; dry-run by default.

Keeps documentation, measurements, existing user changes and Git history.
New source files are moved to quarantine, never deleted. Any subsequent edit
to a touched source file causes refusal, not an overwrite.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rollback(apply=False):
    manifest = json.loads((ROOT/'docs/identity_phase01/change_manifest.json').read_text())
    original = ROOT.parent/'validation/identity_phase01_baseline/config.py'
    if sha(original) != manifest['config_before_sha256']:
        raise RuntimeError('Baseline config checksum mismatch')
    paths = []
    for relative, expected in manifest['source_after_sha256'].items():
        path = ROOT/relative
        if path.resolve().is_relative_to(ROOT) is False:
            raise RuntimeError('Invalid manifest path')
        if not path.is_file() or sha(path) != expected:
            raise RuntimeError('File changed since implementation; preserve and review manually: '+relative)
        paths.append((relative,path))
    result = dict(mode='apply' if apply else 'dry_run', restore=['config.py'],
                  quarantine=[r for r,p in paths if r != 'config.py'],
                  preserve='all existing user changes, docs, logs, caches and Git history')
    if apply:
        destination = ROOT.parent/'validation'/('identity_phase01_quarantine_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        destination.mkdir()
        shutil.copy2(ROOT/'config.py',destination/'config_after.py')
        # Restore only the one existing file modified by this task.
        with tempfile.NamedTemporaryFile(dir=ROOT,delete=False) as f:
            temporary = Path(f.name)
            f.write(original.read_bytes())
        shutil.copymode(ROOT/'config.py',temporary)
        temporary.replace(ROOT/'config.py')
        for relative,path in paths:
            if relative != 'config.py':
                target = destination/relative
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.move(str(path),str(target))
        result['quarantine_directory'] = str(destination)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args = parser.parse_args()
    print(json.dumps(rollback(args.apply),indent=2))
