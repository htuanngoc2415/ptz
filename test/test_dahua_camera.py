"""RPC2 contracts and 37x integration, with no connection to real hardware."""
import hashlib
import json
import threading
import time
import unittest
from unittest.mock import Mock
from camera.zoom_prediction import PositionABSMapping
from camera.zoom_capability import ZoomCapability
from camera.dahua_rpc import ZoomRPC
from camera.ptz_controller import PTZController, PTZ_ZOOM_IN, PTZ_FOCUS_MINUS


class Response:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


class Firmware:
    def __init__(self):
        self.calls = []
        self.headers = {}
        self.cookies = Mock()
        self.reject = None
        self.closed = False
        self.login_count = 0
        self.basic = False
        self.focus = [[{'Mode': 2, 'AutoFocusTrace': 1, 'Unrelated': 77}]]

    def post(self, url, data, timeout):
        p = json.loads(data)
        self.calls.append((url, p))
        m = p['method']
        if m == self.reject:
            return Response({'result': False, 'error': {'code': 99}})
        if m == 'global.login':
            self.login_count += 1
            if self.login_count % 2:
                return Response({'result': False, 'session': 123,
                                 'params': {'realm': 'camera', 'random': 'nonce',
                                            'encryption': 'Basic' if self.basic else 'Default'}})
            return Response({'result': True, 'session': 123})
        if m == 'ptz.factory.instance':
            return Response({'result': 456})
        if m == 'configManager.getConfig':
            return Response({'result': True, 'params': {'table': json.loads(json.dumps(self.focus))}})
        if m == 'configManager.setTemporaryConfig':
            self.focus = p['params']['table']
        if m == 'ptz.getStatus':
            return Response({'result': True, 'params': {'status': {'ZoomValue': 180, 'PTZFocusHD': 12000}}})
        return Response({'result': True})

    def close(self): self.closed = True


