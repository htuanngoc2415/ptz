"""Read-only A/B/C diagnostic observer. Does not instantiate a motor controller."""
import argparse,copy,json,time,uuid
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
import cv2,numpy as np
import config
from tools.identity_replay import offline_guard,digest,dry_pan
from tools.occlusion_benchmark import baseline,observations,overlay
from tools.occlusion_association import OcclusionAssociation
from runtime.byte_tracking import ByteTrackPersonTracker
from runtime.camera_motion import CameraMotionEstimator


def infer(model,tracker,reid,reference,image,timestamp,frame_id,device,segmentation=False):
    result=model.predict(image,classes=[0],conf=.1,device=device,verbose=False)[0]
    raw=[dict(bbox=tuple(map(float,b.xyxy[0].cpu().tolist())),confidence=float(b.conf[0])) for b in result.boxes]
    people=tracker.update(raw,timestamp,image,frame_id)
    used={p['tracker_source_index'] for p in people}
    people += [dict(p,track_id=None,tracker_source_index=i) for i,p in enumerate(raw) if i not in used]
    h,w=image.shape[:2];crops=[]
    for p in people:
        x1,y1,x2,y2=map(int,p['bbox']);crop=image[max(0,y1):min(h,y2),max(0,x1):min(w,x2)]
        if not crop.size:raise ValueError('Invalid detected crop')
        crops.append(crop)
    vectors=reid.extract_embeddings(crops).cpu().numpy() if crops else []
    masks=[] if segmentation else None
    all_masks=[]
    if segmentation and people:
        if result.masks is None:raise ValueError('Segmentation result lacks masks')
        all_masks=[cv2.resize(m.cpu().numpy(),(w,h),interpolation=cv2.INTER_NEAREST)>.5 for m in result.masks.data]
    for i,(p,v) in enumerate(zip(people,vectors)):
        p.update(candidate_id=str(i),embedding=v.tolist(),reid_score=float(v@reference))
        if segmentation:
            if result.masks is None:raise ValueError('Segmentation result lacks masks')
            index=p['tracker_source_index'];mask=all_masks[index].copy()
            for j,other in enumerate(all_masks):
                if j!=index:mask &= ~other
            masks.append(mask)
    return people,observations(image,people,reid,masks),len(raw)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True);source.add_argument('--video',type=Path);source.add_argument('--live-shadow',action='store_true')
    parser.add_argument('--reference',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--camera-config',type=Path,default=Path('camera.json'));parser.add_argument('--seconds',type=float,default=30.)
    parser.add_argument('--seg-model',type=Path);parser.add_argument('--device',default='cpu')
    args=parser.parse_args()
    if args.seconds<=0:parser.error('seconds must be positive')
    if args.video and not args.video.is_file():parser.error('Local video required')
    if args.seg_model and not args.seg_model.is_file():parser.error('Local segmentation weights required; never auto-download')
    args.out.mkdir(parents=True,exist_ok=False)
    with (nullcontext() if args.live_shadow else offline_guard()):
        import torch
        from ultralytics import YOLO
        from person_search.reid import OSNetReID
        cv2.setNumThreads(1);torch.set_num_threads(1)
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        cfg.TRACK_SNAPSHOT_ENABLED=False;cfg.CAMERA_MOTION_DIAGNOSTICS_ENABLED=False;cfg.CAMERA_MOTION_RECORD_ENABLED=False
        if not Path(cfg.PERSON_MODEL_PATH).is_file():raise FileNotFoundError('Local detector missing')
        model=YOLO(str(cfg.PERSON_MODEL_PATH));seg=YOLO(str(args.seg_model)) if args.seg_model else None
        if seg and seg.task!='segment':raise ValueError('Provided model is not instance segmentation')
        reid=OSNetReID(cfg.REID_MODEL_PATH,device=args.device);reference=reid.extract_embedding(args.reference).cpu().numpy()
        a=baseline(cfg,reference);b=OcclusionAssociation(reference,'s');c=OcclusionAssociation(reference,'s')
        tracker=ByteTrackPersonTracker(cfg);segtracker=ByteTrackPersonTracker(cfg) if seg else None
        if args.live_shadow:
            from camera.opencv_player import OpenCVPlayer
            uri=OpenCVPlayer(json.loads(args.camera_config.read_text()),lambda _:None)._uri()
            times=None
        else:
            from tools.phase3_replay import pts
            uri=str(args.video.resolve());times=pts(args.video)
        cap=cv2.VideoCapture(uri,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,3000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,3000])
        if not cap.isOpened():raise RuntimeError('Capture unavailable (credentials redacted)')
        writer=None;records=[];previous=None;gmc=CameraMotionEstimator();start=time.monotonic();fid=0
        try:
            while time.monotonic()-start<args.seconds:
                ok,image=cap.read()
                if not ok:break
                received=time.monotonic();timestamp=received if times is None else times[fid]
                people,obs,raw_count=infer(model,tracker,reid,reference,image,timestamp,fid,args.device)
                motion_state='UNKNOWN';transform=None;c_transform=None
                if previous:
                    old,old_t,old_boxes=previous
                    m=gmc.estimate(old,image,old_t,timestamp,old_boxes,[p['bbox'] for p in people])
                    if m.valid:
                        motion_state='STATIC' if m.classification=='LOW_IMAGE_MOTION' else 'MOVING'
                        if b.observed==old_t:transform=m.transform
                        if c.observed==old_t:c_transform=m.transform
                previous=(image.copy(),timestamp,[p['bbox'] for p in people])
                age=time.monotonic()-received
                # Replay age is compute age too: never treat slow inference as fresh live control evidence.
                a.frame_id=fid;a.frame_age_ms=age*1000
                selected=a.select_person(image,people,timestamp) if age<=cfg.MAX_CONTROL_OBSERVATION_AGE else None
                a.finish_frame(selected,None)
                ar=dict(state=a.stage,reason=a.transition_reason,bbox=selected['bbox'] if selected else None,control_eligible=selected is not None)
                br=b.observe('s',fid,timestamp,timestamp+age,obs,camera_motion=motion_state,camera_transform=transform)
                cr=None
                if seg:
                    cp,co,cn=infer(seg,segtracker,reid,reference,image,timestamp,fid,args.device,True)
                    cr=c.observe('s',fid,timestamp,timestamp+time.monotonic()-received,co,
                        camera_motion=motion_state,camera_transform=c_transform)
                record=dict(frame_id=fid,timestamp=timestamp,observation_age=age,raw_detections=raw_count,tracklets=len(people),
                    A=ar,B=br,C=cr,camera_motion=motion_state,physical_commands=0,
                    B_shadow_pan=dry_pan(br['bbox'],image.shape,age,cfg))
                records.append(record)
                rendered=overlay(image,people,ar,br,raw_count)
                if writer is None:
                    writer=cv2.VideoWriter(str(args.out/'overlay.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),10,(image.shape[1],image.shape[0]))
                    if not writer.isOpened():raise RuntimeError('Overlay unavailable')
                writer.write(rendered);fid+=1
        except KeyboardInterrupt:pass
        finally:
            cap.release()
            if writer:writer.release()
            (args.out/'decisions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in records))
            (args.out/'summary.json').write_text(json.dumps(dict(frames=len(records),physical_commands=0,
                source_sha256=digest(args.video) if args.video else None,reference_sha256=digest(args.reference),
                C='RUN' if seg else 'NOT_RUN_NO_LOCAL_SEG_WEIGHTS',independent_gt=False,
                timestamp_basis='host_after_decode' if args.live_shadow else 'file_pts_plus_compute_age',
                live_warning='Diagnostic sequential observer: decode/network backlog not measured; never control authority',
                localization_recall=None,identity_switch_count=None,lost_target_duration=None,actual_ptz_interruption_s=None),indent=2))
        print('Recorded',len(records),'diagnostic frames; physical commands=0')

if __name__=='__main__':main()
