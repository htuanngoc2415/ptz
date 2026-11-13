"""No hardware: real control threads, arbitration and HANET command validation."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import config
from control.auto_pan_tilt import AutoPanTiltController
from control.orchestrator import ControlOrchestrator
from camera.hanet_controller import HanetController
from telemetry import latency


class PanTiltTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.AUTO_PAN_TILT_PULSE = .15
        self.cfg.AUTO_PAN_TILT_SETTLE = .02
        self.cfg.MAX_CONTROL_OBSERVATION_AGE = .3
        self.ptz = Mock(supports_pan_tilt=True)
        self.started, self.stopped = threading.Event(), threading.Event()
        self.ptz.command.side_effect = lambda *args: (self.started.set() or True, 'OK')
        self.ptz.stop.side_effect = lambda: (self.stopped.set() or True, 'OK')
        self.owner = ControlOrchestrator()
        self.control = AutoPanTiltController(self.ptz, self.owner, self.cfg)
        self.control.session_valid = lambda session, track: session == 's' and track == 1

    def tearDown(self):
        self.ptz.stop.side_effect = lambda: (True, 'OK')
        self.assertTrue(self.control.close(timeout=2))

    def submit(self, box=(700,200,900,400), session='s', received=None):
        return self.control.submit(dict(bbox=box), (600,1000,3),
            time.monotonic() if received is None else received, session, 1)

    def test_right_person_pan_then_stop_without_new_frames(self):
        self.assertTrue(self.submit())
        self.assertTrue(self.started.wait(1))
        self.assertEqual(self.ptz.command.call_args.args[0], 'pan_right')
        self.assertTrue(self.stopped.wait(1))
        self.assertEqual(self.ptz.command.call_count, 1)

    def test_centered_target_does_not_move(self):
        self.assertFalse(self.submit((450,250,550,350)))
        self.assertFalse(self.started.wait(.08))

    def test_all_directions_and_dominant_axis(self):
        for box, expected in [((0,200,100,400),'pan_left'), ((450,0,550,100),'tilt_up'),
                              ((450,500,550,600),'tilt_down'), ((800,400,900,500),'pan_right')]:
            self.control._axis = None
            self.assertEqual(self.control._direction(box,(600,1000,3))[0], expected)

    def test_hysteresis(self):
        self.control._axis = None
        self.assertIsNone(self.control._direction((510,250,610,350),(600,1000,3)))
        self.control._direction((700,250,800,350),(600,1000,3))
        self.assertIsNotNone(self.control._direction((510,250,610,350),(600,1000,3)))
        self.assertIsNone(self.control._direction((480,250,580,350),(600,1000,3)))

    def test_stale_and_unconfirmed_targets_do_not_move(self):
        self.assertFalse(self.submit(received=time.monotonic()-1))
        self.assertFalse(self.submit(session='wrong'))
        self.assertFalse(self.started.wait(.08))

    def test_target_loss_interrupts_pulse(self):
        self.cfg.AUTO_PAN_TILT_PULSE = 1.
        self.submit()
        self.assertTrue(self.started.wait(1))
        self.control.cancel()
        self.assertTrue(self.stopped.wait(.3))

    def test_manual_preemption_interrupts_pulse(self):
        self.cfg.AUTO_PAN_TILT_PULSE = 1.
        self.submit()
        self.assertTrue(self.started.wait(1))
        self.owner.manual = True
        self.assertTrue(self.stopped.wait(.3))
        self.assertFalse(self.control.busy)

    def test_zoom_lease_blocks_pan(self):
        self.owner.acquire('zoom')
        self.submit()
        self.assertFalse(self.started.wait(.08))
        self.owner.release('zoom')
        self.assertTrue(self.started.wait(1))

    def test_disable_and_close_stop_active_pulse(self):
        self.cfg.AUTO_PAN_TILT_PULSE = 1.
        self.submit()
        self.assertTrue(self.started.wait(1))
        self.control.set_enabled(False)
        self.assertTrue(self.stopped.wait(.3))
        self.assertFalse(self.submit())
        self.assertTrue(self.control.close(1))

    def test_wire_rejection_after_target_loss_does_not_disable_reacquisition(self):
        def stale(*args):
            self.control.cancel()
            self.started.set()
            return False, 'Stale camera movement rejected'
        self.ptz.command.side_effect = stale
        self.submit()
        self.assertTrue(self.started.wait(1))
        self.assertTrue(self.stopped.wait(1))
        self.assertTrue(self.control.enabled)

    def test_stop_failure_retains_lease_and_disables_motion(self):
        failed = threading.Event()
        def fail():
            failed.set()
            return False, 'lost ACK'
        self.ptz.stop.side_effect = fail
        self.submit()
        self.assertTrue(failed.wait(1))
        self.assertFalse(self.owner.acquire('zoom'))
        self.assertEqual(self.owner.owner, 'pan_tilt')
        self.assertFalse(self.control.enabled)
        self.ptz.stop.side_effect = lambda: (self.stopped.set() or True, 'OK')
        self.assertTrue(self.stopped.wait(1))
        self.assertFalse(self.control.enabled)


class WireValidationTests(unittest.TestCase):
    def test_pan_uses_own_guard_and_rechecks_before_rpc(self):
        rpc = Mock()
        rpc.motion.return_value = True
        ptz = HanetController(ip='test', username='u', password='p',
            autoconnect=False, rpc_factory=lambda *a, **k: rpc)
        try:
            ptz.zoom_intent_valid = lambda fields: False
            ptz.pan_tilt_intent_valid = lambda fields: False
            with latency.correlated(motor_owner='pan_tilt', lock_session_id='s',
                                    pan_tilt_command='pan_right'):
                self.assertFalse(ptz.command('pan_right', 1)[0])
                rpc.motion.assert_not_called()
                ptz.pan_tilt_intent_valid = lambda fields: True
                self.assertTrue(ptz.command('pan_right', 1)[0])
                rpc.motion.assert_called_with('kCmdRight', True, 1)
                self.assertFalse(ptz.command('zoom_in', 1)[0])
        finally:
            ptz.close()

class WorkerIntegrationTests(unittest.TestCase):
    def test_selected_person_is_forwarded_and_centering_suppresses_zoom(self):
        from test_reid_target import WorkerTests
        pan = Mock()
        pan.submit.side_effect = lambda person, *args, **kwargs: person is not None
        def attach(app, index):
            if index == 0:
                app.auto_pan_tilt = pan
        app, rows = WorkerTests().run_worker([.95]*12, hybrid=True, frame_hook=attach)
        selected = [c for c in pan.submit.call_args_list if c.args[0] is not None]
        self.assertTrue(selected)
        self.assertTrue(all(c.args[3] and c.args[4] == c.args[0]['track_id'] for c in selected))
        app.auto_zoom.request_stop.assert_any_call(reason='PAN_TILT_CENTERING')
        self.assertFalse(any(c.kwargs.get('target_name') == 'PERSON'
                             for c in app.auto_zoom.submit_observation.call_args_list))

if __name__ == '__main__':
    unittest.main()
