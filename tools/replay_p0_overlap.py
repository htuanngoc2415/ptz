"""Replay recorded geometry/evidence against source revisions; no images or invented scores.
Gate-only replay, NOT full detector/tracker inference or ground-truth identity evaluation.
"""
import argparse,json,sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

MARKERS=('09:43:12.642','09:43:25.566','09:43:28.867','09:44:08.234','09:44:28.974')
def extract(root):
    rows=[]
    for p in (root/'logs/latency').glob('latency_trace_20261112_094151_229452.jsonl*'):
        rows.extend(json.loads(l) for l in p.open())
    rows.sort(key=lambda r:r['monotonic_ns']);cases=[]
    for marker in MARKERS:
        transition=next(r for r in rows if r['event']=='REID_STATE_TRANSITION' and marker in r['timestamp'] and r.get('state')=='OCCLUDED_HOLD')
        target=transition['target_track_id'];fid=transition['frame_id'];t=transition['monotonic_ns']/1e9
        ev=next((r for r in rows if r['event']=='TARGET_OVERLAP_EVIDENCE' and r.get('frame_id')==fid and r.get('target_track_id')==target),None)
        diag=next((r for r in rows if r['event']=='TRACKER_OVERLAP_DIAGNOSTICS' and r.get('frame_id')==fid),None)
        if ev:persons=[dict(track_id=p['track_id'],bbox=p['bbox'],confidence=p['confidence']) for p in ev['candidates']];observed=ev['observation_timestamp']
        elif diag:persons=[dict(track_id=p['tracker_id'],bbox=p['observed_bbox'],confidence=p['confidence']) for p in diag['tracks']];observed=diag['observation_timestamp']
        else:raise RuntimeError('No recorded detections for '+marker)
        history=[]
        for r in rows:
            if r['monotonic_ns']/1e9>=observed:break
            if r['event']=='TRACKER_OVERLAP_DIAGNOSTICS':
                for p in r['tracks']:
                    if p['tracker_id']==target:history.append((r['observation_timestamp'],p['observed_bbox']))
            elif r['event']=='PAN_TILT_DECISION' and r.get('target_track_id')==target and r.get('target_bbox'):
                history.append((r['monotonic_ns']/1e9-r.get('observation_age_ms',0)/1000,r['target_bbox']))
        history=[h for h in history if h[0]<observed]
        previous=max(history,key=lambda x:x[0]) if history else None
        # Use only recorded prior appearance evidence, never the score at a later frame.
        ver=[r for r in rows if r['event']=='TARGET_VERIFICATION' and r.get('target_track_id')==target and r['monotonic_ns']/1e9<=observed]
        positive=ev.get('last_positive_evidence_timestamp') if ev else None
        if positive is None:
            prior_locks=[r for r in rows if r['event']=='REID_STATE_TRANSITION' and r.get('state')=='TARGET_LOCKED'
                and r.get('target_track_id')==target and r.get('lock_session_id')==transition.get('lock_session_id')
                and r['monotonic_ns']/1e9<=observed and r.get('received_ns') is not None]
            if prior_locks:positive=prior_locks[-1]['received_ns']/1e9
        cases.append(dict(marker=marker,frame_id=fid,observation_time=observed,persons=persons,target_id=target,
            session=transition.get('lock_session_id'),previous_observation=previous,
            last_positive_evidence_timestamp=positive,logged_similarity=transition.get('similarity'),
            logged_reason=transition.get('transition_reason'),logged_state=transition['state'],
            ground_truth_identity=None,raw_frame_available=False))
    return cases

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--dataset',required=True)
    p.add_argument('--output',required=True);p.add_argument('--extract',action='store_true');args=p.parse_args()
    import config  # Hold runtime config fixed across revisions.
    if args.extract:Path(args.dataset).write_text(json.dumps(extract(Path(__file__).resolve().parents[1]),indent=2))
    sys.path.insert(0,args.source)
    from ai.hybrid_reid import HybridReIDTargetSelector
    import numpy as np
    result=[]
    for case in json.loads(Path(args.dataset).read_text()):
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        s=HybridReIDTargetSelector(cfg,Mock());s.embedding=np.ones(512);s.error=None
        s.locked=True;s.crossing_hold=False;s.track_id=case['target_id'];s.lock_session_id=case['session']
        s.last_positive_verify=case['last_positive_evidence_timestamp'];s.identity_status='VERIFIED'
        prev=case['previous_observation'];s.person_box=prev[1] if prev else None
        s.observation_time=prev[0] if prev else None
        s._previous_observation_time=s.observation_time;s._diagnostic_observation_time=case['observation_time']
        candidate=next(p for p in case['persons'] if p['track_id']==s.track_id)
        blocked=s._candidate_overlap(candidate,case['persons'])
        result.append(dict(marker=case['marker'],geometry_gate='HOLD' if blocked else 'CONTINUE',
            previous_observation_available=prev is not None,ground_truth_identity=None,
            evidence_age=case['observation_time']-s.last_positive_verify if s.last_positive_verify is not None else None))
    Path(args.output).write_text(json.dumps(dict(kind='Recorded gate-only replay; not full tracking/identity benchmark',
        results=result,hold_decisions=sum(r['geometry_gate']=='HOLD' for r in result),
        false_holds=None,identity_switches=None,full_losses=None,reid_model_time=None,detection_throughput=None),indent=2))
if __name__=='__main__':main()
