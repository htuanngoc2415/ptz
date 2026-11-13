"""Hardware-free checks for fresh-frame AF completion and trial deadlines."""
import importlib.util
from pathlib import Path
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

focus = load('dynamic_focus', 'control/auto_focus.py')
trial = load('trial', 'control/acquisition_trial.py')

class Camera:
    def autofocus(self):
        return True, 'OK'

class DynamicFocusTests(unittest.TestCase):
    def make(self, camera=None):
        c = focus.AutoFocusController(camera or Camera(), settle_time=.3,
                                      min_settle_time=0, confirm_frames=3)
        c.set_enabled(True)
        return c

    def await_ack(self, c):
        until = time.monotonic()+1
        while c._ack_time is None and time.monotonic() < until:
            time.sleep(.001)
        self.assertIsNotNone(c._ack_time)

    def await_idle(self, c):
        until = time.monotonic()+1
        while c.busy and time.monotonic() < until:
            time.sleep(.001)
        self.assertFalse(c.busy)

    def test_new_frames_finish_early_but_old_and_duplicate_frames_do_not(self):
        c = self.make()
        c.update(0)
        self.await_ack(c)
        c.update(1000, frame_time=c._ack_time-.01)
        self.assertEqual(c._good_frames, 0)
        stamp = time.monotonic()
        for _ in range(4):
            c.update(1000, frame_time=stamp)
        self.assertEqual(c._good_frames, 1)
        for _ in range(2):
            c.update(1000, frame_time=time.monotonic())
        self.await_idle(c)
        self.assertEqual(c.last_action, 'QUALITY_OK')

    def test_minimum_settle_ignores_early_frames(self):
        c = self.make()
        c.min_settle_time = .2
        c.update(0)
        self.await_ack(c)
        for _ in range(3):
            c.update(1000, frame_time=time.monotonic())
        self.assertEqual(c._good_frames, 0)
        self.await_idle(c)
        self.assertEqual(c.last_action, 'TIMEOUT')

    def test_failed_camera_command_is_not_quality_success(self):
        class FailedCamera:
            def autofocus(self):
                return False, 'Rejected'
        c = self.make(FailedCamera())
        c.update(0)
        self.await_idle(c)
        self.assertEqual(c.last_action, 'ERROR')

    def test_no_frames_times_out_without_quality_success(self):
        c = self.make()
        c.update(0)
        self.await_idle(c)
        self.assertEqual(c.last_action, 'TIMEOUT')

    def test_roi_switch_and_nan_break_consecutive_quality(self):
        c = self.make()
        c.update(0)
        self.await_ack(c)
        for _ in range(2):
            c.update(1000, frame_time=time.monotonic(), roi_kind='PERSON')
        c.update(1000, frame_time=time.monotonic(), roi_kind='FACE')
        self.assertEqual(c._good_frames, 1)
        c.update(float('nan'), frame_time=time.monotonic())
        self.assertEqual(c._good_frames, 0)
        self.await_idle(c)

    def test_reset_keeps_inflight_motor_ownership(self):
        entered, release = threading.Event(), threading.Event()
        class SlowCamera:
            def autofocus(self):
                entered.set()
                release.wait(1)
                return True, 'OK'
        c = self.make(SlowCamera())
        c.update(0)
        self.assertTrue(entered.wait(1))
        c.reset()
        self.assertTrue(c.busy)
        release.set()
        self.await_idle(c)
        self.assertNotEqual(c.last_action, 'QUALITY_OK')

    def test_trial_does_not_accept_late_confirmation(self):
        t = trial.AcquisitionTrial(10)
        t.start(0)
        t.start(4)
        t.finish('CONFIRMED', 10.1)
        self.assertEqual(t.outcome, 'TIMEOUT')
        self.assertTrue(t.expire(11))
        self.assertFalse(t.expire(12))

    def test_trial_success_is_terminal(self):
        t = trial.AcquisitionTrial(10)
        t.start(0)
        t.finish('CONFIRMED', 9)
        self.assertFalse(t.expire(12))
        self.assertEqual(t.outcome, 'CONFIRMED')

if __name__ == '__main__':
    unittest.main()
