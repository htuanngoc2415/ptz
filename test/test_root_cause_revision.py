"""Independent prerecorded-detection chain and pure policy; no detector weights or network."""
import unittest
import time
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import config
from runtime.byte_tracking import ByteTrackPersonTracker
from ai.hybrid_reid import HybridReIDTargetSelector
from control.pan_policy import plan_pan
from tools.pan_dynamics_benchmark import simulate
import test_zoom_ownership as ownership


def detection(x=100, width=70, identity='target'):
    return dict(bbox=(x,50,x+width,250),confidence=.9,identity=identity)

class DetectionReplayTests(unittest.TestCase):
    def setUp(self):
        self.cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.REID_SEARCH_INTERVAL=0
        self.cfg.REID_VERIFY_INTERVAL=100
        self.tracker=ByteTrackPersonTracker(self.cfg)
        self.selector=HybridReIDTargetSelector(self.cfg,Mock())
        self.selector.embedding=np.ones(512);self.selector.error=None
        self.selector._scores=Mock(side_effect=lambda frame,persons,*a,**kw:
            [.95 if p['identity']=='target' else .1 for p in persons])
        self.frame=np.zeros((500,700,3),np.uint8);self.t=0
    def feed(self,persons):
        self.t+=.1
        tracks=self.tracker.update(persons,self.t)
        chosen=self.selector.select_person(self.frame,tracks,self.t)
        self.selector.finish_frame(chosen,None)
        if chosen is not None:self.assertEqual(chosen['identity'],'target')
        return chosen,tracks
    def acquire(self):
        for _ in range(4):chosen,_=self.feed([detection()])
        self.assertIsNotNone(chosen)
    def test_a_proven_same_observation_alias(self):
        for _ in range(4):
            d=detection();chosen,tracks=self.feed([d,d]);self.assertEqual(len(tracks),1)
            self.assertNotIn('track_id',d)
        self.assertIsNotNone(chosen);self.assertFalse(self.selector.crossing_hold)
    def test_b_distinct_overlap_is_not_merged(self):
        for _ in range(4):chosen,tracks=self.feed([detection(),detection(101,identity='other')])
        self.assertEqual(len(tracks),2);self.assertIsNone(chosen)
        self.assertEqual(self.selector._scores.call_count,0)
    def test_c_fragmentation_reverified(self):
        self.acquire();uid=self.selector.target_uid;old=self.selector.track_id
        self.tracker.reset()
        for _ in range(5):chosen,_=self.feed([detection()])
        self.assertIsNotNone(chosen);self.assertNotEqual(old,self.selector.track_id)
        self.assertEqual(uid,self.selector.target_uid)
    def test_c_fragmentation_wrong_identity_rejected(self):
        self.acquire();self.tracker.reset()
        for _ in range(5):chosen,_=self.feed([detection(identity='other')])
        self.assertIsNone(chosen)
    def test_d_full_loss_reacquisition(self):
        self.acquire()
        for _ in range(100):self.feed([])
        self.assertFalse(self.selector.locked)
        for _ in range(6):chosen,_=self.feed([detection()])
        self.assertIsNotNone(chosen)
    def test_e_global_translation_requires_identity(self):
        self.acquire()
        for _ in range(5):chosen,_=self.feed([detection(350),detection(500,identity='other')])
        self.assertIsNotNone(chosen)
    def test_f_missing_never_returns_old_box(self):
        self.acquire()
        for _ in range(8):chosen,_=self.feed([]);self.assertIsNone(chosen)
    def test_out_of_order(self):
        self.acquire();box=self.selector.person_box
        self.assertEqual(self.tracker.update([detection(300)],self.t-.1),[])
        self.assertIsNone(self.selector.select_person(self.frame,[detection(300)],self.t-.1))
        self.assertEqual(box,self.selector.person_box)
    def test_turning_back_without_face(self):
        self.acquire()
        for _ in range(6):chosen,_=self.feed([detection()]);self.assertIsNotNone(chosen)
    def test_far_to_near(self):
        self.acquire()
        for width in (75,80,90,100):chosen,_=self.feed([detection(width=width)])
        self.assertIsNotNone(chosen)

