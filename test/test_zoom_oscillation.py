"""Session-persistent face framing and controlled outward movement; no hardware."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import time
from control.zoom_policy import ZoomPolicy
from control.auto_zoom import AutoZoomController
from camera.dahua_rpc import ZoomRPC
from tools.zoom_benchmark import MockFirmware
from telemetry import latency

class OscillationTests(unittest.TestCase):
    def setUp(self):
        self.p=ZoomPolicy()
        self.body={'bbox':(100,0,700,997)}
        self.face={'bbox':(200,100,400,350)}
        self.control=SimpleNamespace(target_type='FACE')
        self.p.decide('s',self.body,self.face,self.control,(1000,1000),0)
    def decide(self,face=None,now=2,person=True,observed=True):
        return self.p.decide('s',self.body if person else None,face,self.control,(1000,1000),now,observed)
    def test_face_missing_person_997_holds_indefinitely(self):
        for t in (1,3,10,30):
            d=self.decide(now=t)
            self.assertEqual(d['action'],'HOLD')
            self.assertEqual(d['zoom_state'],'FACE_TEMPORARILY_LOST')
            self.assertEqual(d['decision_reason'],'ZOOM_HOLD_FACE_TEMPORARILY_LOST')
    def test_large_face_requires_multiple_frames_and_time(self):
        large={'bbox':(200,100,500,450)}
        self.assertEqual(self.decide(large,1)['action'],'HOLD')
        self.assertEqual(self.decide(large,1.1)['action'],'HOLD')
        d=self.decide(large,1.4)
        self.assertEqual(d['action'],'NORMAL')
        self.assertEqual(d['decision_reason'],'ZOOM_OUT_FACE_TOO_LARGE')
    def test_head_clipping_requires_prior_face_edge_and_is_bounded(self):
        edge={'bbox':(200,0,400,200)}
        self.decide(edge,.1)
        actions=[self.decide(now=t)['action'] for t in (.2,.6,1.3,1.4,2.2,3.2,4.2)]
        self.assertEqual(actions.count('HEAD_RECOVERY'),2)
        self.assertEqual(actions[-1],'HOLD')
    def test_body_clipping_alone_never_allows_out(self):
        for t in range(10): self.assertEqual(self.decide(now=t+1)['action'],'HOLD')
    def test_real_target_loss_enters_recovery(self):
        d=self.decide(person=False)
        self.assertEqual(d['action'],'RECOVERY')
        self.assertEqual(d['decision_reason'],'ZOOM_OUT_TARGET_RECOVERY')
    def test_flicker_never_returns_person_approach(self):
        for i in range(20):
            d=self.decide(self.face if i%2 else None,i+.5)
            self.assertNotEqual(d['zoom_state'],'PERSON_APPROACH')
            self.assertNotIn('PERSON_APPROACH',d['decision_reason'])
    def test_same_session_relock_cannot_reset_face_phase(self):
        self.control.target_type='PERSON'
        self.assertEqual(self.decide()['action'],'HOLD')
        self.assertTrue(self.p.face_mode)
    def test_new_session_can_approach(self):
        self.control.target_type='PERSON'
        d=self.p.decide('new',self.body,None,self.control,(1000,1000),3)
        self.assertEqual(d['zoom_state'],'PERSON_APPROACH')
    def test_person_observation_queued_before_latch_is_rejected(self):
        ptz=Mock();ptz.has_zoom_telemetry=False;ptz.supports_absolute_zoom=False
        z=AutoZoomController(ptz);self.addCleanup(z.close)
        z.set_enabled(True);z._correlation={'lock_session_id':'s'};z.enter_face_mode('s')
        self.assertEqual(z.update((1000,1000),self.body,.5,.04,target_name='PERSON'),'FACE_SESSION_HOLD')
        ptz.command.assert_not_called()
    def test_rpc_rechecks_policy_after_queue_wait(self):
        firmware=MockFirmware()
        rpc=ZoomRPC('mock',80,'mock','mock',session_factory=lambda:firmware)
        self.addCleanup(rpc.close)
        rpc.zoom_policy_valid=lambda fields:fields.get('zoom_domain')!='PERSON'
        with latency.correlated(lock_session_id='s',zoom_domain='PERSON'):
            with self.assertRaises(RuntimeError):rpc.start(-1,8,'zoom')
        self.assertFalse(any(p['method']=='ptz.start' for p in firmware.calls))
    def test_head_recovery_pulse_stops_without_more_inference(self):
        ptz=Mock();ptz.has_zoom_telemetry=False;ptz.supports_absolute_zoom=False
        ptz.command.return_value=(True,'ACK');ptz.stop.return_value=(True,'ACK')
        z=AutoZoomController(ptz,recovery_pulse=.03);self.addCleanup(z.close)
        z.set_enabled(True)
        self.assertTrue(z.head_recovery(time.monotonic()))
        deadline=time.monotonic()+1
        while z.busy and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(z.busy)
        ptz.stop.assert_called_once()
