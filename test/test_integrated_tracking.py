import unittest
from unittest.mock import Mock
import numpy as np
from ai.approach_risk import ApproachRisk
from runtime.target_prediction import TargetPrediction,predict_box
import test_hybrid_tracking as hybrid
person=hybrid.person


class ApproachTests(unittest.TestCase):
    def test_approach_before_overlap_and_session_reset(self):
        risk=ApproachRisk();target=person(1,100)
        self.assertFalse(risk.assess(target,[target,person(2,410)],'s',1.))
        self.assertTrue(risk.assess(target,[target,person(2,330)],'s',1.1))
        self.assertFalse(risk.assess(target,[target,person(2,300)],'new',1.2))
    def test_stationary_and_stale_not_approach(self):
        risk=ApproachRisk();target=person()
        risk.assess(target,[target,person(2,330)],'s',1.)
        self.assertFalse(risk.assess(target,[target,person(2,330)],'s',1.1))
        self.assertFalse(risk.assess(target,[target,person(2,300)],'s',2.))


class IntegrationTests(unittest.TestCase):
    def test_existing_selector_requests_async_verification_early(self):
        fixture=hybrid.HybridTests();fixture.setUp();s=fixture.s
        s.cfg.REID_VERIFY_INTERVAL=10.;s._start_verification=Mock()
        s.select_person(fixture.frame,[person(1,100),person(2,410)],.3)
        s.select_person(fixture.frame,[person(1,100),person(2,330)],.5)
        s._start_verification.assert_called_once()
        self.assertFalse(s.crossing_hold)
        self.assertTrue(s.locked)


class PredictionTests(unittest.TestCase):
    def evidence(self,**kw):
        return dict(dict(timestamp=1.,binding=('s',1,1),velocity=(100.,0.),confidence=.9,camera_motion='LOW_IMAGE_MOTION'),**kw)
    def test_bounded_age_prediction_and_original_not_mutated(self):
        box=(700,200,800,400)
        predicted,fields=predict_box(box,(600,1000,3),self.evidence(),1.1,('s',1,1))
        self.assertAlmostEqual(predicted[0],710)
        self.assertEqual(box,(700,200,800,400))
        self.assertIsNotNone(fields['time_to_edge_s'])
    def test_stale_session_unknown_and_moving_fallback(self):
        b=(700,200,800,400)
        for e,t,binding in [(self.evidence(),2.,('s',1,1)),(self.evidence(),1.1,('s',2,1)),
            (self.evidence(camera_motion='TRANSLATION'),1.1,('s',1,1)),(None,1.1,('s',1,1))]:
            self.assertEqual(predict_box(b,(600,1000,3),e,t,binding),(b,None))
    def test_no_direction_reversal(self):
        box=(460,200,560,400)
        p,_=predict_box(box,(600,1000,3),self.evidence(velocity=(-900,0)),1.2,('s',1,1))
        self.assertEqual((p[0]+p[2])/2,500)
    def test_camera_compensation_and_binding_reset(self):
        tracker=TargetPrediction();image=np.zeros((300,400,3),np.uint8)
        tracker.estimator.estimate=Mock(return_value=Mock(valid=True,confidence=.9,
            classification='LOW_IMAGE_MOTION',transform=[[1,0,1],[0,1,0]]))
        self.assertIsNone(tracker.observe(image,(30,30,100,200),[],1.,('s',1,1),1.01))
        evidence=tracker.observe(image,(31,30,101,200),[],1.1,('s',1,1),1.11)
        self.assertAlmostEqual(evidence['velocity'][0],0.)
        self.assertIsNone(tracker.observe(image,(32,30,102,200),[],1.2,('x',1,1),1.21))
        tracker.reset();self.assertIsNone(tracker.previous)

if __name__=='__main__':unittest.main()

class ExistingPanIntegrationTests(unittest.TestCase):
    def test_predictive_metadata_uses_existing_motor_owner(self):
        import time
        import test_auto_pan_tilt as pan
        fixture=pan.PanTiltTests();fixture.setUp()
        try:
            fixture.owner.acquire('zoom')
            received=time.monotonic()-.1
            evidence=dict(timestamp=received,binding=('s',1,1),velocity=(200.,0.),
                          confidence=.9,camera_motion='LOW_IMAGE_MOTION')
            box=(520,250,620,350)
            result=fixture.control.submit(dict(bbox=box),(600,1000,3),received,'s',1,
                metadata=dict(motion_prediction=evidence,track_generation=1))
            self.assertTrue(result)
            self.assertEqual(fixture.control._latest[5]['target_bbox'],box)
            self.assertGreater(fixture.control._latest[5]['planned_bbox'][0],box[0])
            self.assertFalse(fixture.started.wait(.03))  # Zoom lease is still authoritative.
        finally:fixture.tearDown()
