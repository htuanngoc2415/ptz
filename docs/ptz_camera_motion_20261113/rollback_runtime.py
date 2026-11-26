"""Guarded rollback of the two production hooks only; dry-run unless --apply.

Offline tools/modules and evidence remain available. Refuses to overwrite later
user edits. Does not start/stop the application or access camera/motor state.
"""
import argparse,hashlib,json,subprocess
from pathlib import Path

here=Path(__file__).resolve().parent;root=here.parents[1]
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');args=p.parse_args()
checkpoint=json.loads((here/'checkpoint.json').read_text())
after=json.loads((here/'after_checkpoint.json').read_text())
restore={}
for name in ('config.py','runtime/byte_tracking.py'):
    current=(root/name).read_bytes()
    digest=hashlib.sha256(current).hexdigest()
    if digest==checkpoint['source_sha256'][name]:continue
    if digest!=after['source_sha256'][name]:raise SystemExit('Refusing later edits: '+name)
    restore[name]=subprocess.check_output(['git','-C',checkpoint['path'],'show',checkpoint['before_commit']+':'+name])
    if hashlib.sha256(restore[name]).hexdigest()!=checkpoint['source_sha256'][name]:raise SystemExit('Checkpoint hash mismatch')
for name,data in restore.items():
    print(('RESTORE ' if args.apply else 'WOULD RESTORE ')+name)
    if args.apply:(root/name).write_bytes(data)
print('No camera/motor state accessed; offline evidence retained.')
