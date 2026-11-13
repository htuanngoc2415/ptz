"""Deterministic target verification regressions; no camera/motor/model downloads."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config
spec = importlib.util.spec_from_file_location('reid_target_tested', ROOT/'ai/reid_target.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def settings(**kwargs):
    values = {k: getattr(config, k) for k in dir(config) if k.isupper()}
    values.update(TARGET_CONTINUOUS_REID=False, REID_NORMALIZATION_ENABLED=False, REID_GALLERY_ENABLED=False, REID_LOST_GRACE_SEC=0.)
    values.update(kwargs)
    return types.SimpleNamespace(**values)


def person(x=10):
    return {'bbox': (x, 10, x+60, 190), 'confidence': .9}


class FakeReID:
    def __init__(self):
        self.scores = [.95]
        self.batch_calls = []
        self.target_calls = 0

    def extract_embedding(self, image):
        self.target_calls += 1
        return torch.nn.functional.one_hot(torch.tensor(0), 512).float()

    def extract_embeddings(self, images):
        self.batch_calls.append(len(images))
        result = torch.zeros(len(images), 512)
        for i in range(len(images)):
            result[i, 0] = self.scores[i]
            result[i, 1] = (1-self.scores[i]**2)**.5
        return result


class SelectorTests(unittest.TestCase):
    def setUp(self):
        self.engine = FakeReID()
        self.selector = module.ReIDTargetSelector(settings(), self.engine)
        self.selector.embedding = self.engine.extract_embedding(None)
        self.selector.error = None
        self.frame = np.zeros((400, 400, 3), np.uint8)

    def acquire(self):
        for i in range(3):
            result = self.selector.select_person(self.frame, [person()], i*.1)
        return result

    def test_invalid_target_no_inference(self):
        self.selector.invalidate('missing target')
        self.assertIsNone(self.selector.select_person(self.frame, [person()], 1))
        self.assertEqual(self.engine.batch_calls, [])

    def test_no_detections_no_inference(self):
        self.assertIsNone(self.selector.select_person(self.frame, [], 1))
        self.assertEqual(self.engine.batch_calls, [])

    def test_wrong_person_never_locks(self):
        self.engine.scores = [.5]
        for i in range(6):
            self.assertIsNone(self.selector.select_person(self.frame, [person()], i))
        self.assertFalse(self.selector.locked)

    def test_margin_rejects_two_similar_candidates(self):
        self.engine.scores = [.92, .90]
        for i in range(4):
            self.assertIsNone(self.selector.select_person(self.frame, [person(), person(250)], i))
        self.assertEqual(self.selector.confirm, 0)
        self.assertEqual(self.engine.batch_calls, [2]*4)

    def test_order_changes_preserve_temporal_identity(self):
        for i, (boxes, scores) in enumerate([
            ([person(), person(250)], [.95,.4]),
            ([person(250), person()], [.4,.95]),
            ([person(), person(250)], [.95,.4]),
        ]):
            self.engine.scores = scores
            result = self.selector.select_person(self.frame, boxes, i)
            self.assertEqual(result is not None, i == 2)
        self.assertEqual(result['bbox'], person()['bbox'])

    def test_spatial_jump_resets_confirmation(self):
        self.selector.select_person(self.frame, [person()], 0)
        self.selector.select_person(self.frame, [person(250)], 1)
        self.assertEqual(self.selector.confirm, 1)

    def test_bad_score_resets_confirmation(self):
        self.selector.select_person(self.frame, [person()], 0)
        self.engine.scores = [.4]
        self.selector.select_person(self.frame, [person()], 1)
        self.assertEqual(self.selector.confirm, 0)

    def test_low_reid_after_lock_does_not_unlock(self):
        self.acquire()
        self.engine.scores = [.1]
        self.assertIsNotNone(self.selector.select_person(self.frame, [person()], 5))
        self.assertTrue(self.selector.locked)
        # Locked verification is deliberately decimated; allow the configured frame cadence.
        for offset in range(config.PIPELINE['reid_verification_interval_frames']):
            self.selector.select_person(self.frame, [person()], 10.+offset)
        self.assertAlmostEqual(self.selector.top1, .1)

    def test_upper_body_crop_keeps_spatial_lock_without_reid_gate(self):
        self.acquire()
        self.engine.scores = [.1]
        upper_body = {'bbox': (10, 10, 70, 100)}
        selected = self.selector.select_person(self.frame, [upper_body], 5)
        self.assertIs(selected, upper_body)
        self.assertTrue(self.selector.locked)

    def test_lost_target_does_not_switch_to_distant_person(self):
        self.acquire()
        for i in range(config.TARGET_LOST_FRAMES):
            p = self.selector.select_person(self.frame, [person(250)], 1+i)
            self.assertIsNone(p)
            lost = self.selector.finish_frame(p, None)
            self.assertEqual(lost, i == config.TARGET_LOST_FRAMES-1)
        self.assertFalse(self.selector.locked)
        self.assertEqual(self.selector.stage, 'SEARCHING')

    def test_ambiguous_overlap_holds_target(self):
        self.acquire()
        self.assertIsNone(self.selector.select_person(self.frame, [person(), person(11)], 2))

    def test_face_association_and_closeup_without_body(self):
        p = self.acquire()
        target = {'bbox': (20, 20, 50, 60)}
        other = {'bbox': (250, 20, 290, 60)}
        self.assertIsNone(self.selector.select_face([other], p))
        self.assertIs(self.selector.select_face([other,target], p), target)
        self.selector.finish_frame(p, target)
        for _ in range(config.TARGET_LOST_FRAMES+1):
            face = self.selector.select_face([other,target], None)
            self.assertIs(face, target)
            self.assertFalse(self.selector.finish_frame(None, face))
        self.assertIsNone(self.selector.select_face([other], None))

    def test_target_cached_and_reloaded_only_on_change(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'target.png'
            cv2.imwrite(str(path), self.frame)
            self.selector.cfg = settings(TARGET_IMAGE_PATH=str(path), TARGET_IMAGE_IS_CROP=True)
            detector = Mock()
            self.selector.reload_if_changed(detector, now=0)
            calls = self.engine.target_calls
            self.selector.reload_if_changed(detector, now=2)
            self.assertEqual(self.engine.target_calls, calls)
            path.unlink()
            stopped = Mock()
            self.selector.reload_if_changed(detector, now=4, before_reload=stopped)
            stopped.assert_called_once()
            self.assertFalse(self.selector.valid)

    def test_explicit_retry_recovers_unchanged_target_after_detector_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'target.png'
            cv2.imwrite(str(path), self.frame)
            self.selector.cfg = settings(TARGET_IMAGE_PATH=str(path), TARGET_IMAGE_IS_CROP=False)
            detector = Mock(last_error='CUDA allocation failed')
            detector.detect.return_value = []
            self.selector.reload_if_changed(detector, now=0)
            self.assertFalse(self.selector.valid)
            detector.detect.return_value = [person()]
            detector.last_error = None
            self.assertFalse(self.selector.reload_if_changed(detector, now=.1))
            self.assertTrue(self.selector.reload_if_changed(detector, now=.1, force=True))
            self.assertTrue(self.selector.valid)
            self.assertFalse(self.selector.locked)
            self.assertEqual(self.selector.confirm, 0)

    def test_target_detector_failure_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'target.png'; cv2.imwrite(str(path), self.frame)
            self.selector.cfg = settings(TARGET_IMAGE_PATH=str(path), TARGET_IMAGE_IS_CROP=False)
            detector = Mock(); detector.detect.return_value = []
            self.selector.reload_if_changed(detector, now=0)
            self.assertFalse(self.selector.valid)
            self.assertIn('No person', self.selector.error)

    def test_face_quality_gate(self):
        face = {'bbox': (0,0,100,100), 'landmarks': np.zeros((5,2))}
        self.assertTrue(module.face_ready(face, 500, config))
        self.assertTrue(module.face_ready(face, 20, config))
        self.assertTrue(module.face_ready(face, float('nan'), config))
        face['bbox'] = (0,0,20,20)
        self.assertFalse(module.face_ready(face, 500, config))


class WorkerTests(unittest.TestCase):
    """Execute the real AI worker with fake detections and a motor-command spy."""
    def run_worker(self, scores, boxes=None, face=None, sharpness=500, invalid=False, inference_error=False, face_sequence=None, frame_hook=None, continuous=False, hybrid=False):
        from gui.cctv_app import CCTVApp
        from control.target_state_machine import TargetStateMachine
        app = CCTVApp.__new__(CCTVApp)
        app.running = True
        app.root = Mock(); app.auto_zoom_var = Mock()
        app.frame_lock = threading.Lock(); app.ai_lock = threading.Lock()
        app.tracking_reset = threading.Event()
        app.reid_retry = threading.Event()
        app.ai_result = {}
        app.latest_frame_rgb = np.zeros((400,400,3), np.uint8)
        app.latest_frame_time = time.monotonic(); app.frame_sequence = 0
        engine = FakeReID()
        from ai.hybrid_reid import HybridReIDTargetSelector
        selector = HybridReIDTargetSelector if hybrid else module.ReIDTargetSelector
        app.reid_target = selector(settings(TARGET_CONTINUOUS_REID=continuous), engine)
        app.reid_target.embedding = engine.extract_embedding(None); app.reid_target.error = None
        if invalid:
            app.reid_target.invalidate('Target unavailable')
        if inference_error:
            engine.extract_embeddings = Mock(side_effect=RuntimeError('CUDA inference failure'))
        app.reid_target.reload_if_changed = Mock(return_value=False)
        app.target_state = TargetStateMachine(face_acquire_frames=3,face_ready_frames=3)
        app.auto_zoom = Mock(enabled=True, busy=False, last_zoom_end_time=float('-inf'), last_ptz_ms=0.0, last_control_ms=0.0, control_pending=False, recovery_active=False, zoom_direction=0)
        app.auto_zoom.continue_absolute_face_miss.return_value = False  # no validated motion evidence in this adapter
        app.auto_focus = Mock(busy=False)
        from control.orchestrator import ControlOrchestrator
        app.orchestrator = ControlOrchestrator()
        app.digital_ptz = Mock()
        app.zoom_direction = 0
        app.get_motor_speed = Mock(return_value=50)
        app.get_zoom_level = Mock(return_value=3.0)
        app.set_status = Mock()
        app.face_recognizer = Mock(); app.face_matcher = Mock()
        app.face_matcher.match.return_value = dict(identity='test', score=.9, matched=True)
        app.person_detector = Mock(); app.face_detector = Mock()
        app.face_detector.detect.return_value = []
        emitted = []
        index = 0
        def detect(frame):
            engine.scores = [scores[index]]
            return [dict(boxes[index])] if boxes is not None and boxes[index] else ([] if boxes is not None else [person()])
        app.person_detector.detect.side_effect = detect
        def next_frame(*args):
            nonlocal index
            emitted.append(dict(app.ai_result))
            # Await completion in the test harness only; production detection never waits.
            worker = app.face_worker
            if app.reid_target.locked and not app.reid_target.crossing_hold:
                with worker.condition:
                    if worker.session is not None and worker.last_submitted_frame == app.frame_sequence:
                        self.assertTrue(worker.condition.wait_for(
                            lambda: worker.completed_frame == app.frame_sequence,
                            timeout=2.), 'face branch failed to publish')
            if frame_hook is not None:
                frame_hook(app, index)
            index += 1
            if index == len(scores):
                app.running = False
            app.frame_sequence += 1
            app.latest_frame_time = time.monotonic()
        app.update_ai_text = next_frame
        with patch('ai.face.face_detector.detect_face_inside_person', side_effect=lambda *a, **k: ([face_sequence[index]] if face_sequence is not None and face_sequence[index] is not None else [] if face_sequence is not None else [face] if face else [])), \
             patch('runtime.inference_worker.calculate_face_sharpness', return_value=sharpness), \
             patch('ai.sharpness.calculate_face_sharpness', return_value=sharpness), \
             patch('ai.face.face_alignment.align_face', return_value=np.zeros((112,112,3),np.uint8)), \
             patch.object(config, 'TARGET_CONTINUOUS_REID', continuous), \
             patch.object(config, 'CONTEXT_FRAMING', hybrid), \
             patch.object(config, 'FACE_PIPELINE_MODE', 'baseline'), \
             patch.object(config, 'AI_MAX_FPS', 10000):
            app.ai_worker()
        return app, emitted

    def test_continuous_mode_bypasses_spatial_tracker_and_keeps_one_lock(self):
        with patch('runtime.person_tracking.PersonTracker.update', side_effect=AssertionError('tracking must be disabled')):
            app, rows = self.run_worker([.95]*8, continuous=True)
        locked = [row for row in rows if row['reid']['locked']]
        self.assertTrue(locked)
        self.assertEqual(len({row['reid']['track_id'] for row in locked}), 1)
        self.assertEqual(len({row['target_state']['lock_session_id'] for row in locked}), 1)
        self.assertEqual(len(app.reid_target.reid.batch_calls), 8)
        self.assertTrue(app.auto_zoom.submit_observation.called)

    def test_trial_timeout_does_not_override_operator_auto_zoom(self):
        def expire(app, index):
            # The fake motor must model the enabled flag of the real controller.
            app.auto_zoom.set_enabled.side_effect = lambda value: setattr(app.auto_zoom, 'enabled', value)
            if index == 2:
                app.acquisition_trial.started = time.monotonic()-11
                app.auto_zoom.submit_observation.reset_mock()
        app, rows = self.run_worker([.95]*5, frame_hook=expire)
        self.assertEqual(app.acquisition_trial.outcome, 'TIMEOUT')
        app.auto_zoom.set_enabled.assert_not_called()
        self.assertTrue(app.auto_zoom.enabled)

    def test_auto_zoom_invalid_queues_retry_without_enabling_motor(self):
        from gui.cctv_app import CCTVApp
        app = CCTVApp.__new__(CCTVApp)
        app.ai_lock = threading.Lock()
        app.ai_result = {'reid': {'valid': False}}
        app.auto_zoom_var = Mock()
        app.auto_zoom_var.get.return_value = True
        app.auto_zoom = Mock()
        app.reid_retry = threading.Event()
        app.set_status = Mock()
        app.toggle_auto_zoom()
        self.assertTrue(app.reid_retry.is_set())
        app.auto_zoom.set_enabled.assert_called_once_with(False, wait=False)
        app.auto_zoom_var.set.assert_called_once_with(False)

    def test_worker_invalid_target_disables_auto_zoom(self):
        app, rows = self.run_worker([.95]*3, invalid=True)
        app.auto_zoom.set_enabled.assert_called_with(False, wait=False)
        app.auto_zoom.submit_observation.assert_not_called()
        self.assertFalse(rows[-1]['reid']['valid'])

    def test_worker_inference_error_disables_auto_zoom(self):
        app, rows = self.run_worker([.95]*3, inference_error=True)
        app.auto_zoom.set_enabled.assert_called_with(False, wait=False)
        app.auto_zoom.submit_observation.assert_not_called()
        self.assertIn('CUDA inference failure', rows[-1]['reid']['error'])

    def test_worker_rejects_face_outside_locked_person(self):
        face = {'bbox': (250,10,350,110), 'landmarks': np.zeros((5,2))}
        app, rows = self.run_worker([.95]*8, face=face)
        self.assertTrue(all(row['face_target'] is None for row in rows))
        app.face_recognizer.extract_embedding.assert_not_called()

    def test_worker_never_dispatches_zoom_for_unconfirmed_person(self):
        app, _ = self.run_worker([.4]*5)
        app.auto_zoom.submit_observation.assert_not_called()
        app.face_recognizer.extract_embedding.assert_not_called()

    def test_worker_dispatches_only_after_confirmation(self):
        app, rows = self.run_worker([.95]*4)
        self.assertEqual([r['reid']['locked'] for r in rows], [False,False,True,True])
        self.assertEqual(app.auto_zoom.submit_observation.call_count, 2)
        for call in app.auto_zoom.submit_observation.call_args_list:
            self.assertEqual(call.kwargs['target']['bbox'], person()['bbox'])

    def test_worker_lost_target_no_zoom_out_recovery(self):
        app, rows = self.run_worker([.95]*11, [person()]*3+[None]*8)
        self.assertEqual(rows[-1]['reid']['state'], 'SEARCHING')
        app.auto_zoom.zoom_out_step.assert_not_called()
        self.assertIn('TARGET_LOST', [c.kwargs.get('reason') for c in app.auto_zoom.request_stop.call_args_list])

    def test_face_misses_hold_last_face_before_person_fallback(self):
        p = {'bbox': (0,0,200,350)}
        face = {'bbox': (10,10,110,110), 'landmarks': np.zeros((5,2))}
        app, rows = self.run_worker([.95]*13, [p]*13,
                                   face_sequence=[face]*8+[None]*5)
        for row in rows[9:]:
            self.assertEqual(row['control'].target_type, 'FACE')
            self.assertIsNone(row['control'].control_target)
            self.assertEqual(row['reid']['state'], 'FACE_FINAL_LOCK')
        for call in app.digital_ptz.update.call_args_list[8:]:
            self.assertFalse(call.kwargs.get('hold', False))
            self.assertIsNotNone(call.kwargs['target'])

    def test_digital_crop_tracks_person_after_face_timeout(self):
        p = {'bbox': (0,0,200,350)}
        face = {'bbox': (10,10,110,110), 'landmarks': np.zeros((5,2))}
        def expire_face(app, index):
            if index >= 7:
                app.target_state.last_face_time = time.monotonic()-4
        app, rows = self.run_worker([.95]*11, [p]*11,
            face_sequence=[face]*8+[None]*3, frame_hook=expire_face)
        self.assertEqual(rows[-1]['control'].state.value, 'FACE_LOST_HOLD')
        for call in app.digital_ptz.update.call_args_list[8:]:
            self.assertFalse(call.kwargs.get('hold', False))
            self.assertIsNotNone(call.kwargs['target'])

    def test_no_bbox_submits_out_only_recovery_evidence(self):
        app, rows = self.run_worker([.95]*4, [None]*4)
        self.assertEqual(app.auto_zoom.submit_observation.call_count, 4)
        for call in app.auto_zoom.submit_observation.call_args_list:
            self.assertTrue(call.kwargs['recovery'])
            self.assertNotIn('target', call.kwargs)

    def test_recovery_reappearance_requires_reid_confirmation_again(self):
        with patch.object(config, 'LOST_ZOOM_OUT_DELAY', 0):
            app, rows = self.run_worker([.95]*3+[.4]*5,
                [person()]*3+[None]*2+[person()]*3)
        self.assertTrue(rows[2]['reid']['locked'])
        self.assertFalse(rows[-1]['reid']['locked'])
        self.assertTrue(any(c.kwargs.get('recovery') for c in app.auto_zoom.submit_observation.call_args_list))
        self.assertTrue(all(r['person_target'] is None for r in rows[5:]))

    def test_recognition_rejects_blurry_face_during_zoom_and_focus(self):
        p = {'bbox': (0,0,200,350)}
        face = {'bbox': (10,10,90,90), 'landmarks': np.zeros((5,2))}
        def moving(app, index):
            app.auto_zoom.busy = True
            app.auto_zoom.control_pending = True
            app.auto_focus.busy = True
        app, rows = self.run_worker([.95]*8, [p]*8, face, sharpness=7, frame_hook=moving)
        app.face_recognizer.extract_embedding.assert_not_called()
        self.assertTrue(all(row['recognition'] is None for row in rows))

    def test_sharp_face_recognized_while_zoom_and_focus_are_busy(self):
        p = {'bbox': (0,0,200,350)}
        face = {'bbox': (10,10,90,90), 'landmarks': np.zeros((5,2))}
        def moving(app, index):
            app.auto_zoom.busy = True
            app.auto_zoom.control_pending = True
            app.auto_focus.busy = True
        app, rows = self.run_worker([.95]*8, [p]*8, face, sharpness=200, frame_hook=moving)
        app.face_recognizer.extract_embedding.assert_called()
        self.assertTrue(any(row['recognition'] is not None for row in rows))

    def test_recognition_confirm_survives_short_face_miss_and_resets_on_loss(self):
        p = {'bbox': (0,0,200,350)}
        face = {'bbox': (10,10,110,110), 'landmarks': np.zeros((5,2))}
        with patch.object(config, 'FACE_RECOGNITION_INTERVAL', 0):
            app, rows = self.run_worker([.95]*13, [p]*5+[None]*8,
                face_sequence=[face]*3+[None]+[face]+[None]*8)
        self.assertEqual(rows[3]['recognition']['confirm_count'], 1)
        self.assertEqual(rows[4]['recognition']['confirm_count'], 1)
        self.assertTrue(rows[5]['recognition']['confirmed'])
        self.assertEqual(rows[5]['reid']['state'], 'VERIFIED')
        self.assertEqual(app.face_identity.count, 0)

    def test_face_miss_holds_zoom_when_original_person_remains(self):
        p={'bbox':(0,0,200,350)}
        face={'bbox':(20,20,70,60),'landmarks':np.zeros((5,2))}
        def moving(app,index):
            if index >= 4:
                app.auto_zoom.busy=True
                app.auto_zoom.zoom_direction=1
                app.auto_zoom.absolute_face_active=True
        app,rows=self.run_worker([.95]*12,[p]*12,face_sequence=[face]*7+[None]*5,frame_hook=moving)
        self.assertTrue(any(c.kwargs.get('reason') == 'ZOOM_HOLD_FACE_TEMPORARILY_LOST'
                            for c in app.auto_zoom.request_stop.call_args_list))
        reasons=[c.kwargs.get('reason') for c in app.auto_zoom.request_stop.call_args_list]
        self.assertNotIn('FACE_LOST',reasons)
        self.assertNotIn('PERSON_FRAMING_HOLD',reasons)
        self.assertTrue(rows[-1]['reid']['locked'])

    def test_unknown_attempt_is_logged_with_quality(self):
        face = {'bbox': (10,10,90,90), 'landmarks': np.zeros((5,2))}
        def unknown(app, index):
            app.face_matcher.match.return_value = dict(identity='unknown', score=.41,
                matched=False, nearest_identity='A', second_score=.2, margin=.21)
        with patch('builtins.print') as output:
            app, rows = self.run_worker([.95]*5, boxes=[{'bbox':(0,0,150,350)}]*5, face=face, sharpness=200, frame_hook=unknown)
        app.face_recognizer.extract_embedding.assert_called_once()
        self.assertFalse(rows[-1]['recognition']['confirmed'])
        logs = [str(c.args[0]) for c in output.call_args_list if c.args]
        log = next(line for line in logs if '[RECOGNITION] UNKNOWN' in line)
        for field in ('frame=', 'session=', 'sharpness=200.0', 'size=80x80', 'ratio=',
                      'second=0.200', 'confirm=0/2'):
            self.assertIn(field, log)

    def test_final_lock_stops_optical_zoom_but_keeps_digital_follow(self):
        p = {'bbox': (0,0,200,350)}
        face = {'bbox': (10,10,110,110), 'landmarks': np.zeros((5,2))}
        previously_locked = False
        def snapshot(app, index):
            nonlocal previously_locked
            if app.face_framing.locked:
                self.assertFalse(app.digital_ptz.update.call_args.kwargs.get('hold', False))
                self.assertTrue(app.auto_focus.update.call_args.kwargs['allow_focus'])
                # Person feedback can be queued before this frame establishes the face latch.
                # Once established, later frames must never submit person framing again.
                if previously_locked:
                    app.auto_zoom.submit_observation.assert_not_called()
            previously_locked = app.face_framing.locked
            app.auto_zoom.submit_observation.reset_mock()
        with patch.object(config, 'FACE_RECOGNITION_INTERVAL', 0):
            app, rows = self.run_worker([.95]*10, [p]*10, face, sharpness=200, frame_hook=snapshot)
        self.assertEqual(rows[-1]['reid']['state'], 'FACE_FINAL_LOCK')
        self.assertGreater(app.face_recognizer.extract_embedding.call_count, 3)
        app.auto_zoom.submit_observation.assert_not_called()
        self.assertEqual(app.zoom_policy.state, 'ZOOM_HOLD')

    def test_final_lock_releases_after_face_timeout(self):
        p = {'bbox': (0,0,200,350)}
        face = {'bbox': (10,10,110,110), 'landmarks': np.zeros((5,2))}
        def expire(app, index):
            if index == 8:
                app.face_framing.last_face = time.monotonic()-3
        app, rows = self.run_worker([.95]*10, [p]*10,
            face_sequence=[face]*8+[None]*2, frame_hook=expire)
        self.assertEqual(rows[7]['reid']['state'], 'FACE_FINAL_LOCK')
        self.assertFalse(app.face_framing.locked)
        self.assertEqual(rows[9]['control'].target_type, 'FACE')
        self.assertIsNone(rows[9]['control'].control_target)

    def test_small_face_does_not_run_recognition(self):
        face = {'bbox': (10,10,70,80), 'landmarks': np.zeros((5,2))}
        app, _ = self.run_worker([.95]*8, face=face)
        app.face_recognizer.extract_embedding.assert_not_called()


if __name__ == '__main__':
    unittest.main()
