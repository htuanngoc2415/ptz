"""Offline A/B/C policy replay and real-inference event schedule D.

No camera, no motor. A/B/C share verified evidence. D only sees embeddings
computed by its own scheduled ShadowScorer invocation. Timing is serial replay,
not live scheduler/RTSP behavior. Review templates contain no automatic GT.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
import copy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import torch
import config
from ai.target_identity import Candidate, IdentityPolicy, Phase2Policy, TargetIdentityManager
from runtime.identity_events import EventReID, crop_quality
from runtime.identity_shadow import ShadowScorer
from runtime.scheduling import scheduler
from tools.identity_dataset import load_capture, verified_image
from tools.identity_metrics import evaluate
from tools.identity_replay import offline_guard, digest, write_json, replay_cache, dry_pan
from tools.analyze_identity_live import stats
from person_search.reid import OSNetReID


def observe(manager, row, people, age):
    candidates=[Candidate(p['candidate_id'],p['track_id'],tuple(p['bbox']),p['confidence'],
        tuple(p['embedding']),row['frame_id'],row['timestamp'],manager.session_id,
        occluded=p.get('occluded',False),crop_quality=p.get('crop_quality','unknown')) for p in people]
    begin=time.perf_counter()
    result=manager.observe(session_id=manager.session_id,frame_id=row['frame_id'],
                           timestamp=row['timestamp'],now=row['timestamp']+age,candidates=candidates)
    result.update(frame_id=row['frame_id'],timestamp=row['timestamp'],observation_age_s=age,
                  association_ms=(time.perf_counter()-begin)*1000)
    return result


def score(runner, frame, people, max_seconds, valid=lambda: True):
    holds=[]
    original=scheduler.use
    @contextmanager
    def measured(*args,**kw):
        nested=scheduler.owner==threading.get_ident()
        with original(*args,**kw) as granted:
            start=time.perf_counter()
            try:yield granted
            finally:
                if granted and not nested:holds.append((time.perf_counter()-start)*1000)
    start=time.perf_counter()
    try:
        with patch.object(scheduler,'use',measured):
            runner.score(frame,people,lambda:time.perf_counter()-start <= max_seconds and valid())
        return dict(wall_ms=(time.perf_counter()-start)*1000,held_ms=sum(holds),
                    passes=runner.completed_passes,candidates=len(people))
    finally:
        runner.discard()


def run(cache, output, model):
    output.mkdir(parents=True,exist_ok=False)
    meta=json.loads((cache/'manifest.json').read_text())
    assert digest(cache/'observations.jsonl')==meta['observations_sha256']
    for path,h in {**meta['baseline_source_sha256'],**meta['model_sha256']}.items():
        assert digest(path)==h,path
    capture=Path(meta['video']);_,_,frames=load_capture(capture)
    images={r['frame_id']:r for r in frames}
    rows=[json.loads(l) for l in (cache/'observations.jsonl').read_text().splitlines()]
    row_by_id={r['frame_id']:r for r in rows}
    cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
    vars(cfg).update(meta['config'])
    cfg.IDENTITY_SHADOW_BATCHED=True
    cfg.IDENTITY_CANDIDATE_FILTERING_ENABLED=True
    cfg.IDENTITY_DISTRACTOR_MEMORY_ENABLED=True
    cfg.IDENTITY_MULTI_CUE_ENABLED=True
    cfg.IDENTITY_EVENT_REID_ENABLED=True
    cfg.IDENTITY_MEMORY_GALLERY_ENABLED=False
    replay_cache(cache,output/'AB',identity_enabled=True)
    ab={name:[json.loads(l) for l in (output/'AB'/f'{key}.jsonl').read_text().splitlines()]
        for name,key in [('A','baseline'),('B','identity')]}
    policy=IdentityPolicy.from_config(cfg);p2=Phase2Policy.from_config(cfg)
    def manager():return TargetIdentityManager(meta['reference_embeddings'][0],policy,
        session_id='offline',target_uid='offline_target',reference_views=meta['reference_embeddings'][1:],phase2=p2)
    c,d=manager(),manager()
    event=EventReID(cfg,d.session_id)
    refs=torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device=model.device)
    full_scorer,event_scorer=ShadowScorer(cfg,model,refs),ShadowScorer(cfg,model,refs)
    results_c=[];results_d={};full_cost=[];event_cost=[];requests=[];failures=[];quality_counts=Counter()
    max_score_diff=0.;max_memory=0;timeouts=0
    quality_ms=[];planner_ms=[]
    for row in rows:
        fid,ts=row['frame_id'],row['timestamp']
        frame=cv2.imread(str(verified_image(capture,images[fid])))
        assert frame is not None and list(frame.shape)==row['frame_shape']
        begin=time.perf_counter();people=crop_quality(frame,row['candidates'],cfg)
        quality_ms.append((time.perf_counter()-begin)*1000)
        quality_counts.update(p['crop_quality'] for p in people)
        result=observe(c,row,people,row['observation_age_s'])
        plan_start=time.perf_counter()
        result['ptz_decision']=dry_pan(result['bbox'],row['frame_shape'],row['observation_age_s'],cfg)
        result['dry_plan_ms']=(time.perf_counter()-plan_start)*1000
        results_c.append(result);max_memory=max(max_memory,len(c.distractors))
        results_d[fid]=dict(frame_id=fid,timestamp=ts,selected_candidate=None,bbox=None,
            state='VISIBLE_UNVERIFIED',reason='NO_FRESH_REID_EVENT',ptz_authorized=False,
            observation_age_s=None)
        # Matched all-frame reference cost: actual v4 inference, independent of policy.
        fresh=[{k:v for k,v in p.items() if k not in ('embedding','reid_score','raw_similarity','normalized_similarity')}
               for p in people]
        cost=score(full_scorer,frame,fresh,float('inf'))
        full_cost.append(dict(frame_id=fid,**cost))
        for a,b in zip(people,fresh):
            max_score_diff=max(max_score_diff,abs(a['reid_score']-b['reid_score']))
        assert max_score_diff<1e-5
        # Selection of D frames has no access to fresh/full or cached embeddings.
        begin=time.perf_counter()
        event.offer(frame=frame,persons=people,frame_id=fid,timestamp=ts,
                    now=ts+row['observation_age_s'],session=d.session_id,manager=d)
        if not people:
            # Actual detector absence can invalidate continuity without ReID.
            d.interrupt()
        elif 'ACTIVE_OVERLAP' in event.last_events:
            # Geometry creates uncertainty, never positive identity evidence.
            d.interrupt()
        req=event.take(ts+row['observation_age_s'],d.session_id)
        planner_ms.append((time.perf_counter()-begin)*1000)
        if req:
            age=ts+row['observation_age_s']-req.timestamp
            ds=[dict(p) for p in req.persons]
            try:
                consumed=ts+row['observation_age_s'];started=time.perf_counter()
                valid=lambda:event.valid_result(req,consumed+time.perf_counter()-started,d)
                cost=score(event_scorer,req.frame,ds,policy.max_age-age,valid)
                age+=cost['wall_ms']/1000
                event_cost.append(dict(frame_id=req.frame_id,**cost))
                if not valid():
                    timeouts+=1
                    continue
                dr=observe(d,row_by_id[req.frame_id],ds,age)
            except TimeoutError:
                timeouts+=1
                continue
            plan_start=time.perf_counter()
            dr['ptz_decision']=dry_pan(dr['bbox'],row_by_id[req.frame_id]['frame_shape'],age,cfg)
            dr['dry_plan_ms']=(time.perf_counter()-plan_start)*1000
            dr['consumed_at']=req.timestamp+age
            dr['events']=req.events
            # Preserve the SOURCE frame ID/time; never restamp an old embedding.
            results_d[req.frame_id]=dr
            requests.append(dict(frame_id=req.frame_id,timestamp=req.timestamp,events=req.events,age_s=age))
    ab.update(C=results_c,D=[results_d[r['frame_id']] for r in rows])
    metrics={}
    for name,events in ab.items():
        with (output/f'{name}.jsonl').open('x') as f:
            for e in events:f.write(json.dumps(e,allow_nan=False)+'\n')
        # Missing D evidence is unknown, not age=0 and not fresh identity.
        measured=[r for r in events if r.get('observation_age_s') is not None]
        metric_rows=[dict(r,observation_age_s=r.get('observation_age_s') or 0) for r in events]
        metrics[name]=evaluate(metric_rows)
        metrics[name]['frame_age_ms']=stats([r['observation_age_s']*1000 for r in measured])
        metrics[name].update(selected=sum(r['selected_candidate'] is not None for r in events),
            reasons=dict(Counter(r['reason'] for r in events)),
            false_rejection_of_true_target=None,false_acceptance_of_distractor=None,
            live_detection_fps=None,live_ptz_decision_latency_ms=None,
            association_ms=stats([r['association_ms'] for r in events if 'association_ms' in r]),
            dry_plan_ms=stats([r['dry_plan_ms'] for r in events if 'dry_plan_ms' in r]),
            reid_invocations_actual=len(event_cost) if name=='D' else 0,
            inference_scope='real event frames' if name=='D' else 'shared cached policy replay')
    disagreements={}
    for left,right in [('A','B'),('B','C'),('C','D')]:
        counts=Counter()
        for row,l,r in zip(rows,ab[left],ab[right]):
            a,b=l['selected_candidate'],r['selected_candidate']
            category='same' if a==b else 'right_only' if a is None else 'left_only' if b is None else 'different_candidate'
            counts[category]+=1
            if a!=b:
                failures.append(dict(pair=left+right,frame_id=row['frame_id'],timestamp=row['timestamp'],
                    left=a,right=b,left_reason=l['reason'],right_reason=r['reason'],ground_truth=None))
        disagreements[left+right]=dict(counts)
    # Stratified independent-review pack. Deliberately blank identities, no baseline labels.
    chosen={rows[i]['frame_id']:{'representative'} for i in np.linspace(0,len(rows)-1,min(16,len(rows)),dtype=int)}
    pools={
        'overlap':[r['frame_id'] for r in ab['B'] if r['reason']=='PERSON_OVERLAP'],
        'low_similarity':[r['frame_id'] for r in ab['B'] if r['reason']=='LOW_REFERENCE_SIMILARITY'],
        'disagreement':[r['frame_id'] for r in failures],
        'easy_selection':[r['frame_id'] for r in results_c if r['selected_candidate'] is not None]}
    for kind,ids in pools.items():
        if ids:
            for i in np.linspace(0,len(ids)-1,min(8,len(ids)),dtype=int):chosen.setdefault(ids[i],set()).add(kind)
    review=output/'review';review.mkdir()
    labels=[]
    for fid in sorted(chosen):
        r=row_by_id[fid];frame=cv2.imread(str(verified_image(capture,images[fid])))
        for p in r['candidates']:
            x1,y1,x2,y2=map(int,p['bbox']);cv2.rectangle(frame,(x1,y1),(x2,y2),(0,255,255),2)
            cv2.putText(frame,p['candidate_id'],(x1,max(18,y1)),0,.5,(0,255,255),1)
        cv2.imwrite(str(review/f'{fid}.jpg'),frame)
        labels.append(dict(frame_id=fid,timestamp=r['timestamp'],target_visible=None,
            candidate_identities={p['candidate_id']:None for p in r['candidates']},strata=sorted(chosen[fid])))
    write_json(review/'annotations_template.json',dict(source='UNREVIEWED_TEMPLATE',target_identity=None,
        observations_sha256=meta['observations_sha256'],frames=labels,
        review_status='AWAITING_INDEPENDENT_REVIEW',sampling_bias='stratified, not population accuracy sample'))
    # Compact diagnostic overview, deliberately not ground-truth annotations.
    tiles=[]
    for fid in list(sorted(chosen))[::max(1,len(chosen)//12)][:12]:
        im=cv2.imread(str(review/f'{fid}.jpg'));im=cv2.resize(im,(352,240))
        cv2.putText(im,str(fid),(5,230),0,.5,(0,0,255),1);tiles.append(im)
    while len(tiles)%4:tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(str(output/'review_overview.jpg'),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
    write_json(output/'failures.json',failures)
    write_json(output/'inference_cost.json',dict(full=full_cost,event=event_cost,requests=requests))
    def costs(v):return dict(jobs=len(v),candidates=sum(r['candidates'] for r in v),
        minibatches=sum(r['passes'] for r in v),total_outer_hold_ms=sum(r['held_ms'] for r in v),
        wall_ms=stats([r['wall_ms'] for r in v]),held_ms=stats([r['held_ms'] for r in v]))
    timestamps=[r['timestamp'] for r in requests]
    summary=dict(cache=str(cache),capture=str(capture),observations_sha256=meta['observations_sha256'],
        source_sha256={p:digest(p) for p in ('config.py','ai/target_identity.py','runtime/identity_events.py',
            'runtime/identity_shadow.py','runtime/identity_shadow_batch.py','tools/benchmark_identity_phase2.py')},
        model_sha256=meta['model_sha256'],phase2_policy=asdict(p2),metrics=metrics,disagreement=disagreements,
        quality=dict(quality_counts),max_distractor_entries=max_memory,quality_analysis_ms=stats(quality_ms),
        event_planning_ms=stats(planner_ms),full_inference_reference=costs(full_cost),event_inference=costs(event_cost),
        event_timeouts=timeouts,event_gap_s=stats(np.diff(timestamps)),event_gaps_above_1s=int(sum(np.diff(timestamps)>1)),
        event_reasons=dict(Counter(e for r in requests for e in r['events'])),full_v4_score_cache_max_diff=max_score_diff,
        ground_truth=False,physical_commands=0,review_frames=len(labels),
        limitations=['Serial media-time replay; no live foreground/RTSP contention',
            'Full reference is all-candidate v4 cost, not baseline A invocation cost',
            'D skips are unknown/HOLD; no extrapolated identity authorization between checks',
            'Quality proxy cannot certify full-body visibility; thresholds not calibrated',
            'Development captures, not independent holdout; selection counts are not accuracy'])
    write_json(output/'report.json',summary)
    print(json.dumps(dict(capture=str(capture),frames=len(rows),disagreement=disagreements,
        full_jobs=len(full_cost),event_jobs=len(event_cost),quality=dict(quality_counts))),flush=True)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',type=Path,action='append',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
    with offline_guard():
        cv2.setNumThreads(1);torch.set_num_threads(1)
        model=OSNetReID(config.REID_MODEL_PATH,device='cuda:0',batch_size=config.REID_BATCH_SIZE)
        reports=[run(cache,a.output/f'capture_{i+1}',model) for i,cache in enumerate(a.cache)]
        write_json(a.output/'summary.json',dict(reports=reports,ground_truth=False,physical_commands=0))


if __name__=='__main__':main()
