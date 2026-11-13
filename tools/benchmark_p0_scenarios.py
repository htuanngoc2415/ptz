"""Controlled labelled detection scenarios; scripted scores, no neural/physical benchmark."""
import argparse,json,sys,statistics,math
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
import numpy as np

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();import config
    sys.path.insert(0,args.source)
    from ai.hybrid_reid import HybridReIDTargetSelector
    results=[]
    def p(ident=1,box=(324,95,412,391),label='target'):
        return dict(track_id=ident,bbox=box,confidence=.9,label=label)
    for scenario in ('isolated','single_weak','persistent_weak_unverified','strong_crossing','missing_reacquire','distant_reacquire','strong_with_distractors'):
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        cfg.REID_SEARCH_INTERVAL=0;cfg.REID_VERIFY_INTERVAL=100
        s=HybridReIDTargetSelector(cfg,Mock());s.embedding=np.ones(512);s.error=None
        workloads=[]
        def scores(frame,ps):
            workloads.append(len(ps));return [.95 if r['label']=='target' else .1 for r in ps]
        s._scores=scores;s._start_verification=Mock() # no asynchronous fabricated evidence
        frame=np.zeros((500,700,3),np.uint8);holds=lost=wrong=selected=0;durations=[];lock_at=None;reacquired=None
        for i in range(100):
            now=i*.1;people=[p()]
            if scenario=='single_weak' and i==10 or scenario=='persistent_weak_unverified' and 10<=i<30:
                people.append(p(2,(282,79,335,231),'other'))
            if scenario in ('strong_crossing','strong_with_distractors') and 10<=i<30:
                people.append(p(2,(325,96,413,392),'other'))
                if scenario=='strong_with_distractors':people += [p(3,(0,0,50,100),'other'),p(4,(500,0,550,100),'other')]
            if scenario in ('missing_reacquire','distant_reacquire'):
                if 10<=i<50:people=[]
                elif i>=50 and scenario=='distant_reacquire':people=[p(9,(20,20,80,200))]
            prevhold=s.crossing_hold;prevlocked=s.locked
            chosen=s.select_person(frame,people,now);s.finish_frame(chosen,None)
            if s.crossing_hold and not prevhold:
                holds+=1
                if lock_at is not None:durations.append(now-lock_at);lock_at=None
            if not prevlocked and s.locked or prevhold and not s.crossing_hold and s.locked:lock_at=now
            lost+=int(prevlocked and not s.locked)
            selected+=chosen is not None;wrong+=chosen is not None and chosen['label']!='target'
            if scenario in ('missing_reacquire','distant_reacquire') and i>=50 and chosen is not None and reacquired is None:reacquired=now-5.
        results.append(dict(scenario=scenario,frames=100,holds=holds,full_losses=lost,
            selected_seconds=selected*.1,median_lock_to_hold=statistics.median(durations) if durations else None,
            wrong_target_selections=wrong,reacquisition_seconds=reacquired,
            reid_calls=len(workloads),reid_candidates=sum(workloads),
            projected_batches_if_all_normalized=sum(2*math.ceil(n/cfg.REID_BATCH_SIZE) for n in workloads),
            reid_model_time=None,real_detection_fps=None))
    # Same 5 isolated observed boxes, same periodic verification entry point.
    cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
    s=HybridReIDTargetSelector(cfg,Mock());s.track_id=1;s.lock_session_id='benchmark';s.person_box=p()['bbox']
    calls=[];s._scores=lambda f,ps:calls.append(len(ps)) or [.95 if r['track_id']==1 else .1 for r in ps]
    people=[p()]+[p(i+2,(i*70,0,i*70+40,60),'other') for i in range(4)]
    s._start_verification(np.zeros((500,700,3),np.uint8),people,10.)
    s._verification[0].result(timeout=2)
    output=dict(kind='Synthetic labelled tracks, fixed 100ms observations, scripted ReID',scenarios=results,
        isolated_periodic_candidates=calls,neural_accuracy=None,hardware_validation=False)
    Path(args.output).write_text(json.dumps(output,indent=2))
if __name__=='__main__':main()
