import ast
import dataclasses
import pathlib
import unittest
import cv2
import numpy as np
from tools.phase3_local import LocalTracker,Options


class Phase3Tests(unittest.TestCase):
    def setUp(self):
        cv2.setNumThreads(1);cv2.setRNGSeed(0)
        rng=np.random.default_rng(12)
        self.frame=np.zeros((240,320),np.uint8)
        self.frame[60:180,100:200]=rng.integers(0,255,(120,100),dtype=np.uint8)
        self.box=[100,60,200,180]
        self.p=dict(kind='manual_experiment_anchor',reviewer='synthetic_test',source_sha256='fixture',session='s',timestamp=1.,frame_id=0)
        self.tr=LocalTracker(Options(enabled=True));self.tr.initialize(self.frame,self.box,1.,self.p)
    def moved(self,dx=3,dy=2,scale=1):
        M=cv2.getRotationMatrix2D((150,120),0,scale);M[:,2]+=[dx,dy]
        return cv2.warpAffine(self.frame,M,(320,240))
    def test_initialize(self):self.assertEqual(self.tr.status,'ANCHORED')
    def test_default_off(self):
        tr=LocalTracker();self.assertEqual(tr.initialize(self.frame,self.box,1,self.p)['tracking_status'],'DISABLED')
    def test_no_anchor(self):self.assertIsNone(LocalTracker(Options(enabled=True)).update(self.frame,1,0,'s')['bbox'])
    def test_untrusted(self):
        p=dict(self.p,kind='track_id');self.assertIsNone(self.tr.initialize(self.frame,self.box,1,p)['bbox'])
    def test_pan_tilt_translation(self):
        s=self.tr.update(self.moved(),1.03,1,'s');self.assertIsNotNone(s['bbox']);np.testing.assert_allclose(s['bbox'],[103,62,203,182],atol=.5)
    def test_small_zoom(self):self.assertIsNotNone(self.tr.update(self.moved(scale=1.02),1.03,1,'s')['bbox'])
    def test_extreme_zoom(self):self.assertIsNone(self.tr.update(self.moved(scale=1.4),1.03,1,'s')['bbox'])
    def test_stale_gap(self):self.assertEqual(self.tr.update(self.frame,1.3,1,'s')['tracking_status'],'FRAME_GAP')
    def test_out_of_order(self):self.assertEqual(self.tr.update(self.frame,.9,1,'s')['tracking_status'],'OUT_OF_ORDER')
    def test_duplicate_frame(self):self.assertEqual(self.tr.update(self.frame,1.03,0,'s')['tracking_status'],'OUT_OF_ORDER')
    def test_nonfinite(self):self.assertEqual(self.tr.update(self.frame,float('nan'),1,'s')['tracking_status'],'OUT_OF_ORDER')
    def test_session_reset(self):self.assertEqual(self.tr.update(self.frame,1.03,1,'other')['tracking_status'],'SESSION_CHANGED')
    def test_reset(self):self.tr.reset();self.assertIsNone(self.tr.box)
    def test_feature_loss_full_occlusion(self):self.assertIsNone(self.tr.update(np.zeros_like(self.frame),1.03,1,'s')['bbox'])
    def test_crossing_external_risk(self):self.assertEqual(self.tr.update(self.frame,1.03,1,'s',risk=True)['tracking_status'],'OCCLUSION_OR_CROSSING_RISK')
    def test_partial_occlusion(self):
        frame=self.frame.copy();frame[:,150:]=0
        self.assertIsNone(self.tr.update(frame,1.03,1,'s')['bbox'])
    def test_boundary(self):self.assertEqual(self.tr.initialize(self.frame,[0,60,200,180],1,self.p)['tracking_status'],'BOUNDARY')
    def test_jump(self):self.assertIsNone(self.tr.update(self.moved(70,0),1.03,1,'s')['bbox'])
    def test_resolution(self):self.assertEqual(self.tr.update(self.frame[:120],1.03,1,'s')['tracking_status'],'RESOLUTION_CHANGED')
    def test_expiry_not_extended(self):
        for i in range(1,29):s=self.tr.update(self.frame,1+i*.03,i,'s')
        self.assertEqual(s['tracking_status'],'ANCHOR_EXPIRED');self.assertEqual(self.tr.anchor_time,1.)
    def test_no_identity_or_authorization(self):
        s=self.tr.update(self.moved(),1.03,1,'s');self.assertEqual(s['identity_status'],'UNVERIFIED_LOCALIZATION_ONLY');self.assertFalse(s['control_authorized']);self.assertEqual(s['anchor_timestamp'],1.)
    def test_correction_exact_time(self):
        self.tr.update(self.moved(),1.03,1,'s')
        p=dict(self.p,timestamp=1.03,frame_id=1,kind='trusted_detection')
        self.assertEqual(self.tr.correct(self.moved(),dict(bbox=[103,62,203,182],provenance=p))['tracking_status'],'ANCHORED')
    def test_correction_unaligned(self):
        self.tr.update(self.moved(),1.03,1,'s');self.assertEqual(self.tr.correct(self.moved(),dict(bbox=self.box,provenance=self.p))['tracking_status'],'CORRECTION_NOT_ALIGNED')
    def test_correction_disagreement(self):
        p=dict(self.p,kind='trusted_detection');self.assertEqual(self.tr.correct(self.frame,dict(bbox=[10,10,80,80],provenance=p))['tracking_status'],'CORRECTION_DISAGREEMENT')
    def test_no_motor_gallery_imports(self):
        tree=ast.parse(pathlib.Path('tools/phase3_local.py').read_text())
        names=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        self.assertEqual(set(names),{'dataclasses'})
        self.assertFalse(any(x in vars(self.tr) for x in ('gallery','motor','identity_manager')))

if __name__=='__main__':unittest.main()