class RpcTests(unittest.TestCase):
    def setUp(self):
        self.f = Firmware()
        self.rpc = ZoomRPC('camera', 80, 'admin', 'secret', session_factory=lambda:self.f)

    def tearDown(self):
        self.f.reject = None
        self.rpc.close()

    def test_digest_login_session_object_and_no_cleartext_password(self):
        calls = [p for _,p in self.f.calls]
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest().upper()
        self.assertEqual(calls[1]['params']['password'], md5('admin:nonce:'+md5('admin:camera:secret')))
        self.rpc.start(1, 8, 'zoom')
        p = self.f.calls[-1][1]
        self.assertEqual(p['session'],123)
        self.assertEqual(p['object'],456)
        self.assertEqual(p['params']['code'],'ZoomTele')
        self.assertNotIn('secret',json.dumps(calls))
        self.assertTrue(self.f.calls[0][0].endswith('/RPC2_Login'))
        self.assertTrue(self.f.calls[-1][0].endswith('/RPC2'))

    def test_axis_specific_stop_and_retry_rejected_stop(self):
        self.rpc.start(-1,8,'focus')
        self.f.reject = 'ptz.stop'
        with self.assertRaises(RuntimeError): self.rpc.stop('focus')
        self.f.reject = None
        self.rpc.stop('focus')
        self.assertEqual(self.f.calls[-1][1]['params']['code'],'FocusFar')

    def test_http_success_with_rpc_error_is_failure(self):
        self.f.reject = 'ptz.start'
        with self.assertRaises(RuntimeError): self.rpc.start(1,8,'zoom')

    def test_manual_mode_preserves_and_restores_config(self):
        self.assertTrue(self.rpc.focus_manual(True)[0])
        self.assertEqual(self.f.focus[0][0], {'Mode':4,'AutoFocusTrace':0,'Unrelated':77})
        self.assertTrue(self.rpc.focus_manual(False)[0])
        self.assertEqual(self.f.focus[0][0], {'Mode':2,'AutoFocusTrace':1,'Unrelated':77})

    def test_rejected_manual_mode_is_retried_and_original_restored(self):
        self.f.reject = 'configManager.setTemporaryConfig'
        self.assertFalse(self.rpc.focus_manual(True)[0])
        self.assertFalse(self.rpc.focus_manual(True)[0])
        self.f.reject = None
        self.assertTrue(self.rpc.focus_manual(True)[0])
        self.assertEqual(self.f.focus[0][0]['Mode'], 4)
        self.assertTrue(self.rpc.focus_manual(False)[0])
        self.assertEqual(self.f.focus[0][0]['Mode'], 2)

    def test_missing_focus_trace_field_does_not_claim_manual_mode(self):
        self.f.focus = [[{'Mode': 2}]]
        self.assertFalse(self.rpc.focus_manual(True)[0])
        self.assertFalse(self.rpc.focus_manual(True)[0])

    def test_read_only_close_sends_no_motor_command(self):
        self.f.calls.clear()
        self.rpc.close(stop_motors=False)
        self.assertFalse(any(p['method'].startswith('ptz.') for _, p in self.f.calls))

    def test_position_abs_has_separate_linear_command_scale(self):
        self.rpc.goto((19.-1.)/36.)
        p = self.f.calls[-1][1]['params']
        self.assertEqual(p['code'],'PositionABS')
        self.assertEqual(p['arg3'],65)

    def test_stop_all_still_attempts_both_axes_on_failure(self):
        self.f.reject = 'ptz.stop'
        before = len(self.f.calls)
        with self.assertRaises(RuntimeError): self.rpc.stop_all()
        self.assertEqual(len(self.f.calls)-before,2)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.r = Mock()
        self.r.protocol_caps.return_value = {'result':True,'params':{'caps':{'MoveAbsolutely':{'PTZ':['Zoom']}}}}
        self.r.status_raw.return_value = {'params':{'status':{'ZoomValue':100,'PTZFocusHD':12000}}}
        self.r.focus_manual.return_value = (True,'OK')
        self.r._call.return_value = {'params':{'table':[[{'Speed':50,'Other':1}]]}}
        self.r._profile_dau.side_effect = lambda table:table[0][0]
        self.c = PTZController('camera','user','secret',rpc_factory=lambda **kw:self.r,autoconnect=False)
        self.addCleanup(self.c._command_worker.close)

    def test_no_network_on_constructor_or_cached_status(self):
        self.assertIsNone(self.c.telemetry())
        self.assertFalse(self.r.mock_calls)

    def test_lens_feedback_not_elapsed_time_and_stale_feedback_hidden(self):
        self.c.read_status()
        self.assertEqual(self.c.telemetry()['zoom'],10)
        self.assertEqual(self.c.telemetry()['focus'],12000)
        self.c._snapshot['timestamp'] -= 2
        self.assertIsNone(self.c.telemetry())

    def test_missing_or_invalid_zoom_is_not_assumed_one(self):
        for value in (None,float('nan'),float('inf'),500):
            self.r.status_raw.return_value={'params':{'status':{'ZoomValue':value}}}
            with self.assertRaises(RuntimeError): self.c.read_status()

    def test_wide_target_uses_feedback_seek_instead_of_abs_floor(self):
        self.assertTrue(self.c.start_zoom_target(1)[0])
        self.r.start.assert_called_once_with(-1,8,'zoom')
        self.r.goto.assert_not_called()
        self.assertEqual(self.r._call.call_args.args[1]['table'][0][0]['Other'],1)

    def test_telephoto_target_uses_position_abs(self):
        self.assertTrue(self.c.start_zoom_target(19)[0])
        self.r.goto.assert_called_once_with(PositionABSMapping().zoom_to_command(19))
        self.r.start.assert_not_called()

    def test_focus_direction_enables_manual_mode(self):
        self.assertTrue(self.c.command(PTZ_FOCUS_MINUS)[0])
        self.r.focus_manual.assert_called_once_with(True)
        self.r.start.assert_called_once_with(-1,8,'focus')

    def test_unconfirmed_stop_blocks_next_start_until_stop_succeeds(self):
        self.c.read_status()
        self.r.stop_all.side_effect = RuntimeError('timeout')
        self.assertFalse(self.c.stop()[0])
        self.assertFalse(self.c.command(PTZ_ZOOM_IN)[0])
        self.r.start.assert_not_called()
        self.r.stop_all.side_effect = None
        self.assertTrue(self.c.stop()[0])
        self.assertTrue(self.c.command(PTZ_ZOOM_IN)[0])

    def test_cancelled_focus_never_starts_motor(self):
        cancel = threading.Event();cancel.set()
        self.assertFalse(self.c.move_focus(12000,cancel)[0])
        self.r.chay_toi.assert_not_called()

    def test_unknown_commands_are_not_sent(self):
        self.assertFalse(self.c.command(9)[0])
        self.assertFalse(self.r.mock_calls)

    def test_closed_loop_zoom_always_stops_on_timeout(self):
        ok,_ = self.c.move_zoom(19,timeout=0)
        self.assertFalse(ok)
        self.r.stop_all.assert_called()

    def test_cancelled_zoom_never_starts_motor(self):
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(self.c.move_zoom(19, cancel=cancel)[0])
        self.assertFalse(self.r.mock_calls)

    def test_start_ack_loss_stops_motor_and_latches_stop_failure(self):
        self.r.start.side_effect = RuntimeError('ACK lost')
        self.r.stop_all.side_effect = RuntimeError('STOP lost')
        self.assertFalse(self.c.zoom_in()[0])
        self.r.stop_all.assert_called_once()
        self.r.start.reset_mock()
        self.assertFalse(self.c.zoom_in()[0])
        self.r.start.assert_not_called()

    def test_absolute_start_ack_loss_still_stops_motor(self):
        self.r.goto.side_effect = RuntimeError('ACK lost')
        self.assertFalse(self.c.move_zoom(19)[0])
        self.r.stop_all.assert_called()


