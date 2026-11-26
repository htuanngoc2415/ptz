"""Reproduce nine serial model runs + three control variants into a NEW directory."""
import argparse,subprocess,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,required=True);a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=False)
root=Path('/mnt/files/Project/ptz_performance_experiment_20261116/ptz')
sources={'A':'/mnt/files/Project/ptz_before_async_control_20261116_162242/project/ptz','B':'/mnt/files/Project/ptz_performance_experiment_20261116/installed_async_before_rollback','C':str(root)}
env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1');python='/home/admin1/miniconda3/envs/py/bin/python'
for trial,order in enumerate(['ABC','BCA','CAB'],1):
 for variant in order:
  name=f'pipeline_{variant}_{trial}'
  with (a.output_dir/(name+'.log')).open('w') as log:
   subprocess.run([python,str(root/'tools/audit_pipeline_benchmark.py'),'--source',sources[variant],'--variant',variant,'--output',str(a.output_dir/(name+'.json'))],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
for variant in 'ABC':
 name=f'control_{variant}'
 with (a.output_dir/(name+'.log')).open('w') as log:
  subprocess.run([python,str(root/'tools/audit_control_benchmark.py'),'--source',sources[variant],'--variant',variant,'--output',str(a.output_dir/(name+'.json'))],cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
