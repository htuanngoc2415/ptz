"""Offline causal scheduling experiment on immutable cached evidence.

Decisions choose source frames using time, detector presence and past manager state.
Embeddings are read ONLY after a source frame is admitted. No held/interpolated identity.
Service delay is a scenario input, not a reconstructed live scheduler.
"""
from pathlib import Path
from dataclasses import replace
from collections import Counter
import json,hashlib
import numpy as np
from ai.target_identity import TargetIdentityManager,IdentityPolicy,Candidate
from tools.identity_replay import offline_guard,digest
OUT=Path(__file__).resolve().parent
CAPTURES=[('173204','shadow_d3b836c5ae5c492ba7ddb95282250359'),('190016','shadow_21a42713603243019cfefd8e47793591'),('191352','shadow_36b5020b7c7843828b19de10f27d711e')]
def read(p):return [json.loads(l) for l in p.read_text().splitlines()]
def schedule_interval(kind,manager):
 if kind=='dense':return 0.
 if kind=='fixed200':return .2
 if kind=='fixed333':return 1/3
 if kind=='adaptive':return .5 if manager.state.value=='CONFIRMED_VISIBLE' else .2
 raise ValueError(kind)
def run(tag,shadow):
 cache=Path(f'docs/identity_shadow_audit_20261119_{tag}/capture_cache')
 meta=json.loads((cache/'manifest.json').read_text());assert digest(cache/'observations.jsonl')==meta['observations_sha256']
 rows=read(cache/'observations.jsonl');log=read(Path('logs/identity_shadow')/(shadow+'.jsonl'))
 initial=next(r for r in log if r['event']=='SHADOW_SESSION')
 samples={r['frame_id']:r for r in log if r['event']=='SHADOW_COMPARISON' and r['decision']['session_id']==initial['session_id']}
 # Reset at actual logged event time, not at first completed sample. All variants use same reset stream.
 resets=[r for r in log if r['event']=='SHADOW_RESET' and r['session_id']==initial['session_id']]
 policy=replace(IdentityPolicy(**next(r['policy'] for r in log if r['event']=='SHADOW_POLICY')),acquire=initial['initial_threshold'])
 results=[]
 for delay in (0.,.15,.35):
  for kind in ('dense','fixed200','fixed333','adaptive'):
   if kind=='dense' and delay:continue
   m=TargetIdentityManager(meta['reference_embeddings'][0],policy,session_id='sweep',reference_views=meta['reference_embeddings'][1:])
   due=0;last=-float('inf');busy=-float('inf');events=[];skips=Counter();reset_index=0
   for row in rows:
    ts=row['timestamp'];now=ts+row['observation_age_s']
    if ts<initial['monotonic_ts']:continue
    while reset_index<len(resets) and resets[reset_index]['monotonic_ts']<=now:
     m.interrupt();m.policy=replace(m.policy,acquire=m.policy.reacquire);reset_index+=1
    # Geometry absence invalidates continuity but never fabricates selected evidence.
    if not row['candidates']:m.interrupt()
    if now<busy:skips['BUSY']+=1;continue
    if now-last<schedule_interval(kind,m):skips['RATE']+=1;continue
    last=now
    service=delay+row['reid_ms']/1000
    consumed=now+service;busy=consumed
    # A reset arriving during compute cancels the in-flight job.
    if reset_index<len(resets) and resets[reset_index]['monotonic_ts']<=consumed:
     skips['RESET_IN_FLIGHT']+=1;continue
    if consumed-ts>policy.max_age:skips['AGE']+=1;continue
    cs=[Candidate(p['candidate_id'],p['track_id'],tuple(p['bbox']),p['confidence'],tuple(p['embedding']),row['frame_id'],ts,m.session_id) for p in row['candidates']]
    r=m.observe(session_id=m.session_id,frame_id=row['frame_id'],timestamp=ts,now=consumed,candidates=cs)
    events.append(dict(frame_id=row['frame_id'],timestamp=ts,age_s=consumed-ts,selected_candidate=r['selected_candidate'],reason=r['reason'],state=r['state'],pending_count=m._pending_count))
   by={r['frame_id']:r for r in events};gaps=np.diff([r['timestamp'] for r in events]);intersection=Counter()
   for fid,s in samples.items():
    if fid not in by:intersection['not_sampled']+=1;continue
    a=s['decision']['selected_candidate'];b=by[fid]['selected_candidate']
    intersection['same' if a==b else 'new_selection' if a is None else 'lost_selection' if b is None else 'different_candidate']+=1
   result=dict(capture=tag,kind=kind,extra_service_delay_s=delay,source_frames=len(rows),jobs_attempted=len(events)+skips['AGE']+skips['RESET_IN_FLIGHT'],completed=len(events),selected=sum(r['selected_candidate'] is not None for r in events),reasons=dict(Counter(r['reason'] for r in events)),skips=dict(skips),gaps_above_1s=int(sum(gaps>1)),gap_max_s=float(max(gaps,default=0)),gap_p95_s=float(np.percentile(gaps,95)) if len(gaps) else None,live_intersection=dict(intersection))
   name=f'{tag}_{kind}_{int(delay*1000)}'
   with (OUT/(name+'.jsonl')).open('w') as f:
    for r in events:f.write(json.dumps(r)+'\n')
   result['events']=name+'.jsonl';results.append(result)
 return results
if __name__=='__main__':
 with offline_guard():
  results=[r for tag,shadow in CAPTURES for r in run(tag,shadow)]
  (OUT/'schedule_sweep.json').write_text(json.dumps(dict(results=results,ground_truth=False,physical_commands=0,limitations=['Cached policy experiments; no live accelerator contention measurement','Reset uses logged consumption time; acquisition uses initial logged threshold','Service delay includes cached per-frame serial OSNet wall time plus hypothetical delay','All datasets are development/reused captures, not independent holdout']),indent=2))
  for r in results:print(r['capture'],r['kind'],r['extra_service_delay_s'],r['completed'],r['selected'],r['gaps_above_1s'],round(r['gap_max_s'],2),flush=True)
