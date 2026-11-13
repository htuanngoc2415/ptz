#!/usr/bin/env python3
"""Live, read-only Laplacian monitor. Run beside main.py; drag an ROI, press F.

Capture -> latest-frame slot -> Laplacian worker -> display. No PTZ commands.
ROI coordinates refer to the original stream; measurements never use resized
preview pixels. The selected rectangle does not automatically track a person.
"""
from __future__ import annotations

import argparse
import os
from collections import deque
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
import time

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def laplacian_variance(frame_bgr, bbox):
    """Return (score, clipped ROI); reject empty/tiny regions."""
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = map(int, bbox)
    x1, x2 = max(0, min(w, x1)), max(0, min(w, x2))
    y1, y2 = max(0, min(h, y1)), max(0, min(h, y2))
    if x2-x1 < 8 or y2-y1 < 8:
        raise ValueError('ROI must be at least 8x8 pixels')
    crop = frame_bgr[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var()), (x1, y1, x2, y2)


class FocusEvaluation:
    """Fresh-frame threshold confirmation, with a bounded optional focus trial.

    Plateau is diagnostic only: a flat, blurry image must not pass. A trial
    timeout never means the camera has actually finished focusing.
    """
    def __init__(self, threshold=120., timeout=1., confirm_frames=3, max_age=.3):
        if threshold <= 0 or timeout <= 0 or confirm_frames < 1 or max_age <= 0:
            raise ValueError('Threshold, timeout, confirm_frames and max_age must be positive')
        self.threshold, self.timeout = threshold, timeout
        self.confirm_frames, self.max_age = confirm_frames, max_age
        self.reset()

    def reset(self, started=None):
        self.started = started
        self.outcome = None
        self.last_time = float('-inf')
        self.good = 0
        self.values = deque(maxlen=5)
        self.plateau = False
        self.quality = 'WAITING'

    def tick(self, now):
        if self.started is not None and self.outcome is None and now-self.started >= self.timeout:
            self.outcome = 'TIMEOUT'
        if now-self.last_time > self.max_age:
            self.good = 0
            self.values.clear()
            self.plateau = False
            self.quality = 'STALE'
        return self.outcome

    def update(self, score, frame_time, now):
        self.tick(now)
        if (not np.isfinite(score) or not 0 <= now-frame_time <= self.max_age
                or frame_time <= self.last_time
                or (self.started is not None and frame_time < self.started)):
            return
        self.last_time = frame_time
        self.values.append(score)
        self.plateau = (len(self.values) == self.values.maxlen
                        and max(self.values)-min(self.values) <= .05*max(1., max(self.values)))
        self.good = self.good+1 if score >= self.threshold else 0
        self.quality = 'SHARP' if self.good >= self.confirm_frames else 'CHECKING' if score >= self.threshold else 'BLURRY'
        if self.started is not None and self.outcome is None and self.quality == 'SHARP':
            self.outcome = 'QUALITY_OK'


@dataclass(frozen=True)
class Measurement:
    score: float
    bbox: tuple
    frame_time: float
    measured_at: float
    processing_ms: float


class LaplacianWorker:
    """One pending frame only; superseded input is dropped, never queued up."""
    def __init__(self):
        self._condition = threading.Condition()
        self._pending = None
        self._latest = None
        self._roi = None
        self._generation = 0
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True, name='laplacian')
        self._thread.start()

    def set_roi(self, bbox):
        with self._condition:
            self._roi = None if bbox is None else tuple(bbox)
            self._generation += 1
            self._latest = self._pending = None

    def submit(self, frame_rgb, frame_time):
        # RTSPPlayer supplies its own frame copy; caller must not mutate it.
        with self._condition:
            if not self._closed and self._roi is not None:
                self._pending = (frame_rgb, frame_time, self._roi, self._generation)
                self._condition.notify()

    def latest(self):
        with self._condition:
            return self._latest

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                frame, stamp, roi, generation = self._pending
                self._pending = None
            started = time.monotonic()
            # RGB -> BGR preserves the conversion convention used in the app.
            try:
                score, bbox = laplacian_variance(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), roi)
                now = time.monotonic()
                result = Measurement(score, bbox, stamp, now, (now-started)*1000)
            except (ValueError, cv2.error):
                result = None
            with self._condition:
                if generation == self._generation:
                    self._latest = result

    def close(self):
        with self._condition:
            self._closed = True
            self._pending = None
            self._condition.notify()
        self._thread.join(timeout=2)


class WindowCloseMonitor:
    """Only accept an explicit visible -> hidden transition as a close.

    Unavailable/negative properties and initial hidden state are inconclusive.
    Q/Esc always remain available when the backend cannot report visibility.
    """
    def __init__(self):
        self.was_visible = False

    def closed(self, visibility):
        if visibility >= 1:
            self.was_visible = True
        return self.was_visible and visibility == 0


