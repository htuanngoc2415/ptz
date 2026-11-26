"""Local-file-only matched A/B/C replay. No inference or camera/control imports.
C needs independently trusted, exact-PTS detector corrections supplied as JSON.
Without them C is explicitly unavailable; it is never relabelled KLT as YOLO.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import time
import cv2
import numpy as np
from tools.phase3_local import LocalTracker,Options,iou


def sha256(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def local_video(path):
    p=Path(path).resolve()
    if not p.is_file(): raise ValueError('A local video file is required')
    return p


def pts(path):
    p=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',str(local_video(path))],capture_output=True,check=True)
    times=[float(f['best_effort_timestamp_time']) for f in json.loads(p.stdout)['frames']]
    if not times or not np.isfinite(times).all() or np.any(np.diff(times)<=0): raise ValueError('Missing/nonmonotonic PTS')
    return times


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--video',required=True);ap.add_argument('--out',required=True)
    ap.add_argument('--source-type',default='unknown',choices=['unknown','monitor_recording','reencoded_camera_excerpt']);ap.add_argument('--anchors',required=True);ap.add_argument('--corrections');ap.add_argument('--start',type=int,default=450)
    ap.add_argument('--frames',type=int,default=300);ap.add_argument('--width',type=int,default=960)
    ap.add_argument('--capture-manifest',help='phase3_record manifest: use receive times rather than AVI nominal PTS');ap.add_argument('--enable-poc',action='store_true');ap.add_argument('--review',action='store_true')
    a=ap.parse_args()
    if a.frames<2 or a.start<0 or a.width<64:ap.error('frames >= 2, start >= 0, width >= 64 required')
    video=local_video(a.video); times=pts(video); digest=sha256(video)
    if a.start>=len(times)-1:ap.error('start needs at least two remaining frames')
    timestamp_basis='ffprobe_best_effort_timestamp_time'
    source_ids={}
    if a.capture_manifest:
        mp=Path(a.capture_manifest);manifest=json.loads(mp.read_text())
        if manifest['video_sha256']!=digest or not manifest['integrity_ok']:raise ValueError('Recorder integrity mismatch')
        captured=[json.loads(line) for line in (mp.parent/'frames.jsonl').read_text().splitlines()]
        if len(captured)!=len(times) or [r['video_frame_index'] for r in captured]!=list(range(len(times))):raise ValueError('Recorder frame mapping mismatch')
        times=[r['receive_monotonic']-captured[0]['receive_monotonic'] for r in captured]
        if not np.isfinite(times).all() or np.any(np.diff(times)<=0):raise ValueError('Recorder timestamps invalid')
        source_ids={i:r['frame_id'] for i,r in enumerate(captured)}
        timestamp_basis='recorder_host_receive_relative_not_camera_capture'
    out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    anchors=json.loads(Path(a.anchors).read_text());corrections=json.loads(Path(a.corrections).read_text()) if a.corrections else None
    for pack in [anchors]+([corrections] if corrections else []):
        if pack['source_sha256']!=digest or pack['width']!=a.width:raise ValueError('Source/coordinate mismatch')
    anchors={int(r['frame_id']):r for r in anchors['rows']}
    corr={int(r['frame_id']):r for r in corrections['rows']} if corrections else {}
    cv2.setNumThreads(1); cv2.setRNGSeed(0)
    trackers={k:LocalTracker(Options(enabled=a.enable_poc)) for k in ['B']+(['C'] if corrections else [])}
    cap=cv2.VideoCapture(str(video)); cap.set(cv2.CAP_PROP_POS_FRAMES,a.start)
    rows=[]; latencies={k:[] for k in ['A']+list(trackers)}; history=[]; base=None; writer=None
    review=[]; start=time.perf_counter(); cpu=time.process_time(); decode=[]; previous=None
    if a.review:(out/'frames').mkdir()
    for fid in range(a.start,min(len(times),a.start+a.frames)):
        begin=time.perf_counter();ok,frame=cap.read();decode.append((time.perf_counter()-begin)*1000)
        if not ok:raise RuntimeError('Early decode failure')
        h,w=frame.shape[:2];frame=cv2.resize(frame,(a.width,round(h*a.width/w))); t=times[fid]
        p=dict(kind='manual_experiment_anchor',reviewer='coding_agent_visual_ROI_only_identity_unknown',source_sha256=digest,session=digest,timestamp=t,frame_id=fid)
        anchor=anchors.get(fid); outputs={}
        for k,tr in trackers.items():
            begin=time.perf_counter()
            attempted=bool(anchor) or tr.box is not None
            if anchor:
                p['reviewer']=anchor['reviewer'];state=tr.initialize(frame,anchor['bbox'],t,p)
            else:state=tr.update(frame,t,fid,digest)
            if k=='C' and fid in corr:
                d=corr[fid]
                # Exact trusted provenance is required, never nearest-box snapping.
                if d['provenance'].get('kind')!='trusted_detection':raise ValueError('C requires trusted detection')
                state=tr.correct(frame,d)
            elapsed=(time.perf_counter()-begin)*1000;latencies[k].append(elapsed)
            state.update(active_attempt=attempted,frame_id=fid,media_timestamp=t,observation_age=elapsed/1000 if state['bbox'] else None,
                         observation_age_basis='offline_compute_only_not_live_capture_age',latency_ms=elapsed)
            outputs[k]=state
        begin=time.perf_counter()
        if anchor and a.enable_poc:
            base=np.asarray(anchor['bbox'],float); history.append((t,base.copy()))
        dt=t-history[-1][0] if history else float('inf')
        velocity=(history[-1][1]-history[-2][1])/(history[-1][0]-history[-2][0]) if len(history)>1 else np.zeros(4)
        pred=base+velocity*dt if base is not None and dt<=.8 else None
        if pred is not None and not LocalTracker.valid_box(pred,frame.shape):pred=None
        ms=(time.perf_counter()-begin)*1000;latencies['A'].append(ms)
        outputs['A']=dict(bbox=pred.tolist() if pred is not None else None,bbox_source='CONSTANT_VELOCITY',identity_status='UNVERIFIED_LOCALIZATION_ONLY',tracking_status='PROPAGATED' if pred is not None else 'NO_VALID_ANCHOR',observation_age=dt if pred is not None else None,is_valid_for_control_estimation=False,control_authorized=False,frame_id=fid,capture_timestamp=None,media_timestamp=t,localization_confidence=None,latency_ms=ms)
        rows.append(dict(frame_id=fid,source_frame_id=source_ids.get(fid),media_timestamp=t,frame_gap=None if previous is None else t-previous,outputs=outputs));previous=t
        if a.review:
            name=f'frames/{fid:06d}.jpg';cv2.imwrite(str(out/name),frame,[cv2.IMWRITE_JPEG_QUALITY,92])
            review.append(dict(frame_id=fid,media_timestamp=t,image=name,bbox=None,visibility='UNKNOWN',camera_motion='UNKNOWN',event='UNKNOWN',identity='UNKNOWN',reviewed=False))
        overlay=frame.copy()
        for k,s in outputs.items():
            if s['bbox']:
                x1,y1,x2,y2=map(round,s['bbox']);cv2.rectangle(overlay,(x1,y1),(x2,y2),{'A':(255,0,0),'B':(0,255,255),'C':(0,255,0)}[k],2)
        cv2.putText(overlay,f"frame {fid} B: {outputs['B']['tracking_status']} | LOCAL ONLY",(12,24),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,0,255),1)
        if writer is None:
            writer=cv2.VideoWriter(str(out/'overlay.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),1/float(np.median(np.diff(times))), (overlay.shape[1],overlay.shape[0]))
            if not writer.isOpened():raise RuntimeError('Writer failed')
        writer.write(overlay)
    cap.release()
    if writer:writer.release()
    wall=time.perf_counter()-start; used=time.process_time()-cpu
    duration=times[rows[-1]['frame_id']]-times[a.start] if len(rows)>1 else 0
    summary=dict(source=str(video),source_sha256=digest,coordinate_width=a.width,frame_count=len(rows),media_seconds=duration,
        source_type=a.source_type,capture_timestamp_available=False,independent_gt=False,flags={'enabled':a.enable_poc},anchor_input=anchors,
        pts_basis=timestamp_basis,threads=cv2.getNumThreads(),opencv=cv2.__version__,
        decode_resize_write_included_in_wall=True,wall_seconds=wall,cpu_seconds=used,cpu_percent_one_core=used/wall*100,
        pipeline_throughput_fps=len(rows)/wall,rss_peak_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
        gpu='not used by this CPU-only experiment; other-process GPU load not measured',
        decode_ms_p95=float(np.percentile(decode,95)),frame_gap_p95=float(np.percentile(np.diff([r['media_timestamp'] for r in rows]),95)),
        C_status='RUN' if corrections else 'UNAVAILABLE: no synchronized independently trusted YOLO corrections',
        accuracy_metrics=None,wrong_direction_time=None,takeover_count=None,recovery_accuracy=None,static_vs_ptz=None,variants={})
    for k,lat in latencies.items():
        states=[r['outputs'][k] for r in rows];valid=[s for s in states if s['bbox']]
        active=[s['latency_ms'] for s in states if s.get('active_attempt',s['bbox'] is not None)]
        summary['variants'][k]=dict(valid_frames=len(valid),invalid_frames=len(rows)-len(valid),valid_updates_per_media_second=len(valid)/duration,
            latency_ms_p50=float(np.percentile(lat,50)),latency_ms_p95=float(np.percentile(lat,95)),
            active_attempt_latency_p95=float(np.percentile(active,95)) if active else None,
            observation_age_p95=float(np.percentile([s['observation_age'] for s in valid],95)) if valid else None,
            states=dict(Counter(s['tracking_status'] for s in states)))
    (out/'pts.json').write_text(json.dumps(times))
    (out/'summary.json').write_text(json.dumps(summary,indent=2));(out/'observations.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    if a.review:
        pack=dict(schema='phase3_manual_bbox_v1',source_sha256=digest,width=a.width,height=frame.shape[0],reviewer='',rows=review)
        (out/'review_pack.json').write_text(json.dumps(pack,indent=2))
        template=Path(__file__).with_name('phase3_review.html').read_text()
        (out/'review.html').write_text(template.replace('/*PACK*/',json.dumps(pack)))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
