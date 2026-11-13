"""Acceptance tests for independent zoom and historical identity observations."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contextlib import ExitStack
import json
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np
import config
from runtime.face_worker import FaceWorker, FaceJob
from runtime.inference_worker import InferenceWorker
from runtime.target_session import TargetSession
from control.auto_zoom import AutoZoomController
from control.target_state_machine import TargetStateMachine
from camera.ptz_controller import PTZController
from camera.dahua_rpc import ZoomRPC
from tools.zoom_benchmark import MockFirmware
from telemetry import latency, zoom_trace
from ai.face.face_matcher import FaceMatcher


class IdentityValidityTests(unittest.TestCase):
    def setUp(self):
        self.cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.FACE_PIPELINE_MODE="baseline"  # exercise legacy admission/late-result contract
        self.cfg.FACE_RECOGNITION_INTERVAL=0
        self.recognizer=Mock()
        self.matcher=Mock()
        self.matcher.match.return_value=dict(identity='A',score=.9,matched=True)
        self.session=TargetSession();self.session.publish('A',7)
        self.w=FaceWorker(Mock(),self.recognizer,self.matcher,self.cfg,
                          session_valid=self.session.matches,track_valid=self.session.matches)
        self.w.session='A';self.w.track_id=7
        self.addCleanup(self.w.close)
        self.person=dict(bbox=(0,0,200,400),track_id=7)
        self.face=dict(bbox=(40,20,140,120),landmarks=np.zeros((5,2)))
        self.image=np.zeros((400,400,3),np.uint8)
        self.clock=time.monotonic()
        self.job=FaceJob(1,self.clock-.712,self.image,self.person,None,(self.person,), 'acq','A',7,0,time.perf_counter_ns())
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('ai.face.face_detector.detect_face_inside_person',return_value=[self.face]))
        self.stack.enter_context(patch('ai.sharpness.calculate_face_sharpness',return_value=200))
        self.stack.enter_context(patch('ai.face.face_alignment.align_face',return_value=self.image))
        self.stack.enter_context(patch('runtime.face_worker.time.monotonic',side_effect=lambda:self.clock))
        self.events=[]
        self.stack.enter_context(patch.object(latency.profiler,'event',side_effect=lambda ev,**kw:self.events.append((ev,kw)) or time.perf_counter_ns()))
    def delayed(self, image):
        self.clock+=.388
        return np.ones(512)
    def test_1100ms_identity_accepted_but_never_motor_bbox(self):
        self.recognizer.extract_embedding.side_effect=self.delayed
        result=self.w._process(self.job)
        self.assertAlmostEqual(self.clock-self.job.received,1.1)
        self.assertFalse(self.w.valid(self.job))
        self.w.result=result
        self.assertIsNone(self.w.take())
        identity=self.w.take_identity()
        self.assertEqual(identity['recognition']['identity'],'A')
        self.assertIsNone(self.w.take_identity())
        accepted=[f for e,f in self.events if e=='IDENTITY_OBSERVATION_ACCEPTED']
        self.assertEqual(len(accepted),1)
        self.assertAlmostEqual(accepted[0]['frame_age_ms'],1100,places=3)
        self.assertTrue(any(e=='FACE_SPATIAL_RESULT_REJECTED' and f['reason']=='STALE_FOR_MOTOR' for e,f in self.events))
    def test_session_change_while_arcface_runs_rejects_result(self):
        def change(image):
            self.session.publish('B',8)
            return self.delayed(image)
        self.recognizer.extract_embedding.side_effect=change
        self.assertIsNone(self.w._process(self.job))
        self.assertIsNone(self.w.take_identity())
        self.assertTrue(any(e=='IDENTITY_OBSERVATION_REJECTED' and f['reason']=='SESSION_MISMATCH' for e,f in self.events))
    def test_track_change_same_session_rejected(self):
        def change(image):
            self.session.publish('A',8)
            return self.delayed(image)
        self.recognizer.extract_embedding.side_effect=change
        self.assertIsNone(self.w._process(self.job))
        self.assertTrue(any(f.get('reason')=='TRACK_MISMATCH' for e,f in self.events))
    def test_duplicate_frame_cannot_increment_identity_confirmation(self):
        self.w._process(self.job)
        self.w._process(self.job)
        self.assertEqual(self.w.identity.count,1)
        self.assertTrue(any(f.get('reason')=='DUPLICATE_OBSERVATION' for e,f in self.events))
    def test_other_person_overlap_is_ambiguous(self):
        from dataclasses import replace
        other=dict(bbox=self.person['bbox'],track_id=8)
        job=replace(self.job,persons=(self.person,other))
        result=self.w._process(job)
        self.assertIsNone(result['face'])
        self.recognizer.extract_embedding.assert_not_called()
        self.assertTrue(any(f.get('reason')=='FACE_ASSOCIATION_AMBIGUOUS' for e,f in self.events))
    def test_face_at_feet_is_not_associated(self):
        self.face['bbox']=(40,280,140,380)
        result=self.w._process(self.job)
        self.assertIsNone(result['face'])
        self.recognizer.extract_embedding.assert_not_called()
    def test_face_database_independent_unknown_is_an_accepted_observation(self):
        registered=np.zeros(512,dtype=np.float32);registered[0]=1
        query=np.zeros(512,dtype=np.float32);query[1]=1
        self.w.matcher=FaceMatcher({'registered-person':registered},self.cfg.FACE_RECOGNITION_THRESHOLD)
        self.recognizer.extract_embedding.return_value=query
        self.w._process(self.job)
        rec=self.w.take_identity()['recognition']
        self.assertEqual(rec['identity'],'unknown')
        self.assertFalse(rec['confirmed'])
        self.assertTrue(any(e=='IDENTITY_OBSERVATION_ACCEPTED' and f['outcome']=='UNKNOWN' for e,f in self.events))
    def test_new_visual_result_does_not_overwrite_identity(self):
        self.w._process(self.job)
        self.w.result=dict(job=self.job,face=None,recognition=None)
        self.assertIsNotNone(self.w.take_identity()['recognition'])
    def test_stale_admission_never_runs_detector(self):
        self.clock+=1
        self.assertIsNone(self.w._process(self.job))
        self.recognizer.extract_embedding.assert_not_called()


class ZoomIndependenceTests(unittest.TestCase):
    def test_lock_without_face_dispatches_one_absolute_before_200ms(self):
        with tempfile.TemporaryDirectory() as directory:
            trace=zoom_trace.configure(directory=directory,enabled=True,evidence_source='mock')
            firmware=MockFirmware()
            camera=PTZController('mock','mock','mock',autoconnect=False,
                rpc_factory=lambda **kw:ZoomRPC(**kw,session_factory=lambda:firmware))
            zoom=AutoZoomController(camera,feedback_timeout=.8)
            try:
                camera.read_status();zoom.set_enabled(True)
                locked=time.perf_counter_ns()
                ctx=SimpleNamespace(auto_zoom=zoom,reid_target=SimpleNamespace(locked=True,crossing_hold=False,
                    lock_session_id='lock',track_id=7,locked_ns=locked),tracking_reset=threading.Event(),
                    get_motor_speed=lambda:100)
                worker=InferenceWorker(ctx)
                person=dict(bbox=(100,100,200,200),track_id=7)
                with latency.correlated(lock_session_id='lock',locked_ns=locked):
                    latency.profiler.event('TARGET_LOCKED')
                    self.assertTrue(worker.start_person_zoom_on_lock(person,(1000,1000),1,time.monotonic()))
                deadline=time.monotonic()+1
                while not any(p['method']=='ptz.start' for p in firmware.calls) and time.monotonic()<deadline:
                    time.sleep(.001)
                starts=[p for p in firmware.calls if p['method']=='ptz.start']
                self.assertEqual([p['params']['code'] for p in starts],['PositionABS'])
                self.assertLess((time.perf_counter_ns()-locked)/1e6,200)
            finally:
                zoom.close();camera.close();trace.close()
            rows=[json.loads(l) for l in trace.trace_path.read_text().splitlines()]
            request=next(r for r in rows if r['event']=='ZOOM_REQUEST_CREATED')
            self.assertLess((request['monotonic_ns']-locked)/1e6,100)
            self.assertFalse(any(r['event']=='FACE_DETECTED' for r in rows))
    def test_person_hold_search_does_not_require_face_or_zoom_forever(self):
        state=TargetStateMachine()
        person=dict(bbox=(200,100,400,600))
        for i in range(20):
            result=state.update((1000,1000),person,None,now=i*.1)
        self.assertEqual(result.target_type,'PERSON')
        self.assertEqual(result.person_ratio,.5)
        self.assertFalse(result.target_lost)
    def test_face_loss_hysteresis_preserves_original_person(self):
        state=TargetStateMachine(face_lost_timeout=1.)
        person=dict(bbox=(0,0,500,800),track_id=7)
        face=dict(bbox=(100,100,200,200))
        for i in range(3): state.update((1000,1000),person,face,now=i*.1)
        self.assertEqual(state.update((1000,1000),person,None,now=.6).target_type,'FACE')
        fallback=state.update((1000,1000),person,None,now=1.3)
        self.assertEqual(fallback.target_type,'FACE')
        self.assertIsNone(fallback.control_target)
        self.assertFalse(fallback.target_lost)
        self.assertEqual(state.update((1000,1000),person,face,now=1.4).target_type,'FACE')
    def test_identity_consumption_does_not_write_current_spatial_state(self):
        job=FaceJob(1,time.monotonic()-1.1,None,None,None,(),'acq','A',7,0,0)
        ctx=SimpleNamespace(auto_zoom=SimpleNamespace(session_valid=lambda s:s=='A'),
            reid_target=SimpleNamespace(lock_session_id='A',track_id=7,locked_ns=time.perf_counter_ns()-2_000_000_000),
            _identity_logged_session=None,_usable_logged_session=None,acquisition_trial=Mock(),
            face_recognition_result=None,face_display_recognition=None)
        InferenceWorker(ctx).consume_identity(dict(job=job,recognition=dict(identity='known',score=.9,matched=True,confirmed=True)))
        self.assertEqual(ctx.face_display_recognition['identity'],'known')
        self.assertFalse(hasattr(ctx,'face_target'))
        self.assertFalse(hasattr(ctx.reid_target,'face_box'))
