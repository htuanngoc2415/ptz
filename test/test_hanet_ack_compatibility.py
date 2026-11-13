"""Offline ACK/schema tests. All success responses are SYNTHETIC contracts.

There is no captured HANET firmware success fixture in this repository.
"""
import json
import queue
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera.hanet_ack import (ACK_SUCCESS, ACK_REJECTED, ACK_UNKNOWN,
    PROTOCOL_ERROR, TRANSPORT_ERROR, AckDecision, HanetACKUnknown,
    HanetProtocolError, HanetRPCRejected, validate_response, response_schema,
    failure_classification)
from camera.hanet_rpc import HanetPTZClient
from control.auto_zoom import AutoZoomController
from control.auto_pan_tilt import AutoPanTiltController
from control.orchestrator import ControlOrchestrator
from runtime.stop_retry import StopRetryBudget
from telemetry import latency
import test_hanet_fault_injection as fixtures


class AckParserTests(unittest.TestCase):
    def client(self, replies):
        with patch.object(HanetPTZClient, '_login'):
            client = HanetPTZClient('offline.invalid', 80, 'u', 'never-log-password')
        self.addCleanup(client.close)
        client.session_id = 'never-log-session'
        client._recv_frame = Mock(side_effect=replies)
        client._event = Mock()
        return client

    @staticmethod
    def frame(message):
        return 1, json.dumps(message).encode()

    def test_legacy_boolean_true_contract_start_and_stop(self):
        for operation in ('START', 'STOP'):
            with self.subTest(operation=operation):
                client = self.client([self.frame({'jsonrpc':'2.0', 'id':'new', 'result':True})])
                self.assertTrue(client._receive_response('new', operation=operation, code='kCmdLeft'))
                self.assertEqual(client._event.call_args.kwargs['ack_classification'], ACK_SUCCESS)

    def test_false_and_error_object_are_rejected(self):
        for message in ({'jsonrpc':'2.0', 'id':'new', 'result':False},
                        {'jsonrpc':'2.0', 'id':'new', 'error':{'code':401, 'message':'never-log-password'}}):
            with self.subTest(message=message):
                client = self.client([self.frame(message)])
                with self.assertRaises(HanetRPCRejected): client._receive_response('new')

    def test_missing_null_object_numeric_and_string_remain_unknown(self):
        messages = [{'jsonrpc':'2.0', 'id':'new'}]+[{'jsonrpc':'2.0', 'id':'new', 'result':value} for value in
                    (None, {}, {'success':True}, {'data':{'code':0}}, 1, 0, 'true', 'OK', [])]
        for message in messages:
            with self.subTest(message=message):
                client = self.client([self.frame(message)])
                with self.assertRaises(HanetACKUnknown): client._receive_response('new')
                self.assertEqual(client._event.call_args.kwargs['ack_classification'], ACK_UNKNOWN)

    def test_malformed_json_and_unexpected_envelope(self):
        for reply in ((1,b'{'), (1,b'\xff'), self.frame([]), self.frame(None),
                      self.frame({'id':'new','jsonrpc':'1.0','result':True}),
                      self.frame({'jsonrpc':'2.0', 'id':'new','result':True,'error':False})):
            with self.subTest(reply=reply):
                client = self.client([reply])
                with self.assertRaises(HanetProtocolError): client._receive_response('new')

    def test_ping_pong_are_not_ack_and_binary_is_protocol_error(self):
        client = self.client([(9,b'ping'), (10,b'pong'), self.frame({'jsonrpc':'2.0', 'id':'new','result':True})])
        client._send_frame = Mock()
        self.assertTrue(client._receive_response('new'))
        client._send_frame.assert_called_once_with(b'ping', opcode=10)
        client = self.client([(2,b'{"id":"new","result":true}')])
        with self.assertRaises(HanetProtocolError): client._receive_response('new')

    def test_mismatch_then_valid_ack_and_mismatch_only(self):
        stale = self.frame({'jsonrpc':'2.0', 'id':'old','result':True})
        client = self.client([stale, self.frame({'jsonrpc':'2.0', 'id':'new','result':True})])
        self.assertTrue(client._receive_response('new'))
        self.assertEqual(client._event.call_args_list[0].kwargs['parser_reason'], 'MISMATCHED_REQUEST_ID')
        client = self.client([stale, TimeoutError()])
        with self.assertRaises(HanetProtocolError): client._receive_response('new')
        self.assertEqual(validate_response({'jsonrpc':'2.0', 'id':1,'result':True}, '1').classification, PROTOCOL_ERROR)

    def test_delayed_ack_inside_deadline(self):
        now = [0.]
        client = self.client([])
        def receive():
            now[0] = .5
            return self.frame({'jsonrpc':'2.0', 'id':'new','result':True})
        client._recv_frame.side_effect = receive
        client.timeout = 1.
        with patch('camera.hanet_rpc.time.monotonic', lambda: now[0]):
            self.assertTrue(client._receive_response('new'))

    def test_ack_after_transaction_deadline_is_not_success(self):
        now = [0.]
        client = self.client([])
        client.timeout = 5.
        client._io_deadline = 1.
        def receive():
            now[0] = 1.1
            return self.frame({'jsonrpc':'2.0', 'id':'new','result':True})
        client._recv_frame.side_effect = receive
        with patch('camera.hanet_rpc.time.monotonic', lambda: now[0]):
            with self.assertRaises(TimeoutError): client._receive_response('new')

    def test_connection_generation_changed_before_ack(self):
        client = self.client([])
        client.generation = 2
        def receive():
            client.generation = 3
            return self.frame({'jsonrpc':'2.0', 'id':'new','result':True})
        client._recv_frame.side_effect = receive
        with self.assertRaises(HanetProtocolError):
            client._receive_response('new', generation=2, operation='STOP')
        self.assertEqual(client._event.call_args.kwargs['ack_classification'], PROTOCOL_ERROR)

    def test_failure_classes_are_distinct(self):
        for exc, expected in ((HanetACKUnknown(), ACK_UNKNOWN),
                              (HanetRPCRejected(), ACK_REJECTED),
                              (HanetProtocolError(), PROTOCOL_ERROR),
                              (TimeoutError(), TRANSPORT_ERROR), (OSError(), TRANSPORT_ERROR)):
            self.assertEqual(failure_classification(exc), expected)

    def test_schema_has_no_remote_values_or_untrusted_keys(self):
        secrets = ['never-log-password','never-log-session','cookie-secret','dynamic-secret-key']
        message = {'jsonrpc':'2.0', 'id':secrets[0], 'result': {'status':secrets[1], 'code':123456789,
            'data':{'success':secrets[2]}, secrets[3]:True}, 'token':secrets[1]}
        schema = response_schema(message, 'local-uuid')
        text = json.dumps(schema)
        for secret in secrets+['123456789']:
            self.assertNotIn(secret, text)
        self.assertEqual(schema['response_id'], '<unmatched>')
        self.assertEqual(schema['result_type'], 'dict')
        self.assertEqual(schema['response_shape']['fields']['result']['fields']['status']['type'], 'str')

    def test_diagnostics_bounded_for_large_and_deep_response(self):
        nested = {'status':True}
        for _ in range(50): nested = {'data':nested,'status':'x'*10000}
        for result in (nested, {str(i):'s'*1000 for i in range(1000)}):
            self.assertLessEqual(len(json.dumps(response_schema({'jsonrpc':'2.0', 'id':'new','result':result}, 'new'))), 4096)

    def test_rate_limit_first_unknown_after_success_and_config_off(self):
        client = self.client([])
        with patch('camera.hanet_rpc.time.monotonic', return_value=100.):
            for _ in range(100):
                client._response_diagnostic({'jsonrpc':'2.0', 'id':'new','result':True}, 'new', 'STOP', 'kCmdLeft',
                                            AckDecision(ACK_SUCCESS, 'LEGACY_BOOLEAN_TRUE'))
            client._response_diagnostic({'jsonrpc':'2.0', 'id':'new'}, 'new', 'STOP', 'kCmdLeft',
                                        AckDecision(ACK_UNKNOWN, 'RESULT_MISSING'))
        self.assertEqual(client._event.call_count, 2)
        client.response_diagnostics = False
        client._response_diagnostic({}, 'new', 'START', 'kCmdLeft', AckDecision(ACK_UNKNOWN,'RESULT_MISSING'))
        self.assertEqual(client._event.call_count, 2)
        with patch.dict('os.environ', {'PTZ_HANET_RESPONSE_DIAGNOSTICS':'0'}), patch.object(HanetPTZClient,'_login'):
            other = HanetPTZClient('offline.invalid',80,'u','p')
        self.addCleanup(other.close)
        self.assertFalse(other.response_diagnostics)
        other._recv_frame = Mock(return_value=self.frame({'jsonrpc':'2.0', 'id':'new','result':{}}))
        with self.assertRaises(HanetACKUnknown): other._receive_response('new')

    def test_first_start_and_stop_schemas_survive_rotation(self):
        with tempfile.TemporaryDirectory() as directory:
            profiler = latency.LatencyProfiler(directory=directory, enabled=True, max_bytes=500, backups=1)
            try:
                for op in ('START','STOP'):
                    profiler.event('HANET_RESPONSE_SCHEMA', operation=op, ack_classification=ACK_UNKNOWN,
                                   **response_schema({'jsonrpc':'2.0', 'id':'local','result':{'status':'secret'}}, 'local'))
                for _ in range(300): profiler.event('CAMERA_TEST_ROTATION', message='x'*200)
            finally:
                self.assertTrue(profiler.close())
            rows = json.loads(next((Path(directory)/'camera').glob('first_failures_*.json')).read_text())
            self.assertEqual({r['operation'] for r in rows}, {'START','STOP'})
            self.assertNotIn('secret', json.dumps(rows))

    def test_schema_retained_when_main_log_queue_is_full(self):
        with tempfile.TemporaryDirectory() as directory:
            profiler = latency.LatencyProfiler(directory=directory, enabled=True)
            try:
                with patch.object(profiler.queue,'put_nowait',side_effect=queue.Full):
                    profiler.event('HANET_RESPONSE_SCHEMA', operation='STOP',
                                   ack_classification=ACK_UNKNOWN, result_type='dict')
                self.assertEqual(profiler.counters['dropped_log_events'],1)
            finally:
                self.assertTrue(profiler.close())
            rows = json.loads(next((Path(directory)/'camera').glob('first_failures_*.json')).read_text())
            self.assertEqual(rows[0]['result_type'],'dict')