def parse_roi(value):
    try:
        box = tuple(int(v) for v in value.split(','))
    except ValueError:
        raise argparse.ArgumentTypeError('ROI format: x1,y1,x2,y2')
    if len(box) != 4 or min(box) < 0 or box[2]-box[0] < 8 or box[3]-box[1] < 8:
        raise argparse.ArgumentTypeError('ROI needs nonnegative x1,y1,x2,y2 and size >= 8x8')
    return box


def run_headless(args, cfg, player_class, glib):
    """No OpenCV HighGUI calls; safe on SSH, servers and headless OpenCV."""
    worker = LaplacianWorker()
    evaluation = FocusEvaluation(args.threshold, args.timeout, args.confirm_frames)
    first_frame = threading.Event()
    selected_roi = args.roi
    def receive(frame):
        nonlocal selected_roi
        stamp = time.monotonic()
        if not first_frame.is_set():
            h, w = frame.shape[:2]
            if selected_roi is None:
                selected_roi = (w//4,h//4,3*w//4,3*h//4)
                print('[INFO] Default ROI = central 50% of image; NOT automatic person tracking.', flush=True)
            worker.set_roi(selected_roi)
            evaluation.reset(started=stamp)
            print(f'[INFO] Video={w}x{h} ROI={selected_roi} trial started', flush=True)
            first_frame.set()
        worker.submit(frame, stamp)
    player = player_class(dict(uri=cfg.RTSP_URI, username=cfg.RTSP_USERNAME,
                              password=cfg.RTSP_PASSWORD, latency_ms=args.latency_ms), receive)
    context = glib.MainContext.default()
    started = time.monotonic()
    last_logged = float('-inf')
    print('[INFO] Terminal mode. Waiting for RTSP video; Ctrl+C to stop.', flush=True)
    try:
        player.start()
        while args.duration <= 0 or time.monotonic()-started < args.duration:
            while context.pending():
                context.iteration(False)
            now = time.monotonic()
            if not first_frame.is_set() and now-started >= args.stream_timeout:
                raise RuntimeError('No RTSP frame received. Check camera.json, network and camera stream access.')
            result = worker.latest()
            if result is not None:
                evaluation.update(result.score, result.frame_time, now)
            evaluation.tick(now)
            if now-last_logged >= .2:
                score = '--' if result is None else f'{result.score:.1f}'
                print(f'[LAPLACIAN] score={score} quality={evaluation.quality} '
                      f'plateau={evaluation.plateau} trial={evaluation.outcome or "PENDING"}', flush=True)
                last_logged = now
            time.sleep(.01)
    finally:
        try:
            player.stop()
        finally:
            worker.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threshold', type=float, default=120.)
    parser.add_argument('--timeout', type=float, default=1.)
    parser.add_argument('--latency-ms', type=int, default=100)
    parser.add_argument('--confirm-frames', type=int, default=3)
    parser.add_argument('--headless', action='store_true', help='Print scores in terminal; no graphical window')
    parser.add_argument('--roi', type=parse_roi, help='Original frame coordinates: x1,y1,x2,y2')
    parser.add_argument('--duration', type=float, default=0, help='Terminal run duration in seconds; 0 until Ctrl+C')
    parser.add_argument('--stream-timeout', type=float, default=10, help='Fail clearly if no initial RTSP frame arrives')
    args = parser.parse_args(argv)
    if args.duration < 0 or args.stream_timeout <= 0:
        parser.error('duration must be >= 0; stream-timeout must be > 0')
    if sys.platform.startswith('linux') and not (os.getenv('DISPLAY') or os.getenv('WAYLAND_DISPLAY')):
        if not args.headless:
            print('[INFO] No graphical display (DISPLAY/WAYLAND_DISPLAY unset). Switching to terminal mode.', flush=True)
        args.headless = True
    print('[INFO] Laplacian monitor starting...', flush=True)
    evaluation = FocusEvaluation(args.threshold, args.timeout, args.confirm_frames)
    if args.latency_ms < 0:
        parser.error('--latency-ms must be nonnegative')
    import config as cfg
    from camera.rtsp_player import RTSPPlayer
    from gi.repository import GLib

    if args.headless:
        return run_headless(args, cfg, RTSPPlayer, GLib)

    worker = LaplacianWorker()
    lock = threading.Lock()
    latest_frame = None
    latest_time = None
    roi = args.roi
    worker.set_roi(roi)
    drag_start = None
    scale_x = scale_y = 1.
    window = 'Laplacian | drag ROI | F: 1s trial | R: clear | Q: quit'

    def receive(frame):
        nonlocal latest_frame, latest_time
        stamp = time.monotonic()
        with lock:
            latest_frame, latest_time = frame, stamp
        worker.submit(frame, stamp)

    def mouse(event, x, y, flags, param):
        nonlocal roi, drag_start
        point = (int(x*scale_x), int(y*scale_y))
        if event == cv2.EVENT_LBUTTONDOWN:
            drag_start = point
        elif event == cv2.EVENT_LBUTTONUP and drag_start is not None:
            x0, y0 = drag_start
            roi = (min(x0,point[0]), min(y0,point[1]), max(x0,point[0]), max(y0,point[1]))
            if roi[2]-roi[0] < 8 or roi[3]-roi[1] < 8:
                roi = None
            worker.set_roi(roi)
            evaluation.reset()
            drag_start = None

    player = RTSPPlayer(dict(uri=cfg.RTSP_URI, username=cfg.RTSP_USERNAME,
                            password=cfg.RTSP_PASSWORD, latency_ms=args.latency_ms), receive)
    last_logged = float('-inf')
    reported_outcome = None
    window_close = WindowCloseMonitor()
    context = GLib.MainContext.default()
    try:
        cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
        cv2.setMouseCallback(window, mouse)
        player.start()
        stream_started = time.monotonic()
        print('[INFO] Window opened. Waiting for RTSP video; drag a region to measure.', flush=True)
        while True:
            while context.pending():
                context.iteration(False)
            now = time.monotonic()
            with lock:
                frame, stamp = latest_frame, latest_time
            if frame is None and now-stream_started >= args.stream_timeout:
                raise RuntimeError('No RTSP frame received. Check camera.json, network and camera stream access.')
            result = worker.latest()
            if result is not None:
                evaluation.update(result.score, result.frame_time, now)
            evaluation.tick(now)  # timeout even when there are no new frames
            preview = np.zeros((540,960,3), np.uint8)
            if frame is not None:
                h, w = frame.shape[:2]
                factor = min(960/w, 540/h)
                width, height = max(1,round(w*factor)), max(1,round(h*factor))
                scale_x, scale_y = w/width, h/height
                preview = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), (width,height))
                if roi is not None:
                    x1,y1,x2,y2 = roi
                    cv2.rectangle(preview, (int(x1/scale_x),int(y1/scale_y)),
                                  (int(x2/scale_x),int(y2/scale_y)), (0,255,255), 2)
            score = '--' if result is None else f'{result.score:.1f}'
            age = '--' if stamp is None else f'{now-stamp:.2f}s'
            quality = evaluation.quality if roi is not None else 'SELECT ROI'
            lines = [f'Laplacian={score} threshold={args.threshold:g}  {quality}',
                     f'Trial: {evaluation.outcome or ("FOCUSING" if evaluation.started is not None else "F to start")}  plateau={evaluation.plateau}',
                     f'Stream age={age} | ROI is fixed: reselect if target moves']
            for i, line in enumerate(lines):
                cv2.putText(preview,line,(10,25+i*25),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,255,255),1,cv2.LINE_AA)
            cv2.imshow(window, preview)
            if now-last_logged >= .2 or evaluation.outcome != reported_outcome:
                if result is not None or evaluation.started is not None:
                    print(f'[LAPLACIAN] score={score} quality={quality} plateau={evaluation.plateau} '
                          f'trial={evaluation.outcome or "PENDING"} '
                          f'roi_age={now-result.frame_time if result else -1:.3f}s', flush=True)
                last_logged, reported_outcome = now, evaluation.outcome
            key = cv2.waitKey(10) & 0xff
            if key in (ord('q'), 27):
                print('[INFO] Closing: Q/Esc pressed.', flush=True)
                break
            try:
                visibility = cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE)
            except cv2.error:
                visibility = -1  # Unsupported/unavailable is not a close event.
            if window_close.closed(visibility):
                print('[INFO] Closing: window was closed.', flush=True)
                break
            if key == ord('f') and roi is not None:
                evaluation.reset(started=time.monotonic())
            elif key == ord('r'):
                roi = None
                worker.set_roi(None)
                evaluation.reset()
    finally:
        try:
            player.stop()
        finally:
            worker.close()
            try:
                cv2.destroyAllWindows()
            except cv2.error:
                pass


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n[INFO] Stopped.', flush=True)
    except cv2.error as exc:
        print('[ERROR] OpenCV could not run the monitor. If window/GTK/GUI initialization failed, '
              'run again with --headless or use a terminal on the graphical desktop.', file=sys.stderr, flush=True)
        print(f'[ERROR] OpenCV function: {exc.func}, code: {exc.code}', file=sys.stderr, flush=True)
        raise SystemExit(1)
    except (ValueError, RuntimeError, ImportError) as exc:
        print(f'[ERROR] {exc}', file=sys.stderr, flush=True)
        raise SystemExit(1)