class PanPolicyTests(unittest.TestCase):
    def test_gates(self):
        for options,reason in [({'manual':True},'MANUAL_OVERRIDE'),({'verified':False},'TARGET_UNCERTAIN'),
            ({'age':10},'STALE_OBSERVATION'),({'busy':True},'MOTOR_BUSY'),
            ({'settling':True},'COOLDOWN'),({'enabled':False},'AUTO_PAN_DISABLED')]:
            with self.subTest(reason=reason):
                p=plan_pan((750,400,850,600),(1000,1000),config,active=True,**options)
                self.assertEqual((p.action,p.reason),('STOP',reason))
    def test_all_dynamics_scenarios(self):
        for offset in (.05,.15,.3,.5):
            for scenario in ('stationary','moving','fast','sudden_stop','missing','delayed','busy','manual'):
                for adaptive in (False,True):
                    with self.subTest(offset=offset,scenario=scenario,adaptive=adaptive):
                        r=simulate(offset,scenario,adaptive)
                        self.assertEqual(r['safety_violations'],0)
                        self.assertEqual(r['starts'],r['stops'])
    def test_hysteresis_and_invalid_geometry(self):
        self.assertEqual(plan_pan((520,400,600,600),(1000,1000),config).action,'HOLD')
        self.assertEqual(plan_pan((520,400,600,600),(1000,1000),config,previous_axis='pan').action,'MOVE')
        for box in (None,(0,0,0,0),(0,0,float('nan'),10)):
            self.assertEqual(plan_pan(box,(1000,1000),config,active=True).action,'STOP')

class PulseRecoveryTests(unittest.TestCase):
    # Reuse only mock setup helpers, not inherited test methods.
    make=ownership.ZoomOwnershipTests.make
    wait=ownership.ZoomOwnershipTests.wait
    def setup_recovery(self,**kwargs):
        z,lens=self.make(telemetry=False,pulse_recovery_without_telemetry=True,
            pulse_recovery_budget=.12,recovery_pulse=.04,recovery_interval=0,**kwargs)
        lens.telemetry.side_effect=None;lens.telemetry.return_value=None
        return z,lens
    def recover(self,z):
        now=time.monotonic();return z.recover_missing(now,now-2,float('nan'))
    def test_none_telemetry_nan_position_and_bounded_repeats(self):
        z,lens=self.setup_recovery()
        for n in range(3):
            self.assertTrue(self.recover(z));self.wait(lambda:not z.busy)
        for _ in range(10):self.assertFalse(self.recover(z))
        self.assertEqual(lens.command.call_count,3);self.assertLessEqual(z._recovery_spent,.12)
    def test_stale_stream_and_session_block(self):
        z,lens=self.setup_recovery();now=time.monotonic()
        self.assertFalse(z.recover_missing(now-10,now-20,float('nan')))
        z._correlation={'lock_session_id':'expired'};z.session_valid=lambda s:False
        self.assertFalse(self.recover(z));lens.command.assert_not_called()
    def test_manual_busy_and_reacquired(self):
        from control.orchestrator import ControlOrchestrator
        owner=ControlOrchestrator();z,lens=self.setup_recovery(orchestrator=owner)
        owner.manual=True;self.assertFalse(self.recover(z))
        owner.manual=False;owner.acquire('pan_tilt');self.assertFalse(self.recover(z))
        owner.release('pan_tilt');self.assertTrue(self.recover(z));self.wait(lambda:lens.command.called)
        z.motion_feedback(dict(bbox=(300,100,600,800),track_id=7),(1000,1000),time.monotonic(),'s',7)
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'BBOX_FOUND')
    def test_disconnect_request_stops_recovery(self):
        z,lens=self.setup_recovery();self.recover(z);self.wait(lambda:lens.command.called)
        z.request_stop('CAMERA_DISCONNECTED');self.wait(lambda:not z.busy)
        self.assertEqual(z.last_stop_reason,'CAMERA_DISCONNECTED')
    def test_shutdown_stops_recovery(self):
        z,lens=self.setup_recovery();self.recover(z);self.wait(lambda:lens.command.called)
        z.close();self.assertFalse(z.busy);self.assertTrue(lens.stop.called)


