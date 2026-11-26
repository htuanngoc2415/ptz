"""Bounded opt-in raw decoded frame recorder. Never opens a camera or motor."""
import atexit,json,queue,threading,time,hashlib
from pathlib import Path
from uuid import uuid4
import cv2


class MotionRecorder:
    def __init__(self,cfg, *, stop_on_limit=False):
        self.stop_on_limit = stop_on_limit
        self.root=Path(cfg.CAMERA_MOTION_RECORD_DIRECTORY)/('capture_'+uuid4().hex)
        self.max_bytes=int(getattr(cfg,'CAMERA_MOTION_RECORD_MAX_BYTES',256_000_000))
        self.max_frames=int(getattr(cfg,'CAMERA_MOTION_RECORD_MAX_FRAMES',600))
        self.pending=queue.Queue(maxsize=2);self.stop=threading.Event();self.dropped=0;self.written=0;self.bytes=0;self.errors=0
        self.thread=threading.Thread(target=self._run,name='motion-recorder',daemon=True);self.thread.start();atexit.register(self.close)

    def submit(self,frame,detections,metadata):
        if self.stop.is_set() or self.written>=self.max_frames or frame.nbytes>24_000_000 or self.pending.full():
            self.dropped+=1;return False
        item=(frame.copy(),json.loads(json.dumps(dict(metadata,detections=[
            {'bbox':[float(v) for v in d['bbox']], 'confidence':float(d['confidence']) if d.get('confidence') is not None else None}
            for d in detections]))))
        try:self.pending.put_nowait(item);return True
        except queue.Full:self.dropped+=1;return False

    def close(self):
        self.stop.set()
        if self.thread is not threading.current_thread():self.thread.join(timeout=2)
        return not self.thread.is_alive()

    def _run(self):
        while not self.stop.is_set() or not self.pending.empty():
            try:frame,meta=self.pending.get(timeout=.1)
            except queue.Empty:continue
            try:
                ok,buf=cv2.imencode('.png',frame)
                if not ok:raise ValueError('encode failed')
                # Lossless decoded image, not raw sensor/encoded RTSP or capture PTS.
                meta.update(source_kind='decoded_rtsp_frame',image=f'{self.written:06d}.png',color='BGR',
                            recording_gaps_possible=True,dropped_before=self.dropped)
                if getattr(self, 'include_image_hash', False):
                    meta['image_sha256'] = hashlib.sha256(buf).hexdigest()
                encoded=(json.dumps(meta)+'\n').encode()
                if self.written>=self.max_frames or self.bytes+len(buf)+len(encoded)>self.max_bytes:
                    self.dropped+=1
                    if self.stop_on_limit:self.stop.set()
                    continue
                self.root.mkdir(parents=True,exist_ok=True)
                (self.root/meta['image']).write_bytes(buf.tobytes())
                with (self.root/'frames.jsonl').open('ab') as f:f.write(encoded)
                self.bytes+=len(buf)+len(encoded);self.written+=1
            except Exception:self.errors+=1
            finally:self.pending.task_done()
