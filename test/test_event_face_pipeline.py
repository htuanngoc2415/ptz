"""Offline optimized-path checks with real crops/flow and mocked neural inference."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import time
import threading
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch
import cv2
import numpy as np
import config
from runtime.face_worker import FaceWorker, FaceJob
from runtime.face_candidates import BestShots
from runtime.face_tracking import FaceTracker
from gui.face_panel import draw_face_panel


class EventFaceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.TARGET_CONTINUOUS_REID = False
        self.cfg.FACE_TRACKING_ENABLED = True
        self.cfg.FACE_CANDIDATE_SPACING = .01
        self.cfg.FACE_RECOGNITION_INTERVAL = 0
        self.cfg.FACE_DETECTION_HZ = 10000
        self.cfg.FACE_QUALITY_MIN_SCORE = .1
        self.image = np.random.default_rng(42).integers(0,256,(240,240,3),dtype=np.uint8)
        self.face = dict(bbox=(50,20,150,120),landmarks=np.array([[75,50],[125,50],[100,70],[80,95],[120,95]],np.float32))
        self.person = dict(bbox=(10,0,200,230),track_id=7)
        self.matcher = Mock()
        self.matcher.match.return_value = dict(identity='A',matched=True,score=.9)
        self.recognizer = Mock()
        self.worker = FaceWorker(Mock(),self.recognizer,self.matcher,self.cfg)
        self.addCleanup(self.worker.close)
        self.worker.session='s'; self.worker.track_id=7
        self.detector = patch('ai.face.face_detector.detect_face_inside_person',return_value=[self.face]).start()
        self.addCleanup(patch.stopall)
        patch('ai.face.face_alignment.align_face',return_value=self.image).start()

    def job(self, frame=1, image=None):
        return FaceJob(frame,time.monotonic(),self.image if image is None else image,self.person,None,
                       (self.person,),'a','s',7,self.worker.generation,time.perf_counter_ns(),self.worker.lens_unstable)

    def wait_identity(self):
        with self.worker.condition:
            self.assertTrue(self.worker.condition.wait_for(lambda: not self.worker.recognition_busy,2))
        return self.worker.take_identity()

    def test_confirmed_identity_stops_arcface_but_geometry_continues(self):
        self.worker._process(self.job())
        self.assertFalse(self.wait_identity()['recognition']['confirmed'])
        time.sleep(.02)
        self.worker._process(self.job(2,255-self.image))
        self.assertTrue(self.wait_identity()['recognition']['confirmed'])
        for i in range(3,10):
            result = self.worker._process(self.job(i))
            self.assertIsNotNone(result['face'])
        self.assertEqual(self.recognizer.extract_embedding.call_count,2)
        self.assertEqual(self.worker.panel('s',7)['state'],'IDENTIFIED')
        self.assertIsNone(self.worker.panel('other',7))

    def test_slow_recognition_does_not_block_detection_and_new_session_rejects(self):
        entered,release = threading.Event(),threading.Event()
        self.addCleanup(release.set)
        def slow(image):
            entered.set(); release.wait(2); return np.ones(512)
        self.recognizer.extract_embedding.side_effect=slow
        self.worker._process(self.job())
        self.assertTrue(entered.wait(1))
        result = self.worker._process(self.job(2))
        self.assertIsNotNone(result['face'])
        self.assertTrue(self.worker.recognition_busy)
        self.worker.invalidate()
        release.set()
        self.wait_identity()
        self.assertIsNone(self.worker.cache)
        self.assertIsNone(self.worker.take_identity())

    def test_crossing_person_excluded(self):
        job=replace(self.job(),persons=(self.person,dict(self.person,track_id=8)))
        self.assertIsNone(self.worker._process(job)['face'])
        self.recognizer.extract_embedding.assert_not_called()

    def test_missing_face_and_low_quality_timeout(self):
        self.detector.return_value=[]
        self.assertIsNone(self.worker._process(self.job())['face'])
        shots=BestShots(self.cfg)
        self.cfg.FACE_QUALITY_MIN_SCORE=2.
        job=self.job()
        shots.add(job,self.face,{})
        self.assertIsNone(shots.pop(job.received))
        self.assertIsNotNone(shots.pop(job.received+self.cfg.FACE_QUALITY_FALLBACK_TIMEOUT))
        self.assertIsNone(shots.pop(job.received+self.cfg.FACE_QUALITY_FALLBACK_TIMEOUT))

    def test_flow_motion_and_detection_expiry(self):
        tracker=FaceTracker(self.cfg)
        image=cv2.GaussianBlur(self.image,(5,5),1.)
        tracker.correct(image,self.face,1.)
        moved=cv2.warpAffine(image,np.float32([[1,0,3],[0,1,2]]),(240,240))
        face=tracker.update(moved,1.1)
        self.assertIsNotNone(face)
        np.testing.assert_allclose(face['bbox'],(53,22,153,122),atol=1)
        self.assertIsNone(tracker.update(moved,2.))

    def test_short_face_loss_preserves_identity_snapshot(self):
        self.worker._process(self.job()); self.wait_identity()
        self.worker.cache=dict(identity='A',final_identity='A',confirmed=True)
        self.worker.state='IDENTIFIED'
        before=self.worker.panel('s',7)['crop']
        self.detector.return_value=[]
        self.worker.lens_unstable=True
        self.worker._process(self.job(2))
        panel=self.worker.panel('s',7)
        self.assertEqual(panel['recognition']['identity'],'A')
        self.assertIs(panel['crop'],before)

    def test_unknown_retry_is_controlled(self):
        self.matcher.match.return_value=dict(identity='unknown',matched=False,score=.1)
        self.worker._process(self.job()); self.wait_identity()
        time.sleep(.02)
        self.worker._process(self.job(2,255-self.image))
        self.assertEqual(self.recognizer.extract_embedding.call_count,1)
        self.assertEqual(self.worker.state,'UNKNOWN')

    def test_best_snapshot_prefers_stable_candidate_and_rejects_duplicates(self):
        shots=BestShots(self.cfg)
        job=self.job()
        shots.add(job,self.face,{},stable=False)
        first=shots.best
        later=replace(job,frame_id=2,received=job.received+.4)
        shots.add(later,self.face,{},stable=True)
        self.assertGreater(shots.best.score,first.score)
        chosen=shots.pop(later.received)
        self.assertEqual(chosen.job.frame_id,2)
        self.assertIsNone(shots.pop(later.received))
        again=replace(job,frame_id=3,received=job.received+.8)
        shots.add(again,self.face,{})
        self.assertIsNone(shots.pop(again.received))

    def test_tracker_rejects_resolution_change_and_blank_occlusion(self):
        tracker=FaceTracker(self.cfg)
        tracker.correct(self.image,self.face,1.)
        self.assertIsNone(tracker.update(np.zeros((120,120,3),np.uint8),1.1))
        tracker.correct(self.image,self.face,1.)
        self.assertIsNone(tracker.update(np.zeros_like(self.image),1.1))

    def test_snapshot_overlay_preserves_video_dimensions(self):
        frame=np.zeros((240,320,3),np.uint8)
        draw_face_panel(frame,dict(crop=self.image,state='IDENTIFIED',recognition=dict(final_identity='A',score=.9),session='s',track=7),True)
        self.assertEqual(frame.shape,(240,320,3))
        self.assertTrue(frame.any())

    def test_stale_tracker_geometry_cannot_reach_motor_consumer(self):
        job=self.job()
        face=dict(self.face,tracked=True,tracking_confidence=.99,
                  detection_timestamp=time.monotonic()-self.cfg.FACE_TRACK_MAX_AGE-1)
        self.worker.result=dict(job=job,face=face)
        self.assertIsNone(self.worker.take())

    def test_cancelled_pending_recognition_does_not_leave_worker_busy(self):
        with self.worker.condition:
            self.worker._process(self.job())
            self.assertTrue(self.worker.recognition_busy)
            self.assertIsNotNone(self.worker.recognition_pending)
            self.worker.invalidate()
            self.assertFalse(self.worker.recognition_busy)
        self.worker.session='s'; self.worker.track_id=7
        self.worker._process(self.job(2))
        self.assertIsNotNone(self.wait_identity())

    def test_stable_frame_after_lens_motion_requires_detection(self):
        self.worker.cache=dict(identity='A')
        self.worker.lens_unstable=True
        self.worker._process(self.job())
        self.worker.lens_unstable=False
        self.worker._process(self.job(2))
        self.assertEqual(self.detector.call_count,2)

    def test_reacquisition_requests_reverify(self):
        self.worker._process(self.job()); self.wait_identity()
        self.worker.cache=dict(identity='A')
        self.worker.last_associated=time.monotonic()-10
        self.worker.last_attempt=time.monotonic()+10
        self.worker._process(self.job(2))
        self.assertIsNone(self.worker.cache)
        self.assertEqual(self.worker.state,'REVERIFY')

if __name__ == '__main__':
    unittest.main()