if __name__ == '__main__': unittest.main()

class AbsoluteZoomIntegrationTests(unittest.TestCase):
    def controller(self):
        from control.auto_zoom import AutoZoomController
        c = PTZController('camera','user','secret',autoconnect=False)
        c._snapshot = {'zoom':10.,'focus':12000.,'timestamp':time.monotonic()}
        c._absolute_capability = ZoomCapability(True, 'mock')
        c.move_to_zoom_position = Mock(return_value=(True,'OK'))
        c.stop = Mock(return_value=(True,'STOP'))
        c.command = Mock(side_effect=AssertionError('Production auto zoom must use position'))
        controller = AutoZoomController(c, wait_after_zoom=0, feedback_timeout=.15)
        controller.set_enabled(True)
        self.addCleanup(controller.close)
        self.addCleanup(c._command_worker.close)
        return controller,c

    def wait_for(self, predicate):
        deadline=time.monotonic()+1.
        while not predicate() and time.monotonic()<deadline: time.sleep(.005)
        self.assertTrue(predicate())

    def test_target_ratio_maps_to_position_and_position_feedback_stops(self):
        z,c=self.controller()
        z.update((1000,1000),{'bbox':(450,400,550,600)},.4,.01)
        self.wait_for(lambda:c.move_to_zoom_position.called)
        self.assertEqual(c.move_to_zoom_position.call_args.args[0],PositionABSMapping().zoom_to_command(20.))
        time.sleep(.01)  # status observed after ACK
        c._snapshot={'zoom':20.,'focus':12000.,'timestamp':time.monotonic(),'zoom_motion_state':'Idle'}
        self.wait_for(lambda:not z.busy)
        self.assertEqual(z.last_stop_reason,'POSITION_REACHED')
        c.command.assert_not_called()
        c.stop.assert_not_called()

    def test_stale_telemetry_prevents_start(self):
        z,c=self.controller();c._snapshot['timestamp']-=2
        result=z.update((1000,1000),{'bbox':(450,400,550,600)},.4,.01)
        self.assertEqual(result,'WAITING FOR LENS TELEMETRY')
        c.move_to_zoom_position.assert_not_called()

    def test_lost_telemetry_stops_inflight_position_command(self):
        z,c=self.controller()
        z.update((1000,1000),{'bbox':(450,400,550,600)},.4,.01)
        self.wait_for(lambda:c.move_to_zoom_position.called)
        c._snapshot=None
        self.wait_for(lambda:not z.busy)
        self.assertEqual(z.last_stop_reason,'STALE_LENS_TELEMETRY')

    def test_lost_visual_feedback_stops_even_with_fresh_lens_position(self):
        z,c=self.controller()
        z.update((1000,1000),{'bbox':(450,400,550,600)},.4,.01)
        self.wait_for(lambda:not z.busy)
        self.assertEqual(z.last_stop_reason,'WORKER_TIMEOUT')
