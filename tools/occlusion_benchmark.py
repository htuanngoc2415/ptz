"""Source-bound offline policy replay. No production changes or motor imports."""
import argparse,copy,json,time
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import Future
from collections import Counter
import cv2,numpy as np
import config
from ai.hybrid_reid import HybridReIDTargetSelector
from ai.overlap_policy import overlap_geometry
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import digest,offline_guard,dry_pan
from tools.occlusion_association import Observation,OcclusionAssociation
from tools.occlusion_visibility import regions


class Baseline(HybridReIDTargetSelector):
    def _scores(self,frame,people):return [p['reid_score'] for p in people]
    def _start_verification(self,frame,people,now):
        if getattr(self,'_verification',None) is not None:return
        future=Future();persons=copy.deepcopy(people);future.set_result(self._scores(frame,persons))
        self._verification=(future,(self.verification_epoch,self.lock_session_id,self.track_id),now,self.frame_id,persons)
        self.last_verify=now;self.identity_status='VERIFYING'


def baseline(cfg,reference):
    c=copy.copy(cfg);c.REID_GALLERY_ENABLED=False
    s=Baseline(c);s.embedding=np.asarray(reference);s.error=None
    return s


def observations(image,persons,reid=None,masks=None):
    crops=[];mapping=[];supports=[]
    for i,p in enumerate(persons):
        mask=masks[i] if masks is not None else None
        parts,support=regions(image,p['bbox'],[q['bbox'] for q in persons if q is not p],mask)
        supports.append(support)
        if reid:
            for name,crop in parts.items():mapping.append((i,name));crops.append(crop)
    vectors=reid.extract_embeddings(crops).cpu().numpy() if reid and crops else []
    parts=[{} for _ in persons]
    for (i,name),v in zip(mapping,vectors):parts[i][name]=v.tolist()
    h,w=image.shape[:2];result=[]
    for i,p in enumerate(persons):
        b=p['bbox'];x1,y1,x2,y2=map(int,b)
        crop=image[max(0,y1):min(h,y2),max(0,x1):min(w,x2)]
        clear=(0<=x1<x2<=w and 0<=y1<y2<=h and crop.size>0 and
            cv2.Laplacian(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()>=20)
        result.append(Observation(str(p.get('candidate_id',i)),p.get('track_id'),tuple(b),p['confidence'],p['embedding'],
            parts[i],supports[i] if parts[i] else 0.,'instance_mask_parts' if masks is not None else 'isolated_parts',clear))
    return result


def overlay(image,persons,a,b,raw_count=None):
    im=image.copy()
    for p in persons:
        x1,y1,x2,y2=map(int,p['bbox']);cv2.rectangle(im,(x1,y1),(x2,y2),(255,150,0),1)
        cv2.putText(im,str(p.get('track_id')),(x1,max(12,y1)),cv2.FONT_HERSHEY_SIMPLEX,.4,(255,150,0),1)
    cv2.rectangle(im,(0,0),(im.shape[1],86),(0,0,0),-1)
    cv2.putText(im,f'YOLO raw={raw_count} tracklets={len(persons)} | visibility UNKNOWN | B support={b.get("support")}',(8,65),cv2.FONT_HERSHEY_SIMPLEX,.4,(255,255,255),1)
    for label,result,color,y in [('A',a,(0,0,255),20),('B',b,(0,255,0),40)]:
        box=result.get('bbox')
        if box:cv2.rectangle(im,tuple(map(int,box[:2])),tuple(map(int,box[2:])),color,2)
        cv2.putText(im,label+': '+result.get('state','')+' '+result.get('reason',''),(8,y),cv2.FONT_HERSHEY_SIMPLEX,.4,color,1)
    return im


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--parts',action='store_true',help='Compute real OSNet isolated-part evidence')
    p.add_argument('--limit',type=int,default=0);p.add_argument('--device',default='cpu')
    p.add_argument('--split',choices=['development','holdout'],default='development')
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    with offline_guard():
        cv2.setNumThreads(1)
        meta=json.loads((args.cache/'manifest.json').read_text())
        if digest(args.cache/'observations.jsonl')!=meta['observations_sha256']:raise ValueError('Cache checksum mismatch')
        source=Path(meta['video']);_,source_summary,source_rows=load_capture(source)
        if digest(source/'frames.jsonl')!=meta['source_capture_sha256']:raise ValueError('Source capture mismatch')
        images={r['frame_id']:r for r in source_rows}
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config'])
        a=baseline(cfg,meta['reference_embeddings'][0]);b=OcclusionAssociation(meta['reference_embeddings'][0],'replay')
        reid=None
        if args.parts:
            import torch
            from person_search.reid import OSNetReID
            torch.set_num_threads(1);reid=OSNetReID(cfg.REID_MODEL_PATH,device=args.device)
        rows=[json.loads(l) for l in (args.cache/'observations.jsonl').read_text().splitlines()]
        if args.limit:rows=rows[:args.limit]
        from runtime.camera_motion import CameraMotionEstimator
        gmc=CameraMotionEstimator();previous=None
        records=[];writer=None
        try:
            for row in rows:
                im=cv2.imread(str(verified_image(source,images[row['frame_id']])))
                people=copy.deepcopy(row['candidates']);a.frame_id=row['frame_id'];a.frame_age_ms=row['observation_age_s']*1000
                selected=a.select_person(im,people,row['timestamp']) if 0<=row['observation_age_s']<=cfg.MAX_CONTROL_OBSERVATION_AGE else None
                a.finish_frame(selected,None)
                ar=dict(state=a.stage,reason=('SELECTED' if selected else 'OVERLAP:'+str(getattr(a,'overlap_reason','UNKNOWN')) if a.crossing_hold else 'NO_SELECTION:'+a.stage),legacy_transition_reason=a.transition_reason,bbox=selected['bbox'] if selected else None,
                    control_eligible=selected is not None,selected_candidate=selected['candidate_id'] if selected else None)
                begin=time.perf_counter();obs=observations(im,people,reid)
                motion_state='UNKNOWN';transform=None
                if previous is not None:
                    old,old_time,old_boxes=previous
                    motion=gmc.estimate(old,im,old_time,row['timestamp'],old_boxes,[p['bbox'] for p in people])
                    if motion.valid:
                        motion_state='STATIC' if motion.classification=='LOW_IMAGE_MOTION' else 'MOVING'
                        if b.observed==old_time:transform=motion.transform
                previous=(im,row['timestamp'],[p['bbox'] for p in people])
                br=b.observe('replay',row['frame_id'],row['timestamp'],row['timestamp']+row['observation_age_s'],obs,
                    camera_motion=motion_state,camera_transform=transform)
                pairs=[dict(a=x['candidate_id'],b=y['candidate_id'],**overlap_geometry(x['bbox'],y['bbox'])) for i,x in enumerate(people) for y in people[i+1:]]
                rec=dict(frame_id=row['frame_id'],timestamp=row['timestamp'],camera_motion=motion_state,A=ar,B=br,
                    raw_detections=len(row['detections']),tracklets=len(people),pairs=pairs,
                    pre_nms_detections=None,detector_vs_tracker='COUNT_DIFFERENCE' if len(row['detections'])!=len(people) else 'COUNTS_EQUAL_NOT_ASSOCIATION_TRUTH',
                    visibility_part_latency_ms=(time.perf_counter()-begin)*1000,
                    A_shadow_pan=dry_pan(ar['bbox'],im.shape,row['observation_age_s'],cfg),
                    B_shadow_pan=dry_pan(br['bbox'],im.shape,row['observation_age_s'],cfg))
                records.append(rec)
                rendered=overlay(im,people,ar,br,len(row['detections']))
                if writer is None:
                    writer=cv2.VideoWriter(str(args.out/'overlay.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),10,(im.shape[1],im.shape[0]))
                    if not writer.isOpened():raise RuntimeError('Video writer unavailable')
                writer.write(rendered)
                if pairs and max(x['overlap'] for x in pairs)>=.15 and len(list(args.out.glob('sample_*.jpg')))<8:
                    cv2.imwrite(str(args.out/f"sample_{row['frame_id']}.jpg"),rendered)
        finally:
            if writer:writer.release()
        with (args.out/'decisions.jsonl').open('w') as f:
            for rec in records:f.write(json.dumps(rec)+'\n')
        result=dict(samples=len(records),parts_real_osnet=args.parts,split=args.split,
            source_capture_sha256=meta['source_capture_sha256'],observations_sha256=meta['observations_sha256'],
            C=dict(status='NOT_RUN_NO_SEGMENTATION_WEIGHTS',reason='Use occlusion_shadow --video --seg-model with an existing local model'),
            baseline_scope='Current selector using historical cached full-body scores; no claim of original live schedule parity',
            historical_source_differences=[k for k,v in meta['baseline_source_sha256'].items() if digest(k)!=v],
            independent_gt=False,physical_commands=0)
        for variant in ('A','B'):
            rs=[x[variant] for x in records]
            result[variant]=dict(selected=sum(r['bbox'] is not None for r in rs),reasons=dict(Counter(r['reason'] for r in rs)),
                policy_control_interruption_sample_interval_s=sum(records[i+1]['timestamp']-records[i]['timestamp'] for i in range(len(rs)-1) if not rs[i]['control_eligible']),
                localization_recall=None,identity_switch_count=None,lost_target_duration=None,actual_ptz_interruption_s=None)
        (args.out/'summary.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))

if __name__=='__main__':main()
