"""Motor/state regressions without RTSP, inference models, or camera requests."""
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


camera = types.ModuleType('camera')
camera.__path__ = [str(ROOT/'camera')]
camera.PTZ_ZOOM_IN, camera.PTZ_ZOOM_OUT = 9, 10
with patch.dict(sys.modules, camera=camera):
    zoom = load('zoom_regression', 'control/auto_zoom.py')
state = load('zoom_state_regression', 'control/target_state_machine.py')


def box(ratio):
    return {'bbox': (350, 500-ratio*500, 650, 500+ratio*500)}


class FakePTZ:
    def __init__(self):
        self.commands = []
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.fail_stop = False

    def command(self, command, speed=None):
        self.commands.append(command)
        self.started.set()
        return True, ''

    def stop(self):
        self.commands.append('STOP')
        self.stopped.set()
        return not self.fail_stop, ''


class StateTests(unittest.TestCase):
    def test_acquisition_bypasses_person_size_only_after_three_frames(self):
        for height in (.2, .8):
            machine = state.TargetStateMachine()
            for i in range(3):
                result = machine.update((1000, 1000), box(height), box(.08), now=i * .1)
                self.assertEqual(result.target_type, 'FACE' if i == 2 else 'PERSON')
            self.assertEqual(result.state, state.TargetState.ZOOM_FACE)

    def test_single_face_does_not_acquire(self):
        machine = state.TargetStateMachine()
        for i, face in enumerate((box(.1), None, box(.1), None)):
            result = machine.update((1000, 1000), box(.5), face, now=i * .1)
            self.assertEqual(result.target_type, 'PERSON')

    def test_face_loss_latched_by_time_not_frame_count(self):
        machine = state.TargetStateMachine()
        for i in range(3):
            machine.update((1000, 1000), box(.8), box(.1), now=i * .1)
        for i in range(10):
            result = machine.update((1000, 1000), box(.9), None, now=.3 + i * .03)
            self.assertEqual(result.state, state.TargetState.FACE_LOST_HOLD)
            self.assertIsNone(result.control_target)
            self.assertEqual(result.target_type, 'FACE')
        result = machine.update((1000, 1000), box(.9), box(.12), now=.7)
        self.assertEqual(result.target_type, 'FACE')
        result = machine.update((1000, 1000), box(.9), None, now=1.6)
        self.assertEqual(result.target_type, 'FACE')
        self.assertIsNone(result.control_target)

    def test_median_rejects_one_outlier_and_unlocks_on_sustained_change(self):
        machine = state.TargetStateMachine()
        for i, ratio in enumerate((.25, .25, .25, .25, .25, .6)):
            result = machine.update((1000, 1000), box(.9), box(ratio), now=i * .1)
        self.assertEqual(result.face_ratio, .25)
        self.assertEqual(result.state, state.TargetState.TRACK_FACE)
        for i in range(3):
            result = machine.update((1000, 1000), box(.9), box(.35), now=.6 + i * .1)
        self.assertEqual(result.state, state.TargetState.ZOOM_FACE)

    def test_face_only_loss_uses_last_face_timestamp(self):
        machine = state.TargetStateMachine()
        for i in range(3):
            machine.update((1000, 1000), None, box(.1), now=i * .1)
        result = machine.update((1000, 1000), None, None, now=.4)
        self.assertEqual(result.state, state.TargetState.FACE_LOST_HOLD)
        result = machine.update((1000, 1000), None, None, now=3.3)
        self.assertTrue(result.target_lost)


