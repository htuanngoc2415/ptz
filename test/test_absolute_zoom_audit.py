"""Hardware-free absolute zoom regression and evidence contracts."""
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera.zoom_prediction import ZoomCalibration, ZoomEstimator, PositionABSMapping
from camera.zoom_capability import ZoomCapability
from camera.dahua_rpc import ZoomRPC, RPCError
from camera.ptz_controller import PTZController
from control.auto_zoom import AutoZoomController
from control.orchestrator import ControlOrchestrator
from telemetry import latency, zoom_trace
from tools.zoom_benchmark import MockFirmware, mock_run
from tools.zoom_calibration import build


def calibration():
    return dict(schema_version=1, coordinate='zoom_value_x', camera_id='test',
        reference_target_id='fixed',fixed_distance=True,timestamp='test-fixture',
        samples=[dict(zoom_value_x=p,magnification=m,stable=True,reference_target_id='fixed')
                 for p,m in [(1,1),(10,3),(20,10),(37,20)]])


class PredictionTests(unittest.TestCase):
    def test_nonlinear_inverse_not_linear_position_scaling(self):
        c = ZoomCalibration(calibration(), 'test')
        self.assertAlmostEqual(c.estimate_target_zoom(10,.1,.2), 10+30/7)
        self.assertAlmostEqual(c.zoom_to_magnification(c.magnification_to_zoom(6)),6)
    def test_saturation_no_extrapolation(self):
        c = ZoomCalibration(calibration())
        self.assertEqual(c.magnification_to_zoom(1000),37)
        with self.assertRaises(ValueError): c.zoom_to_magnification(0)
        self.assertTrue(ZoomEstimator(c).estimate_target_zoom(20,.01,.5).saturated)
    def test_uncalibrated_major_not_capped_at_one_point_five(self):
        p = ZoomEstimator().estimate_target_zoom(3,.1,.5)
        self.assertEqual(p.target_zoom_value,15)
        self.assertFalse(p.calibration_valid)
        self.assertEqual(p.prediction_confidence_or_status,'UNCALIBRATED_OPTICAL_SCALE')
    def test_correction_is_bounded(self):
        p = ZoomEstimator().estimate_target_zoom(10,.1,.5,correction=True)
        self.assertAlmostEqual(p.target_zoom_value,11.5)
    def test_below_verified_absolute_range_is_explicit(self):
        self.assertIsNone(ZoomEstimator().estimate_target_zoom(1,.1,.5).requested_zoom_position)
    def test_invalid_calibration(self):
        for change in ('order','reference','unstable','binding','nan','coordinate'):
            data = calibration()
            if change=='order': data['samples'][1]['magnification']=.5
            if change=='reference': data['samples'][0]['reference_target_id']='other'
            if change=='unstable': data['samples'][0]['stable']=False
            if change=='binding': data['camera_id']='other'
            if change=='nan': data['samples'][0]['zoom_value_x']=float('nan')
            if change=='coordinate': data['coordinate']='encoder'
            with self.subTest(change=change),self.assertRaises(ValueError): ZoomCalibration(data,'test')
    def test_heldout_points_must_be_independent(self):
        c=ZoomCalibration(calibration())
        row=dict(zoom_value_x=15,magnification=6.5,stable=True,reference_target_id='fixed')
        self.assertEqual(c.validate_holdout([row])['max_relative_error'],0)
        row['zoom_value_x']=10
        with self.assertRaises(ValueError): c.validate_holdout([row])
    def test_reject_invalid_ratio_and_position(self):
        for v in (float('nan'),float('inf'),-1,0,2):
            with self.assertRaises(ValueError): ZoomEstimator().estimate_target_zoom(10,v,.5)
        for v in (0,.14,1.01,float('nan')):
            with self.assertRaises(ValueError): PositionABSMapping().command_to_zoom(v)
    def test_runtime_lut_requires_independent_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'lut.json'
            data=calibration()
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError): ZoomCalibration.load(path,'test')
            data['holdout_samples']=[dict(zoom_value_x=15,magnification=6.5,stable=True,reference_target_id='fixed')]
            path.write_text(json.dumps(data))
            self.assertEqual(ZoomCalibration.load(path,'test').camera_id,'test')

    def test_collection_build_rejects_mixed_and_reports_holdout(self):
        rows=[dict(zoom_value_x=z,measured_person_ratio=r,reference_target_id='r',camera_id='c',
                   stable=True,fixed_distance=True,timestamp='fixture',image_sha256='fixture')
              for z,r in [(1,.1),(5,.2),(10,.3),(20,.5),(37,.8)]]
        data=build(rows,{2},'person')
        self.assertEqual(data['holdout_validation']['count'],1)
        rows[2]['reference_target_id']='different'
        with self.assertRaises(ValueError): build(rows,{2},'person')


