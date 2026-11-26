from pathlib import Path
import json,shutil,os,hashlib,subprocess,datetime
workspace=Path('/mnt/files/Project/ptz');active=workspace/'ptz';exp=Path('/mnt/files/Project/ptz_performance_experiment_20261116');baseline=Path('/mnt/files/Project/ptz_before_async_control_20261116_162242/project/ptz')
for proc in Path('/proc').iterdir():
 if not proc.name.isdigit():continue
 try:args=(proc/'cmdline').read_bytes().split(b'\0')
 except OSError:continue
 if args and b'python' in args[0] and any(x.endswith(b'main.py') or b'cctv_app' in x for x in args[1:]):raise RuntimeError('Application still running')
for p in (active/'logs/camera/motor_state').glob('*.json'):
 assert not json.loads(p.read_text()).get('active_codes'), 'Persisted motor uncertainty: '+str(p)
camera=active/'logs/camera/camera_control_20261116_163635_205025.log'
rows=[json.loads(l[l.index('{'):]) for l in camera.open() if '{' in l]
shutdown=[r for r in rows if r['event']=='SHUTDOWN_COMPLETED'][-1]
assert shutdown['unconfirmed_motor'] is False and shutdown['cleanup_failures']==[]
stage=workspace/'ptz_verified_pre_async_staging';assert not stage.exists()
shutil.copytree(baseline,stage,symlinks=True)
checked=0
for p in baseline.rglob('*'):
 if p.is_file() and not p.is_symlink():
  q=stage/p.relative_to(baseline)
  def digest(f):
   h=hashlib.sha256()
   with f.open('rb') as s:
    for b in iter(lambda:s.read(1024*1024),b''):h.update(b)
   return h.hexdigest()
  assert digest(p)==digest(q),p;checked+=1
# Preserve current diagnostics while runtime source/config/assets remain baseline.
for name in ['logs','docs']:
 shutil.copytree(active/name,stage/name,dirs_exist_ok=True,symlinks=True)
preserved=exp/'installed_async_before_rollback';assert not preserved.exists()
active.rename(preserved);stage.rename(active)
subprocess.run(['git','checkout','-b','baseline/verified-pre-async-20261116'],cwd=active,check=True)
receipt=dict(result='ROLLBACK TO VERIFIED BASELINE',time=datetime.datetime.now().astimezone().isoformat(),active=str(active),baseline=str(baseline),preserved_async=str(preserved),candidate=str(exp/'ptz'),verified_files=checked,shutdown_evidence=shutdown,physical_stop_verified=False,stop_basis='prior confirmed protocol shutdown + no application process + empty persisted active_codes; no new motor command',head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=active,text=True).strip(),config_sha256=hashlib.sha256((active/'config.py').read_bytes()).hexdigest(),camera_config_sha256=hashlib.sha256((active/'camera.json').read_bytes()).hexdigest())
out=exp/'ptz/docs/comprehensive_audit_20261116';(out/'rollback_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt,indent=2))
