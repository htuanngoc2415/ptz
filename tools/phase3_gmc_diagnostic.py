"""Separate offline background diagnostic using the existing estimator.
Only use reviewed conservative foreground exclusion masks. This does not produce
camera-corrected target predictions or validate PTZ/target localization accuracy.
"""
import argparse,json,time
from pathlib import Path
from collections import Counter
import cv2
import numpy as np
from runtime.camera_motion import CameraMotionEstimator
from tools.phase3_replay import local_video,pts,sha256


def main():
    p=argparse.ArgumentParser();p.add_argument('--video',required=True);p.add_argument('--masks',required=True);p.add_argument('--out',required=True);p.add_argument('--enable',action='store_true');a=p.parse_args()
    if not a.enable:p.error('OFF by default; --enable required')
    source=local_video(a.video);digest=sha256(source);mask=json.loads(Path(a.masks).read_text())
    if mask['source_sha256']!=digest or not mask.get('reviewer'):raise ValueError('Reviewed source-bound masks required')
    times=pts(source);cap=cv2.VideoCapture(str(source));cv2.setNumThreads(1);cv2.setRNGSeed(0);est=CameraMotionEstimator();rows=[];prev=None
    for fid,t in enumerate(times):
        ok,frame=cap.read()
        if not ok:raise RuntimeError('Decode failure')
        frame=cv2.resize(frame,(mask['width'],round(frame.shape[0]*mask['width']/frame.shape[1])))
        boxes=mask['foreground_exclusion_boxes']
        if prev is not None:
            r=est.estimate(prev,frame,times[fid-1],t,boxes,boxes,mask.get('osd_boxes',[])).payload()
            if isinstance(r['transform'],np.ndarray):r['transform']=r['transform'].tolist()
            rows.append(dict(frame_id=fid,**r))
        prev=frame
    cap.release();out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    (out/'frames.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    good=[r for r in rows if r['valid']]
    summary=dict(source_sha256=digest,pairs=len(rows),valid=len(good),reasons=dict(Counter(r['reason'] for r in rows)),
        classifications=dict(Counter(r['classification'] for r in good)),latency_p95_ms=float(np.percentile([r['latency_ms'] for r in rows],95)),
        residual_median_px=float(np.median([r['reprojection_error'] for r in good])) if good else None,
        inlier_ratio_median=float(np.median([r['inlier_ratio'] for r in good])) if good else None,
        pose_ground_truth=False,target_localization_accuracy=None,ptz_validation=False,
        note='Static/reencoded excerpt diagnostic only; low background residual is not evidence of better target localization.')
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