class RpcSafetyTests(unittest.TestCase):
    def test_readonly_probe_cannot_mutate_even_on_close(self):
        f=MockFirmware()
        rpc=ZoomRPC('mock',80,'mock','mock',read_only=True,session_factory=lambda:f)
        rpc.status_raw()
        for m in ('ptz.start','ptz.stop','configManager.setTemporaryConfig'):
            with self.assertRaises(RuntimeError): rpc._call(m,{})
        rpc.close()
        self.assertFalse(any(p['method'] in ('ptz.start','ptz.stop','configManager.setTemporaryConfig') for p in f.calls))
    def test_capability_unsupported_and_unknown(self):
        self.assertFalse(ZoomCapability.from_response({'result':True,'params':{'caps':{'Move':['Relatively']}}}).supported)
        self.assertIsNone(ZoomCapability.from_response({'result':False,'error':{'code':1}}).supported)
        self.assertIsNone(ZoomCapability.from_response({'result':True,'params':{}}).supported)
    def test_rejection_never_falls_back_to_pulses(self):
        f=MockFirmware()
        rpc=ZoomRPC('mock',80,'mock','mock',session_factory=lambda:f)
        c=PTZController('mock','mock','mock',autoconnect=False,rpc_factory=lambda **kw:rpc)
        self.addCleanup(c.close)
        c.read_status()
        rpc.goto=Mock(side_effect=RPCError('ptz.start',{'result':False,'error':{'code':4}}))
        self.assertFalse(c.move_to_zoom_position(.5)[0])
        self.assertFalse(any(p['method']=='ptz.start' for p in f.calls))
    def test_supervised_benchmark_rejects_endpoints_before_connection(self):
        from tools.zoom_benchmark import motor_run
        with patch('tools.zoom_benchmark.PTZController') as factory:
            for position in (0,.15,.151,.999,1.,float('nan')):
                with self.assertRaises(ValueError): motor_run(Path('unused'),position,Path('unused'))
            factory.assert_not_called()
    def test_calibration_binding_checks_connected_camera(self):
        rpc=Mock()
        rpc._call.return_value={'result':True,'params':{'serialNumber':'other-camera'}}
        c=PTZController('mock','mock','mock',autoconnect=False,rpc_factory=lambda **kw:rpc)
        self.addCleanup(c._command_worker.close)
        c.expected_camera_id='expected'
        with self.assertRaises(RuntimeError): c.read_status()
        rpc.status_raw.assert_not_called()

    def test_invalid_position_does_not_send_rpc(self):
        c=PTZController('mock','mock','mock',autoconnect=False,rpc_factory=Mock(side_effect=AssertionError('network')))
        self.addCleanup(c._command_worker.close)
        for v in (0,.14,1.01,float('nan')):
            self.assertFalse(c.move_to_zoom_position(v)[0])


