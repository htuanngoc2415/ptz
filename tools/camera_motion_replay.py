#!/usr/bin/env python3
"""Local decoded-frame bundle replay. No network, motors, YOLO, OSNet or GPU.

Two branches use identical cached detections; synthetic GT is evaluated only
AFTER both runs. Screen recordings are not accepted as raw replay bundles.
"""
import argparse,json,time,sys
from pathlib import Path
from types import SimpleNamespace
import cv2,numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
from runtime.camera_motion import CameraMotionEstimator
from runtime.camera_motion_shadow import ShadowBranch


def cfg_copy():
    c=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
    c.CAMERA_MOTION_DIAGNOSTICS_ENABLED=c.CAMERA_MOTION_RECORD_ENABLED=c.TRACK_SNAPSHOT_ENABLED=False
    return c


def evaluate(outputs,truth):
    last={};was_visible={};seen_ids={};switches=0;fragments=0;misses=0;visible=0
    for out,gt in zip(outputs,truth):
        assigned=set()
        for person in gt:
            ident=person['identity'];box=np.asarray(person['bbox']);visible+=1
            candidates=[]
            for i,row in enumerate(out):
                a=np.asarray(row['bbox']);lo=np.maximum(a[:2],box[:2]);hi=np.minimum(a[2:],box[2:]);inter=np.maximum(hi-lo,0).prod()
                union=np.maximum(a[2:]-a[:2],0).prod()+np.maximum(box[2:]-box[:2],0).prod()-inter
                if i not in assigned and union>0:candidates.append((inter/union,i,row['track_id']))
            match=max(candidates,default=(0,None,None))
            if match[0]<.5:misses+=1;was_visible[ident]=False;continue
            _,i,tid=match;assigned.add(i)
            if ident in last and last[ident]!=tid:switches+=1
            if ident in last and not was_visible.get(ident,True):fragments+=1
            last[ident]=tid;was_visible[ident]=True;seen_ids.setdefault(ident,set()).add(tid)
    return dict(id_switches=switches,track_fragmentations=fragments,missing_visible_observations=misses,
                visible_observations=visible,unique_track_ids={k:len(v) for k,v in seen_ids.items()},
                wrong_target_follow_rate=None,false_hold=None,full_loss=None,target_selected_dwell=None,
                note='Spatial tracker metrics only; no selector/identity-follow inference in shadow branch')


def replay(frames,records,truth=None):
    estimates=[];estimator=CameraMotionEstimator();previous=None
    for frame,row in zip(frames,records):
        if previous is None or row.get('session')!=previous[1].get('session'):
            estimates.append(None)
        else:
            estimates.append(estimator.estimate(previous[0],frame,previous[1]['receive_timestamp'],row['receive_timestamp'],
                [d['bbox'] for d in previous[1]['detections']],[d['bbox'] for d in row['detections']],row.get('osd_boxes',())))
        previous=(frame,row)
    branches={}
    for enabled in (False,True):
        branch=ShadowBranch(cfg_copy(),enabled);outputs=[];timings=[]
        previous_session=None
        for row,estimate in zip(records,estimates):
            if previous_session is not None and row.get('session')!=previous_session:
                branch.adapter.reset()
            previous_session=row.get('session')
            detections=[{'bbox':list(d['bbox']),'confidence':float(d['confidence'])} for d in row['detections']]
            t=time.perf_counter();outputs.append(branch.update(detections,row['receive_timestamp'],row['frame_id'],estimate));timings.append((time.perf_counter()-t)*1000)
        branches['gmc' if enabled else 'baseline']=dict(outputs=outputs,tracker_mean_ms=float(np.mean(timings)),tracker_p95_ms=float(np.percentile(timings,95)),
            metrics=evaluate(outputs,truth) if truth is not None else None)
    valid=[e for e in estimates if e is not None];lat=[e.latency_ms for e in valid]
    return dict(branches=branches,estimates=[e.payload() if e else None for e in estimates],
        gmc_success_rate=sum(e.valid for e in valid)/len(valid) if valid else None,
        gmc_mean_ms=float(np.mean(lat)) if lat else None,gmc_p95_ms=float(np.percentile(lat,95)) if lat else None,
        raw_rtsp_benchmark=False,wrong_target_follow_acceptance='NOT_MEASURED',production_enable_recommended=False)


def synthetic(camera_dx=0,person_dx=0,frames_count=16):
    rng=np.random.default_rng(12);background=rng.integers(0,256,(360,1024,3),dtype=np.uint8)
    background=cv2.GaussianBlur(background,(3,3),0);frames=[];records=[];truth=[]
    for i in range(frames_count):
        shift=i*camera_dx
        H=np.array([[1.,0,shift],[0,1.,0.]])
        frame=cv2.warpAffine(background,H,(1024,360))
        x=150+shift+i*person_dx;box=[x,140,x+24,230]
        detections=[];gt=[]
        if 0<=x and x+24<1024:
            cv2.rectangle(frame,(int(x),140),(int(x+24),230),(20,40,180),-1)
            detections=[dict(bbox=box,confidence=.95)];gt=[dict(identity='synthetic_person_A',bbox=box)]
        frames.append(frame);records.append(dict(frame_id=i,receive_timestamp=100+i*.1,detections=detections,session='synthetic'));truth.append(gt)
    return frames,records,truth


def load_bundle(directory):
    records=[json.loads(l) for l in (directory/'frames.jsonl').read_text().splitlines()]
    frames=[];previous=-float('inf')
    for row in records:
        if row.get('source_kind')!='decoded_rtsp_frame':raise ValueError('Replay requires decoded RTSP images, not monitor recording')
        if not np.isfinite(row['receive_timestamp']) or row['receive_timestamp']<=previous:raise ValueError('timestamps must be finite and increase')
        previous=row['receive_timestamp']
        p=(directory/row['image']).resolve()
        if directory.resolve() not in p.parents or not p.is_file():raise ValueError('invalid image path')
        frame=cv2.imread(str(p))
        if frame is None:raise ValueError('unreadable frame')
        if any(d.get('confidence') is None for d in row['detections']):raise ValueError('Missing original detection confidence')
        frames.append(frame)
    return frames,records


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--bundle',type=Path);p.add_argument('--synthetic',action='store_true');p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();cv2.setNumThreads(1)
    if a.synthetic:
        result={}
        for name,dx,px in [('stationary',0,2),('pan',25,0),('pan_and_walking',25,2)]:
            frames,rows,truth=synthetic(dx,px);result[name]=replay(frames,rows,truth)
        result['provenance']='SYNTHETIC stress test, same cached detections per branch; not real tracking acceptance'
    elif a.bundle:
        frames,rows=load_bundle(a.bundle);result=replay(frames,rows);result['provenance']='decoded frames; ground truth absent; cannot accept production GMC'
    else:p.error('choose --synthetic or --bundle')
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,indent=2))


if __name__=='__main__':main()
