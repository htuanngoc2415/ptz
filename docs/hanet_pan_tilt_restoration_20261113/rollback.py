"""Hash-checked rollback, dry-run by default. Never clears motor checkpoints."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backend-only',action='store_true')
    args=parser.parse_args()
    directory=Path(__file__).resolve().parent;root=directory.parents[1]
    manifest=json.loads((directory/'change_manifest.json').read_text())
    modified=manifest['modified'];added=manifest['added']
    if args.backend_only:
        modified=[p for p in modified if p.startswith('camera/')]
        added=[p for p in added if p.startswith('camera/')]
    pending=list((root/'logs/camera/motor_state').glob('*.pending'))
    # The previous backend does not understand STOP guard files. Refuse to
    # silently lose their uncertainty during rollback. Custom state paths must
    # also be checked by the operator before using this repository helper.
    if pending:
        raise SystemExit('Refusing rollback: pending STOP guards exist; finish verified recovery with the current backend first. Do not delete guards.')
    mismatch=[p for p in modified+added if not (root/p).is_file()
              or hashlib.sha256((root/p).read_bytes()).hexdigest()!=manifest['sha256'][p]]
    if mismatch:raise SystemExit('Refusing later edits/missing files: '+', '.join(mismatch))
    originals={p:subprocess.check_output(['git','-C',manifest['checkpoint_path'],
        'show',manifest['checkpoint']+':'+p]) for p in modified}
    for p in modified:
        print(('RESTORE ' if args.apply else 'Would restore ')+p)
        if args.apply:(root/p).write_bytes(originals[p])
    for p in added:
        print(('REMOVE ' if args.apply else 'Would remove ')+p)
        if args.apply:(root/p).unlink()
    print('Tracking, configuration, models, logs and motor checkpoints are untouched.')


if __name__=='__main__':main()
