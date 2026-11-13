"""Ownership acceptance tests. Mock camera only: no HTTP, models or motor hardware."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from control.auto_zoom import AutoZoomController
from control.zoom_policy import ZoomPolicy
from control.orchestrator import ControlOrchestrator
from telemetry import latency

class ZoomOwnershipTests(unittest.TestCase):
    def make(self, telemetry=True, **options):
        lens = Mock()
        lens.has_zoom_telemetry = lens.supports_absolute_zoom = telemetry
        lens.speed = 100
        self.zoom = 2.; self.motion = 'Moving'; self.fault = False
        lens.telemetry.side_effect = lambda: dict(zoom=self.zoom, timestamp=time.monotonic(),
            zoom_motion_state=self.motion, fault=self.fault)
        lens.command.return_value = lens.move_to_zoom_position.return_value = lens.stop.return_value = (True, 'ACK')
        z = AutoZoomController(lens, wait_after_zoom=0, direction_cooldown=0,
            direction_hold=0, recovery_delay=0, **options)
        z.set_enabled(True)
        self.addCleanup(z.close)
        return z, lens

    def wait(self, predicate):
        deadline = time.monotonic()+1
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.002)
        self.assertTrue(predicate())

    def person(self, z, ratio):
        z._correlation = dict(frame_id=1, lock_session_id='s', target_track_id=7)
        return z.update((1000,1000), dict(bbox=(300,100,600,100+ratio*1000),track_id=7),
            .5,.04,target_name='PERSON',observation_time=time.monotonic())

    def recover(self, z):
        now=time.monotonic()
        return z.recover_missing(now,now-2,self.zoom)

    def test_small_person_without_face_starts_in(self):
        z,lens=self.make();self.person(z,.2)
        self.wait(lambda:lens.command.called)
        self.assertEqual(z.zoom_direction,1)
        self.assertEqual(z._intent.target_kind,'PERSON')

    def test_person_hold_range_emits_reason_without_start(self):
        z,lens=self.make()
        with patch.object(latency.profiler,'event',wraps=latency.profiler.event) as event:
            self.assertEqual(self.person(z,.5),'PERSON SIZE OK')
        lens.command.assert_not_called()
        row=next(c.kwargs for c in event.call_args_list if c.args[0]=='ZOOM_CONTROL_DECISION')
        required={'frame_id','lock_session_id','person_track_valid','person_ratio','face_detected',
            'face_latched','face_ratio','zoom_owner','zoom_state','zoom_direction_requested',
            'decision','decision_suppression_reason','observation_age_ms'}
        self.assertTrue(required<=row.keys());self.assertEqual(row['zoom_direction_requested'],0)

    def test_large_person_with_person_authority_starts_out(self):
        z,lens=self.make();self.person(z,.7)
        self.wait(lambda:lens.command.called);self.assertEqual(z.zoom_direction,-1)

    def test_large_person_at_wide_limit_does_not_zoom_in(self):
        z,lens=self.make();self.zoom=1.1
        self.assertEqual(self.person(z,.72),'ZOOM LIMIT')
        lens.command.assert_not_called();lens.move_to_zoom_position.assert_not_called()

    def test_face_latch_blocks_person_reframing_on_miss(self):
        z,lens=self.make();z.enter_face_mode('s')
        self.assertEqual(self.person(z,.7),'FACE_SESSION_HOLD');lens.command.assert_not_called()

    def test_face_owner_not_created_from_missing_face_geometry(self):
        p=ZoomPolicy()
        p.decide('s',dict(bbox=(300,100,600,300)),None,
            SimpleNamespace(target_type='FACE'),(1000,1000),0)
        self.assertFalse(p.face_mode)

    def test_invalid_or_stale_face_geometry_cannot_take_person_authority(self):
        ctl=SimpleNamespace(target_type='FACE');body=dict(bbox=(300,100,600,300))
        for face,received in [(dict(bbox=(0,0,0,0)),1),
                              (dict(bbox=(0,0,100,float('nan'))),1),
                              (dict(bbox=(100,100,200,200)),0)]:
            p=ZoomPolicy();p.decide('s',body,face,ctl,(1000,1000),1,face_received=received)
            self.assertFalse(p.face_mode)

    def test_sustained_head_clipping_starts_bounded_face_recovery(self):
        z,lens=self.make();p=ZoomPolicy()
        body=dict(bbox=(300,0,600,990),track_id=7);face=dict(bbox=(350,0,450,100))
        ctl=SimpleNamespace(target_type='FACE')
        p.decide('s',body,face,ctl,(1000,1000),0)
        for t in (.1,.5,1.2):d=p.decide('s',body,None,ctl,(1000,1000),t)
        self.assertEqual(d['action'],'HEAD_RECOVERY');self.assertTrue(p.face_mode)
        z._correlation=dict(lock_session_id='s',target_track_id=7)
        self.assertTrue(z.head_recovery(time.monotonic()))
        self.wait(lambda:lens.command.called)
        self.assertEqual(z._recovery_scope,'FACE');self.assertEqual(z.zoom_direction,-1)
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'PULSE_COMPLETE')

    def test_face_recovery_retains_motor_ownership_during_missing_face(self):
        z,lens=self.make()
        z._correlation=dict(lock_session_id='s',target_track_id=7)
        z.enter_face_mode('s')
        z.motion_feedback(dict(bbox=(300,0,600,990),track_id=7),(1000,1000),time.monotonic(),'s',7)
        z.head_recovery(time.monotonic());self.wait(lambda:lens.command.called)
        self.assertTrue(z.continue_face_recovery())
        self.assertFalse(z.target_recovery_active)
        self.assertTrue(z.recovery_active)
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'PULSE_COMPLETE')

    def test_face_recovery_stops_when_person_really_lost(self):
        z,lens=self.make();z._correlation=dict(lock_session_id='s',target_track_id=7)
        z.head_recovery(time.monotonic());self.wait(lambda:lens.command.called)
        z.motion_feedback(None,(1000,1000),time.monotonic(),'s',7)
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'TARGET_LOST')

    def test_recovery_without_lens_feedback_does_not_repeat_blindly(self):
        z,lens=self.make(telemetry=False,recovery_pulse=.04,recovery_interval=0)
        self.recover(z);self.wait(lambda:lens.command.called);self.wait(lambda:not z.busy)
        self.assertFalse(self.recover(z));self.assertEqual(lens.command.call_count,1)

    def test_missing_target_starts_recovery_and_persistent_loss_does_not_cancel(self):
        z,lens=self.make(recovery_pulse=.22)
        self.assertTrue(self.recover(z));self.wait(lambda:lens.command.called)
        for _ in range(4):
            z.motion_feedback(None,(1000,1000),time.monotonic(),None,None)
            z.request_stop('TARGET_LOST');z.stop(reason='TARGET_LOST')
            self.recover(z)
        self.assertTrue(z.busy);lens.stop.assert_not_called()
        self.wait(lambda:not z.busy)
        self.assertEqual(z.last_stop_reason,'PULSE_COMPLETE');self.assertEqual(lens.command.call_count,1)

    def test_target_recovery_does_not_require_missing_track_but_preserves_session_guard(self):
        z,lens=self.make();z._correlation=dict(lock_session_id='s',target_track_id=7)
        z.track_valid=lambda session,track:False
        z.motion_feedback(None,(1000,1000),time.monotonic(),'s',7)
        self.assertTrue(self.recover(z));self.wait(lambda:lens.command.called)
        time.sleep(.02);self.assertTrue(z.busy)
        z.session_valid=lambda session:False
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'STALE_SESSION')

    def test_valid_target_reacquisition_stops_recovery(self):
        z,lens=self.make();self.recover(z);self.wait(lambda:lens.command.called)
        z.motion_feedback(dict(bbox=(300,100,600,800),track_id=7),(1000,1000),time.monotonic(),'s',7)
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'BBOX_FOUND')

    def test_invalid_target_does_not_stop_recovery_as_reacquisition(self):
        z,lens=self.make();self.recover(z);self.wait(lambda:lens.command.called)
        z.motion_feedback(dict(bbox=(0,100,20,800),track_id=7),(1000,1000),time.monotonic(),'s',7)
        self.assertTrue(z.busy);lens.stop.assert_not_called()

    def test_absolute_brief_face_miss_keeps_same_move(self):
        z,lens=self.make();z._correlation=dict(lock_session_id='s',target_track_id=7)
        z.enter_face_mode('s')
        z.update((1000,1000),dict(bbox=(400,300,500,366)),.25,.03,
            target_name='FACE',observation_time=time.monotonic())
        self.wait(lambda:lens.move_to_zoom_position.called)
        z.motion_feedback(dict(bbox=(300,100,600,800),track_id=7),(1000,1000),time.monotonic(),'s',7)
        self.assertTrue(z.continue_absolute_face_miss())
        time.sleep(.02);self.assertTrue(z.busy);lens.stop.assert_not_called()

    def test_manual_override_cancels_owned_recovery(self):
        owner=ControlOrchestrator();z,lens=self.make(orchestrator=owner)
        self.recover(z);self.wait(lambda:lens.command.called);owner.manual=True
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'MANUAL_OVERRIDE')

    def test_real_fault_stops_recovery_and_blocks_restart(self):
        z,lens=self.make();self.recover(z);self.wait(lambda:lens.command.called)
        self.fault=True
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'MOTOR_FAULT')
        self.assertFalse(self.recover(z));self.assertEqual(lens.command.call_count,1)

    def test_wide_limit_stops_active_recovery(self):
        z,lens=self.make();self.recover(z);self.wait(lambda:lens.command.called)
        self.zoom=1.1
        self.wait(lambda:not z.busy);self.assertEqual(z.last_stop_reason,'ZOOM_LIMIT')
        self.assertFalse(self.recover(z))

    def test_recovery_waits_for_idle_and_progress_before_next_segment(self):
        z,lens=self.make(recovery_pulse=.04,recovery_interval=0)
        self.recover(z);self.wait(lambda:lens.command.called);self.wait(lambda:not z.busy)
        self.assertFalse(self.recover(z)) # Moving, even though STOP acknowledged
        self.motion='Idle'
        self.assertFalse(self.recover(z)) # no actual zoom change: latch no-progress
        self.zoom=1.8
        self.assertFalse(self.recover(z));self.assertEqual(lens.command.call_count,1)

    def test_progress_and_idle_allow_next_bounded_segment(self):
        z,lens=self.make(recovery_pulse=.04,recovery_interval=0)
        self.recover(z);self.wait(lambda:lens.command.called);self.wait(lambda:not z.busy)
        self.zoom=1.8;self.motion='Idle'
        self.assertTrue(self.recover(z));self.wait(lambda:lens.command.call_count==2)

    def test_stop_ack_does_not_emit_motor_idle_without_idle_telemetry(self):
        z,lens=self.make(recovery_pulse=.04)
        with patch.object(latency.profiler,'event',wraps=latency.profiler.event) as event:
            self.recover(z);self.wait(lambda:lens.command.called);self.wait(lambda:not z.busy)
            z._observe_motor_idle()
            self.assertNotIn('ZOOM_MOTOR_IDLE_OBSERVED',[c.args[0] for c in event.call_args_list])
            self.motion='Idle';z._observe_motor_idle()
            self.assertIn('ZOOM_MOTOR_IDLE_OBSERVED',[c.args[0] for c in event.call_args_list])
            summary=next(c.kwargs for c in event.call_args_list if c.args[0]=='ZOOM_ACTION_ENDED')
            self.assertIsNone(summary['motor_stopped'])
            self.assertEqual(summary['zoom_before'],2.)
            self.assertIn('stop_initiator',summary)

if __name__=='__main__':unittest.main()
