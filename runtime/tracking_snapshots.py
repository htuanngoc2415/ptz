"""Opt-in, bounded asynchronous geometry snapshots. No embeddings or names."""
import copy,json,queue,threading,time,atexit
from pathlib import Path
from telemetry import latency

class TrackingSnapshots:
    def __init__(self,cfg):
        self.cfg=cfg;self.pending=queue.Queue(maxsize=1);self.closed=False
        self.last=float('-inf');self.thread=None
    def submit(self,frame,metadata,now):
        if not getattr(self.cfg,'TRACK_SNAPSHOT_ENABLED',False) or self.closed:return False
        if now-self.last < getattr(self.cfg,'TRACK_SNAPSHOT_COOLDOWN',1.):return False
        if frame is None or frame.size>24_000_000:return False
        if self.thread is None:
            self.thread=threading.Thread(target=self._run,name='tracking-snapshots',daemon=True)
            self.thread.start();atexit.register(self.close)
        if self.pending.full():
            latency.profiler.count('tracking_snapshot_dropped');return False
        try:self.pending.put_nowait((frame.copy(),copy.deepcopy(metadata)))
        except queue.Full:
            latency.profiler.count('tracking_snapshot_dropped');return False
        self.last=now;return True
    def close(self):
        self.closed=True
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=2)
    def _run(self):
        while not self.closed or not self.pending.empty():
            try:frame,metadata=self.pending.get(timeout=.1)
            except queue.Empty:continue
            try:self._write(frame,metadata)
            except Exception as exc:
                latency.profiler.event('TRACKER_SNAPSHOT_FAILED',error_type=type(exc).__name__)
            finally:self.pending.task_done()
    def _write(self,frame,metadata):
        import cv2
        root=Path(self.cfg.TRACK_SNAPSHOT_DIRECTORY);root.mkdir(parents=True,exist_ok=True)
        # Clean interrupted writes from this recorder only, never unrelated files.
        for path in root.glob('ptz_snapshot_*.jpg'):
            base=path.stem.removesuffix('_raw').removesuffix('_annotated')
            if not (root/(base+'.json')).exists():path.unlink()
        stem='ptz_snapshot_'+str(time.monotonic_ns());annotated=frame.copy()
        for row in metadata.get('detections',[]):
            x1,y1,x2,y2=map(int,row['bbox']);cv2.rectangle(annotated,(x1,y1),(x2,y2),(0,255,255),1)
        for row in metadata.get('tracks',[]):
            x1,y1,x2,y2=map(int,row['observed_bbox']);tid=row['tracker_id']
            color=(0,255,0) if tid==metadata.get('selected_target') else (255,128,0)
            cv2.rectangle(annotated,(x1,y1),(x2,y2),color,2)
            cv2.putText(annotated,f"ID {tid} src {row['detection_index']} c{row['class_id']} {row['confidence']:.2f}",
                (x1,max(12,y1)),cv2.FONT_HERSHEY_SIMPLEX,.4,color,1)
        cv2.putText(annotated,str(metadata.get('trigger')),(8,20),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,0,255),1)
        # Encode on the writer thread; enforce per-bundle and total byte limits.
        encoded=[]
        for suffix,img in [('_raw.jpg',frame),('_annotated.jpg',annotated)]:
            ok,data=cv2.imencode('.jpg',img,[cv2.IMWRITE_JPEG_QUALITY,90])
            if not ok:raise ValueError('JPEG encode failed')
            encoded.append((suffix,data.tobytes()))
        encoded.append(('.json',json.dumps(metadata,ensure_ascii=False).encode()))
        budget=max(1,int(getattr(self.cfg,'TRACK_SNAPSHOT_MAX_BYTES',64_000_000)))
        if sum(len(data) for _,data in encoded)>budget:
            latency.profiler.count('tracking_snapshot_too_large');return
        for suffix,data in encoded:(root/(stem+suffix)).write_bytes(data)
        limit=max(1,min(100,self.cfg.TRACK_SNAPSHOT_MAX_FILES))
        bundles=sorted(root.glob('ptz_snapshot_*.json'))
        files=list(root.glob('ptz_snapshot_*'));size=sum(p.stat().st_size for p in files if p.is_file())
        while bundles and (len(bundles)>limit or size>budget):
            old=bundles.pop(0)
            for p in root.glob(old.stem+'*'):
                if p.is_file():size-=p.stat().st_size;p.unlink()
