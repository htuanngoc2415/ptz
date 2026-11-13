"""Offline camera-motion tests. No live camera, motor, detector or ReID inference."""
import json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import cv2,numpy as np
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'tools')]
from runtime.camera_motion import CameraMotionEstimator,MotionEstimate,MotionObserver
from runtime.camera_motion_shadow import ShadowBranch,translation_for,warp_xyah,safe_handling_proposal
from runtime.motion_recording import MotionRecorder
from runtime.byte_tracking import ByteTrackPersonTracker
from camera_motion_replay import synthetic,replay,cfg_copy,load_bundle
from camera_motion_audit import exposure_seconds,exposure_at
from camera_motion_metadata import enrich


class MotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cv2.setNumThreads(1)
    def setUp(self):
        rng=np.random.default_rng(5)
        self.frame=cv2.GaussianBlur(rng.integers(0,256,(360,640,3),dtype=np.uint8),(3,3),0)
        self.est=CameraMotionEstimator()
    def pair(self,H):return cv2.warpAffine(self.frame,np.array(H,dtype=float),(640,360))
    def test_static_camera_moving_foreground_masked(self):
        a=self.frame.copy();b=a.copy();a[100:220,200:270]=220;b[100:220,240:310]=220
        r=self.est.estimate(a,b,0,.1,[[200,100,270,220]],[[240,100,310,220]])
        self.assertTrue(r.valid,r);self.assertLess(abs(r.dx)+abs(r.dy),1)
    def test_pan_left_right_and_tilt(self):
        for dx,dy in ((-20,0),(20,0),(0,-18),(0,18)):
            with self.subTest(dx=dx,dy=dy):
                r=self.est.estimate(self.frame,self.pair([[1,0,dx],[0,1,dy]]),0,.1)
                self.assertTrue(r.valid,r);self.assertAlmostEqual(r.dx,dx,delta=.8);self.assertAlmostEqual(r.dy,dy,delta=.8)
    def test_fast_pan_reliable_or_fallback_never_nan(self):
        r=self.est.estimate(self.frame,self.pair([[1,0,260],[0,1,0]]),0,.1)
        self.assertFalse(r.valid,r);self.assertIsNone(r.transform)
    def test_time_gap_and_reversed_time(self):
        for t in (0,-.1,1,float('nan')):
            self.assertFalse(self.est.estimate(self.frame,self.frame,0,t).valid)
    def test_zoom_and_pan_zoom_detected_not_warped_as_translation(self):
        for dx in (0,8):
            H=cv2.getRotationMatrix2D((320,180),0,1.05);H[0,2]+=dx
            r=self.est.estimate(self.frame,self.pair(H),0,.1)
            self.assertTrue(r.valid,r);self.assertAlmostEqual(r.scale,1.05,delta=.01)
            self.assertEqual(r.classification,'SCALE_CHANGE');self.assertIsNone(translation_for(r))
    def test_blur_and_textureless_rejected(self):
        for a,b in ((np.zeros_like(self.frame),np.zeros_like(self.frame)),(self.frame,cv2.GaussianBlur(self.frame,(61,61),20))):
            self.assertFalse(self.est.estimate(a,b,0,.1).valid)
    def test_nan_transform_rejected(self):
        with patch('cv2.estimateAffinePartial2D',return_value=(np.full((2,3),np.nan),None)):
            self.assertFalse(self.est.estimate(self.frame,self.frame,0,.1).valid)
    def test_occluded_osd_mask_and_all_masked_fallback(self):
        r=self.est.estimate(self.frame,self.frame,0,.1,osd_boxes=[[0,0,640,360]])
        self.assertFalse(r.valid)
    def test_invalid_frame_shape(self):
        self.assertFalse(self.est.estimate(self.frame,self.frame[:200],0,.1).valid)
    def test_session_reset_prevents_cross_session_estimate(self):
        observer=MotionObserver(cfg_copy())
        observer.observe(self.frame,0,1,[],'a')
        r=observer.observe(self.frame,.1,2,[],'b')
        self.assertEqual(r['observed_global_motion']['reason'],'SESSION_CHANGED')
        r=observer.observe(self.frame,.2,2,[],'b')
        self.assertEqual(r['observed_global_motion']['reason'],'FRAME_ORDER')
        self.assertIsNone(r['capture_timestamp']);self.assertIsNone(r['expected_camera_moving'])
    def test_xyah_translation_preserves_aspect_velocities_and_psd_covariance(self):
        t=SimpleNamespace(mean=np.array([10,20,.5,80,2,3,.01,4.]),covariance=np.eye(8))
        old=t.mean.copy();warp_xyah(t,[15,-8],.2)
        np.testing.assert_allclose(t.mean[:2],[25,12]);np.testing.assert_array_equal(t.mean[2:],old[2:])
        self.assertGreaterEqual(np.linalg.eigvalsh(t.covariance).min(),0)
        self.assertEqual(t.covariance[2,2],1)
    def test_invalid_confidence_and_geometry_fallback(self):
        for r in (MotionEstimate(),MotionEstimate(valid=True,confidence=.8,transform=[[float('nan'),0,0],[0,1,0]]),
                  MotionEstimate(valid=True,confidence=float('nan'),transform=[[1,0,3],[0,1,0]]),
                  MotionEstimate(valid=True,confidence=.9,transform='malformed'),
                  MotionEstimate(valid=True,confidence=.1,transform=[[1,0,3],[0,1,0]])):
            self.assertIsNone(translation_for(r))
    def test_flags_off_and_invalid_transform_are_baseline_identical(self):
        frames,rows,truth=synthetic(0,2,10)
        a=ShadowBranch(cfg_copy(),False);outa=[a.update(r['detections'],r['receive_timestamp'],r['frame_id']) for r in rows]
        b=ShadowBranch(cfg_copy(),True);outb=[b.update(r['detections'],r['receive_timestamp'],r['frame_id'],MotionEstimate()) for r in rows]
        self.assertEqual(outa,outb)
        with patch('runtime.camera_motion.MotionObserver',side_effect=AssertionError('OFF path instantiated observer')):
            tracker=ByteTrackPersonTracker(cfg_copy());tracker.update([],0,frame=self.frame,frame_id=1)
            self.assertIsNone(tracker.motion_observer)
    def test_diagnostics_exception_does_not_change_tracking(self):
        cfg=cfg_copy();cfg.CAMERA_MOTION_DIAGNOSTICS_ENABLED=True
        tracker=ByteTrackPersonTracker(cfg)
        with patch.object(tracker.motion_observer,'observe',side_effect=RuntimeError('synthetic')):
            self.assertEqual(tracker.update([],0,frame=self.frame,frame_id=1),[])
    def test_diagnostics_on_preserves_tracking_outputs(self):
        frames,rows,_=synthetic(0,2,8)
        baseline=ByteTrackPersonTracker(cfg_copy())
        expected=[baseline.update(r['detections'],r['receive_timestamp'],frame=f,frame_id=r['frame_id']) for f,r in zip(frames,rows)]
        cfg=cfg_copy();cfg.CAMERA_MOTION_DIAGNOSTICS_ENABLED=True
        diagnostic=ByteTrackPersonTracker(cfg)
        actual=[diagnostic.update(r['detections'],r['receive_timestamp'],frame=f,frame_id=r['frame_id']) for f,r in zip(frames,rows)]
        self.assertEqual(actual,expected)
    def test_warp_exactly_once_at_association_boundary(self):
        b=ShadowBranch(cfg_copy(),True)
        b.update([dict(bbox=[100,40,140,140],confidence=.95)],0,1)
        estimate=MotionEstimate(valid=True,confidence=.9,transform=[[1,0,10],[0,1,0]],reprojection_error=0)
        with patch('runtime.camera_motion_shadow.warp_xyah',wraps=warp_xyah) as warp:
            b.update([dict(bbox=[110,40,150,140],confidence=.95)],.1,2,estimate)
            self.assertEqual(warp.call_count,1)
        self.assertEqual(b.adapter.tracker.applied,1)
    def test_mask_outside_image_does_not_hide_background(self):
        normal=self.est.mask((360,640),[])
        clipped=self.est.mask((360,640),[[-100,-100,-20,-20],[700,400,800,500]])
        np.testing.assert_array_equal(normal,clipped)
    def test_command_metadata_requires_verified_clock_and_keeps_capture_unknown(self):
        events=[dict(monotonic_ns=1_000_000_000,event='PAN_TILT_START_SENT',rpc_code='Left'),
                dict(monotonic_ns=2_000_000_000,event='PAN_TILT_STOP_SENT',rpc_code='Left')]
        row=dict(receive_timestamp=1.5,capture_timestamp=None,observation_monotonic=1.6,
                 observation_wall_time='1970-01-01T00:16:41.600000+00:00')
        result=enrich(row,events,[(1,2,'Left')],1000)
        self.assertTrue(result['ptz_command_active']);self.assertIsNone(result['capture_timestamp'])
        self.assertIsNone(enrich(row,events,[(1,2,'Left')],2000)['ptz_command_active'])
    def test_single_target_pan_fragmentation_experiment(self):
        result=replay(*synthetic(25,0,10))
        self.assertGreater(result['gmc_success_rate'],.5)
        self.assertLessEqual(result['branches']['gmc']['metrics']['id_switches'],result['branches']['baseline']['metrics']['id_switches'])
    def test_person_walking_with_pan_same_observations(self):
        result=replay(*synthetic(25,2,8));self.assertEqual(len(result['branches']['baseline']['outputs']),len(result['branches']['gmc']['outputs']))
    def test_out_of_fov_and_return_do_not_extrapolate_output(self):
        b=ShadowBranch(cfg_copy(),True);r=MotionEstimate(valid=True,confidence=.9,transform=[[1,0,5],[0,1,0]],reprojection_error=.1)
        b.update([{'bbox':[10,20,40,100],'confidence':.9}],0,1,r)
        for i in range(1,10):self.assertEqual(b.update([],i*.1,i+1,r),[])
        out=b.update([{'bbox':[60,20,90,100],'confidence':.9}],1.,11,r)
        self.assertLessEqual(len(out),1)
    def test_duplicate_alias_handling_unchanged(self):
        person={'bbox':[30,30,60,120],'confidence':.95}
        b=ShadowBranch(cfg_copy(),True);out=b.update([person,person],0,1)
        self.assertEqual(len(out),1)
    def test_crossing_and_overlap_do_not_claim_identity(self):
        b=ShadowBranch(cfg_copy(),True)
        for i in range(12):
            persons=[dict(bbox=[100+i*4,40,130+i*4,140],confidence=.95),dict(bbox=[190-i*4,40,220-i*4,140],confidence=.95)]
            out=b.update(persons,i*.1,i+1)
            self.assertTrue(all('identity' not in row for row in out))
    def test_motion_after_reacquisition_and_session_gap_is_bounded(self):
        b=ShadowBranch(cfg_copy(),True)
        p=[dict(bbox=[100,40,140,140],confidence=.95)]
        b.update(p,0,1)
        self.assertLessEqual(len(b.update(p,10,2,MotionEstimate())),1)
    def test_independent_safe_handling_experiments(self):
        base=dict(age_s=2,session_valid=True,image_quality_good=False,gmc_valid=False,prediction_age_s=10)
        result=safe_handling_proposal(**base,reject_stale=True,gate_gallery=True,bound_prediction=True)
        self.assertTrue(all(v is False for v in result.values()))
        base.update(age_s=.1,session_valid=False,prediction_age_s=.1)
        self.assertFalse(safe_handling_proposal(**base,reject_stale=True)['allow_new_ptz_observation'])
        base['gmc_valid']=True
        self.assertFalse(safe_handling_proposal(**base)['allow_gmc'])
    def test_exposure_union_not_double_counted(self):
        intervals=[(1,2,'pan'),(1.5,2.5,'zoom')]
        result=exposure_seconds(0,4,intervals)
        self.assertAlmostEqual(sum(result.values()),4)
        self.assertAlmostEqual(result['command_active'],1.5)
        self.assertEqual(exposure_at(2.6,intervals),'post_stop_0_300ms')
    def test_recorder_bounded_and_retains_confidence_and_unknown_capture_time(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=cfg_copy();cfg.CAMERA_MOTION_RECORD_DIRECTORY=d;cfg.CAMERA_MOTION_RECORD_MAX_FRAMES=2
            rec=MotionRecorder(cfg)
            for i in range(8):rec.submit(self.frame,[dict(bbox=[1,2,3,4],confidence=.73)],dict(frame_id=i,receive_timestamp=i*.1,capture_timestamp=None,session='x'))
            self.assertTrue(rec.close());self.assertLessEqual(rec.written,2);self.assertGreater(rec.dropped,0)
            rows=[json.loads(l) for l in (rec.root/'frames.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['detections'][0]['confidence'],.73);self.assertIsNone(rows[0]['capture_timestamp'])
            frames,loaded=load_bundle(rec.root);self.assertEqual(len(frames),len(rows))
    def test_recorder_error_closes_without_deadlock(self):
        with tempfile.TemporaryDirectory() as d:
            cfg=cfg_copy();cfg.CAMERA_MOTION_RECORD_DIRECTORY=d
            with patch('cv2.imencode',side_effect=ValueError('synthetic')):
                rec=MotionRecorder(cfg);rec.submit(self.frame,[],{});self.assertTrue(rec.close());self.assertEqual(rec.errors,1)


if __name__=='__main__':
    with patch('socket.create_connection',side_effect=AssertionError('NO NETWORK')),patch('requests.sessions.Session.request',side_effect=AssertionError('NO HTTP')):
        unittest.main()
