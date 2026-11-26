"""Postprocess completed benchmarks, without new inference or pseudo-GT."""
from collections import Counter
import json
from pathlib import Path
import time
from types import SimpleNamespace
import config
from tools.analyze_identity_live import stats
from tools.identity_replay import dry_pan, write_json, offline_guard

out=Path(__file__).resolve().parent
reports=json.loads((out/'benchmark_final/summary.json').read_text())['reports']
result=[]
with offline_guard():
 for i,r in enumerate(reports,1):
  directory=out/'benchmark_final'/f'capture_{i}'
  data={k:[json.loads(l) for l in (directory/f'{k}.jsonl').read_text().splitlines()] for k in 'ABCD'}
  source={row['frame_id']:row for row in map(json.loads,(Path(r['cache'])/'observations.jsonl').open())}
  b,c,d=data['B'],data['C'],data['D']
  bc=Counter();cases=[];labels=Counter();quality=Counter()
  for old,new in zip(b,c):
   labels.update(a.get('identity_class','unknown') for a in new['candidate_audit'])
   if old['selected_candidate'] and not new['selected_candidate']:
    bc[new['reason']]+=1
    if len(cases)<12 or new['reason'] not in {x['reason'] for x in cases}:
     cases.append(dict(frame_id=new['frame_id'],reason=new['reason'],baseline_B=old['selected_candidate'],C=None))
  sampled=[(x,y) for x,y in zip(c,d) if 'association_ms' in y]
  conditional=Counter('same' if a['selected_candidate']==b['selected_candidate'] else
       'D_only' if a['selected_candidate'] is None else 'C_only' if b['selected_candidate'] is None
       else 'different_candidate' for a,b in sampled)
  req=[row for row in d if 'association_ms' in row]
  duration=b[-1]['timestamp']-b[0]['timestamp']
  costs=json.loads((directory/'inference_cost.json').read_text())
  # Measure pure planner costs consistently for all A/B/C/D after replay.
  meta=json.loads((Path(r['cache'])/'manifest.json').read_text())
  cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config'])
  dry={}
  for name,rows in data.items():
   times=[]
   for row in rows:
    frame=source[row['frame_id']]
    start=time.perf_counter()
    dry_pan(row.get('bbox'),frame['frame_shape'],row.get('observation_age_s') or 0,cfg)
    times.append((time.perf_counter()-start)*1000)
   dry[name]=stats(times)
  result.append(dict(capture=i,frames=len(b),duration_s=duration,
      selected={k:sum(x['selected_candidate'] is not None for x in v) for k,v in data.items()},
      C_lost_B_selection_reasons=dict(bc),C_classifications=dict(labels),examples=cases,
      D_processed_frames=len(req),D_coverage=len(req)/len(b),D_comparisons_per_media_second=len(req)/duration,
      D_age_ms=stats([x['observation_age_s']*1000 for x in req]),C_D_same_sample=dict(conditional),
      full_nonempty_jobs=sum(x['passes']>0 for x in costs['full']),
      full_hold_s=r['full_inference_reference']['total_outer_hold_ms']/1000,
      D_hold_s=r['event_inference']['total_outer_hold_ms']/1000,
      dry_planner_only_ms=dry,ground_truth=False))
 write_json(out/'comparison_summary.json',dict(captures=result,ground_truth=False,
     physical_commands=0,planner_timing_scope='pure plan_pan after replay, excludes inference/queue/physical response'))
 print(json.dumps(result,indent=2))
