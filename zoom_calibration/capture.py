"""GStreamer RTSP callback retains PTS; OpenCV is used only for offline files."""
import os
import threading
import time

class Capture:
    def __init__(self, source, callback, error, offline=False, camera=None):
        self.source, self.callback, self.error = source, callback, error
        self.offline, self.camera = offline, camera or {}
        self.closed = threading.Event()
        self.last_receive = None
        self.frame_id = 0
        self.pipeline = None
        self.thread = threading.Thread(target=self._run, name='calibration-capture',daemon=True)
    def start(self):
        self.thread.start()
    def _deliver(self, image, pts):
        self.frame_id += 1
        self.last_receive = time.monotonic()
        self.callback(dict(frame_id=self.frame_id, receive_timestamp=self.last_receive,
                           timestamp=self.last_receive, wall_timestamp=time.time(), frame_pts=pts,
                           capture_timestamp=None, image=image))
    def _run(self):
        if self.offline:
            self._file()
            return
        delay = 1
        while not self.closed.is_set():
            try:
                self._rtsp()
            except Exception as exc:
                self.error('RTSP_UNAVAILABLE_'+type(exc).__name__)
            if not self.closed.wait(delay):
                delay = min(30,delay*2)
    def _rtsp(self):
        import gi
        gi.require_version('Gst','1.0')
        from gi.repository import Gst
        import numpy as np
        Gst.init(None)
        p = Gst.parse_launch('rtspsrc name=source protocols=tcp ! decodebin ! videoconvert ! video/x-raw,format=BGR ! appsink name=sink emit-signals=true max-buffers=1 drop=true sync=false')
        self.pipeline = p
        source = p.get_by_name('source')
        source.set_property('location',self.source)
        source.set_property('latency',int(self.camera.get('latency_ms',200)))
        source.set_property('drop-on-latency',True)
        if self.camera.get('username'):
            source.set_property('user-id',self.camera['username'])
            source.set_property('user-pw',self.camera.get('password',''))
        sink = p.get_by_name('sink')
        def sample(sink):
            s = sink.emit('pull-sample')
            if s is None:
                return Gst.FlowReturn.ERROR
            cap = s.get_caps().get_structure(0)
            w,h = cap.get_value('width'),cap.get_value('height')
            b = s.get_buffer()
            ok, data = b.map(Gst.MapFlags.READ)
            if not ok:
                return Gst.FlowReturn.ERROR
            try:
                # GStreamer RGB/BGR rows have 4-byte alignment.
                stride = ((w*3+3)//4)*4
                from gi.repository import GstVideo
                info = GstVideo.VideoInfo.new_from_caps(s.get_caps())
                if info:
                    stride = info.stride[0]
                im = np.frombuffer(data.data,dtype=np.uint8).reshape(h,stride)[:,:w*3].reshape(h,w,3).copy()
                pts = None if b.pts == Gst.CLOCK_TIME_NONE else b.pts/Gst.SECOND
                self._deliver(im,pts)
            except Exception as exc:
                self.error('CAPTURE_CALLBACK_'+type(exc).__name__)
            finally:
                b.unmap(data)
            return Gst.FlowReturn.OK
        gi.require_version('GstVideo','1.0')
        sink.connect('new-sample',sample)
        p.set_state(Gst.State.PLAYING)
        try:
            bus = p.get_bus()
            while not self.closed.is_set():
                msg = bus.timed_pop_filtered(200*Gst.MSECOND,Gst.MessageType.ERROR | Gst.MessageType.EOS)
                if msg:
                    raise RuntimeError('RTSP stream ended')
        finally:
            p.set_state(Gst.State.NULL)
            self.pipeline = None
    def _file(self):
        import cv2
        cap = cv2.VideoCapture(str(self.source))
        try:
            if not cap.isOpened():
                raise RuntimeError('Cannot open replay video')
            fps = cap.get(cv2.CAP_PROP_FPS) or 25
            started = time.monotonic()
            count = 0
            while not self.closed.is_set():
                ok, image = cap.read()
                if not ok:
                    self.error('REPLAY_EOF')
                    break
                self._deliver(image,cap.get(cv2.CAP_PROP_POS_MSEC)/1000)
                count += 1
                self.closed.wait(max(0,started+count/fps-time.monotonic()))
        except Exception as exc:
            self.error('REPLAY_ERROR_'+type(exc).__name__)
        finally:
            cap.release()
    def close(self):
        self.closed.set()
        self.thread.join(4)
