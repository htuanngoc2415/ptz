"""Fresh appearance selection without spatial track association."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import numpy as np
import config
from ai.reid_target import ReIDTargetSelector
from runtime.face_worker import FaceWorker, FaceJob
import time


class ContinuousReIDTests(unittest.TestCase):
    def setUp(self):
        self.cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.TARGET_CONTINUOUS_REID=True
        self.cfg.FACE_TRACKING_ENABLED=False
        self.selector=ReIDTargetSelector(self.cfg,Mock())
        self.selector.embedding=np.ones(512)
        self.selector.error=None
        self.selector._scores=Mock(return_value=[.95])
        self.frame=np.zeros((400,600,3),np.uint8)

    def person(self,x=10,ident=-1):
        return dict(bbox=(x,10,x+70,300),track_id=ident)

    def acquire(self):
        for i in range(3):
            result=self.selector.select_person(self.frame,[self.person(i*100)],i*.1)
        self.assertIsNotNone(result)
        return result

    def test_every_frame_scores_even_large_displacements_and_changed_detection_ids(self):
        first=self.acquire()
        session=self.selector.lock_session_id
        with patch.object(self.selector,'associate',side_effect=AssertionError('no spatial association')):
            for i in range(3,8):
                selected=self.selector.select_person(self.frame,[self.person(350 if i%2 else 0,-i)],i*.1)
                self.assertEqual(selected['track_id'],first['track_id'])
                self.assertEqual(self.selector.lock_session_id,session)
        self.assertEqual(self.selector._scores.call_count,8)

    def test_low_score_stops_selection_and_reappearance_requires_confirmation(self):
        self.acquire()
        self.selector._scores.return_value=[.2]
        self.assertIsNone(self.selector.select_person(self.frame,[self.person()],1.))
        self.assertTrue(self.selector.crossing_hold)
        self.selector._scores.return_value=[.95]
        for now in (1.1,1.2):
            self.assertIsNone(self.selector.select_person(self.frame,[self.person(350)],now))
        self.assertIsNotNone(self.selector.select_person(self.frame,[self.person(350)],1.3))

    def test_ambiguous_appearance_never_uses_previous_geometry_to_pick(self):
        self.acquire()
        self.selector._scores.return_value=[.9,.89]
        self.assertIsNone(self.selector.select_person(self.frame,[self.person(),self.person(350)],1.))
        self.assertTrue(self.selector.crossing_hold)

    def test_missing_person_eventually_expires_session(self):
        self.acquire()
        for i in range(1,20):
            now=1.+i*.2
            person=self.selector.select_person(self.frame,[],now)
            lost=self.selector.finish_frame(person,None)
            if lost:
                break
        self.assertTrue(lost)
        self.assertFalse(self.selector.locked)
        self.assertIsNone(self.selector.lock_session_id)

    def test_no_face_flow_in_continuous_mode(self):
        worker=FaceWorker(Mock(),Mock(),Mock(),self.cfg)
        self.addCleanup(worker.close)
        worker.session='s';worker.track_id=1
        person=self.person(10,1)
        stamp=time.monotonic()
        job=FaceJob(1,stamp,self.frame,person,None,(person,),'a','s',1,0,time.perf_counter_ns())
        with patch.object(worker.tracker,'update',side_effect=AssertionError('face tracking disabled')), \
             patch.object(worker.tracker,'correct',side_effect=AssertionError('face features must not be initialized')), \
             patch('ai.face.face_detector.detect_face_inside_person',return_value=[]) as detect:
            worker._process(job)
            worker._process(FaceJob(2,time.monotonic(),self.frame,person,None,(person,),'a','s',1,0,time.perf_counter_ns()))
        self.assertEqual(detect.call_count,2)

if __name__=='__main__':
    unittest.main()
