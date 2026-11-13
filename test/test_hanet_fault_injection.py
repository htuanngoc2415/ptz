"""Offline fault injection. No real transport or camera construction is allowed."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from camera.hanet_rpc import HanetPTZClient
from control.auto_pan_tilt import AutoPanTiltController
from control.orchestrator import ControlOrchestrator
import config


class BaselineFaultTests(unittest.TestCase):
    def test_broken_socket_invalidated(self):
        with patch.object(HanetPTZClient, '_login'):
            client = HanetPTZClient('offline.invalid', 80, 'u', 'p', timeout=.05)
        client.session_id = 'test-session'
        old = Mock()
        old.sendall.side_effect = OSError('offline')
        client._ws = old
        try:
            with self.assertRaises(OSError):
                client.motion('kCmdLeft', False, 1)
            self.assertIsNone(client._ws)
        finally:
            client.session_id = None
            client.close()

    def test_missing_result_is_not_ack(self):
        client = HanetPTZClient.__new__(HanetPTZClient)
        client.timeout = .05
        client._recv_frame = Mock(return_value=(1, b'{"id":"matching"}'))
        with self.assertRaises(RuntimeError):
            client._receive_response('matching')

    def test_shutdown_fault_worker_exits(self):
        cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        ptz = Mock(supports_pan_tilt=True)
        ptz.stop.return_value = (False, 'unconfirmed')
        owner = ControlOrchestrator()
        owner.acquire('pan_tilt')
        pan = AutoPanTiltController(ptz, owner, cfg)
        with pan._condition:
            pan._fault = True
            pan._condition.notify_all()
        try:
            self.assertTrue(pan.close(timeout=.15))
            self.assertEqual(owner.owner, 'pan_tilt')
        finally:
            ptz.stop.return_value = (True, 'ack')
            pan.close(timeout=1)


from collections import deque
from contextlib import ExitStack
from pathlib import Path
import tempfile
import struct
from camera.hanet_controller import HanetController
from camera.hanet_rpc import HanetRPCRejected
from control.manual import ManualController
from runtime.camera_worker import CameraCommandWorker
from runtime.stop_retry import StopRetryBudget
from runtime.shutdown import close_controls
from telemetry import latency


class Clock:
    def __init__(self): self.now = 100.
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class FakeSocket:
    """Real frame encoder/decoder exercises the production WebSocket path."""
    def __init__(self, outcomes=()):
        self.outcomes = deque(outcomes)
        self.responses = deque()
        self.commands = []
        self.closed = False
        self.timeout = None
        self.late = None

    def settimeout(self, timeout): self.timeout = timeout
    def shutdown(self, how): pass
    def close(self): self.closed = True

    def sendall(self, frame):
        if self.closed: raise OSError('closed')
        if frame.startswith(b'GET '):
            self.responses.append(b'HTTP/1.1 101 Switching Protocols\r\n\r\n')
            return
        length = frame[1] & 127
        offset = 2
        if length == 126:
            length = struct.unpack('!H', frame[2:4])[0]; offset = 4
        elif length == 127:
            length = struct.unpack('!Q', frame[2:10])[0]; offset = 10
        mask = frame[offset:offset+4]
        payload = bytes(v ^ mask[i%4] for i,v in enumerate(frame[offset+4:]))
        if not payload: return
        request = json.loads(payload)
        self.commands.append(request)
        outcome = self.outcomes.popleft() if self.outcomes else True
        if outcome == 'send_error': raise OSError('send failed')
        if outcome == 'closed': self.responses.append(b''); return
        if isinstance(outcome, BaseException): self.responses.append(outcome); return
        if self.late:
            self.responses.append(self.frame(self.late)); self.late = None
        response = {'jsonrpc':'2.0', 'id': request['id'], 'result': outcome}
        if outcome == 'expired': response = {'jsonrpc':'2.0', 'id': request['id'], 'error': {'code': 401, 'message': 'synthetic expired session'}}
        self.responses.append(self.frame(response))

    @staticmethod
    def frame(response):
        data = json.dumps(response).encode()
        return bytes([0x81, len(data)])+data if len(data)<126 else b'\x81\x7e'+struct.pack('!H',len(data))+data

    def recv(self, size):
        if not self.responses: raise TimeoutError('no ACK')
        reply = self.responses.popleft()
        if isinstance(reply, BaseException): raise reply
        if len(reply)>size:
            self.responses.appendleft(reply[size:]); return reply[:size]
        return reply


class FaultInjectionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.tmp = self.stack.enter_context(tempfile.TemporaryDirectory())
        # Fail closed even if a test accidentally reaches an unmocked network path.
        self.stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('REAL HTTP FORBIDDEN')))
        self.socket_factory = self.stack.enter_context(patch('camera.hanet_rpc.socket.create_connection', side_effect=AssertionError('REAL SOCKET FORBIDDEN')))
        self.clock = Clock()

    def client(self, sockets):
        self.sockets = list(sockets)
        self.socket_factory.side_effect = self.sockets
        def login(client):
            client.session_id = 'private-test-session'
            client.next_heartbeat = float('inf')
        self.stack.enter_context(patch.object(HanetPTZClient, '_login', login))
        client = HanetPTZClient('offline.invalid', 80, 'u', 'private-password', timeout=.1)
        self.addCleanup(client.close)
        self.stack.enter_context(patch('camera.hanet_rpc.time.monotonic', self.clock))
        return client

    def controller(self, rpc=None, name='state.json'):
        rpc = rpc or Mock()
        if isinstance(rpc, Mock): rpc.motion.return_value = True
        controller = HanetController(ip='offline.invalid', username='u', password='p',
            timeout=.1, autoconnect=False, rpc_factory=lambda *a, **k: rpc,
            safety_state_path=Path(self.tmp)/name)
        controller._stop_budget = StopRetryBudget(clock=self.clock)
        self.addCleanup(controller.close)
        return controller, rpc

    def test01_normal_start_stop_and_ownership(self):
        wire = FakeSocket()
        client = self.client([wire])
        camera, _ = self.controller(client)
        owner = ControlOrchestrator(); owner.acquire('pan_tilt')
        pan = AutoPanTiltController.__new__(AutoPanTiltController)
        pan.ptz, pan.orchestrator = camera, owner
        pan.cfg = SimpleNamespace(AUTO_PAN_TILT_SETTLE=0)
        pan._fault = False; pan._stop_budget = StopRetryBudget(clock=self.clock)
        self.assertTrue(camera.command('pan_left')[0])
        self.assertEqual(camera.motor_state, 'MOTOR_ACTIVE')
        self.assertTrue(pan._stop())
        self.assertEqual(camera.motor_state, 'READY')
        self.assertIsNone(owner.owner)
        self.assertEqual(len(wire.commands), 2)

    def test02_start_timeout_never_replayed_then_safe_stop(self):
        first, second = FakeSocket([TimeoutError()]), FakeSocket()
        camera, _ = self.controller(self.client([first, second]))
        self.assertFalse(camera.command('pan_left')[0])
        self.assertTrue(camera._motor_fault)
        self.clock.advance(1)
        self.assertTrue(camera.stop()[0])
        operations = [r['params']['data']['operate'] for w in (first,second) for r in w.commands]
        self.assertEqual(operations, ['kOperateStart', 'kOperateStop'])

    def test03_stop_timeout_blocks_manual_and_zoom_start(self):
        wire = FakeSocket([True, TimeoutError()])
        camera, _ = self.controller(self.client([wire]))
        camera.command('pan_left')
        self.assertFalse(camera.stop()[0])
        for command in ('pan_right', 'zoom_in', 'zoom_out'):
            self.assertFalse(camera.command(command)[0])
        self.assertEqual(len(wire.commands), 2)
        self.assertIn('kCmdLeft', camera._active_codes)

    def test04_websocket_closed_during_stop_reconnects_once(self):
        first, second = FakeSocket([True, 'closed']), FakeSocket()
        client = self.client([first, second]); camera, _ = self.controller(client)
        camera.command('pan_left'); camera.stop()
        self.assertTrue(first.closed); self.assertIsNone(client._ws)
        self.clock.advance(1)
        self.assertTrue(camera.stop()[0])
        self.assertEqual(client.generation, 2)
        self.assertEqual(self.socket_factory.call_count, 2)

    def test05_stale_socket_not_reused_and_backoff_enforced(self):
        first, second = FakeSocket(['send_error']), FakeSocket()
        client = self.client([first, second])
        with self.assertRaises(OSError): client.motion('kCmdLeft', False, 1)
        for _ in range(50):
            with self.assertRaises(RuntimeError): client.motion('kCmdLeft', False, 1)
        self.assertEqual(len(first.commands), 1)
        self.clock.advance(1)
        self.assertTrue(client.motion('kCmdLeft', False, 1))
        self.assertTrue(first.closed)

    def test06_expired_session_reauth_without_start_replay(self):
        first, second = FakeSocket(['expired']), FakeSocket()
        client = self.client([first,second]); camera, _ = self.controller(client)
        with patch.object(client, '_login', wraps=client._login) as login:
            camera.command('pan_left')
            self.assertIsNone(client.session_id)
            self.clock.advance(1)
            self.assertTrue(camera.stop()[0])
            login.assert_called_once()
        self.assertEqual(second.commands[0]['params']['data']['operate'], 'kOperateStop')

    def test07_late_ack_old_connection_cannot_confirm_new_stop(self):
        first, second = FakeSocket([TimeoutError()]), FakeSocket([False])
        client = self.client([first,second])
        with self.assertRaises(TimeoutError): client.motion('kCmdLeft', True, 1)
        second.late = {'jsonrpc':'2.0', 'id': first.commands[0]['id'], 'result': True}
        self.clock.advance(1)
        with self.assertRaises(HanetRPCRejected): client.motion('kCmdLeft', False, 1)
        self.assertIsNone(client._ws)
        self.assertEqual(client.generation, 2)

    def test08_false_stop_ack_retry_budget_keeps_fault(self):
        camera, rpc = self.controller(); camera.command('pan_left')
        rpc.motion.return_value = False
        for _ in range(100):
            camera.stop(); self.clock.advance(5)
        self.assertEqual(rpc.motion.call_count, 6)  # one START + five STOPs
        self.assertEqual(camera.motor_state, 'FAULT_LATCHED')
        self.assertFalse(camera.command('zoom_in')[0])

    def test09_reconnect_success_is_not_motor_safe(self):
        camera, _ = self.controller(self.client([FakeSocket([TimeoutError()]), FakeSocket([False])]))
        camera.command('pan_left'); self.clock.advance(1)
        self.assertFalse(camera.stop()[0])
        self.assertTrue(camera._motor_fault)
        self.assertEqual(camera._rpc.generation, 2)
        self.assertFalse(camera.command('pan_right')[0])

    def test10_operator_stop_after_budget_exhaustion_recovers(self):
        camera, rpc = self.controller(); camera.command('pan_left'); rpc.motion.return_value = False
        for _ in range(5): camera.stop(); self.clock.advance(5)
        rpc.motion.return_value = True
        self.assertTrue(camera.emergency_stop()[0])
        self.assertFalse(camera._motor_fault)
        self.assertEqual(camera.motor_state, 'READY')
        self.assertTrue(camera.command('zoom_in')[0])

    def test11_shutdown_fault_is_bounded_and_no_camera_threads_leak(self):
        camera, rpc = self.controller(); camera.command('pan_left'); rpc.motion.return_value = False
        cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        owner = ControlOrchestrator(); owner.acquire('pan_tilt')
        pan = AutoPanTiltController(camera, owner, cfg)
        with pan._condition: pan._fault=True; pan._condition.notify_all()
        manual = ManualController(camera, Mock(), Mock(), owner, Mock())
        zoom, focus = Mock(), Mock()
        zoom.close.return_value = focus.close.return_value = True
        begin = time.perf_counter()
        result = close_controls(pan,zoom,focus,manual,camera,owner,timeout=1.)
        elapsed = time.perf_counter()-begin
        self.assertLess(elapsed, 1.)
        self.assertTrue(result['unconfirmed_motor'])
        self.assertTrue(owner.closing); self.assertEqual(owner.owner, 'pan_tilt')
        self.assertFalse(owner.acquire('manual'))
        self.assertFalse(any(t.is_alive() for t in (pan._thread,manual._thread,camera._command_worker.thread)))
        print('SHUTDOWN_MEASUREMENT', json.dumps({'seconds':elapsed,'unconfirmed_motor':True,'threads_alive':0}))

    def test12_concurrent_zoom_cannot_acquire_pan_fault(self):
        owner=ControlOrchestrator(); owner.acquire('pan_tilt')
        camera,rpc=self.controller(); camera.command('pan_left'); rpc.motion.return_value=False; camera.stop()
        results=[]
        threads=[threading.Thread(target=lambda:results.append(owner.acquire('zoom'))) for _ in range(8)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(1)
        self.assertEqual(results,[False]*8)
        self.assertFalse(camera.command('zoom_in')[0])
        self.assertEqual(rpc.motion.call_count,2)

    def test13_manual_emergency_stop_bypasses_owner_and_is_sticky(self):
        owner=ControlOrchestrator(); owner.acquire('pan_tilt')
        camera,rpc=self.controller(); camera.command('pan_left')
        stopped=threading.Event()
        rpc.motion.side_effect=lambda *a: stopped.set() or True
        manual=ManualController(camera,Mock(),Mock(),owner,Mock())
        try:
            with manual._condition:
                manual.request()
                manual.request('zoom_in')  # cannot replace pending STOP
            self.assertTrue(stopped.wait(.5))
            self.assertEqual(rpc.motion.call_args.args[1],False)
            self.assertEqual(owner.owner,'pan_tilt')  # foreign owner is not blindly released
        finally:
            manual.close(); manual._thread.join(1)
        self.assertFalse(manual._thread.is_alive())

    def test14_rpc_cleanup_failure_recorded_and_lane_exits(self):
        camera,rpc=self.controller(); camera.read_status()
        rpc.close.side_effect=RuntimeError('private-password')
        with patch.object(latency.profiler,'event',wraps=latency.profiler.event) as log:
            self.assertFalse(camera.close(timeout=1))
        self.assertTrue(camera.cleanup_failed)
        self.assertFalse(camera._command_worker.thread.is_alive())
        events=[c for c in log.call_args_list if c.args[0]=='HANET_CLEANUP_ERROR']
        self.assertTrue(events)
        self.assertNotIn('private-password',str(events))

    def test15_repeated_reconnect_failures_bounded_and_shutdown_supported(self):
        first=FakeSocket([TimeoutError()])
        client=self.client([first]); camera,_=self.controller(client)
        camera.command('pan_left')
        self.socket_factory.side_effect=OSError('offline')
        for _ in range(100):
            self.clock.advance(10); camera.stop()
        self.assertLessEqual(self.socket_factory.call_count,5)
        self.assertEqual(camera.motor_state,'FAULT_LATCHED')
        self.assertTrue(camera.close(timeout=1))
        self.assertTrue(camera.shutdown_unconfirmed)

    def test16_concurrent_stop_serializes_and_uses_one_connection(self):
        wire=FakeSocket(); client=self.client([wire]); camera,_=self.controller(client)
        camera.command('pan_left')
        results=[]
        threads=[threading.Thread(target=lambda:results.append(camera.stop()[0])) for _ in range(12)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(1)
        self.assertEqual(results,[True]*12)
        self.assertEqual(self.socket_factory.call_count,1)
        self.assertEqual(len(wire.commands),2)
        self.assertFalse(camera._active_codes)

    def test_restart_retains_uncertainty_until_explicit_stop(self):
        camera,rpc=self.controller(); camera.command('pan_left'); rpc.motion.return_value=False
        camera.close()
        restarted,new_rpc=self.controller()
        self.assertEqual(restarted.motor_state,'FAULT_LATCHED')
        self.assertFalse(restarted.command('zoom_in')[0]); new_rpc.motion.assert_not_called()
        self.assertTrue(restarted.emergency_stop()[0])
        self.assertTrue(restarted.command('zoom_in')[0])

    def test_handshake_failure_closes_unpublished_socket(self):
        wire=FakeSocket(); wire.sendall=Mock(side_effect=TimeoutError())
        client=self.client([wire])
        with self.assertRaises(TimeoutError):client.motion('kCmdLeft',True,1)
        self.assertTrue(wire.closed);self.assertIsNone(client._ws)

    def test_first_failure_preserved_after_rotation(self):
        profiler=latency.LatencyProfiler(directory=self.tmp,enabled=True,max_bytes=500,backups=1)
        profiler.event('HANET_TRANSPORT_ERROR',exception_message='first',connection_generation=1)
        for i in range(500):profiler.event('PAN_TILT_STOP_UNCONFIRMED',exception_message=str(i))
        self.assertTrue(profiler.close())
        data=json.loads(next((Path(self.tmp)/'camera').glob('first_failures_*.json')).read_text())
        self.assertEqual(data[0]['exception_message'],'first')
        self.assertEqual(len(data),2)

    def test_backoff_fake_clock_schedule_and_exhaustion(self):
        budget=StopRetryBudget(clock=self.clock)
        for delay in (.5,1,2,4,4):
            self.assertTrue(budget.ready());budget.failed()
            self.assertFalse(budget.ready());self.clock.advance(delay)
        self.assertTrue(budget.exhausted);self.assertFalse(budget.ready())

    def test_stop_queue_priority_when_normal_queue_full(self):
        worker=CameraCommandWorker(); entered=threading.Event();release=threading.Event();order=[]
        from concurrent.futures import Future
        from contextvars import copy_context
        def blocking():entered.set();release.wait(1)
        caller=threading.Thread(target=lambda:worker.call(blocking));caller.start()
        self.assertTrue(entered.wait(1))
        futures=[]
        for i in range(16):
            future=Future();futures.append(future)
            worker.queue.put_nowait((future,copy_context(),None,lambda:order.append('normal'),(),{}))
        def stop():order.append('STOP')
        admitted = threading.Event()
        enqueue = worker.priority.put_nowait
        def admit(item):
            enqueue(item); admitted.set()
        with patch.object(worker.priority, 'put_nowait', admit):
            urgent=threading.Thread(target=lambda:worker.call(stop));urgent.start()
            self.assertTrue(admitted.wait(1))
        release.set();caller.join(1);urgent.join(1);worker.close(1)
        self.assertEqual(order[0],'STOP');self.assertEqual(len(order),17)

    def test_pan_worker_exhaustion_waits_without_rpc_or_log_spin(self):
        cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        ptz = Mock(supports_pan_tilt=True)
        ptz.stop.return_value = (False, 'unconfirmed')
        owner = ControlOrchestrator(); owner.acquire('pan_tilt')
        with patch('control.auto_pan_tilt.threading.Thread'):
            pan = AutoPanTiltController(ptz, owner, cfg)
        pan._fault = True
        pan._stop_budget = StopRetryBudget(clock=self.clock)
        waits = []
        exhausted_counts = []
        class Condition:
            def __enter__(inner): return inner
            def __exit__(inner, *args): pass
            def wait(inner, timeout):
                waits.append(timeout)
                self.clock.advance(timeout)
                if pan._stop_budget.exhausted:
                    exhausted_counts.append(ptz.stop.call_count)
                    if len(exhausted_counts) == 10:
                        pan._closed = True
        pan._condition = Condition()
        with patch('control.auto_pan_tilt.time.monotonic', self.clock):
            pan._run()
        self.assertEqual(exhausted_counts, [5]*10)
        self.assertEqual(ptz.stop.call_count, 6)  # five automatic + final close attempt
        self.assertTrue(all(delay > 0 for delay in waits))
        self.assertTrue(pan.shutdown_unconfirmed)
        self.assertEqual(owner.owner, 'pan_tilt')

    def test_client_lock_serializes_direct_multithreaded_stop(self):
        wire = FakeSocket(); client = self.client([wire])
        results = []
        threads = [threading.Thread(target=lambda: results.append(client.motion('kCmdLeft', False, 1))) for _ in range(12)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(1)
        self.assertEqual(results, [True]*12)
        self.assertEqual(self.socket_factory.call_count, 1)
        self.assertEqual(client.generation, 1)


if __name__ == '__main__':
    unittest.main()
