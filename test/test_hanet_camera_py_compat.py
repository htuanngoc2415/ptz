"""Operator-reported camera.py compatibility, offline only; no physical ACK claims."""
import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack
from uuid import uuid4

from camera.hanet_ack import validate_response, ACK_SUCCESS, ACK_UNKNOWN, ACK_REJECTED, PROTOCOL_ERROR
from camera.hanet_rpc import HanetPTZClient
from camera.hanet_controller import HanetController
from telemetry import latency
import test_hanet_fault_injection as wire_fixtures
import test_hanet_persisted_recovery as pan_fixtures


class CompatibilityTests(unittest.TestCase):
    def decision(self, message, **kwargs):
        args=dict(operation='STOP', code='kCmdZoomWide', ack_profile='camera_py')
        args.update(kwargs)
        return validate_response(message, 'r', **args)

    def test_empty_result_is_scoped_and_strict_mode_preserved(self):
        message={'jsonrpc':'2.0','id':'r','result':{}}
        for code in HanetController.CODES.values():
            for operation in ('START','STOP'):
                self.assertEqual(self.decision(message,code=code,operation=operation).classification,ACK_SUCCESS)
        self.assertEqual(self.decision(message).reason,'CAMERA_PY_EMPTY_RESULT')
        self.assertEqual(self.decision(message,ack_profile='strict').classification,ACK_UNKNOWN)
        self.assertEqual(self.decision(message,method='Auth.Heartbeat').classification,ACK_UNKNOWN)
        self.assertNotEqual(self.decision(message,operation=None).classification,ACK_SUCCESS)
        self.assertNotEqual(self.decision(message,code='foreign').classification,ACK_SUCCESS)

    def test_profile_does_not_promote_other_unknown_or_bad_envelopes(self):
        for result in (None,0,1,'OK',[],{'success':True}):
            self.assertEqual(self.decision({'jsonrpc':'2.0','id':'r','result':result}).classification,ACK_UNKNOWN)
        for message in ({'jsonrpc':'2.0','id':'old','result':{}},
                        {'id':'r','result':{}},
                        {'jsonrpc':'2.0','id':'r','result':{},'error':None}):
            self.assertEqual(self.decision(message).classification,PROTOCOL_ERROR)
        self.assertEqual(self.decision({'jsonrpc':'2.0','id':'r'}).classification,ACK_UNKNOWN)
        self.assertEqual(self.decision({'jsonrpc':'2.0','id':'r','result':False}).classification,ACK_REJECTED)
        self.assertEqual(self.decision({'jsonrpc':'2.0','id':'r','error':{'code':1,'message':'denied'}}).classification,ACK_REJECTED)

    def test_payload_matches_working_reference_all_directions_start_stop(self):
        # Extract only the method: never import/run reference video, GUI or login.
        reference=Path(__file__).resolve().parents[2]/'camera.py'
        tree=ast.parse(reference.read_text())
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HanetPTZClient')
        motion=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='motion')
        namespace={'json':json,'uuid4':uuid4}
        exec(compile(ast.Module(body=[motion],type_ignores=[]),str(reference),'exec'),namespace)
        ref=Mock();ref.session_id='synthetic-session'
        current=HanetPTZClient.__new__(HanetPTZClient);current.session_id=ref.session_id
        for code in HanetController.CODES.values():
            for start in (True,False):
                namespace['motion'](ref,code,start,4)
                actual=json.loads(ref._send_frame.call_args.args[0])
                self.assertEqual(actual,current._motion_payload(actual['id'],code,start,4))


class BackendCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        directory=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path=directory/'motor.json'
        self.wire=wire_fixtures.FakeSocket([{} for _ in range(30)])
        self.stack.enter_context(patch('socket.create_connection',return_value=self.wire))
        self.stack.enter_context(patch('requests.sessions.Session.request',side_effect=AssertionError('NO LIVE HTTP')))
        def login(client):client.session_id='synthetic-session'
        self.stack.enter_context(patch.object(HanetPTZClient,'_login',login))
        self.events=[]
        self.stack.enter_context(patch.object(latency.profiler,'event',side_effect=lambda n,**f:self.events.append((n,f))))

    def camera(self, **kwargs):
        camera=HanetController(ip='offline.invalid',username='u',password='p',timeout=.1,
                               autoconnect=False,safety_state_path=self.path,**kwargs)
        self.addCleanup(camera.close)
        return camera

    def test_default_backend_recovers_persisted_fault_then_manual_all_directions(self):
        self.path.write_text(json.dumps({'version':1,'active_codes':['kCmdZoomWide']}))
        camera=self.camera()
        self.assertFalse(camera.command('pan_left')[0])
        self.assertFalse(self.wire.commands)  # no bypass of persisted uncertainty
        self.assertTrue(camera.emergency_stop()[0])
        self.assertEqual(camera.motor_state,'READY')
        self.assertEqual(json.loads(self.path.read_text())['active_codes'],[])
        self.assertFalse(camera._stop_guard_path.exists())
        for direction,code in camera.CODES.items():
            self.assertTrue(camera.command(direction,4)[0])
            self.assertTrue(camera.stop()[0])
            self.assertEqual([r['params']['data']['cmd'] for r in self.wire.commands[-2:]],[code,code])
        self.assertEqual(len(self.wire.commands),13)
        ack=[f for n,f in self.events if n in ('PAN_TILT_START_ACK','PAN_TILT_STOP_ACK')]
        self.assertEqual(len(ack),13)
        self.assertTrue(all(f['evidence']=='CAMERA_PY_EMPTY_RESULT' and f['physical_motor_stopped'] is None for f in ack))
        camera.close();restarted=self.camera()
        self.assertEqual(restarted.motor_state,'READY')

    def test_strict_backend_option_still_blocks_empty_stop(self):
        self.path.write_text(json.dumps({'version':1,'active_codes':['kCmdZoomWide']}))
        camera=self.camera(ack_profile='strict')
        self.assertFalse(camera.emergency_stop()[0])
        self.assertTrue(camera._motor_fault)
        self.assertEqual(json.loads(self.path.read_text())['active_codes'],['kCmdZoomWide'])

    def test_timeout_is_not_ack_even_in_compatibility_mode(self):
        self.wire.outcomes.clear();self.wire.outcomes.append(TimeoutError('synthetic timeout'))
        camera=self.camera()
        self.assertFalse(camera.command('pan_left')[0])
        self.assertTrue(camera._motor_fault)
        self.assertFalse(any(n=='PAN_TILT_START_ACK' for n,f in self.events))

    def test_old_generation_empty_response_is_not_accepted(self):
        from camera.hanet_ack import HanetProtocolError
        camera=self.camera();client=camera._connect()
        client.generation=1
        def response():
            client.generation=2
            return 1,b'{"jsonrpc":"2.0","id":"r","result":{}}'
        with patch.object(client,'_recv_frame',side_effect=response):
            with self.assertRaises(HanetProtocolError):
                client._receive_response('r',operation='STOP',code='kCmdLeft',generation=1)


class AutoPanCompatibilityTests(unittest.TestCase):
    setUp=pan_fixtures.PanTransportRecoveryTests.setUp
    submit=pan_fixtures.PanTransportRecoveryTests.submit

    def test_auto_pan_real_wire_path_with_empty_start_and_stop(self):
        self.client.ack_profile='camera_py'
        self.wire.outcomes.extend([{},{}])
        self.assertTrue(self.submit());self.assertTrue(self.ended.wait(2))
        commands=[r['params']['data'] for r in self.wire.commands]
        self.assertEqual([(c['cmd'],c['operate']) for c in commands],
                         [('kCmdRight','kOperateStart'),('kCmdRight','kOperateStop')])
        self.assertIn('PAN_TILT_DISPATCH',[n for n,f in self.events])
        self.assertEqual(self.camera.motor_state,'READY');self.assertIsNone(self.owner.owner)


if __name__=='__main__':unittest.main()
