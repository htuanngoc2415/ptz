"""Bounded RTSP receiver for environments without GStreamer Python bindings."""
import os
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit

import cv2
from runtime.reconnect import ReconnectBackoff


class OpenCVPlayer:
    def __init__(self, config, frame_callback, status_callback=None, disconnect_callback=None):
        self.config = config
        self.frame_callback = frame_callback
        self.status_callback = status_callback or (lambda _: None)
        self.disconnect_callback = disconnect_callback or (lambda: None)
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._counts = dict(frames=0, reconnects=0, failures=0)
        self._last_frame = None

    def _uri(self):
        parts = urlsplit(self.config['uri'])
        host = parts.hostname
        if not host or parts.scheme != 'rtsp':
            raise ValueError('Expected RTSP URI')
        host = f'[{host}]' if ':' in host else host
        if parts.port:
            host += f':{parts.port}'
        user = self.config.get('username', '')
        if user:
            host = quote(user, safe='') + ':' + quote(self.config.get('password', ''), safe='') + '@' + host
        elif parts.username:
            host = parts.netloc
        return urlunsplit((parts.scheme, host, parts.path, parts.query, parts.fragment))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='opencv-capture', daemon=True)
        self._thread.start()

    def _run(self):
        backoff = ReconnectBackoff(self.config.get('reconnect_initial', 1.),
                                   self.config.get('reconnect_max', 30.))
        os.environ.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS', 'rtsp_transport;tcp')
        uri = self._uri()
        while not self._stop.is_set():
            capture = None
            try:
                # Open/read timeouts are open-only FFmpeg properties, not setters.
                capture = cv2.VideoCapture(uri, cv2.CAP_FFMPEG, [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 3000,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, 3000])
                if not capture.isOpened():
                    raise RuntimeError('RTSP open failed')
                self.status_callback('RTSP connected (OpenCV)')
                while not self._stop.is_set():
                    ok, frame = capture.read()
                    if not ok:
                        raise RuntimeError('RTSP frame unavailable')
                    if self._stop.is_set():
                        break
                    # Match the application's RGB contract; no display/AI queue.
                    self.frame_callback(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    with self._lock:
                        self._counts['frames'] += 1
                        self._last_frame = time.monotonic()
                    backoff.reset()
            except Exception as exc:
                with self._lock:
                    self._counts['failures'] += 1
                self.status_callback(f'RTSP unavailable ({type(exc).__name__})')
            finally:
                if capture is not None:
                    capture.release()
                self.disconnect_callback()
            if self._stop.wait(backoff.next_delay()):
                break
            with self._lock:
                self._counts['reconnects'] += 1

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(7)
            if self._thread.is_alive():
                raise RuntimeError('RTSP receiver did not stop within timeout')

    def diagnostics(self):
        with self._lock:
            return dict(counts=dict(self._counts), last_frame=self._last_frame,
                        worker_alive=bool(self._thread and self._thread.is_alive()))
