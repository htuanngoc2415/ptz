"""Acceptance A–G using fake lens/clock/lanes only; never opens camera or models."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from camera.zoom_prediction import PositionABSMapping
from camera.dahua_rpc import ZoomRPC
from control.auto_zoom import AutoZoomController
from control.zoom_policy import ZoomPolicy
from runtime.inference_worker import InferenceWorker
from runtime.scheduling import InferenceScheduler, inference_resource
from runtime.target_session import TargetSession
from telemetry import latency
from tools.zoom_benchmark import MockFirmware


class ZoomLatencySafetyTests(unittest.TestCase):
    def make(self):
        lens = Mock()
        lens.has_zoom_telemetry = lens.supports_absolute_zoom = True
        lens.speed = 100
        lens.telemetry.side_effect = lambda: dict(zoom=1.9, timestamp=time.monotonic(), zoom_motion_state='Moving')
        lens.move_to_zoom_position.return_value = (True, 'ACK')
        lens.stop.return_value = (True, 'STOP ACK')
        lens.command.return_value = (True, 'ACK')
        z = AutoZoomController(lens, wait_after_zoom=0, feedback_timeout=.8)
        sessions = TargetSession(); sessions.publish('session', 7)
        z.session_valid = sessions.matches
        z.track_valid = sessions.matches
        z.set_enabled(True)
        z._correlation = dict(frame_id=1, lock_session_id='session', target_track_id=7,
                              locked_ns=time.perf_counter_ns())
        self.addCleanup(z.close)
        return z, lens, sessions

    def wait(self, predicate):
        deadline = time.monotonic()+1
        while not predicate() and time.monotonic()<deadline:
            time.sleep(.002)
        self.assertTrue(predicate())

    def start_face(self, z):
        z.enter_face_mode('session')
        z.update((1000,1000), {'bbox': (400,300,500,366)}, .25, .03,
                 target_name='FACE', observation_time=time.monotonic())

    def body(self, z, person=None):
        return z.motion_feedback(person or {'bbox':(300,100,700,900), 'track_id':7},
                                 (1000,1000), time.monotonic(), 'session', 7)

    def test_A_short_face_miss_keeps_absolute_move_without_new_command(self):
        z, lens, _ = self.make(); self.start_face(z)
        self.wait(lambda: lens.move_to_zoom_position.called)
        self.assertTrue(self.body(z))
        self.assertTrue(z.continue_absolute_face_miss())
        time.sleep(.025)
        self.assertTrue(z.busy)
        lens.stop.assert_not_called()
        self.assertEqual(lens.move_to_zoom_position.call_count, 1)
        lens.command.assert_not_called()

    def test_B_actual_target_loss_stops(self):
        z, lens, _ = self.make(); self.start_face(z)
        self.wait(lambda: lens.move_to_zoom_position.called)
        z.motion_feedback(None, (1000,1000), time.monotonic(), 'session', 7)
        self.wait(lambda: lens.stop.called)
        self.assertEqual(z.last_stop_reason, 'TARGET_LOST')

    def test_face_miss_has_bounded_grace_and_fresh_track_watchdog(self):
        z, lens, _ = self.make(); self.start_face(z)
        self.wait(lambda: lens.move_to_zoom_position.called)
        self.body(z); self.assertTrue(z.continue_absolute_face_miss())
        z._face_miss_since = time.monotonic()-z.face_miss_grace-.01
        self.wait(lambda: lens.stop.called)
        self.assertEqual(z.last_stop_reason, 'FACE_MISS_SAFETY_TIMEOUT')

    def test_track_leaving_fov_stops_absolute(self):
        z, lens, _ = self.make(); self.start_face(z)
        self.wait(lambda: lens.move_to_zoom_position.called)
        self.assertFalse(self.body(z, {'bbox':(0,100,50,800), 'track_id':7}))
        self.wait(lambda: lens.stop.called)
        self.assertEqual(z.last_stop_reason, 'STALE_TRACK')

    def test_small_person_at_any_frame_edge_cannot_start_zoom_in(self):
        for bbox in ((0,400,100,500), (900,400,1000,500),
                     (400,0,500,100), (400,900,500,1000),
                     (-20,400,100,500), (400,910,500,1020)):
            with self.subTest(bbox=bbox):
                z, lens, _ = self.make()
                result = z.update((1000,1000), {'bbox':bbox}, .5, .04,
                                  target_name='PERSON', observation_time=time.monotonic())
                self.assertEqual(result, 'BBOX FRAME EDGE: HOLD ZOOM IN')
                lens.command.assert_not_called()
                lens.move_to_zoom_position.assert_not_called()

    def test_edge_stop_uses_width_and_height_independently(self):
        z, lens, _ = self.make()
        result = z.update((1080,1920), {'bbox':(1885,400,1910,500)}, .5, .04,
                          target_name='PERSON', observation_time=time.monotonic())
        self.assertEqual(result, 'BBOX FRAME EDGE: HOLD ZOOM IN')
        lens.command.assert_not_called()

    def test_body_edge_stops_absolute_face_zoom_without_face_sample(self):
        z, lens, _ = self.make(); self.start_face(z)
        self.wait(lambda: lens.move_to_zoom_position.called)
        self.assertTrue(self.body(z, {'bbox':(300,10,700,900), 'track_id':7}))
        self.wait(lambda: not z.busy)
        lens.stop.assert_called_once()
        self.assertEqual(z.last_stop_reason, 'BBOX_FRAME_EDGE')
        self.wait(lambda: not z.control_pending)
        self.assertEqual(z._correlation.get('lock_session_id'), 'session')
        self.start_face(z)
        self.assertEqual(lens.move_to_zoom_position.call_count, 1)
        self.assertEqual(z.last_action, 'BBOX FRAME EDGE: HOLD ZOOM IN')

    def test_body_edge_cancels_start_waiting_for_camera_queue(self):
        z, lens, _ = self.make()
        with z._command_lock:
            self.start_face(z)
            self.body(z, {'bbox':(300,10,700,900), 'track_id':7})
            self.assertTrue(z._cancel.is_set())
            self.assertFalse(z.validate_rpc_intent({'zoom_intent_id':z._intent.command_id}))
        self.wait(lambda: not z.busy)
        lens.command.assert_not_called()
        lens.move_to_zoom_position.assert_not_called()

    def test_body_edge_hysteresis_and_new_session(self):
        z, lens, sessions = self.make()
        for top, blocked in ((20,True), (25,True), (30,True), (31,False)):
            self.body(z, {'bbox':(300,top,700,900), 'track_id':7})
            self.assertEqual(z._body_at_frame_edge('session',7), blocked)
        self.start_face(z)
        self.wait(lambda: lens.move_to_zoom_position.called)
        self.body(z, {'bbox':(300,10,700,900), 'track_id':7})
        self.wait(lambda: not z.busy)
        sessions.publish('new', 8)
        self.assertFalse(z._body_at_frame_edge('new',8))

    def test_clipped_body_allows_outward_motion(self):
        z, lens, _ = self.make()
        person = {'bbox':(300,0,700,900), 'track_id':7}
        self.body(z, person)
        z.update((1000,1000), person, .5, .04,
                 target_name='PERSON', observation_time=time.monotonic())
        self.wait(lambda: lens.command.called)
        self.assertEqual(z.zoom_direction, -1)
        self.body(z, person)
        lens.stop.assert_not_called()

    def test_pulse_stops_at_edge_even_with_filtered_ratio_still_small(self):
        z, lens, _ = self.make()
        lens.has_zoom_telemetry = lens.supports_absolute_zoom = False
        z.step_medium = .5
        z.update((1000,1000), {'bbox':(400,400,500,500)}, .5, .04,
                 target_name='PERSON', observation_time=time.monotonic())
        self.wait(lambda: lens.command.called)
        z.update((1000,1000), {'bbox':(10,400,110,500)}, .5, .04,
                 target_name='PERSON', filtered_ratio=.1, observation_time=time.monotonic())
        lens.stop.assert_called_once()
        self.assertEqual(z.last_stop_reason, 'BBOX_FRAME_EDGE')

    def test_edge_stop_failure_keeps_error_and_disables_auto_zoom(self):
        z, lens, _ = self.make()
        lens.has_zoom_telemetry = lens.supports_absolute_zoom = False
        z.step_medium = .5
        z.update((1000,1000), {'bbox':(400,400,500,500)}, .5, .04,
                 target_name='PERSON', observation_time=time.monotonic())
        self.wait(lambda: lens.command.called)
        lens.stop.return_value = (False, 'unreachable')
        result = z.update((1000,1000), {'bbox':(10,400,110,500)}, .5, .04,
                          target_name='PERSON', observation_time=time.monotonic())
        self.assertIn('STOP FAILED', result)
        self.assertFalse(z.enabled)
        self.assertTrue(z.busy)
        lens.stop.return_value = (True, 'STOP ACK')

    def test_C_expired_intent_replans_new_same_track_geometry(self):
        z, lens, _ = self.make()
        with z._command_lock:
            z.update((1000,1000), {'bbox':(300,200,600,300)}, .5, .04,
                     target_name='PERSON', observation_time=time.monotonic()-.79)
            original = z._intent
            fresh = time.monotonic()
            z.submit_observation(correlation=dict(z._correlation, frame_id=2),
                frame_shape=(1000,1000), target={'bbox':(300,200,600,400)},
                target_ratio=.5, tolerance=.04, target_name='PERSON', observation_time=fresh)
            time.sleep(.035)
        self.wait(lambda: lens.command.called)
        lens.move_to_zoom_position.assert_not_called()
        self.assertEqual(z.replanning_count, 1)
        self.assertEqual(z._intent.frame_id, 2)
        self.assertEqual(z._intent.frame_timestamp, fresh)
        self.assertEqual(z._intent.bbox, (300,200,600,400))
        self.assertAlmostEqual(z._intent.target_zoom, 4.75)  # below ABS: fresh geometry must choose pulse!

    def test_D_session_mismatch_rejects_before_motor_start(self):
        z, lens, session = self.make()
        with z._command_lock:
            self.start_face(z)
            session.publish('other', 9)
        self.wait(lambda: not z.busy)
        lens.move_to_zoom_position.assert_not_called()
        lens.command.assert_not_called()

    def test_reason_flags_and_immutable_intent_are_specific(self):
        z, lens, sessions = self.make()
        with z._command_lock:
            self.start_face(z)
            intent = z._intent
            with self.assertRaises(FrozenInstanceError): intent.ratio = .9
            checks = [('STALE_OBSERVATION', replace(intent, expiry=time.monotonic()-1)),
                      ('STALE_COMMAND_GENERATION', replace(intent, generation=-1)),
                      ('STALE_SESSION', replace(intent, session_id='other')),
                      ('STALE_TRACK', replace(intent, track_id=8)),
                      ('STALE_PERSON_ZOOM_POLICY', replace(intent, target_kind='PERSON'))]
            for expected, modified in checks:
                reason, flags = z.intent_rejection(modified)
                self.assertEqual(reason, expected)
                self.assertTrue(any(flags.values()))
            z.set_enabled(False, wait=False)

    def test_new_observation_does_not_refresh_old_plan_at_wire_boundary(self):
        z, _, _ = self.make()
        with z._command_lock:
            self.start_face(z)
            z._intent = replace(z._intent, expiry=time.monotonic()-1)
            z._last_observation = time.monotonic()  # simulate fresh feedback, not a new plan
            with patch.object(latency.profiler, 'event') as events:
                self.assertFalse(z.validate_rpc_intent({'zoom_intent_id':z._intent.command_id}))
            rejection = [c.kwargs for c in events.call_args_list if c.args[0]=='ZOOM_COMMAND_REJECTED'][-1]
            self.assertEqual(rejection['command_rejection_reason'], 'STALE_OBSERVATION')
            self.assertTrue(rejection['stale_observation'])
            z.set_enabled(False, wait=False)

    def test_E_locked_person_submits_without_face_worker_or_quality(self):
        app = SimpleNamespace(auto_zoom=Mock(enabled=True), reid_target=SimpleNamespace(
            locked=True, crossing_hold=False, lock_session_id='session', track_id=7, locked_ns=time.perf_counter_ns()),
            tracking_reset=threading.Event(), get_motor_speed=lambda:50)
        app.zoom_policy = ZoomPolicy()
        self.assertTrue(InferenceWorker.start_person_zoom_on_lock(app,
            {'bbox':(100,100,300,300)}, (1000,1000), 1, time.monotonic()))
        self.assertEqual(app.auto_zoom.submit_observation.call_args.kwargs['target_name'], 'PERSON')
        z, lens, _ = self.make()
        z.submit_observation(**app.auto_zoom.submit_observation.call_args.kwargs)
        self.wait(lambda: lens.command.called or lens.move_to_zoom_position.called)

    def test_F_face_latch_never_returns_to_person_half_frame(self):
        policy = ZoomPolicy()
        control = SimpleNamespace(target_type='FACE')
        policy.decide('session', {'bbox':(0,0,1000,1000)}, {'bbox':(400,300,500,400)},
                      control, (1000,1000), 0)
        decision = policy.decide('session', {'bbox':(0,0,1000,1000)}, None,
                                 control, (1000,1000), .2, face_observed=True)
        self.assertEqual(decision['decision_reason'], 'ZOOM_HOLD_FACE_TEMPORARILY_LOST')
        self.assertNotIn('PERSON', decision['decision_reason'])

    def test_G_expired_queued_inference_skips_model_and_releases_lane(self):
        scheduler = InferenceScheduler(); fresh = threading.Event(); fresh.set()
        entered = threading.Event(); model = Mock()
        def queued():
            entered.set()
            with scheduler.use('face', valid=fresh.is_set) as granted:
                if granted: model()
        with scheduler.use('detection'):
            worker = threading.Thread(target=queued); worker.start()
            self.assertTrue(entered.wait(1))
            fresh.clear()
        worker.join(1); self.assertFalse(worker.is_alive()); model.assert_not_called()
        with scheduler.use('detection') as granted: self.assertTrue(granted)

    def test_rpc_guard_does_not_send_rejected_start_but_allows_stop(self):
        firmware = MockFirmware()
        rpc = ZoomRPC('mock',80,'mock','mock',session_factory=lambda:firmware)
        self.addCleanup(lambda:rpc.close(stop_motors=False))
        rpc.zoom_intent_valid = lambda fields: False
        with self.assertRaises(RuntimeError): rpc.goto(.3)
        self.assertFalse(any(c['method']=='ptz.start' for c in firmware.calls))
        rpc.stop('zoom')
        self.assertTrue(any(c['method']=='ptz.stop' for c in firmware.calls))

    def test_positionabs_stop_convention_is_observed_not_claimed_physical(self):
        firmware = MockFirmware()
        rpc = ZoomRPC('mock',80,'mock','mock',session_factory=lambda:firmware)
        self.addCleanup(lambda:rpc.close(stop_motors=False))
        rpc.goto(.3); rpc.stop('zoom')
        motor = [c for c in firmware.calls if c['method'] in ('ptz.start','ptz.stop')]
        self.assertEqual(motor[0]['params']['code'], 'PositionABS')
        self.assertEqual(motor[1]['params']['code'], 'ZoomTele')
        self.assertEqual(PositionABSMapping.zoom_values[0], 6.4)
        with self.assertRaises(ValueError): PositionABSMapping().zoom_to_command(6.092)

    def test_cpu_only_face_does_not_hold_accelerator_lane(self):
        scheduler = InferenceScheduler()
        model = Mock(); model.session.get_providers.return_value = ['CPUExecutionProvider']
        self.assertEqual(inference_resource(model), 'cpu')
        detection_finished = threading.Event()
        def detect():
            with scheduler.use('detection'):
                detection_finished.set()
        with scheduler.use('face', resource=inference_resource(model)):
            worker = threading.Thread(target=detect); worker.start()
            self.assertTrue(detection_finished.wait(.5))
        worker.join(1)
        for providers in (['CUDAExecutionProvider','CPUExecutionProvider'], [], ['UnknownProvider']):
            model.session.get_providers.return_value = providers
            self.assertEqual(inference_resource(model), 'accelerator')
        self.assertEqual(inference_resource(object()), 'accelerator')

    def test_stale_body_feedback_stops_even_if_face_detector_is_unavailable(self):
        z, lens, _ = self.make(); self.start_face(z)
        self.wait(lambda: lens.move_to_zoom_position.called)
        self.body(z)
        z._motion_evidence = (time.monotonic()-1, 'session', 7, True)
        self.wait(lambda: lens.stop.called)
        self.assertEqual(z.last_stop_reason, 'STALE_TRACK')

    def test_stop_cannot_be_overwritten_by_new_framing_observation(self):
        z, lens, _ = self.make()
        with z._command_lock:
            self.start_face(z)
            z.request_stop('TARGET_LOST')
            z.submit_observation(correlation=dict(z._correlation, frame_id=2), frame_shape=(1000,1000),
                target={'bbox':(300,200,500,300)}, target_ratio=.25, tolerance=.03,
                target_name='FACE', observation_time=time.monotonic())
        self.wait(lambda: not z.busy and not z.control_pending)
        lens.move_to_zoom_position.assert_not_called()

    def test_wire_expiry_replans_instead_of_latching_error(self):
        z, lens, _ = self.make()
        def wire(position, speed):
            old = z._intent
            # Geometry becomes stale in camera queue/speed setup, after worker preflight.
            z._intent = replace(old, expiry=time.monotonic()-1)
            fresh = time.monotonic()
            z.submit_observation(correlation=dict(z._correlation, frame_id=2), frame_shape=(1000,1000),
                target={'bbox':(400,300,500,400)}, target_ratio=.25, tolerance=.03,
                target_name='FACE', observation_time=fresh)
            self.assertFalse(z.validate_rpc_intent({'zoom_intent_id':old.command_id}))
            lens.move_to_zoom_position.side_effect = None
            return False, 'intent rejected'
        lens.move_to_zoom_position.side_effect = wire
        self.start_face(z)
        self.wait(lambda: z.replanning_count == 1 and lens.command.called)
        self.assertNotEqual(z.phase, 'ERROR')
        self.assertEqual(z._intent.frame_id, 2)

    def test_zoom_summary_records_replan_cancel_and_first_valid_plan(self):
        from telemetry.zoom_trace import ZoomTrace
        recorder = ZoomTrace(enabled=False)
        base = dict(lock_session_id='session', locked_ns=1_000_000)
        for event, stamp, fields in [
            ('FIRST_VALID_ZOOM_PLAN', 3_000_000, {}),
            ('ZOOM_REQUEST_REPLANNED', 4_000_000, {}),
            ('ABSOLUTE_ZOOM_CANCELLED', 5_000_000, {'cancellation_reason':'TARGET_LOST'}),
            ('ZOOM_DISPATCH_VALIDATION',6_000_000, {'observation_age_ms':123}),
            ('ZOOM_STATUS',7_000_000, {'after_ack':True, 'actual_zoom_value':7.2}),
            ('ZOOM_TARGET_REACHED',8_000_000, {})]:
            recorder._aggregate(dict(base, event=event, monotonic_ns=stamp, **fields))
        row = recorder.sessions['session']
        self.assertEqual(row['zoom_request_replanning_count'], 1)
        self.assertEqual(row['absolute_zoom_cancellation_count'], 1)
        self.assertEqual(row['cancellation_reasons'], {'TARGET_LOST':1})
        self.assertEqual(row['observation_age_at_rpc_dispatch_ms'],123)
        self.assertEqual(row['motor_telemetry_after_ack'],7.2)
        self.assertEqual(row['zoom_target_reached_count'],1)
        self.assertEqual(row['_times']['FIRST_VALID_ZOOM_PLAN'],3_000_000)


if __name__ == '__main__': unittest.main()
