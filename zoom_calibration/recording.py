"""Bounded recorder; mapping CSV is authoritative for variable receive FPS."""
import queue
import threading

class Recorder:
    def __init__(self, session, cfg):
        self.session, self.cfg = session,cfg
        self.queue = queue.Queue(maxsize=cfg['queue_size'])
        self.closed = False
        self.enabled = threading.Event()
        self.failed = threading.Event()
        self.lock = threading.Lock()
        self.segment = 0
        self.flush_requested = threading.Event()
        self.thread = threading.Thread(target=self._run,name='video-recorder',daemon=True)
        self.thread.start()
    def start(self):
        with self.lock:
            if not self.enabled.is_set():
                self.segment += 1
                self.flush_requested.clear()
                self.enabled.set()
        self.session.event('RECORDING_START',str(self.segment))
    def stop(self):
        self.enabled.clear()
        self.flush_requested.set()
        self.session.event('RECORDING_STOP','')
    def submit(self, packet):
        if not self.enabled.is_set() or self.closed:
            return
        try:
            self.queue.put_nowait((self.segment,packet))
        except queue.Full:
            self.session.meta['frame_drops'] += 1
            self.session.log('recording',dict(frame_id=packet['frame_id'],receive_timestamp=packet['receive_timestamp'],dropped=True))
    def _run(self):
        writer = None
        active_segment = None
        index = 0
        shape = None
        try:
            while True:
                try:
                    item = self.queue.get(timeout=.1)
                except queue.Empty:
                    if self.flush_requested.is_set():
                        if writer:
                            writer.release()
                            writer = None
                            active_segment = None
                        self.flush_requested.clear()
                    continue
                if item is None:
                    break
                segment,p = item
                import cv2
                im = p['image']
                h,w = im.shape[:2]
                if active_segment != segment:
                    if writer:
                        writer.release()
                    path = self.session.path/'video'/('original.mp4' if segment==1 else f'original_{segment}.mp4')
                    writer = cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),self.cfg['recording_fps'],(w,h))
                    if not writer.isOpened():
                        raise RuntimeError('MP4 encoder unavailable')
                    active_segment,index,shape = segment,0,(h,w)
                if (h,w)!=shape:
                    raise RuntimeError('Frame size changed; recording stopped')
                writer.write(im)
                self.session.log('recording',dict(frame_id=p['frame_id'],receive_timestamp=p['receive_timestamp'],
                                 recording_index=f'{segment}:{index}',recording_pts_s=index/self.cfg['recording_fps'],dropped=False))
                index += 1
        except Exception as exc:
            self.failed.set()
            self.enabled.clear()
            self.session.event('RECORDING_ERROR',type(exc).__name__+': '+str(exc))
        finally:
            if writer:
                writer.release()
    def close(self):
        self.stop()
        self.closed = True
        while self.thread.is_alive():
            try:
                self.queue.put(None, timeout=.2)
                break
            except queue.Full:
                continue
        self.thread.join(10)
        if self.thread.is_alive():
            self.failed.set()
            self.session.event('RECORDING_ERROR','Shutdown timeout')
