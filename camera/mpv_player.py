"""Low latency mpv capture adapted from phanmem_v1.0/phanmem.

Decode continuously, sample only the latest frame, and bound images before
copying into Python. AI inference continues to use its configured CUDA device.
"""
import threading
import time
from urllib.parse import quote, urlsplit, urlunsplit

import numpy as np

from runtime.reconnect import ReconnectBackoff
from event_logging import event_print, redact


class MPVPlayer:
    def __init__(self, config, frame_callback, status_callback=None, disconnect_callback=None):
        self.config = config
        self.frame_callback = frame_callback
        self.status_callback = status_callback
        self.disconnect_callback = disconnect_callback
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.player = None
        self.running = False
        self._generation = 0
        self._backoff = ReconnectBackoff(config.get('reconnect_initial', 1.),
                                         config.get('reconnect_max', 30.))
        self.fps = float(config.get('video_fps', 15))
        self.width = int(config.get('video_max_width', 1280))
        self.height = int(config.get('video_max_height', 720))
        if not 1 <= self.fps <= 60 or self.width < 2 or self.height < 2:
            raise ValueError('Invalid video frame limits')

    def _status(self, message):
        event_print(f'[RTSP] {message}')
        if self.status_callback:
            self.status_callback(message)

    def _log(self, level, prefix, message):
        if level in ('error', 'fatal'):
            event_print(f'[MPV] {prefix}: {redact(message)}')

    def _uri(self):
        parts = urlsplit(self.config['uri'])
        if self.config.get('username') and parts.username is None:
            auth = quote(self.config['username'], safe='') + ':'
            auth += quote(self.config.get('password', ''), safe='')
            parts = parts._replace(netloc=auth + '@' + parts.netloc)
        return urlunsplit(parts)

    def start(self):
        with self._lock:
            if self._stop.is_set() or self._thread is not None:
                return
            # Missing dependencies must be reported at startup, not retried forever.
            import mpv
            self._mpv = mpv
            self._thread = threading.Thread(target=self._run, name='mpv-capture', daemon=True)
            self.running = True
            self._thread.start()

    def _open(self):
        # The reference GUI uses vo=gpu/hwdec=auto-safe for direct display.
        # Our AI/overlay path needs RGB in RAM, so request a copy-capable
        # hardware decoder. mpv may fall back if the installed codec lacks one.
        player = self._mpv.MPV(
            profile='low-latency', vo='null',
            log_handler=self._log,
            hwdec=self.config.get('video_hwdec', 'no'),
            untimed=True, video_latency_hacks=True, cache='no', audio='no',
            rtsp_transport=self.config.get('transport', 'tcp'),
            demuxer_lavf_o='fflags=nobuffer,flags=low_delay,reorder_queue_size=0',
            demuxer_lavf_analyzeduration=0.1,
            # Scaling in libmpv avoids a full 4K RGB screenshot in Python.
            vf=f'lavfi=[scale=w=min(iw\\,{self.width}):h=min(ih\\,{self.height}):force_original_aspect_ratio=decrease]',
            osc=False, input_default_bindings=False, keep_open='no',
        )
        try:
            player.play(self._uri())
        except Exception:
            player.terminate()
            raise
        return player

    def _capture(self, player):
        last_pts = None
        last_frame = time.monotonic()
        timeout = float(self.config.get('video_stall_timeout', 12))
        while not self._stop.is_set():
            began = time.monotonic()
            pts = player.time_pos
            if pts is not None and pts != last_pts:
                # time-pos can appear before the first decoded frame is ready.
                # mpv then returns COMMAND_ERROR (-12) for screenshot-raw;
                # restarting here would prevent the stream ever warming up.
                try:
                    snapshot = player.screenshot_raw(includes='video')
                except SystemError:
                    snapshot = None
                if snapshot is None:
                    if time.monotonic() - last_frame > timeout:
                        raise TimeoutError('No decoded video frame')
                    self._stop.wait(1 / self.fps)
                    continue
                # screenshot_raw returns RGB; no BGR conversion or extra copy.
                frame = np.asarray(snapshot)
                if frame.ndim != 3 or frame.shape[2] != 3:
                    raise RuntimeError('mpv returned an invalid RGB frame')
                if frame.shape[1] > self.width or frame.shape[0] > self.height:
                    raise RuntimeError('mpv did not apply video size limits')
                observer = self.config.get('_capture_observer')
                if observer:
                    observer(pts, self._generation, frame.shape, time.perf_counter_ns())
                self.frame_callback(frame)
                if last_pts is None:
                    self._status('RTSP connected (mpv)')
                    self._backoff.reset()
                last_pts = pts
                last_frame = time.monotonic()
            if time.monotonic() - last_frame > timeout:
                raise TimeoutError('Video stalled')
            self._stop.wait(max(0., 1 / self.fps - (time.monotonic() - began)))

    def _run(self):
        try:
            while not self._stop.is_set():
                try:
                    self.player = self._open()
                    self._generation += 1
                    self._capture(self.player)
                except Exception as exc:
                    # Do not expose RTSP credentials from libmpv exceptions.
                    event_print(f'[RTSP] {type(exc).__name__}: {redact(str(exc))}')
                    self._status(f'RTSP unavailable ({type(exc).__name__})')
                    if not self._stop.is_set() and self.disconnect_callback:
                        self.disconnect_callback()
                finally:
                    if self.player is not None:
                        self.player.terminate()
                        self.player = None
                if not self._stop.is_set():
                    delay = self._backoff.next_delay()
                    self._status(f'RTSP disconnected; retry in {delay:.1f}s')
                    self._stop.wait(delay)
        finally:
            self.running = False

    def stop(self):
        self._stop.set()
        if self._thread is not None and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)
