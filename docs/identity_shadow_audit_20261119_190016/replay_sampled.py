"""Replay actual sample times and a dense diagnostic with fixed reset frame boundaries."""
from pathlib import Path
from collections import Counter
from dataclasses import replace
import json
import numpy as np
from ai.target_identity import TargetIdentityManager,IdentityPolicy,Candidate
from ai.reid_target import iou
from tools.identity_replay import offline_guard
out=Path(__file__).resolve().parent
read=lambda p:[json.loads(l) for l in p.read_text().splitlines()]
with offline_guard():
 meta=json.loads((out/'capture_cache/manifest.json').read_text());rows=read(out/'capture_cache/observations.jsonl');by={r['frame_id']:r for r in rows}
 log=read(Path('logs/identity_shadow/shadow_21a42713603243019cfefd8e47793591.jsonl'));samples=[r for r in log if r['event']=='SHADOW_COMPARISON'];policy=IdentityPolicy(**next(r['policy'] for r in log if r['event']=='SHADOW_POLICY'));initial=next(r for r in log if r['event']=='SHADOW_SESSION')
 policy=replace(policy,acquire=initial['initial_threshold'])
 def manager():return TargetIdentityManager(meta['reference_embeddings'][0],policy,session_id='offline_sampled',reference_views=meta['reference_embeddings'][1:])
 def candidates(row,m):return [Candidate(p['candidate_id'],p['track_id'],tuple(p['bbox']),p['confidence'],tuple(p['embedding']),row['frame_id'],row['timestamp'],m.session_id) for p in row['candidates']]
 m=manager();serial=0;counts=Counter();mismatches=[];diagnostics=[];boundaries={}
 for s in samples:
  if s['frame_id'] not in by:continue
  if s['reset_serial']!=serial:
   serial=s['reset_serial'];boundaries[s['frame_id']]=serial;m.interrupt();m.policy=replace(m.policy,acquire=m.policy.reacquire)
  if s['frame_id'] not in by:continue
  row=by[s['frame_id']];prev=m._pending
  cs=candidates(row,m);best=max(cs,key=lambda c: max(np.dot(c.embedding,v) for v in m.reference_views)) if cs else None
  continuity={}
  if prev is not None and best is not None:
   continuity=dict(gap=row['timestamp']-prev[0],iou=iou(best.bbox,prev[1].bbox),appearance=float(np.dot(best.embedding,prev[2])))
  result=m.observe(session_id=m.session_id,frame_id=row['frame_id'],timestamp=row['timestamp'],now=row['timestamp']+s['age_ms']/1000,candidates=cs)
  keys=('selected_candidate','reason','state','action')
  if any(result[k]!=s['decision'][k] for k in keys):mismatches.append(dict(frame_id=row['frame_id'],actual={k:s['decision'][k] for k in keys},recomputed={k:result[k] for k in keys}))
  counts['replayed']+=1
  counts['selected']+=int(result['selected_candidate'] is not None)
  if result['reason']=='AWAITING_MULTI_OBSERVATION_CONFIRMATION':
   counts['waiting_count_'+str(m._pending_count)]+=1
   if m._pending_count==1:
    if prev is None:counts['new_chain_no_pending']+=1
    else:
     counts['broken_pending_chain']+=1
     counts['broken_gap']+=int(not 0<continuity['gap']<=policy.max_gap)
     counts['broken_iou']+=int(continuity['iou']<policy.confirmation_iou)
     counts['broken_appearance']+=int(continuity['appearance']<policy.reacquire)
   diagnostics.append(dict(frame_id=row['frame_id'],pending_count=m._pending_count,previous_pending=prev is not None,**continuity))
 # Dense counterfactual: hold the reset boundary at first completed sample of each new serial.
 # This isolates sampling conditional on these boundaries; not the exact live reset schedule.
 dense=manager();future=list(sorted(boundaries));dense_by={};dense_counts=Counter();dense_events=[]
 for row in rows:
  while future and row['frame_id']>=future[0]:
   future.pop(0);dense.interrupt();dense.policy=replace(dense.policy,acquire=dense.policy.reacquire)
  result=dense.observe(session_id=dense.session_id,frame_id=row['frame_id'],timestamp=row['timestamp'],now=row['timestamp']+row['observation_age_s'],candidates=candidates(row,dense))
  dense_by[row['frame_id']]=result;dense_counts[result['reason']]+=1
  dense_events.append(dict(frame_id=row['frame_id'],timestamp=row['timestamp'],**result))
 comparison=Counter()
 for s in samples:
  if s['frame_id'] not in dense_by:continue
  a=s['decision']['selected_candidate'];b=dense_by[s['frame_id']]['selected_candidate']
  comparison['same_choice']+=int(a==b);comparison['dense_selected_at_actual_hold']+=int(a is None and b is not None);comparison['dense_hold_at_actual_selected']+=int(a is not None and b is None);comparison['both_select_different']+=int(a is not None and b is not None and a!=b)
 result=dict(sampled_counts=counts,mismatches=mismatches,reset_boundaries=boundaries,dense_reasons=dense_counts,dense_at_recorded_samples=comparison,scope='Available PNG sample times only; unmatched samples listed in capture_audit.json. Dense counterfactual resets at first completed frame of each new serial; not exact live reset/scheduler replay.',ground_truth=False,physical_commands=0)
 (out/'sampled_replay_summary.json').write_text(json.dumps(result,indent=2));(out/'pending_diagnostics.json').write_text(json.dumps(diagnostics,indent=2))
 with (out/'dense_diagnostic.jsonl').open('w') as f:
  for r in dense_events:f.write(json.dumps(r)+'\n')
 print(json.dumps(result,indent=2))
