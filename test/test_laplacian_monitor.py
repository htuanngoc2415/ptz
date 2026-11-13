"""No camera/display needed: quality, freshness, timeout and worker ownership."""
import importlib.util
from pathlib import Path
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np

spec = importlib.util.spec_from_file_location('laplacian_monitor_tested',
    Path(__file__).resolve().parents[1] / 'tools/laplacian_monitor.py')
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)

class LaplacianTests(unittest.TestCase):
    def test_window_does_not_close_on_unavailable_or_initial_visibility(self):
        window = m.WindowCloseMonitor()
        for value in (-1, 0, -1, 0, 1, -1, 1):
            self.assertFalse(window.closed(value))
        self.assertTrue(window.closed(0))

    def test_headless_runs_without_gui_and_stops_player(self):
        class FakePlayer:
            instance = None
            def __init__(self, config, callback):
                self.callback, self.stopped = callback, False
                FakePlayer.instance = self
            def start(self):
                self.callback(np.zeros((32,32,3), np.uint8))
            def stop(self):
                self.stopped = True
        context = SimpleNamespace(pending=lambda: False)
        glib = SimpleNamespace(MainContext=SimpleNamespace(default=lambda: context))
        cfg = SimpleNamespace(RTSP_URI='fake', RTSP_USERNAME='', RTSP_PASSWORD='')
        args = SimpleNamespace(threshold=120, timeout=1, confirm_frames=3,
                               roi=None, latency_ms=100, duration=.05, stream_timeout=1)
        with patch.object(m.cv2,'namedWindow',side_effect=AssertionError('No GUI allowed')):
            m.run_headless(args,cfg,FakePlayer,glib)
        self.assertTrue(FakePlayer.instance.stopped)

    def test_roi_argument_validation(self):
        self.assertEqual(m.parse_roi('0,0,20,20'), (0,0,20,20))
        with self.assertRaises(m.argparse.ArgumentTypeError):
            m.parse_roi('1,2,3')

    def test_matches_formula_on_crop(self):
        frame = np.random.default_rng(3).integers(0,256,(80,100,3),dtype=np.uint8)
        score, box = m.laplacian_variance(frame, (10,20,60,70))
        gray = cv2.cvtColor(frame[20:70,10:60], cv2.COLOR_BGR2GRAY)
        self.assertAlmostEqual(score, cv2.Laplacian(gray, cv2.CV_64F).var())
        self.assertEqual(box, (10,20,60,70))
        blurred = cv2.GaussianBlur(frame,(9,9),2)
        self.assertLess(m.laplacian_variance(blurred, box)[0], score)

    def test_invalid_roi(self):
        with self.assertRaises(ValueError):
            m.laplacian_variance(np.zeros((20,20,3), np.uint8), (-3,0,2,20))

    def test_threshold_needs_distinct_fresh_frames(self):
        e = m.FocusEvaluation()
        e.reset(started=10)
        for _ in range(5):
            e.update(200,10.1,10.1)
        self.assertIsNone(e.outcome)
        e.update(200,9.9,10.2)
        e.update(200,10.2,10.2)
        e.update(200,10.3,10.3)
        self.assertEqual(e.outcome, 'QUALITY_OK')
        e.tick(11)
        self.assertEqual(e.quality, 'STALE')
        self.assertEqual(e.outcome, 'QUALITY_OK')  # completed trial stays recorded

    def test_blurry_plateau_never_passes(self):
        e = m.FocusEvaluation()
        e.reset(started=10)
        for i in range(5):
            e.update(30,10+i*.1,10+i*.1)
        self.assertTrue(e.plateau)
        self.assertIsNone(e.outcome)
        e.tick(11)
        self.assertEqual(e.outcome, 'TIMEOUT')

    def test_timeout_without_video_and_late_good_frames(self):
        e = m.FocusEvaluation()
        e.reset(started=10)
        e.tick(11)
        for i in range(3):
            e.update(200,11+i*.05,11+i*.05)
        self.assertEqual(e.outcome, 'TIMEOUT')

    def test_no_pretrial_or_stale_frame_success(self):
        e = m.FocusEvaluation(confirm_frames=1)
        e.reset(started=10)
        e.update(200,9.9,10)
        e.update(200,10,10.5)
        self.assertIsNone(e.outcome)

    def test_worker_discards_old_roi_and_replaces_pending_frame(self):
        entered, release = threading.Event(), threading.Event()
        original = m.laplacian_variance
        calls = []
        def slow(frame, roi):
            calls.append(roi)
            if len(calls) == 1:
                entered.set()
                release.wait(2)
            return original(frame, roi)
        with patch.object(m,'laplacian_variance',side_effect=slow):
            worker = m.LaplacianWorker()
            try:
                frame = np.zeros((30,30,3),np.uint8)
                worker.set_roi((0,0,20,20))
                worker.submit(frame,1)
                self.assertTrue(entered.wait(1))
                worker.set_roi((5,5,25,25))
                worker.submit(frame,2)
                worker.submit(frame,3)
                release.set()
                until = time.monotonic()+2
                while worker.latest() is None and time.monotonic() < until:
                    time.sleep(.001)
                result = worker.latest()
                self.assertIsNotNone(result)
                self.assertEqual(result.frame_time,3)
                self.assertEqual(result.bbox,(5,5,25,25))
                self.assertEqual(len(calls),2)
            finally:
                release.set()
                worker.close()
            self.assertFalse(worker._thread.is_alive())

if __name__ == '__main__':
    unittest.main()
