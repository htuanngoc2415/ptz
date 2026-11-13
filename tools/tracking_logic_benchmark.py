"""Identical scripted workload across checkpoint/current code; no neural/GPU claims."""
import argparse
import json
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--output',required=True)
    args=p.parse_args()
    import config  # Identical runtime settings for both source revisions; no camera I/O.
    sys.path.insert(0,args.root)
    from ai.hybrid_reid import HybridReIDTargetSelector
    from runtime.byte_tracking import ByteTrackPersonTracker
    results=[]
    for scenario in ('single','proven_alias','ambiguous_overlap','fragmentation','missing_reacquire','camera_translation'):
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        cfg.REID_SEARCH_INTERVAL=0;cfg.REID_VERIFY_INTERVAL=100
        tracker=ByteTrackPersonTracker(cfg);s=HybridReIDTargetSelector(cfg,Mock())
        s.embedding=np.ones(512);s.error=None;score_calls=[]
        def scores(frame,persons):
            score_calls.append(len(persons));return [.95 if p['identity']=='target' else .1 for p in persons]
        s._scores=scores
        frame=np.zeros((500,700,3),np.uint8);selected=holds=wrong=0;ids=set();first_reacquire=None
        elapsed=[]
        for i in range(160):
            x=350 if scenario=='camera_translation' and i>=20 else 100
            d=dict(bbox=(x,50,x+70,250),confidence=.9,identity='target')
            persons=[d]
            if scenario=='proven_alias':persons=[d,d]
            if scenario=='ambiguous_overlap':persons.append(dict(bbox=(101,50,171,250),confidence=.9,identity='other'))
            if scenario=='missing_reacquire' and 20<=i<120:persons=[]
            if scenario=='fragmentation' and i==20:tracker.reset()
            before=time.perf_counter()
            tracks=tracker.update(persons,(i+1)*.1)
            chosen=s.select_person(frame,tracks,(i+1)*.1);s.finish_frame(chosen,None)
            elapsed.append((time.perf_counter()-before)*1000)
            ids.update(p['track_id'] for p in tracks if p['identity']=='target')
            selected+=chosen is not None;holds+=bool(s.crossing_hold)
            wrong+=chosen is not None and chosen['identity']!='target'
            if scenario=='missing_reacquire' and i>=120 and chosen is not None and first_reacquire is None:first_reacquire=(i-120)*.1
        results.append(dict(scenario=scenario,frames=160,selected=selected,hold_frames=holds,
            incorrect_following=wrong,target_track_ids=len(ids),scripted_reid_calls=len(score_calls),
            scripted_reid_candidates=sum(score_calls),reacquisition_seconds=first_reacquire,
            logic_wall_ms=dict(zip(['p50','p95','p99'],map(float,np.percentile(elapsed,[50,95,99]))))))
    from control.auto_zoom import AutoZoomController
    import inspect
    lens=Mock();lens.has_zoom_telemetry=False;lens.supports_absolute_zoom=False
    lens.telemetry.return_value=None;lens.command.return_value=lens.stop.return_value=(True,'ACK')
    options=dict(recovery_pulse=.04,recovery_interval=0,recovery_delay=0,
        recovery_max_duration=.12,wait_after_zoom=0,direction_hold=0,direction_cooldown=0)
    if 'pulse_recovery_without_telemetry' in inspect.signature(AutoZoomController).parameters:
        options.update(pulse_recovery_without_telemetry=True,pulse_recovery_budget=.12)
    z=AutoZoomController(lens,**options);z.set_enabled(True)
    try:
        for i in range(20):
            now=time.monotonic();z.recover_missing(now,now-2,float('nan'))
            deadline=now+1
            while z.busy and time.monotonic()<deadline:time.sleep(.002)
            if z.busy:raise RuntimeError('Mock recovery exceeded timeout')
        recovery=dict(attempts=20,pulses=lens.command.call_count,spent_seconds=z._recovery_spent,
            exhausted=z._recovery_spent>=.12,successful_reacquisitions=0,
            invalid_direction_commands=sum(c.args[0]!='zoom_out' for c in lens.command.call_args_list))
    finally:z.close()
    with open(args.output,'w') as f:json.dump(dict(kind='Scripted scores; no YOLO/OSNet/GPU/FPS benchmark',
        results=results,recovery=recovery),f,indent=2)
if __name__=='__main__':main()