class FeedbackTests(unittest.TestCase):
    def make(self, **kwargs):
        ptz=Mock()
        ptz.has_zoom_telemetry=True
        ptz.supports_absolute_zoom=True
        ptz.speed=100
        ptz.telemetry.side_effect=lambda:dict(zoom=10.,timestamp=time.monotonic(),zoom_motion_state='Moving')
        ptz.move_to_zoom_position.return_value=(True,'ACK')
        ptz.command.return_value=(True,'ACK')
        ptz.stop.return_value=(True,'STOP')
        z=AutoZoomController(ptz,wait_after_zoom=0,feedback_timeout=.8,**kwargs)
        z.set_enabled(True)
        self.addCleanup(z.close)
        return z,ptz
    def update(self,z,ratio=.2,**kwargs):
        return z.update((1000,1000),dict(bbox=(200,200,400,200+ratio*1000)),.4,.01,target_name='PERSON',**kwargs)
    def wait(self,predicate):
        deadline=time.monotonic()+1
        while not predicate() and time.monotonic()<deadline: time.sleep(.005)
        self.assertTrue(predicate())
    def test_no_reissue_during_moving_even_with_many_fresh_frames(self):
        z,p=self.make()
        self.update(z)
        self.wait(lambda:p.move_to_zoom_position.called)
        for _ in range(20): self.update(z)
        self.assertEqual(p.move_to_zoom_position.call_count,1)
        self.assertTrue(z.busy)
    def test_absolute_timeout_is_not_pulse_complete(self):
        z,p=self.make(absolute_timeout=.04)
        self.update(z)
        self.wait(lambda:not z.busy)
        self.assertEqual(z.last_stop_reason,'ABSOLUTE_TIMEOUT')
        self.assertEqual(z.phase,'ERROR')
        self.update(z)
        self.assertEqual(p.move_to_zoom_position.call_count,1)
    def test_target_loss_stops(self):
        z,p=self.make();self.update(z)
        self.wait(lambda:p.move_to_zoom_position.called)
        z.update((1000,1000),None,.4,.01,target_name='PERSON')
        self.assertFalse(z.busy)
        p.stop.assert_called_once()
    def test_unsupported_uses_explicit_pulse_path(self):
        z,p=self.make();p.supports_absolute_zoom=False
        self.update(z)
        self.wait(lambda:p.command.called)
        p.move_to_zoom_position.assert_not_called()
    def test_unbound_cached_telemetry_cannot_use_calibrated_plan(self):
        z,p=self.make(estimator=ZoomEstimator(ZoomCalibration(calibration())))
        self.assertEqual(self.update(z),'WAITING FOR VERIFIED CALIBRATION CAMERA')
        p.move_to_zoom_position.assert_not_called()
    def test_pulse_baseline_is_opt_in_even_when_absolute_supported(self):
        z,p=self.make(control_mode='pulse')
        self.update(z)
        self.wait(lambda:p.command.called)
        p.move_to_zoom_position.assert_not_called()
    def test_manual_override_blocks_auto_command(self):
        owner=ControlOrchestrator();owner.manual=True
        z,p=self.make(orchestrator=owner);self.update(z)
        self.assertFalse(z.busy)
        p.move_to_zoom_position.assert_not_called()

    def test_focus_owner_blocks_zoom(self):
        owner=ControlOrchestrator();self.assertTrue(owner.acquire('focus'))
        z,p=self.make(orchestrator=owner);self.update(z)
        self.assertFalse(z.busy)
        p.move_to_zoom_position.assert_not_called()
    def test_session_invalidation_stops_inflight(self):
        z,p=self.make();z._correlation={'lock_session_id':'old'}
        self.update(z);self.wait(lambda:p.move_to_zoom_position.called)
        z.session_valid=lambda s:False
        self.wait(lambda:not z.busy)
        self.assertEqual(z.last_stop_reason,'STALE_SESSION')
    def test_mailbox_heartbeat_requires_fresh_same_session(self):
        z,p=self.make();z._correlation={'lock_session_id':'s'}
        self.update(z);self.wait(lambda:p.move_to_zoom_position.called)
        self.assertFalse(z.heartbeat(time.monotonic(),'other'))
        self.assertFalse(z.heartbeat(time.monotonic()-10,'s'))
        self.assertTrue(z.heartbeat(time.monotonic(),'s'))
        self.assertEqual(p.move_to_zoom_position.call_count,1)
    def test_settling_gates_correction_and_correction_budget(self):
        z,p=self.make(max_corrections=1)
        self.update(z);self.wait(lambda:p.move_to_zoom_position.called)
        p.telemetry.side_effect=lambda:dict(zoom=20.,timestamp=time.monotonic(),zoom_motion_state='Idle')
        self.wait(lambda:not z.busy)
        self.assertEqual(z.phase,'ZOOM_SETTLING')
        self.update(z,ratio=.3)
        self.assertEqual(p.move_to_zoom_position.call_count,1)
        for i in range(3): z.observe_stability(.3,i,time.monotonic(),target_kind='PERSON')
        self.update(z,ratio=.3)
        self.wait(lambda:p.move_to_zoom_position.call_count==2)
        self.assertAlmostEqual(z._zoom_target,23)
        p.telemetry.side_effect=lambda:dict(zoom=23.,timestamp=time.monotonic(),zoom_motion_state='Idle')
        self.wait(lambda:not z.busy)
        for i in range(3,6): z.observe_stability(.3,i,time.monotonic(),target_kind='PERSON')
        self.assertEqual(self.update(z,ratio=.3),'CORRECTION_LIMIT')
        self.assertEqual(p.move_to_zoom_position.call_count,2)


class EvidenceTests(unittest.TestCase):
    def test_full_rpc_path_one_absolute_no_pulses_and_null_unknowns(self):
        with tempfile.TemporaryDirectory() as directory:
            result=mock_run(directory)
            self.assertEqual(result['absolute_commands'],1)
            self.assertEqual(result['pulse_commands'],0)
            rows=[json.loads(line) for line in Path(result['trace']).read_text().splitlines()]
            commands=[r for r in rows if r['event']=='ZOOM_COMMAND_SENT' and r['rpc_method']=='ptz.start']
            self.assertEqual(len(commands),1)
            self.assertEqual(commands[0]['actual_command_method'],'ptz.start:PositionABS')
            self.assertIsNone(commands[0]['zoom_error'])
            self.assertTrue(all(r['evidence_source']=='mock' for r in rows))
            summaries=list(csv.DictReader(Path(result['summary']).open()))
            self.assertEqual(summaries[0]['number_of_absolute_commands'],'1')
            self.assertEqual(summaries[0]['zoom_position_error'],'NULL')
            self.assertEqual(summaries[0]['target_lock_to_identity_confirmed_ms'],'NULL')
    def test_zoom_logging_independent_of_profiling_toggle(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=zoom_trace.configure(directory=directory,enabled=True)
            with patch.object(latency.profiler,'enabled',False):
                latency.profiler.event('ZOOM_PLAN',calibration_valid=False)
            recorder.close()
            self.assertIn('ZOOM_PLAN',recorder.trace_path.read_text())
