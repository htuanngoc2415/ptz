"""HANET protocol routing, cancellation and unsupported capability regressions."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import Mock

from camera import create_controller
from camera.hanet_controller import HanetController
from telemetry import latency


class HanetTests(unittest.TestCase):
    def setUp(self):
        self.rpc = Mock()
        self.rpc.motion.return_value = True
        self.controller = HanetController(ip='test', username='u', password='p',
            autoconnect=False, rpc_factory=lambda *a, **k: self.rpc)

    def tearDown(self):
        self.rpc.motion.side_effect = None
        self.controller.close()

    def test_routing_speed_and_stop(self):
        self.assertTrue(self.controller.command('pan_left', 100)[0])
        self.rpc.motion.assert_called_with('kCmdLeft', True, 8)
        self.assertTrue(self.controller.stop()[0])
        self.rpc.motion.assert_called_with('kCmdLeft', False, 1)

    def test_new_direction_stops_previous_direction(self):
        self.controller.command('zoom_in', 1)
        self.controller.command('zoom_out', 2)
        self.assertEqual([call.args for call in self.rpc.motion.call_args_list], [
            ('kCmdZoomTele', True, 1), ('kCmdZoomTele', False, 1),
            ('kCmdZoomWide', True, 2)])

    def test_lost_start_ack_attempts_stop(self):
        self.rpc.motion.side_effect = [TimeoutError(), True]
        self.assertFalse(self.controller.command('zoom_in', 1)[0])
        self.assertEqual(self.rpc.motion.call_args_list[-1].args, ('kCmdZoomTele', False, 1))

    def test_lost_stop_ack_blocks_motion_until_stop_retry(self):
        self.controller.command('zoom_in')
        self.rpc.motion.side_effect = TimeoutError()
        self.assertFalse(self.controller.stop()[0])
        self.assertFalse(self.controller.command('zoom_out')[0])
        self.rpc.motion.side_effect = None
        self.assertTrue(self.controller.emergency_stop()[0])
        self.assertTrue(self.controller.command('zoom_out')[0])

    def test_stale_session_is_rejected_before_io(self):
        self.controller.session_valid = lambda _: False
        with latency.correlated(lock_session_id='expired'):
            self.assertFalse(self.controller.command('zoom_in')[0])
        self.rpc.motion.assert_not_called()

    def test_no_fabricated_positions_or_dahua_commands(self):
        self.assertIsNone(self.controller.telemetry())
        self.assertFalse(self.controller.supports_absolute_zoom)
        self.assertFalse(self.controller.move_zoom(10)[0])
        self.assertFalse(self.controller.move_focus(100)[0])
        self.assertFalse(self.controller.autofocus()[0])
        self.assertFalse(self.controller.command('focus_near')[0])
        self.rpc.motion.assert_not_called()

    def test_status_and_close_without_motion(self):
        self.assertTrue(self.controller.read_status()['connected'])
        self.controller.close()
        self.controller.close()
        self.rpc.motion.assert_not_called()
        self.rpc.close.assert_called_once()
        self.assertFalse(self.controller._command_worker.thread.is_alive())

    def test_factory_rejects_unknown_backend(self):
        with self.assertRaises(ValueError):
            create_controller('unknown')
