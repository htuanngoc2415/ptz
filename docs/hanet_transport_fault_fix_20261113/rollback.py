#!/usr/bin/env python3
"""Restore the source checkpoint. Dry run by default; never touches motor state/logs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    directory = Path(__file__).resolve().parent
    root = directory.parents[1]
    manifest = json.loads((directory/'change_manifest.json').read_text())
    files = manifest['modified']+manifest['added']
    mismatches = [name for name in files if not (root/name).is_file()
                  or hashlib.sha256((root/name).read_bytes()).hexdigest() != manifest['sha256'][name]]
    if mismatches:
        raise SystemExit('Refusing to overwrite later edits/missing files: '+', '.join(mismatches))
    originals = {name: subprocess.check_output(['git', '-C', manifest['checkpoint_path'],
                 'show', manifest['checkpoint']+':'+name]) for name in manifest['modified']}
    for name in manifest['modified']:
        print(('RESTORE ' if args.apply else 'Would restore ')+name)
        if args.apply:
            (root/name).write_bytes(originals[name])
    for name in manifest['added']:
        print(('REMOVE ' if args.apply else 'Would remove ')+name)
        if args.apply:
            (root/name).unlink()
    print('Motor checkpoints, logs, credentials, tracking configuration and reports are preserved.')


if __name__ == '__main__':
    main()
