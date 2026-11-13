"""No devices: control threads, scripted overlap, model dynamics and fake HANET RPC."""
import unittest
import time
import tempfile
import csv
from pathlib import Path
from unittest.mock import Mock, patch
import test_auto_pan_tilt as pan_tests
import test_hybrid_tracking as hybrid_tests
from test_hybrid_tracking import person
from control.auto_zoom import AutoZoomController
from camera.hanet_controller import HanetController
from telemetry.zoom_trace import ZoomTrace


class FollowTests(unittest.TestCase):
    setUp = pan_tests.PanTiltTests.setUp
    tearDown = pan_tests.PanTiltTests.tearDown
    submit = pan_tests.PanTiltTests.submit

    def test_post_stop_frame_during_settle_is_usable_after_settle(self):
        self.control._last_stop = time.monotonic()-.01
        self.control._ready_after = time.monotonic()+.08
        self.assertTrue(self.submit())
        self.assertFalse(self.started.wait(.03))
        self.assertTrue(self.started.wait(.2))

    def test_older_observation_cannot_reverse_latest_direction(self):
        self.owner.acquire('zoom')
        now = time.monotonic()
        self.submit(received=now)
        self.submit((0,200,100,400), received=now-.01)
        self.owner.release('zoom')
        self.assertTrue(self.started.wait(1))
        self.assertEqual(self.ptz.command.call_args.args[0], 'pan_right')

    def test_verifying_identity_with_valid_session_can_follow(self):
        self.control.submit(person(x=700), (500,1000,3), time.monotonic(), 's', 1,
            metadata=dict(identity_state='VERIFYING', tracking_state='TRACKED', frame_id=1))
        self.assertTrue(self.started.wait(1))

    def test_adaptive_simulator_bounds_and_fewer_pulses(self):
        self.cfg.AUTO_PAN_TILT_PULSE = .12
        self.cfg.AUTO_PAN_TILT_MAX_PULSE = .24
        self.cfg.AUTO_PAN_TILT_SETTLE = .15
        def simulate(adaptive, gain):
            self.cfg.AUTO_PAN_TILT_ADAPTIVE = adaptive
            error, pulses, elapsed = .40, 0, 0.
            self.control._axis = None
            while error > self.cfg.AUTO_PAN_TILT_RELEASE and pulses < 100:
                center = (error+.5)*1000
                box = (center-40,250,center+40,350)
                direction = self.control._direction(box, (600,1000,3))
                if direction is None:
                    break
                duration = self.control._pulse(box, (600,1000,3))
                self.assertLessEqual(duration, .24)
                # Bounded plant: normalized image velocity per command speed;
                # assumed constant during a pulse. No claim about real motor gain.
                error -= gain*direction[1]*duration
                self.assertGreaterEqual(error, -.04)
                elapsed += duration+self.cfg.AUTO_PAN_TILT_SETTLE
                pulses += 1
            self.assertLessEqual(abs(error), .04+1e-9)
            return pulses, elapsed
        for gain in (.06, .12, .25):
            baseline = simulate(False, gain)
            adaptive = simulate(True, gain)
            self.assertLessEqual(adaptive[0], baseline[0])
            # Longer pulses do not guarantee shorter wall time on every plant.
            self.assertLess(adaptive[1], baseline[1]+.24)


class OverlapTests(unittest.TestCase):
    setUp = hybrid_tests.HybridTests.setUp

    def test_acquisition_uses_same_overlap_gate_as_tracking(self):
        self.s.reset()
        self.s._scores.return_value = [.95, .2]
        for t in (1.,1.1,1.2,1.3):
            self.assertIsNone(self.s.select_person(self.frame, [person(), person(2,120)], t))
        self.assertFalse(self.s.locked)
        for t in (1.4,1.5,1.6):
            p = self.s.select_person(self.frame, [person(), person(2,400)], t)
        self.assertIsNotNone(p)
        self.assertIsNotNone(self.s.select_person(self.frame, [person(), person(2,400)], 1.7))
        self.assertFalse(self.s.crossing_hold)

    def test_recovery_checks_new_candidate_not_unrelated_old_box(self):
        self.s._scores.return_value = [.1, .2, .95]
        # Two other people near the old location must not block a verified
        # candidate far away; full ReID+margin+three-frame confirmation still apply.
        for t in (.3,.4,.5):
            result = self.s.select_person(self.frame,
                [person(4,100),person(5,120),person(6,400)], t)
        self.assertIsNotNone(result)
        self.assertEqual(result['track_id'], 6)

    def test_same_tracker_duplicate_copy_is_not_two_people(self):
        self.assertFalse(self.s._candidate_overlap(person(), [person(), person()]))
        self.assertTrue(self.s._candidate_overlap(person(), [person(), person(2)]))


class RecoveryTests(unittest.TestCase):
    def test_precise_freshness_reasons(self):
        z = AutoZoomController(Mock(), max_observation_age=.8, feedback_timeout=.2)
        try:
            cases = [(float('nan'), 8.,10., 'RECOVERY_INVALID_TIMESTAMP'),
                     (9.,9.5,10., 'RECOVERY_TIMESTAMP_ORDER'),
                     (9.,8.,10., 'RECOVERY_OBSERVATION_TOO_OLD'),
                     (9.5,8.,9.6, 'RECOVERY_PUBLISH_TOO_OLD')]
            with patch('control.auto_zoom.time.monotonic', return_value=10.):
                for received, absent, published, reason in cases:
                    self.assertFalse(z.recover_missing(received,absent,2.,publish_time=published))
                    self.assertEqual(z._recovery_hold_reason,reason)
                z._correlation = {'lock_session_id':'old'}
                z.session_valid = lambda _:False
                self.assertFalse(z.recover_missing(9.9,8.,2.,publish_time=9.95))
                self.assertEqual(z._recovery_hold_reason,'RECOVERY_SESSION_MISMATCH')
        finally:
            z.close()


class HanetTraceTests(unittest.TestCase):
    def test_four_zoom_pulses_count_once_each_and_pan_is_excluded(self):
        rpc = Mock()
        rpc.motion.return_value = True
        ptz = HanetController(ip='mock',username='u',password='p',autoconnect=False,
            rpc_factory=lambda *a,**k:rpc)
        try:
            with tempfile.TemporaryDirectory() as directory:
                trace = ZoomTrace(directory,enabled=True,evidence_source='mock')
                try:
                    with patch('telemetry.zoom_trace.recorder',trace):
                        for _ in range(4):
                            self.assertTrue(ptz.command('zoom_in',2)[0])
                            self.assertTrue(ptz.stop()[0])
                        self.assertTrue(ptz.command('pan_right',1)[0])
                        ptz.stop()
                finally:
                    trace.close()
                with trace.summary_path.open() as stream:
                    rows = list(csv.DictReader(stream))
                self.assertEqual(sum(int(r['number_of_zoom_commands']) for r in rows),4)
                self.assertEqual(sum(int(r['number_of_pulse_commands']) for r in rows),4)
                self.assertEqual(sum(int(r['rpc_errors']) for r in rows),0)
        finally:
            ptz.close()
