"""Explicit manual recorder. No production imports, inference, PTZ or RPC.
Host receive timestamps are NOT camera exposure times. Decoder/network losses
cannot be counted without source RTP sequence instrumentation; report unknown.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import threading
import time
import cv2
import numpy as np


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--record',action='store_true');ap.add_argument('--out',required=True)
    ap.add_argument('--seconds',type=float,default=120);ap.add_argument('--uri-env',default='PHASE3_RTSP_URI')
    ap.add_argument('--pace-file',action='store_true',help='Pace local smoke input at nominal fps');ap.add_argument('--input-file',help='Local-file recorder smoke test only');a=ap.parse_args()
    if not a.record:ap.error('OFF by default: pass --record explicitly')
    if not 0<a.seconds<=1800:ap.error('seconds must be in (0,1800]')
    if a.input_file:
        source=str(Path(a.input_file).resolve())
        if not Path(source).is_file():ap.error('Local file missing')
    else:
        source=os.environ.get(a.uri_env,'')
        if not source.startswith(('rtsp://','rtsps://')):ap.error('Set RTSP URI in the requested environment variable')
    out=Path(a.out);out.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS','rtsp_transport;tcp')
    cv2.setNumThreads(1)
    cap=cv2.VideoCapture(source,cv2.CAP_FFMPEG,[cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,3000,cv2.CAP_PROP_READ_TIMEOUT_MSEC,3000])
    if not cap.isOpened():raise RuntimeError('Capture open failed (URI intentionally omitted)')
    nominal=cap.get(cv2.CAP_PROP_FPS);nominal=nominal if np.isfinite(nominal) and nominal>0 else 30.
    q=queue.Queue(maxsize=16); errors=[];rows=[];drops=[]; received=[]
    def write():
        writer=None
        try:
            with (out/'frames.jsonl').open('w') as f:
                while True:
                    item=q.get()
                    if item is None:break
                    frame,row=item
                    if writer is None:
                        h,w=frame.shape[:2];writer=cv2.VideoWriter(str(out/'video.avi'),cv2.VideoWriter_fourcc(*'FFV1'),nominal,(w,h))
                        if not writer.isOpened():raise RuntimeError('FFV1 writer unavailable')
                    row['video_frame_index']=len(rows);begin=time.perf_counter();writer.write(frame)
                    row['encode_ms']=(time.perf_counter()-begin)*1000;rows.append(row);f.write(json.dumps(row)+'\n');f.flush()
        except Exception as e:errors.append(type(e).__name__)
        finally:
            if writer is not None:writer.release()
    worker=threading.Thread(target=write,name='phase3-writer');worker.start()
    start=time.monotonic(); seq=0;failures=0;shape=None;interrupted=False
    try:
        while time.monotonic()-start<a.seconds and not errors:
            ok,frame=cap.read()
            if a.input_file and a.pace_file:time.sleep(max(0,start+seq/nominal-time.monotonic()))
            now=time.monotonic()
            if not ok:failures+=1;break
            if shape is not None and shape!=frame.shape:errors.append('ResolutionChanged');break
            shape=frame.shape;received.append(now)
            row=dict(frame_id=seq,receive_monotonic=now,receive_unix_ns=time.time_ns(),capture_timestamp=None,
                width=shape[1],height=shape[0],receive_gap=None if len(received)==1 else now-received[-2],
                timestamp_basis='host_after_decode',source_pts=None)
            try:q.put_nowait((frame,row))
            except queue.Full:drops.append(seq)
            seq+=1
    except KeyboardInterrupt:interrupted=True
    finally:
        cap.release()
        while worker.is_alive():
            try:q.put(None,timeout=.1);break
            except queue.Full:continue
        worker.join()
    count=0;digest=hashlib.sha256();path=out/'video.avi'
    if path.exists():
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):digest.update(chunk)
        check=cv2.VideoCapture(str(path))
        while True:
            ok,frame=check.read()
            if not ok:break
            count+=1
        check.release()
    gaps=np.diff(received)
    manifest=dict(schema='phase3_capture_v1',source_type='local_smoke' if a.input_file else 'manual_rtsp',paced_smoke=a.pace_file,
        received_frames=seq,written_frames=len(rows),decoded_integrity_frames=count,integrity_ok=count==len(rows)>0 and not errors,
        video_sha256=digest.hexdigest() if path.exists() else None,queue_dropped_frames=len(drops),queue_dropped_ids=drops,
        read_failures_or_eof=failures,upstream_network_decoder_drops=None,errors=errors,interrupted=interrupted,
        nominal_container_fps=nominal,measured_receive_fps=(len(received)-1)/(received[-1]-received[0]) if len(received)>1 else None,
        receive_gap_p95=float(np.percentile(gaps,95)) if len(gaps) else None,receive_gap_max=float(max(gaps)) if len(gaps) else None,
        capture_timestamp_available=False,source_pts_available=False,pose_available=False,
        note='Use frames.jsonl receive times; AVI playback fps is not source timing. Local smoke receive FPS is decode throughput.')
    (out/'manifest.json').write_text(json.dumps(manifest,indent=2));print(json.dumps(manifest,indent=2))
    if not manifest['integrity_ok']:raise SystemExit(2)


if __name__=='__main__':main()