class MotorTests(unittest.TestCase):
    def setUp(self):
        self.ptz = FakePTZ()
        self.controller = zoom.AutoZoomController(self.ptz, step_medium=.08,
            feedback_timeout=.3, max_observation_age=.8, wait_after_zoom=.02,
            direction_hold=.1, direction_cooldown=.05)
        self.controller.set_enabled(True)

    def tearDown(self):
        self.ptz.fail_stop = False
        self.controller.set_enabled(False)

    def update(self, ratio, **kw):
        return self.controller.update((1000,1000), box(ratio) if ratio else None,
                                      .25,.03,target_name='FACE',**kw)

    def start(self):
        self.update(.08)
        self.assertTrue(self.ptz.started.wait(.5))

    def test_bounded_pulse_finishes_without_more_inference(self):
        self.start()
        self.assertTrue(self.ptz.stopped.wait(.5))
        self.assertEqual(self.controller.last_stop_reason,'PULSE_COMPLETE')
        self.assertEqual(self.ptz.commands,[9,'STOP'])

    def test_old_but_accepted_observation_gets_bounded_pulse_not_immediate_watchdog(self):
        self.update(.08,observation_time=time.monotonic()-.65)
        self.assertTrue(self.ptz.stopped.wait(.5))
        self.assertEqual(self.controller.last_stop_reason,'PULSE_COMPLETE')

    def test_stale_observation_never_starts(self):
        self.update(.08,observation_time=time.monotonic()-1)
        self.assertEqual(self.ptz.commands,[])

    def test_old_publish_is_worker_timeout(self):
        now=time.monotonic()
        self.assertEqual(self.update(.08,observation_time=now-.5,publish_time=now-.4),'WORKER_TIMEOUT')
        self.assertEqual(self.ptz.commands,[])

    def test_missing_face_stops(self):
        self.start();self.update(None)
        self.assertEqual(self.ptz.commands,[9,'STOP'])

    def test_hold_band_stops(self):
        self.start();self.update(.25)
        self.assertEqual(self.ptz.commands,[9,'STOP'])

    def test_duplicate_observation_cannot_start_second_pulse(self):
        observed=time.monotonic()
        self.update(.08,observation_time=observed)
        self.assertTrue(self.ptz.stopped.wait(.5))
        self.update(.08,observation_time=observed)
        self.assertEqual(self.ptz.commands,[9,'STOP'])

    def test_frame_before_settle_cannot_restart_even_if_processed_later(self):
        self.start();self.controller.stop()
        observed=time.monotonic()
        time.sleep(.03)
        self.update(.08,observation_time=observed)
        self.assertEqual(self.ptz.commands,[9,'STOP'])

    def test_direction_reversal_waits_for_cooldown_and_new_frame(self):
        self.start();self.update(.4)
        self.assertEqual(self.ptz.commands,[9,'STOP'])
        self.update(.4)
        self.assertEqual(self.ptz.commands,[9,'STOP'])
        time.sleep(.12)
        self.ptz.started.clear();self.update(.4)
        self.assertTrue(self.ptz.started.wait(.5))
        self.assertEqual(self.ptz.commands,[9,'STOP',10])

    def test_small_opposite_error_does_not_reverse(self):
        self.start();self.controller.stop();time.sleep(.12)
        self.update(.285)
        self.assertEqual(self.ptz.commands,[9,'STOP'])

    def test_stop_failure_disables_and_retains_ownership(self):
        self.start();self.ptz.fail_stop=True;self.controller.stop()
        self.assertFalse(self.controller.enabled)
        self.assertTrue(self.controller.busy)
        self.controller.set_enabled(True)
        self.assertFalse(self.controller.enabled)

    def test_disable_before_manual_has_no_late_stop(self):
        self.start();self.controller.set_enabled(False);self.ptz.command(10)
        time.sleep(.15)
        self.assertEqual(self.ptz.commands,[9,'STOP',10])

    def test_mailbox_submission_does_not_wait_for_network(self):
        entered=threading.Event();release=threading.Event()
        def slow(command,speed=None):
            entered.set();release.wait(.5)
            return True,''
        self.ptz.command=slow
        self.controller.submit_observation(frame_shape=(1000,1000),target=box(.08),
            target_ratio=.25,tolerance=.03,observation_time=time.monotonic())
        self.assertTrue(entered.wait(.5))
        started=time.monotonic()
        self.controller.submit_observation(frame_shape=(1000,1000),target=None,
            target_ratio=.25,tolerance=.03,observation_time=time.monotonic())
        self.assertLess(time.monotonic()-started,.05)
        self.assertTrue(self.controller._lock.acquire(timeout=.05))
        self.controller._lock.release();release.set()
        self.assertTrue(self.ptz.stopped.wait(.5))

    def test_recovery_waits_1250ms_then_runs_out_bounded(self):
        now=time.monotonic()
        self.assertFalse(self.controller.recover_missing(now,now,3.0))
        self.assertEqual(self.ptz.commands,[])
        now=time.monotonic()
        self.assertTrue(self.controller.recover_missing(now,now-1.3,3.0))
        self.assertTrue(self.ptz.started.wait(.5))
        self.assertEqual(self.ptz.commands[0],10)
        self.assertTrue(self.ptz.stopped.wait(.6))
        self.assertEqual(self.controller.last_stop_reason,'PULSE_COMPLETE')

    def test_recovery_stops_at_wide_limit_and_rejects_stale(self):
        now=time.monotonic()
        self.assertFalse(self.controller.recover_missing(now,now-2,1.0))
        self.assertFalse(self.controller.recover_missing(now-1,now-3,3.0))
        self.assertEqual(self.ptz.commands,[])

    def test_recovery_is_bounded_even_if_zoom_estimate_stuck(self):
        self.controller._recovery_spent=self.controller.recovery_max_duration
        now=time.monotonic()
        self.assertFalse(self.controller.recover_missing(now,now-2,3.0))
        self.assertEqual(self.ptz.commands,[])

    def test_person_reappearance_stops_recovery_before_any_zoom_in(self):
        now=time.monotonic()
        self.controller.recover_missing(now,now-2,3.0)
        self.assertTrue(self.ptz.started.wait(.5))
        self.update(.08)
        self.assertEqual(self.ptz.commands,[10,'STOP'])
        self.assertFalse(self.controller.busy)

    def test_worker_watchdog_distinct_from_capture_age(self):
        self.controller.step_medium=.3;self.controller.feedback_timeout=.05
        self.start();self.assertTrue(self.ptz.stopped.wait(.5))
        self.assertEqual(self.controller.last_stop_reason,'WORKER_TIMEOUT')


if __name__ == '__main__':
    unittest.main()
