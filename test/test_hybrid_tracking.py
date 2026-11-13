"""Identity safety checks use scripted OSNet scores; no camera or GPU required."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import Mock
import unittest
import numpy as np
import config
from ai.hybrid_reid import HybridReIDTargetSelector
from runtime.byte_tracking import ByteTrackPersonTracker
from control.zoom_policy import ZoomPolicy


def person(ident=1, x=100, confidence=.9):
    return dict(track_id=ident, bbox=(x, 50, x+70, 250), confidence=confidence)


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.REID_SEARCH_INTERVAL = 0
        self.s = HybridReIDTargetSelector(self.cfg, Mock())
        self.s.embedding = np.ones(512)
        self.s.error = None
        self.s._scores = Mock(return_value=[.95])
        self.frame = np.zeros((500, 700, 3), np.uint8)
        for t in (0., .1, .2):
            self.s.select_person(self.frame, [person()], t)
        self.assertTrue(self.s.locked)

    def verify(self, score, now):
        self.s._scores.return_value = [score]
        result = self.s.select_person(self.frame, [person()], now)
        job = self.s._verification
        job[0].result(timeout=2)
        return result, self.s.select_person(self.frame, [person()], now+.01)

    def test_periodic_and_single_negative_keep_tracking(self):
        old = self.s.lock_session_id
        first, second = self.verify(.2, 1.3)
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertFalse(self.s.crossing_hold)
        self.assertEqual(old, self.s.lock_session_id)
        self.assertEqual(self.s.negative_verifications, 1)
        self.assertEqual(self.s.stage, 'TARGET_LOCKED')

    def test_repeated_negatives_hold_and_positive_resets_counter(self):
        self.verify(.2, 1.3)
        self.verify(.55, 2.4)
        self.assertEqual(self.s.negative_verifications, 0)
        self.verify(.2, 3.5)
        self.verify(.2, 4.6)
        _, result = self.verify(.2, 5.7)
        self.assertIsNone(result)
        self.assertTrue(self.s.crossing_hold)
        self.assertEqual(self.s.identity_status, 'UNCERTAIN')

    def test_delayed_old_session_result_is_ignored(self):
        from concurrent.futures import Future
        future = Future()
        token = (self.s.verification_epoch, self.s.lock_session_id, self.s.track_id)
        self.s._verification = (future, token, .2, 2, [person()])
        old_uid = self.s.target_uid
        for t in (.3, .4, .5):
            self.s.select_person(self.frame, [person(2)], t)
        self.assertEqual(self.s.target_uid, old_uid)
        future.set_result([.01])
        self.assertIsNotNone(self.s.select_person(self.frame, [person(2)], .6))
        self.assertEqual(self.s.negative_verifications, 0)
        self.assertFalse(self.s.crossing_hold)

    def test_inflight_verification_does_not_block_motion(self):
        import threading
        entered, release = threading.Event(), threading.Event()
        def slow_scores(*args):
            entered.set()
            release.wait(2)
            return [.9]
        self.s._scores.side_effect = slow_scores
        try:
            self.assertIsNotNone(self.s.select_person(self.frame, [person()], 1.3))
            self.assertTrue(entered.wait(1))
            self.assertIsNotNone(self.s.select_person(self.frame, [person(x=105)], 1.4))
            self.assertEqual(self.s.person_box, person(x=105)['bbox'])
            self.assertFalse(self.s.crossing_hold)
        finally:
            release.set()
            self.s._verification[0].result(timeout=2)

    def test_no_face_does_not_lose_body(self):
        for t in (.3, .4, .5):
            p = self.s.select_person(self.frame, [person()], t)
            self.assertFalse(self.s.finish_frame(p, None))
        self.assertTrue(self.s.locked)

    def test_out_of_order_observation_does_not_change_bbox(self):
        self.s.select_person(self.frame, [person(x=105)], .4)
        self.assertIsNone(self.s.select_person(self.frame, [person(x=300)], .3))
        self.assertEqual(self.s.person_box, person(x=105)['bbox'])

    def test_expired_verification_is_not_negative_evidence(self):
        from concurrent.futures import Future
        future = Future()
        future.set_result([.01])
        self.s._verification = (future,
            (self.s.verification_epoch, self.s.lock_session_id, self.s.track_id),
            -10., 0, [person()])
        self.assertIsNotNone(self.s.select_person(self.frame, [person()], .3))
        self.assertEqual(self.s.negative_verifications, 0)

    def test_ambiguous_background_evidence_revokes_authorization(self):
        from concurrent.futures import Future
        future = Future()
        future.set_result([.75, .74])
        self.s._verification = (future,
            (self.s.verification_epoch, self.s.lock_session_id, self.s.track_id),
            .2, 2, [person(), person(2, 350)])
        self.s._scores.return_value = [.75, .74]
        self.assertIsNone(self.s.select_person(self.frame, [person(), person(2, 350)], .3))
        self.assertTrue(self.s.crossing_hold)

    def test_unavailable_verification_has_bounded_continuation(self):
        from concurrent.futures import Future
        self.s._verification = (Future(),
            (self.s.verification_epoch, self.s.lock_session_id, self.s.track_id),
            .2, 2, [person()])
        self.assertIsNotNone(self.s.select_person(self.frame, [person()], 1.3))
        self.assertIsNone(self.s.select_person(self.frame, [person()], 5.3))
        self.assertEqual(self.s.identity_status, 'UNCERTAIN')

    def test_reacquisition_rejects_wrong_person_then_keeps_target_uid(self):
        uid = self.s.target_uid
        for t in np.arange(.3, 3.5, .1):
            self.s.select_person(self.frame, [], float(t))
            self.s.finish_frame(None, None)
        self.assertFalse(self.s.locked)
        self.s._scores.return_value = [.4]
        for t in (3.6, 3.7, 3.8):
            self.assertIsNone(self.s.select_person(self.frame, [person(2)], t))
        self.s._scores.return_value = [.95]
        for t in (3.9, 4., 4.1):
            result = self.s.select_person(self.frame, [person(3)], t)
        self.assertIsNotNone(result)
        self.assertEqual(self.s.target_uid, uid)

    def test_new_id_requires_three_matches_and_rotates_session(self):
        old = self.s.lock_session_id
        for t in (.3, .4):
            self.assertIsNone(self.s.select_person(self.frame, [person(2)], t))
        self.assertIsNotNone(self.s.select_person(self.frame, [person(2)], .5))
        self.assertEqual(self.s.track_id, 2)
        self.assertNotEqual(old, self.s.lock_session_id)

    def test_crossing_same_id_cannot_authorize_wrong_person(self):
        self.s._scores.return_value = [.1, .95]
        self.assertIsNone(self.s.select_person(self.frame, [person(), person(2, 120)], .3))
        self.assertTrue(self.s.crossing_hold)
        self.s._scores.return_value = [.1, .95]
        for t in (.4, .5):
            self.assertIsNone(self.s.select_person(self.frame, [person(), person(2, 350)], t))
        result = self.s.select_person(self.frame, [person(), person(2, 350)], .6)
        self.assertEqual(result['track_id'], 2)

    def test_temporary_then_full_loss_keeps_reference(self):
        embedding = self.s.embedding
        for t in np.arange(.3, 3.3, .1):
            self.s.select_person(self.frame, [], float(t))
            self.assertFalse(self.s.finish_frame(None, person()))
        self.assertEqual(self.s.snapshot()['loss_state'], 'TEMP_LOST')
        self.s.select_person(self.frame, [], 3.4)
        self.assertTrue(self.s.finish_frame(None, None))
        self.assertEqual(self.s.snapshot()['loss_state'], 'LOST')
        self.assertIs(self.s.embedding, embedding)

    def test_weak_detection_maintains_only_existing_track(self):
        self.assertIsNotNone(self.s.select_person(self.frame, [person(confidence=.2)], .3))
        self.assertFalse(self.s.crossing_hold)
        self.s.reset()
        for t in (.4, .5, .6):
            self.assertIsNone(self.s.select_person(self.frame, [person(confidence=.2)], t))
        self.assertFalse(self.s.locked)

    def test_same_id_after_gap_requires_confirmation(self):
        self.s.select_person(self.frame, [], .3)
        for t in (.4, .5):
            self.assertIsNone(self.s.select_person(self.frame, [person()], t))
        self.assertIsNotNone(self.s.select_person(self.frame, [person()], .6))


class ByteTests(unittest.TestCase):
    def test_low_score_cannot_initialize_new_track(self):
        tracker = ByteTrackPersonTracker(config)
        self.assertEqual(tracker.update([person(confidence=.2)], 0), [])
        self.assertEqual(tracker.update([person(confidence=.2)], .1), [])

    def test_low_score_continuity_original_index_and_reset(self):
        tracker = ByteTrackPersonTracker(config)
        first = tracker.update([person(x=100), person(x=400)], 0)
        ident = first[0]['track_id']
        # High/low partitions reorder detections internally.
        output = tracker.update([person(x=100, confidence=.2), person(x=400)], .1)
        low = next(p for p in output if p['confidence'] == .2)
        self.assertEqual(low['track_id'], ident)
        self.assertEqual(low['bbox'], person()['bbox'])
        self.assertEqual(tracker.update([], .2), [])
        tracker.reset()
        self.assertGreater(tracker.update([person()], .3)[0]['track_id'], max(p['track_id'] for p in first))

    def test_large_time_gap_cannot_reuse_old_id(self):
        tracker = ByteTrackPersonTracker(config)
        first = tracker.update([person()], 0)[0]['track_id']
        self.assertGreater(tracker.update([person()], 10)[0]['track_id'], first)


class ContextTests(unittest.TestCase):
    def test_face_cannot_take_zoom_ownership(self):
        p = ZoomPolicy(context=True)
        result = p.decide('s', person(), dict(bbox=(110, 60, 150, 100)),
                          SimpleNamespace(target_type='FACE'), (500,700,3), 0)
        self.assertFalse(p.face_mode)
        self.assertEqual(result['zoom_owner'], 'PERSON')

    def test_edge_blocks_zoom_in(self):
        p = ZoomPolicy(context=True)
        result = p.decide('s', dict(bbox=(1,100,50,200)), None,
                          SimpleNamespace(target_type='PERSON'), (500,700,3), 0)
        self.assertEqual(result['action'], 'HOLD')
        self.assertEqual(result['decision_reason'], 'CONTEXT_EDGE_HOLD')

class WorkerIntegrationTests(unittest.TestCase):
    def test_hybrid_context_reaches_worker_and_face_never_owns_control(self):
        from test_reid_target import WorkerTests
        app, rows = WorkerTests().run_worker([.95]*12, hybrid=True,
            face=dict(bbox=(100,100,140,140), confidence=.95))
        locked = [row for row in rows if row.get('reid', {}).get('locked')]
        self.assertTrue(locked)
        self.assertFalse(app.zoom_policy.face_mode)
        self.assertTrue(all(row.get('control', {}).get('target_type') != 'FACE'
                            for row in locked if isinstance(row.get('control'), dict)))
        self.assertTrue(any(c.kwargs.get('target_name') == 'PERSON'
                            for c in app.auto_zoom.submit_observation.call_args_list))

if __name__ == '__main__':
    unittest.main()
