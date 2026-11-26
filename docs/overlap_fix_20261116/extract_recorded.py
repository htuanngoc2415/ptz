from pathlib import Path
import json
root=Path(__file__).resolve().parents[2];out=Path(__file__).parent
rows=[]
for p in (root/'logs/latency').glob('latency_trace_20261116_115349_764314.jsonl*'):
 rows.extend(json.loads(l) for l in p.open())
rows.sort(key=lambda x:x['monotonic_ns']);history={};cases=[];transitions=[]
for r in rows:
 if r['event']=='REID_STATE_TRANSITION' and r.get('state')=='OCCLUDED_HOLD':transitions.append(r)
 if r['event']=='TARGET_OVERLAP_EVIDENCE':
  target=r['target_track_id'];people=[dict(track_id=p['track_id'],bbox=p['bbox'],confidence=p.get('confidence',1)) for p in r['candidates']]
  if any(p['track_id']==target for p in people):
   prev=history.get(target);observed=r['observation_timestamp']
   cases.append(dict(marker=r['timestamp'],frame_id=r['frame_id'],observation_time=observed,persons=people,target_id=target,session=r.get('lock_session_id'),previous_observation=prev if prev and prev[0]<observed else None,last_positive_evidence_timestamp=r.get('last_positive_evidence_timestamp'),ground_truth_identity=None,raw_frame_available=False))
 if r['event']=='TRACKER_OVERLAP_DIAGNOSTICS':
  for p in r['tracks']:history[p['tracker_id']]=[r['observation_timestamp'],p['observed_bbox']]
(out/'recorded_cases.json').write_text(json.dumps(cases,indent=2));(out/'recorded_coverage.json').write_text(json.dumps(dict(start=rows[0]['timestamp'],end=rows[-1]['timestamp'],cases=len(cases),hold_transitions=transitions),indent=2));print('recorded cases',len(cases))
