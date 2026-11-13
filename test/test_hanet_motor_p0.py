"""P0 regressions: synthetic ACKs only; fixtures deny live network access."""
import json
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from camera.hanet_ack import validate_response, ACK_SUCCESS, ACK_UNKNOWN, ACK_REJECTED, PROTOCOL_ERROR
from control.auto_zoom import AutoZoomController
from control.orchestrator import ControlOrchestrator
from telemetry import latency
import test_hanet_persisted_recovery as fixtures


class EnvelopeTests(unittest.TestCase):
    def test_success_requires_complete_unambiguous_envelope(self):
        for response in ({'id':'r','result':True},
                         {'jsonrpc':'2.0','id':'r','result':True,'error':None},
                         {'jsonrpc':'2.0','id':'r','result':True,'error':{}},
                         {'jsonrpc':'2.0','id':'r','result':True,'error':{'code':1,'message':'denied'}}):
            with self.subTest(response=response):
                self.assertEqual(validate_response(response,'r').classification,PROTOCOL_ERROR)

    def test_explicit_result_contract_and_reason_matrix(self):
        for result,classification,reason in (
            (True,ACK_SUCCESS,'VALID_METHOD_SUCCESS'),
            (False,ACK_REJECTED,'EXPLICIT_REJECTION'),
            ({},ACK_UNKNOWN,'EMPTY_RESULT_UNVERIFIED'),
            (None,ACK_UNKNOWN,'UNVERIFIED_RESULT_SCHEMA')):
            with self.subTest(result=result):
                decision=validate_response({'jsonrpc':'2.0','id':'r','result':result},'r')
                self.assertEqual((decision.classification,decision.reason),(classification,reason))
        self.assertEqual(validate_response({'jsonrpc':'2.0','id':'r'},'r').reason,'MISSING_RESULT')
        self.assertEqual(validate_response({'jsonrpc':'2.0','id':'old','result':True},'r').reason,'MISMATCHED_REQUEST_ID')

    def test_malformed_error_is_protocol_error(self):
        for error in (None,False,{}, {'code':True,'message':'denied'}, {'code':1}, {'code':1,'message':False}):
            with self.subTest(error=error):
                self.assertEqual(validate_response({'jsonrpc':'2.0','id':'r','error':error},'r').classification,PROTOCOL_ERROR)

    def test_valid_error_and_other_method_never_accept(self):
        self.assertEqual(validate_response({'jsonrpc':'2.0','id':'r','error':{'code':1,'message':'denied'}},'r').classification,ACK_REJECTED)
        self.assertEqual(validate_response({'jsonrpc':'2.0','id':'r','result':True},'r',method='Other.Method').classification,ACK_UNKNOWN)


class CleanupOwnershipTests(unittest.TestCase):
    setUp=fixtures.PersistedRecoveryTests.setUp
    camera=fixtures.PersistedRecoveryTests.camera

    def test_guard_unlink_failure_keeps_zoom_owner_until_recovery_and_restart(self):
        camera,rpc=self.camera(); owner=ControlOrchestrator();owner.acquire('zoom')
        zoom=AutoZoomController(camera,orchestrator=owner,wait_after_zoom=0)
        self.addCleanup(zoom.close)
        self.assertTrue(camera.command('zoom_out')[0])
        zoom.busy=zoom.zoom_motor_active=True
        zoom._started=0;zoom.zoom_direction=-1
        real_unlink=Path.unlink
        def unlink(path,*a,**kw):
            if path==camera._stop_guard_path: raise OSError('synthetic guard cleanup failure')
            return real_unlink(path,*a,**kw)
        with patch.object(Path,'unlink',unlink):
            zoom.stop()
        self.assertEqual(owner.owner,'zoom')
        self.assertTrue(camera._motor_fault)
        self.assertNotEqual(camera.motor_state,'READY')
        self.assertFalse(owner.acquire('pan_tilt'))
        self.assertFalse(camera.command('pan_left')[0])
        self.assertTrue(camera.emergency_stop()[0]);zoom.stop()
        self.assertIsNone(owner.owner)
        self.assertFalse(camera._stop_guard_path.exists())
        self.assertEqual(json.loads(self.path.read_text())['active_codes'],[])
        camera.close()
        restarted,new_rpc=self.camera()
        self.assertEqual(restarted.motor_state,'READY');new_rpc.motion.assert_not_called()

    def test_cleanup_directory_fsync_failure_does_not_report_ready(self):
        import os,stat
        camera,rpc=self.camera();camera.command('pan_left')
        real=os.fsync
        def sync(fd):
            if (stat.S_ISDIR(os.fstat(fd).st_mode) and not camera._stop_guard_path.exists()
                    and not json.loads(self.path.read_text())['active_codes']):
                raise OSError('synthetic cleanup directory fsync failure')
            return real(fd)
        with patch('camera.hanet_controller.os.fsync',side_effect=sync):
            self.assertFalse(camera.stop()[0])
        self.assertTrue(camera._motor_fault)
        self.assertFalse(camera.command('pan_right')[0])
        self.assertTrue(camera.emergency_stop()[0])
        self.assertEqual(camera.motor_state,'READY')


class PanAdmissionTests(unittest.TestCase):
    setUp=fixtures.PanTransportRecoveryTests.setUp
    submit=fixtures.PanTransportRecoveryTests.submit

    def test_backend_fault_without_owner_never_emits_dispatch(self):
        self.camera._motor_fault=True;self.camera.motor_state='FAULT_LATCHED'
        decided=threading.Event()
        def event(name,**fields):
            self.events.append((name,fields))
            if name=='PAN_TILT_DECISION' and fields.get('decision_reason')=='MOTOR_FAULT':decided.set()
            if name=='PAN_TILT_DISPATCH':decided.set()
        with patch.object(latency.profiler,'event',side_effect=event):
            self.submit();self.assertTrue(decided.wait(1))
        self.assertNotIn('PAN_TILT_DISPATCH',[n for n,f in self.events])
        self.assertFalse(self.wire.commands)
        self.assertIsNone(self.owner.owner)

    def test_manual_override_does_not_dispatch(self):
        self.owner.manual=True
        self.assertFalse(self.submit())
        self.assertEqual(self.events[-1][1]['decision_reason'],'MANUAL_OVERRIDE')
        self.assertFalse(self.wire.commands)
