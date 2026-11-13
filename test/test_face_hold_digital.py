"""Face ownership and display regressions; no motor or camera required."""
import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from control.target_state_machine import TargetStateMachine, TargetState
from control.digital_ptz import DigitalPTZController

class FaceHoldTests(unittest.TestCase):
    def setUp(self):
        self.sm=TargetStateMachine()
        self.person={'bbox':(0,0,600,950)}
        self.face={'bbox':(200,100,400,350)}
        for i in range(5):
            self.sm.update((1000,1000),self.person,self.face,now=i*.1)

    def test_five_misses_preserve_median_and_face_ownership(self):
        history=list(self.sm.face_ratios)
        for i in range(5):
            r=self.sm.update((1000,1000),self.person,None,now=.5+i*.2)
            self.assertEqual(r.state,TargetState.FACE_LOST_HOLD)
            self.assertEqual(r.target_type,'FACE')
            self.assertIsNone(r.control_target)
        self.assertEqual(list(self.sm.face_ratios),history)
        r=self.sm.update((1000,1000),self.person,self.face,now=1.6)
        self.assertEqual(r.target_type,'FACE')
        self.assertIs(r.control_target,self.face)

    def test_face_timeout_preserves_face_phase(self):
        for now in (3.5,8,20):
            r=self.sm.update((1000,1000),self.person,None,now=now)
            self.assertEqual(r.state,TargetState.FACE_LOST_HOLD)
            self.assertIsNone(r.control_target)
            self.assertEqual(r.target_type,'FACE')
            self.assertFalse(r.target_lost)

    def test_slow_face_update_does_not_expire_lock(self):
        r=self.sm.update((1000,1000),self.person,self.face,now=2)
        self.assertEqual(r.target_type,'FACE')
        self.assertTrue(self.sm.face_latched)

    def test_explicit_identity_reset_allows_body_again(self):
        self.sm.reset()
        r=self.sm.update((1000,1000),self.person,None,now=10)
        self.assertEqual(r.target_type,'PERSON')

class DigitalTests(unittest.TestCase):
    def test_ema_and_hold_freeze_both_center_and_zoom(self):
        ptz=DigitalPTZController(enabled=True,center_smoothing=.25)
        ptz.update((1000,1000),{'bbox':(0,0,100,100)},now=0)
        ptz.update((1000,1000),{'bbox':(400,400,500,500)},now=.1)
        self.assertEqual(ptz.center_x,150)
        self.assertEqual(ptz.center_y,150)
        state=(ptz.center_x,ptz.center_y,ptz.zoom)
        ptz.update((1000,1000),None,now=5,hold=True)
        self.assertEqual((ptz.center_x,ptz.center_y,ptz.zoom),state)
        frame=np.zeros((1000,1000,3),np.uint8)
        display,transform=ptz.render(frame)
        self.assertEqual(display.shape,frame.shape)
        self.assertTrue(transform['enabled'])

if __name__=='__main__': unittest.main()
