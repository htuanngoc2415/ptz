"""Offline tests. No real motor and no network."""
import sys,threading,unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np,cv2
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from benchmark_ptz_motor import pulse,validate,measured_motion,MockMotor
from evaluate_local_tracking import LocalBodyProbe

class MotorBenchmarkTests(unittest.TestCase):
    def test_default_mock(self):
        r=pulse(MockMotor(),'pan_right',1,.02)
        self.assertTrue(r['stop_ack']);self.assertIsNone(r['angular_displacement_deg'])
        self.assertEqual([x['event'] for x in r['events']],['START_REQUEST','START_RETURN','STOP_REQUEST','STOP_RETURN'])
    def test_start_exception_still_stops(self):
        m=Mock();m.command.side_effect=RuntimeError('network');m.stop.return_value=(True,'ok')
        r=pulse(m,'pan_right',1,.02);m.stop.assert_called_once();self.assertFalse(r['start_ack'])
    def test_stop_failure_not_success(self):
        m=Mock();m.command.return_value=(True,'ok');m.stop.return_value=(False,'fault')
        self.assertFalse(pulse(m,'tilt_up',1,.02)['stop_ack'])
    def test_cancelled_never_starts(self):
        m=Mock();m.stop.return_value=(True,'ok');e=threading.Event();e.set()
        pulse(m,'pan_left',1,.02,e);m.command.assert_not_called();m.stop.assert_called_once()
    def test_logging_failure_cannot_skip_stop(self):
        m=Mock();m.command.return_value=(True,'ok');m.stop.return_value=(True,'ok')
        r=pulse(m,'pan_left',1,.02,emit=Mock(side_effect=OSError('disk full')))
        m.stop.assert_called_once();self.assertTrue(r['stop_ack']);self.assertTrue(r['logging_errors'])
    def test_limits(self):
        for speed,duration in [(0,.1),(9,.1),(1,float('nan')),(1,2.)]:
            with self.assertRaises(ValueError):validate('pan_right',speed,duration)
    def test_angle_samples(self):
        rows=[dict(t_s=t,angle_deg=10*t,video_pts_s=t) for t in np.arange(0.,1.1,.1)]
        r=measured_motion(rows,.2,.8);self.assertAlmostEqual(r['angular_displacement_deg'],6.)
        self.assertAlmostEqual(r['mean_speed_deg_s'],10.)
    def test_samples_must_bracket_pulse(self):
        with self.assertRaises(ValueError):measured_motion([dict(t_s=t,angle_deg=t) for t in range(5)],-1.,2.)
    def test_nonmonotonic_rejected(self):
        with self.assertRaises(ValueError):measured_motion([dict(t_s=0.,angle_deg=0.)]*5,0.,1.)

class LocalProbeTests(unittest.TestCase):
    def setUp(self):
        rng=np.random.default_rng(2);self.frame=np.zeros((240,320,3),np.uint8)
        self.frame[60:180,70:170]=rng.integers(0,256,(120,100,3),dtype=np.uint8)
        self.p=LocalBodyProbe();self.p.seed(self.frame,(70,60,170,180),0.,'s')
    def test_translation_is_visual_not_identity(self):
        moved=cv2.warpAffine(self.frame,np.float32([[1,0,3],[0,1,2]]),(320,240))
        r=self.p.update(moved,.03,'s');self.assertIsNotNone(r)
        self.assertLess(abs(r['bbox'][0]-73),2);self.assertFalse(r['ptz_authorized']);self.assertFalse(r['identity_confirmed'])
    def test_expired_roi_falls_back(self):self.assertIsNone(self.p.update(self.frame,.5,'s'))
    def test_camera_motion_falls_back(self):self.assertIsNone(self.p.update(self.frame,.03,'s',camera_moving=True))
    def test_changed_session_falls_back(self):self.assertIsNone(self.p.update(self.frame,.03,'new'))
    def test_out_of_order_falls_back(self):self.assertIsNone(self.p.update(self.frame,-.1,'s'))
    def test_unverified_seed_not_admitted(self):
        self.p.seed(self.frame,(70,60,170,180),0.,'s',verified=False)
        self.assertIsNone(self.p.update(self.frame,.03,'s'))
