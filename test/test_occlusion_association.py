import unittest
from dataclasses import replace
import numpy as np
from tools.occlusion_association import Observation,OcclusionAssociation
from tools.occlusion_visibility import regions


def embed(i):
    x=np.zeros(512);x[i]=1;return x

def obs(key='t',track=1,bbox=(50,20,150,220),embedding=None,**kw):
    return Observation(key,track,bbox,.95,embed(0) if embedding is None else embedding,
        parts={'0:0':embed(0),'1:0':embed(0)},support=.5,evidence_kind='isolated_parts',quality=True,**kw)

class OcclusionTests(unittest.TestCase):
    def setUp(self):
        self.p=OcclusionAssociation(embed(0),'s');self.t=obs()
        for i in range(3):self.p.observe('s',i,i*.1,i*.1,[self.t])
        self.assertEqual(self.p.state,'LOCKED')
    def step(self,people,i=3,**kw):return self.p.observe('s',i,i*.1,i*.1,people,**kw)
    def other(self,bbox=(85,20,185,220)):
        return replace(obs('o',2,bbox,embed(1)),parts={'0:0':embed(1),'1:0':embed(1)})
    def test_foreground_clear_with_large_bbox_overlap(self):
        r=self.step([self.t,self.other()]);self.assertTrue(r['control_eligible']);self.assertEqual(r['state'],'LOCKED')
        self.assertEqual(r['actual_occlusion'],'UNKNOWN')
    def test_stationary_target_other_moves_behind(self):
        for i in range(3,8):
            self.assertTrue(self.step([self.t,self.other((80+i,20,180+i,220))],i)['control_eligible'])
    def test_target_moves_in_front(self):
        for i in range(3,8):
            t=replace(self.t,bbox=(50+i,20,150+i,220))
            self.assertTrue(self.step([t,self.other()],i)['control_eligible'])
    def test_partial_support_not_geometry_only(self):
        r=self.step([self.t,self.other()]);self.assertEqual(r['tracking_status'],'PARTIAL_OBSERVED')
        r=self.step([replace(self.t,parts={},support=0.),self.other()],4)
        self.assertEqual(r['state'],'PARTIAL_UNVERIFIED');self.assertTrue(r['identity_known']);self.assertFalse(r['control_eligible'])
    def test_full_occlusion_and_missed_detection_not_identity_lost(self):
        r=self.step([]);self.assertEqual(r['state'],'OCCLUDED_UNOBSERVED');self.assertTrue(r['identity_known'])
        r=self.step([],20);self.assertEqual(r['state'],'LOST_LOCALIZATION');self.assertTrue(r['identity_known'])
    def test_crossing_and_separation_track_rebind(self):
        r=self.step([replace(self.t,track_id=2),replace(self.other(),track_id=1)])
        self.assertFalse(r['control_eligible'])
        for i in range(4,7):r=self.step([replace(self.t,track_id=2)],i)
        self.assertTrue(r['control_eligible'])
    def test_similar_clothes_preserve_multiple_hypotheses(self):
        r=self.step([self.t,replace(self.other(),embedding=embed(0))])
        self.assertEqual(r['state'],'AMBIGUOUS');self.assertEqual(len(r['hypotheses']),2)
    def test_camera_ptz_requires_transform(self):
        r=self.step([self.t,self.other()],camera_motion='MOVING')
        self.assertFalse(r['control_eligible'])
        r=self.step([self.t,self.other()],4,camera_motion='MOVING',camera_transform=[[1,0,0],[0,1,0]])
        self.assertTrue(r['control_eligible'])
    def test_overlap_does_not_update_anchor_gallery(self):
        anchor=self.p.anchor;r=self.step([self.t,self.other()])
        self.assertIs(self.p.anchor,anchor);self.assertFalse(r['gallery_updated'])
    def test_stale_session_and_order_no_mutation(self):
        before=self.p.anchor
        for s,f,t,now in [('x',3,.3,.3),('s',2,.2,.2),('s',3,.3,1.)]:
            r=self.p.observe(s,f,t,now,[self.t]);self.assertFalse(r['control_eligible']);self.assertIs(self.p.anchor,before)
    def test_regions_exclude_other_bbox(self):
        image=np.zeros((250,250,3),np.uint8)
        crops,support=regions(image,self.t.bbox,[(100,0,250,250)])
        self.assertEqual(len(crops),3);self.assertAlmostEqual(support,.5)
    def test_instance_mask_requires_real_shape(self):
        with self.assertRaises(ValueError):regions(np.zeros((250,250,3),np.uint8),self.t.bbox,[],np.ones((20,20)))

class BaselineRootCause(unittest.TestCase):
    def test_both_detections_present_but_osnet_skipped_and_ptz_loses_box(self):
        import test_hybrid_tracking as baseline
        from unittest.mock import Mock
        f=baseline.HybridTests();f.setUp();s=f.s;s._scores=Mock(side_effect=AssertionError('Should not run in geometry early exit'))
        a=baseline.person(1,100);b=baseline.person(2,125)
        result=s.select_person(f.frame,[a,b],.3)
        self.assertIsNone(result);self.assertTrue(s.crossing_hold);s._scores.assert_not_called()
        self.assertEqual(s.transition_reason,'PERSON_OVERLAP')

if __name__=='__main__':unittest.main()

class MetricTests(unittest.TestCase):
    def test_unlabelled_not_zero_and_provenance(self):
        from tools.occlusion_metrics import evaluate
        labels=dict(source_sha256='s',source='independent_manual',reviewer='reviewer',frames=[])
        self.assertEqual(evaluate([],labels,'s'),{})
        with self.assertRaises(ValueError):evaluate([],labels,'other')
    def test_missing_prediction_is_recall_failure(self):
        from tools.occlusion_metrics import evaluate
        records=[dict(frame_id=1,timestamp=1.,A=dict(bbox=None),B=dict(bbox=(0,0,10,20)))]
        labels=dict(source_sha256='s',source='independent_manual',reviewer='r',frames=[dict(frame_id=1,timestamp=1.,reviewed=True,visibility='VISIBLE',bbox=(0,0,10,20),camera_motion='static')])
        result=evaluate(records,labels,'s')
        self.assertEqual(result['static:A']['localization_recall_iou50'],0.)
        self.assertEqual(result['static:B']['localization_recall_iou50'],1.)
        self.assertIsNone(result['static:B']['identity_switch_count'])
