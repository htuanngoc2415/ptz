"""Synthetic identity-labelled trajectories; never connects to a camera."""
import time,unittest
from unittest.mock import Mock,patch
import numpy as np
import test_target_tracking_p0 as fixtures
from test_target_tracking_p0 import person,cfgcopy
from ai.hybrid_reid import HybridReIDTargetSelector
from ai.overlap_policy import overlap_geometry, minor_contact
from telemetry import latency

class MinorOverlapTests(unittest.TestCase):
    setUp=fixtures.OverlapBehaviorTests.setUp
    feed=fixtures.OverlapBehaviorTests.feed
    result=fixtures.OverlapBehaviorTests.result
    # 4% of target, 20% of competitor; old min-area gate sees 20% > 15%.
    def scene(self,ident=1):
        return [person(ident,(100,100,200,300)),person(2,(196,100,216,300),'other')]
    def acquire(self,scene=None):
        self.s.reset();self.s._scores=Mock(side_effect=lambda f,ps:[.95 if p['label']=='target' else .1 for p in ps])
        for t in (1.,1.1,1.2):chosen=self.feed(t,scene or [self.scene()[0]])
        return chosen
    def test_no_overlap(self):self.assertIsNotNone(self.feed(.3,[person()]))
    def test_tiny_overlap_confirm_and_continue(self):
        self.assertIsNotNone(self.acquire(self.scene()))
        for t in (1.3,1.5,1.7):self.assertIsNotNone(self.feed(t,self.scene()))
        self.assertFalse(self.s.crossing_hold)
    def test_hand_contact_does_not_expire_grace(self):
        self.acquire();session=self.s.lock_session_id
        for t in (1.3,1.5,1.7,1.9):self.assertIsNotNone(self.feed(t,self.scene()))
        self.assertEqual(session,self.s.lock_session_id)
    def test_moderate_overlap_needs_fresh_verification(self):
        from test_target_tracking_p0 import other
        self.assertIsNotNone(self.feed(.3,[person(),other()]))
        self.result(.31,[.95,.1],[person(),other()])
        self.assertIsNotNone(self.feed(.7,[person(),other()]))
    def test_severe_overlap_immediate_hold(self):
        self.assertIsNone(self.feed(.3,[person(),person(2,person()['bbox'],'other')]))
        self.assertTrue(self.s.crossing_hold)
    def test_small_contained_competitor_not_minor(self):
        self.acquire();scene=[self.scene()[0],person(2,(110,110,120,130),'other')]
        self.assertFalse(minor_contact(overlap_geometry(scene[0]["bbox"],scene[1]["bbox"]),self.cfg))
        self.feed(1.3,scene)
        self.assertIsNone(self.feed(1.7,scene))
    def test_id_switch_requires_three_appearance_results(self):
        self.acquire()
        for t in (1.3,1.4):self.assertIsNone(self.feed(t,self.scene(3)))
        self.assertIsNotNone(self.feed(1.5,self.scene(3)))
        self.assertEqual(self.s.track_id,3)
    def test_strong_reid_progress_not_reset_by_minor_contact(self):
        self.s.reset()
        for t,count in ((1.,1),(1.1,2),(1.2,3)):
            self.feed(t,self.scene());self.assertEqual(self.s.confirm,count)
    def test_acquisition_counter_cannot_cross_ids(self):
        self.s.reset()
        for t,ident in ((1.,3),(1.1,4),(1.2,5)):
            self.assertIsNone(self.feed(t,self.scene(ident)))
            self.assertEqual(self.s.confirm,1)
    def test_acquisition_counter_bounded_gap(self):
        self.s.reset();self.feed(1.,self.scene())
        self.assertIsNone(self.feed(3.,self.scene()));self.assertEqual(self.s.confirm,1)
    def test_ambiguous_reid_cannot_lock(self):
        self.s.reset();self.s._scores=Mock(return_value=[.95,.94])
        for t in (1.,1.1,1.2,1.3):self.assertIsNone(self.feed(t,self.scene()))
        self.assertFalse(self.s.locked)
    def test_contradictory_verify_still_holds_on_minor_contact(self):
        self.acquire();self.result(1.21,[.1,.95],self.scene())
        self.assertIsNone(self.feed(1.3,self.scene()));self.assertTrue(self.s.crossing_hold)
    def test_stale_result_cannot_authorize(self):
        self.acquire();token=(self.s.verification_epoch,self.s.lock_session_id,self.s.track_id)
        self.feed(1.3,[]);self.result(1.2,[.99,.1],self.scene(),token)
        self.feed(1.4,self.scene());self.assertTrue(self.s.crossing_hold)
    def test_disappeared_target_expires(self):
        for t in np.arange(.3,4.,.1):self.assertIsNone(self.feed(float(t),[]))
        self.assertFalse(self.s.locked);self.assertTrue(self.s.reacquiring)
    def test_minor_contact_cannot_renew_expired_identity(self):
        self.acquire()
        t=1.3
        while t < 1.3+self.cfg.REID_IDENTITY_MAX_AGE:
            self.feed(t,self.scene());t+=.1
        # Remove appearance support: no new evidence can authorize the target.
        self.s._scores=Mock(return_value=[.1,.95])
        self.assertIsNone(self.feed(t+.1,self.scene()))
        self.assertTrue(self.s.crossing_hold)
    def test_minor_contact_does_not_override_motion_jump(self):
        self.acquire()
        scene=[person(1,(250,100,350,300)),person(2,(346,100,366,300),'other')]
        self.assertIsNone(self.feed(1.3,scene));self.assertTrue(self.s.crossing_hold)

    def test_diagnostics_opt_in(self):
        self.cfg.REID_OVERLAP_DIAGNOSTICS=True
        with patch.object(latency.profiler,'event') as event:
            self.feed(.3,[person()])
        rows=[c.kwargs for c in event.call_args_list if c.args[0]=='OVERLAP_DECISION']
        self.assertEqual(len(rows),1);self.assertIn('confirmation_before',rows[0])
    def test_overlap_ratios_are_directional(self):
        a,b=self.scene();g=overlap_geometry(a['bbox'],b['bbox'])
        self.assertAlmostEqual(g['target_cover'],.04);self.assertAlmostEqual(g['competitor_cover'],.2)

class VerificationExceptionTests(unittest.TestCase):
    def test_producer_logs_exception_even_when_superseded_and_recovers(self):
        s=HybridReIDTargetSelector(cfgcopy(),Mock());s.track_id=1;s.person_box=person()['bbox'];s.lock_session_id='s'
        s._scores=Mock(side_effect=ValueError('synthetic invalid crop'))
        with patch.object(latency.profiler,'event') as event:
            s._start_verification(np.zeros((500,700,3),np.uint8),[person()],time.monotonic())
            with self.assertRaises(ValueError):s._verification[0].result(timeout=2)
            rows=[c.kwargs for c in event.call_args_list if c.args[0]=='REID_VERIFICATION_EXCEPTION']
            self.assertEqual(rows[0]['error_type'],'ValueError');self.assertIn('Traceback',rows[0]['traceback'])
            s._take_verification(time.monotonic());self.assertIsNone(s._verification)
            s._scores=Mock(return_value=[.95]);s._start_verification(np.zeros((500,700,3),np.uint8),[person()],time.monotonic())
            s._verification[0].result(timeout=2);s._take_verification(time.monotonic())
            self.assertEqual(s.identity_status,'VERIFIED')
