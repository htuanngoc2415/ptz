#!/usr/bin/env python3
"""Same-video face-branch A/B replay with fixed, explicitly supplied target ROI.

This isolates face scheduling; it does not benchmark OSNet, YOLO or physical PTZ.
Use a single-person clip or per-frame annotations for accuracy assessment elsewhere.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import cv2
import config
from ai.face import FaceDetector, FaceRecognizer, FaceDatabase, FaceMatcher
from runtime.face_worker import FaceWorker
from telemetry import latency


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video')
    p.add_argument('--roi',type=int,nargs=4,required=True,metavar=('X1','Y1','X2','Y2'))
    p.add_argument('--start',type=float,default=0)
    p.add_argument('--seconds',type=float,default=5)
    p.add_argument('--fps',type=float,default=10)
    p.add_argument('--provider',default='CPUExecutionProvider')
    p.add_argument('--output',default='logs/face_ab.json')
    args=p.parse_args()
    if args.fps <= 0 or args.seconds <= 0:
        p.error('fps and seconds must be positive')
    detector=FaceDetector(config.FACE_DETECT_MODEL,providers=[args.provider],
                          input_size=config.FACE_DET_INPUT_SIZE,conf_threshold=config.FACE_DET_CONF)
    recognizer=FaceRecognizer(config.FACE_RECOGNITION_MODEL,providers=[args.provider])
    matcher=FaceMatcher(FaceDatabase(config.FACE_DATABASE_PATH),config.FACE_RECOGNITION_THRESHOLD)
    report=dict(video=args.video,start=args.start,seconds=args.seconds,fps=args.fps,roi=args.roi,
                providers=recognizer.session.get_providers(),scope='face branch; fixed ROI; no identity ground truth',
                accuracy=dict(false_match=None,identity_switch=None),
                full_system=dict(yolo_fps=None,zoom_response_ms=None,gpu_utilization=None,cpu_utilization=None),runs={})
    for mode in ('baseline','optimized'):
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        cfg.FACE_PIPELINE_MODE=mode
        profiler=latency.configure(directory=str(Path(args.output).parent / mode),enabled=True)
        worker=FaceWorker(detector,recognizer,matcher,cfg)
        cap=cv2.VideoCapture(args.video)
        if not cap.isOpened():
            worker.close();profiler.close();raise RuntimeError('Cannot open video')
        started=time.monotonic()
        identities=[]; detected=set(); frame=0
        def consume():
            spatial=worker.take()
            if spatial and spatial['face']:
                detected.add(spatial['job'].frame_id)
            while True:
                result=worker.take_identity()
                if result is None:
                    break
                identities.append(dict(frame=result['job'].frame_id,**result['recognition']))
        try:
            for frame in range(1,int(args.seconds*args.fps)+1):
                cap.set(cv2.CAP_PROP_POS_MSEC,(args.start+(frame-1)/args.fps)*1000)
                ok,image=cap.read()
                if not ok:
                    break
                delay=started+(frame-1)/args.fps-time.monotonic()
                if delay>0:
                    time.sleep(delay)
                consume()
                worker.submit(frame,time.monotonic(),image,dict(bbox=tuple(args.roi),track_id=1),
                              None,(dict(bbox=tuple(args.roi),track_id=1),),'replay','replay',1)
            with worker.condition:
                worker.condition.wait_for(lambda: worker.completed_frame==frame and not worker.recognition_busy,timeout=15)
            consume()
            report['runs'][mode]=dict(wall_seconds=time.monotonic()-started,frames_submitted=frame,
                detected_observations=len(detected),identities=identities,counters=profiler.snapshot(),
                latencies=profiler.summary())
        finally:
            cap.release();worker.close(timeout=None);profiler.close()
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report,indent=2))
    print(target)

if __name__=='__main__':
    main()
