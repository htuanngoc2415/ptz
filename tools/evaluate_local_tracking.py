"""Offline body-flow feasibility probe, not identity accuracy or a live controller.
Uses the existing forward/backward KLT implementation with a bounded body ROI.
No output from this tool is admitted to PTZ or face recognition.
"""
import argparse,json,sys,time,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
import cv2,numpy as np
from runtime.face_tracking import FaceTracker
from ai.reid_target import iou

class LocalBodyProbe:
    def __init__(self,max_age=.2):
        cfg=SimpleNamespace(FACE_TRACK_MAX_DIMENSION=640,FACE_TRACK_PYRAMID_LEVELS=2,
            FACE_TRACK_MAX_AGE=max_age,FACE_TRACK_FB_ERROR=1.,FACE_TRACK_MIN_CONFIDENCE=.7,
            FACE_TRACK_MAX_SCALE_CHANGE=.08)
        self.tracker=FaceTracker(cfg);self.session=None;self.last=None
    def seed(self,frame,box,t,session,verified=True):
        self.tracker.reset();self.session=session;self.last=t
        if verified and session and math.isfinite(t):self.tracker.correct(frame,{'bbox':box},t)
    def update(self,frame,t,session,camera_moving=False):
        if (session!=self.session or not math.isfinite(t) or self.last is None or t<=self.last or camera_moving):
            self.tracker.reset();return None
        self.last=t;r=self.tracker.update(frame,t)
        if r is None:return None
        return dict(bbox=r['bbox'],timestamp=t,detector_timestamp=self.tracker.detected,
            source='LOCAL_VISUAL_ONLY',identity_confirmed=False,ptz_authorized=False,
            confidence=r['tracking_confidence'])

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--video',type=Path,required=True)
    p.add_argument('--frames',type=int,default=180);p.add_argument('--detector-stride',type=int,default=3)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    if a.frames<1 or a.detector_stride<1:p.error('Positive frame count and stride required')
    if a.output.exists():p.error('Output exists')
    if not a.video.is_file():p.error('Local video file required')
    from ultralytics import YOLO
    import config
    model=YOLO(config.PERSON_MODEL_PATH);cap=cv2.VideoCapture(str(a.video));fps=cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or not math.isfinite(fps) or fps<=0:raise RuntimeError('Video/fps unavailable')
    flow=LocalBodyProbe();prev=None;detections=local=0;timings=[];agreements=[];events=[]
    try:
        for fid in range(a.frames):
            ok,frame=cap.read()
            if not ok:break
            frame=cv2.resize(frame,(704,480));t=fid/fps;beg=time.perf_counter()
            r=flow.update(frame,t,'offline')
            timings.append((time.perf_counter()-beg)*1000)
            if r:local+=1
            if fid%a.detector_stride==0:
                result=model.predict(frame,device=config.DEVICE,verbose=False,classes=[0])[0]
                boxes=[tuple(map(float,b)) for b in result.boxes.xyxy.cpu().numpy()]
                # Geometric probe association only. Never called an OSNet identity.
                ranked=sorted(boxes,key=lambda b:iou(prev,b) if prev else (b[2]-b[0])*(b[3]-b[1]),reverse=True)
                box=ranked[0] if ranked else None
                if prev and (box is None or iou(prev,box)<.25 or (len(ranked)>1 and iou(prev,ranked[1])>=iou(prev,box)-.1)):box=None
                if box:
                    detections+=1
                    if r:agreements.append(iou(box,r['bbox']))
                    flow.seed(frame,box,t,'offline');prev=box
                else:flow.seed(frame,(0,0,1,1),t,'offline',verified=False);prev=None
            events.append(dict(frame_id=fid,video_pts_s=t,local_available=r is not None,
                               source='offline_geometry_probe',ptz_authorized=False))
    finally:cap.release()
    n=len(events);seconds=n/fps
    report=dict(video=str(a.video),frames=n,video_seconds=seconds,detector_stride=a.detector_stride,
        detector_roi_updates=detections,local_observations=local,local_updates_per_video_second=local/seconds if seconds else 0,
        local_cpu_mean_ms=float(np.mean(timings)) if timings else None,local_cpu_p95_ms=float(np.percentile(timings,95)) if timings else None,
        detector_agreement_iou_mean=float(np.mean(agreements)) if agreements else None,agreement_samples=len(agreements),
        correct_target_time=None,id_switches=None,full_target_losses=None,physical_speed=None,
        exposure_to_actuation_ms=None,ground_truth=False,camera_motion_compensated=False,
        caveat='ROI selected by geometry, no OSNet target annotations. Local flow measures feasibility only; no live integration.')
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2))
    a.output.with_suffix('.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in events));print(json.dumps(report,indent=2))
if __name__=='__main__':main()