class AckRecoveryTests(unittest.TestCase):
    # Reuse only the network-denied fixtures, not their test methods.
    setUp = fixtures.FaultInjectionTests.setUp
    client = fixtures.FaultInjectionTests.client
    controller = fixtures.FaultInjectionTests.controller

    def zoom(self, camera, owner):
        zoom = AutoZoomController(camera, orchestrator=owner, wait_after_zoom=0)
        zoom.busy = zoom.zoom_motor_active = True
        zoom._started = self.clock()
        zoom.zoom_direction = -1
        zoom._stop_budget = StopRetryBudget(clock=self.clock)
        self.addCleanup(zoom.close)
        return zoom

    def pan(self, camera, owner):
        pan = AutoPanTiltController.__new__(AutoPanTiltController)
        pan.ptz, pan.orchestrator = camera, owner
        pan.cfg = SimpleNamespace(AUTO_PAN_TILT_SETTLE=0)
        pan._fault = True
        pan._stop_budget = StopRetryBudget(clock=self.clock)
        pan.status = Mock()
        return pan

    def test_zoom_unknown_reconnect_unknown_then_stop_restores_pan_acquisition(self):
        wires = [fixtures.FakeSocket([{}]), fixtures.FakeSocket([None]), fixtures.FakeSocket([True])]
        camera, _ = self.controller(self.client(wires))
        owner = ControlOrchestrator(); self.assertTrue(owner.acquire('zoom'))
        zoom = self.zoom(camera, owner)
        self.assertFalse(camera.command('zoom_out')[0])
        self.clock.advance(1); zoom.stop()
        self.assertTrue(camera._motor_fault); self.assertTrue(zoom.busy)
        self.assertFalse(owner.acquire('pan_tilt'))
        self.clock.advance(2); zoom.stop()
        self.assertFalse(camera._motor_fault); self.assertFalse(zoom.busy)
        self.assertFalse(camera._active_codes); self.assertTrue(owner.acquire('pan_tilt'))
        self.assertEqual([r['params']['data']['operate'] for w in wires for r in w.commands],
                         ['kOperateStart','kOperateStop','kOperateStop'])

    def test_pan_unknown_then_confirmed_stop_releases_for_zoom(self):
        camera, _ = self.controller(self.client([fixtures.FakeSocket([{}]), fixtures.FakeSocket()]))
        owner = ControlOrchestrator(); owner.acquire('pan_tilt')
        pan = self.pan(camera, owner)
        self.assertFalse(camera.command('pan_left')[0])
        self.assertFalse(pan._stop()); self.assertFalse(owner.acquire('zoom'))
        self.clock.advance(1)
        self.assertTrue(pan._stop()); self.assertFalse(pan._fault)
        self.assertTrue(owner.acquire('zoom'))

    def test_external_stop_after_exhaustion_reconciles_zoom_owner(self):
        camera, rpc = self.controller()
        owner = ControlOrchestrator(); owner.acquire('zoom')
        zoom = self.zoom(camera, owner)
        camera.command('zoom_out'); rpc.motion.return_value = False
        for _ in range(8): zoom.stop(); self.clock.advance(10)
        self.assertTrue(zoom._stop_budget.exhausted)
        self.assertEqual(owner.owner, 'zoom')
        rpc.motion.return_value = True
        self.assertTrue(camera.emergency_stop()[0])
        zoom.stop()  # no extra wire STOP; backend READY permits bookkeeping
        self.assertFalse(zoom.busy); self.assertTrue(owner.acquire('pan_tilt'))

    def test_shutdown_does_not_reopen_admission_after_stop_success(self):
        camera, _ = self.controller(self.client([fixtures.FakeSocket()]))
        owner = ControlOrchestrator(); owner.acquire('zoom')
        zoom = self.zoom(camera, owner)
        self.assertTrue(camera.command('zoom_out')[0])
        owner.begin_shutdown(); camera.begin_shutdown()
        zoom.stop(reason='APPLICATION_CLOSE')
        self.assertIsNone(owner.owner)
        self.assertFalse(owner.acquire('pan_tilt')); self.assertFalse(camera.command('pan_left')[0])

    def test_unknown_stop_persists_across_restart(self):
        camera, _ = self.controller(self.client([fixtures.FakeSocket([True,{}])]))
        camera.command('zoom_out'); camera.stop(); camera.close()
        restarted, rpc = self.controller()
        self.assertEqual(restarted.motor_state, 'FAULT_LATCHED')
        self.assertFalse(restarted.command('pan_left')[0]); rpc.motion.assert_not_called()
        self.assertTrue(restarted.emergency_stop()[0])
        self.assertEqual(json.loads(restarted._safety_state_path.read_text())['active_codes'], [])


if __name__ == '__main__':
    unittest.main()