class CalibrationSafetyTests(unittest.TestCase):
    def test_dry_adapter_limits_disconnect_emergency_stop_retry(self):
        import threading
        from tools.calibrate_pan_manual import run_pulse
        for connected,cancel,ack in ((False,False,True),(True,True,True),(True,False,False),(True,False,True)):
            with self.subTest(connected=connected,cancel=cancel,ack=ack):
                camera=Mock();camera.read_status.return_value={'connected':connected}
                camera.command.return_value=(ack,'ACK');camera.stop.side_effect=[(False,'lost'),(True,'ACK')]
                emergency=threading.Event()
                if cancel:emergency.set()
                rows=[];run_pulse(camera,'pan_right',1,.02,rows.append,emergency)
                if not connected or cancel:camera.command.assert_not_called()
                else:self.assertEqual(camera.stop.call_count,2)
        with self.assertRaises(ValueError):run_pulse(Mock(),'pan_right',4,.12,lambda _:None,threading.Event())
    def test_start_exception_still_stops(self):
        import threading
        from tools.calibrate_pan_manual import run_pulse
        camera=Mock();camera.read_status.return_value={'connected':True}
        camera.command.side_effect=TimeoutError;camera.stop.return_value=(True,'ACK')
        self.assertFalse(run_pulse(camera,'pan_right',1,.02,lambda _:None,threading.Event()))
        camera.stop.assert_called_once()

class CombinedOwnershipTests(unittest.TestCase):
    make=ownership.ZoomOwnershipTests.make
    wait=ownership.ZoomOwnershipTests.wait
    def test_pan_zoom_exclusive_then_manual_and_shutdown(self):
        from control.auto_pan_tilt import AutoPanTiltController
        from control.orchestrator import ControlOrchestrator
        owner=ControlOrchestrator();z,lens=self.make(orchestrator=owner,telemetry=False,
            pulse_recovery_without_telemetry=True,recovery_pulse=.15)
        lens.supports_pan_tilt=True
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        pan=AutoPanTiltController(lens,owner,cfg);self.addCleanup(pan.close)
        pan.set_enabled(True);pan.session_valid=lambda *_:True
        now=time.monotonic();self.assertTrue(z.recover_missing(now,now-2,float('nan')))
        self.wait(lambda:lens.command.called)
        pan.submit({'bbox':(600,100,690,300)},(500,700),time.monotonic(),'s',1)
        time.sleep(.03)
        self.assertEqual(lens.command.call_count,1)
        owner.manual=True;self.wait(lambda:not z.busy)
        self.assertFalse(pan.submit({'bbox':(600,100,690,300)},(500,700),time.monotonic(),'s',1))
        pan.close();z.close();self.assertFalse(pan.busy);self.assertFalse(z.busy)

class PulseRecoveryFaultTests(unittest.TestCase):
    make=ownership.ZoomOwnershipTests.make
    wait=ownership.ZoomOwnershipTests.wait
    def test_optical_loss_stops_before_recovery(self):
        z,lens=self.make(telemetry=False,pulse_recovery_without_telemetry=True)
        z.update((1000,1000),dict(bbox=(300,100,600,300)),.5,.04,
                 target_name='PERSON',observation_time=time.monotonic())
        self.wait(lambda:lens.command.called)
        now=time.monotonic();self.assertFalse(z.recover_missing(now,now-2,float('nan')))
        self.wait(lambda:not z.busy);self.assertTrue(lens.stop.called)
    def test_stop_failure_disables_recovery(self):
        z,lens=self.make(telemetry=False,pulse_recovery_without_telemetry=True,recovery_pulse=.04)
        lens.stop.return_value=(False,'FAIL')
        now=time.monotonic();z.recover_missing(now,now-2,float('nan'))
        self.wait(lambda:not z.enabled)
        now=time.monotonic();self.assertFalse(z.recover_missing(now,now-2,float('nan')))
        lens.stop.return_value=(True,'ACK')

class PerformanceAdmissionTests(unittest.TestCase):
    def test_expired_periodic_job_does_not_enter_model(self):
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        model=Mock();s=HybridReIDTargetSelector(cfg,model)
        s._score_valid=lambda:False
        with self.assertRaises(TimeoutError):
            s._scores(np.zeros((500,700,3),np.uint8),[detection()])
        model.extract_embeddings.assert_not_called()

if __name__=='__main__':unittest.main()
