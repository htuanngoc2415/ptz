#!/usr/bin/env python3
"""Optical zoom motor control; target selection/filtering belongs to the state machine."""
from __future__ import annotations
from event_logging import event_print as print
from telemetry.events import emit
from telemetry import latency
from uuid import uuid4

import math
import copy
from collections import deque
from runtime.zoom_intent import ZoomIntent
from runtime.stop_retry import StopRetryBudget
import threading
import time
from typing import Optional

from camera import PTZ_ZOOM_IN, PTZ_ZOOM_OUT
from camera.zoom_prediction import ZoomEstimator


class AutoZoomController:
    def __init__(self, ptz, status_callback=None, zoom_callback=None,
                 step_large=0.8, step_medium=0.30, step_small=0.06,
                 wait_after_zoom=0.10, allow_zoom_out=True,
                 continuous_error=0.25, medium_error=0.10,
                 continuous_timeout=5.0, feedback_timeout=0.8,
                 stop_lead_time=0.15, short_error=0.04, short_pulse=0.12,
                 debug=False, debug_interval=0.15, max_observation_age=0.8,
                 direction_hold=0.4, direction_cooldown=0.4, reverse_error=0.1,
                 recovery_delay=1.25, recovery_pulse=.30, recovery_interval=.35,
                 recovery_max_duration=7.0, min_zoom=1.0, min_zoom_epsilon=.15, orchestrator=None, estimator=None, absolute_timeout=8.,
                 max_corrections=2, absolute_settle_timeout=3., control_mode="auto",
                 frame_edge_margin=.02, frame_edge_release_margin=.03,
                 pulse_recovery_without_telemetry=False, pulse_recovery_budget=1.2):
        self.pulse_recovery_without_telemetry = bool(pulse_recovery_without_telemetry)
        self.pulse_recovery_budget = float(pulse_recovery_budget)
        if not math.isfinite(self.pulse_recovery_budget) or not 0 < self.pulse_recovery_budget <= 7.:
            raise ValueError('Invalid pulse recovery budget')
        self.frame_edge_margin = float(frame_edge_margin)
        self.frame_edge_release_margin = float(frame_edge_release_margin)
        if not 0 <= self.frame_edge_margin < self.frame_edge_release_margin < .5:
            raise ValueError('Invalid frame edge stop/release margins')
        self._body_edge = None
        if control_mode not in ('auto','pulse'):
            raise ValueError('Zoom control mode must be auto or pulse')
        self.control_mode = control_mode
        self.estimator = estimator or ZoomEstimator()
        self.absolute_timeout = float(absolute_timeout)
        self.max_corrections = int(max_corrections)
        self.absolute_settle_timeout = float(absolute_settle_timeout)
        if (not all(math.isfinite(v) and v > 0 for v in (self.absolute_timeout, self.absolute_settle_timeout))
                or self.max_corrections < 0):
            raise ValueError('Invalid absolute zoom timeout/correction budget')
        self._face_session = None
        self.phase = 'IDLE'
        self._plan = None
        self._plan_key = None
        self._major_done = False
        self.correction_count = 0
        self._held_bbox = None
        self._focus_wait_started = None
        self.orchestrator = orchestrator
        self._closed = threading.Event()
        self._stop_budget = StopRetryBudget()
        self._motor_thread = None
        self.session_valid = lambda session: True
        self.track_valid = lambda session, track: True
        self._intent = None
        self._dispatch_rejection = None
        self._planning_evidence = None
        self._latest_spatial = None
        self._motion_evidence = None
        self._face_miss_since = None
        self.face_miss_grace = 1.0  # bounded short miss, matching face lost soft timeout
        self.replanning_count = 0
        self._first_plan_sessions = deque(maxlen=128)
        self._correlation = {}
        self._action_request_ns = None
        self._action_correlation = {}
        self._stability_ratios = []
        self._stability_frame = None
        self._stability_reported = True
        self.recovery_delay = float(recovery_delay)
        self.recovery_pulse = float(recovery_pulse)
        self.recovery_interval = float(recovery_interval)
        self.recovery_max_duration = float(recovery_max_duration)
        self.min_zoom = float(min_zoom)
        self.min_zoom_epsilon = float(min_zoom_epsilon)
        self._recovery_spent = 0.0
        self._recovery_scope = None
        self._recovery_feedback = None
        self._recovery_blocked = False
        self._motor_before = None
        self._idle_pending = None
        self.max_observation_age = float(max_observation_age)
        self.direction_hold = float(direction_hold)
        self.direction_cooldown = float(direction_cooldown)
        self.reverse_error = float(reverse_error)
        self._last_direction = 0
        self._last_start = float('-inf')
        self._last_publish = float('-inf')
        self.last_ptz_ms = 0.0
        self.last_control_ms = 0.0
        self._command_lock = threading.RLock()
        self._mail_lock = threading.Lock()
        self._mail_event = threading.Event()
        self._pending = None
        self._pending_stop = None
        self._dispatcher = None
        self._dispatching = False
        self.ptz = ptz
        self.ptz.zoom_intent_valid = self.validate_rpc_intent
        self.ptz.zoom_policy_valid = lambda fields: not (fields.get('zoom_domain') == 'PERSON'
            and self._face_session is not None and fields.get('lock_session_id') == self._face_session)
        self.status_callback = status_callback
        self.zoom_callback = zoom_callback
        # Retained constructor names for existing callers; large pulses are retired.
        self.step_large = float(step_large)
        self.step_medium = float(step_medium)
        self.step_small = float(step_small)
        self.wait_after_zoom = float(wait_after_zoom)
        self.allow_zoom_out = bool(allow_zoom_out)
        self.continuous_error = float(continuous_error)
        self.medium_error = float(medium_error)
        self.short_error = float(short_error)
        self.short_pulse = float(short_pulse)
        self.debug = bool(debug)
        self.debug_interval = max(0.1, float(debug_interval))
        self._log_origin = time.monotonic()
        self._last_log = float("-inf")
        self._last_observation = float("-inf")
        self.last_stop_reason = None
        self.continuous_timeout = float(continuous_timeout)
        self.feedback_timeout = float(feedback_timeout)
        self.stop_lead_time = float(stop_lead_time)
        if not (0 < self.short_error < self.medium_error < self.continuous_error
                and min(self.step_medium, self.step_small, self.short_pulse,
                        self.continuous_timeout, self.feedback_timeout) > 0
                and min(self.wait_after_zoom, self.stop_lead_time) >= 0):
            raise ValueError("Invalid auto zoom timing/error configuration")
        self.enabled = False
        self.busy = False
        self.zoom_motor_active = False
        self.zoom_direction = 0
        self.last_action = "IDLE"
        self.last_zoom_end_time = float("-inf")
        self._lock = threading.RLock()
        self._generation = 0
        self._cancel = threading.Event()
        self._cancel_reason = None
        self._cancel_initiator = None
        self._started = None
        self._last_feedback = 0.0
        self._mode = None
        self._target_name = None
        self._zoom_target = None
        self._previous_ratio = None
        self._previous_time = None
        self._fast_run = False

    def enter_face_mode(self, session):
        # A session latch also rejects person observations already in the mailbox.
        if session is not None:
            self._face_session = session

    def _observe_motor_idle(self):
        pending = self._idle_pending
        if pending is None or not self._has_telemetry():
            return
        status = self.ptz.telemetry()
        if status and status.get('timestamp', 0) > pending['stop_ack_time']:
            if status.get('zoom_motion_state') == 'Idle':
                fields = dict(pending)
                fields.pop('stop_ack_time')
                before = fields['zoom_before']
                fields.update(zoom_after=status.get('zoom'),
                    zoom_status_after='Idle', actual_zoom_delta=(status['zoom']-before
                    if before is not None else None), motor_stopped=True)
                latency.profiler.event('ZOOM_MOTOR_IDLE_OBSERVED', **fields)
                if self._idle_pending is pending:
                    self._idle_pending = None

    @staticmethod
    def _lens_fault(status):
        return bool(status and (status.get('fault') or
            status.get('zoom_motion_state') in ('Fault', 'Error', 'Alarm')))

    def head_recovery(self, observation_time, speed=None, publish_time=None):
        now = time.monotonic()
        if not self.enabled or not 0 <= now-observation_time <= self.max_observation_age:
            return False
        if self.busy or now-self.last_zoom_end_time < max(.5, self.wait_after_zoom):
            return False
        if self._has_telemetry():
            status = self.ptz.telemetry()
            if status is None or status['zoom'] <= self.min_zoom+self.min_zoom_epsilon or self._lens_fault(status):
                return False
            if self._recovery_feedback is not None:
                before, ended = self._recovery_feedback
                if status.get('timestamp', 0) <= ended or status.get('zoom_motion_state') != 'Idle':
                    return False
                self._recovery_feedback = None
                if status['zoom'] >= before-.01:
                    self._recovery_blocked = True
        if self._recovery_blocked:
            return False
        self._last_observation = observation_time
        self._last_publish = now
        self._recovery_scope = 'FACE'
        return self._start_zoom(PTZ_ZOOM_OUT,-1,min(.3,self.recovery_pulse),speed,
                                'HEAD CLIPPED: BOUNDED OUT',mode='recovery')

    def _set_phase(self, phase, reason=None):
        if phase != self.phase:
            self.phase = phase
            latency.profiler.event('ZOOM_STATE', motor_state=phase, reason=reason,
                                   correction_count=self.correction_count)

    def _has_telemetry(self):
        return (getattr(self.ptz, 'has_zoom_telemetry', False) is True or
                getattr(self.ptz, 'supports_absolute_zoom', False) is True)

    def heartbeat(self, observation_time, lock_session_id):
        """Fresh tracked-person evidence while the async face mailbox is empty.

        This only maintains the watchdog; it cannot plan/reissue a motor command.
        Actual face miss, target ambiguity, lost session or missing body still stops.
        """
        now = time.monotonic()
        if (lock_session_id != self._action_correlation.get('lock_session_id') or
                not self.session_valid(lock_session_id) or
                not 0 <= now-observation_time <= self.max_observation_age):
            return False
        with self._lock:
            if not self.busy:
                return False
            self._last_publish = now
        return True

    @latency.observed_result('zoom_dispatch_guard', reason_index=0)
    def intent_rejection(self, intent, now=None):
        now = time.monotonic() if now is None else now
        requires_track = not (intent.target_kind == 'RECOVERY' and self._recovery_scope == 'TARGET')
        flags = dict(
            stale_command_generation=(intent.generation != self._generation or not self.enabled
                                      or self._cancel.is_set()),
            stale_session=bool(intent.session_id and not self.session_valid(intent.session_id)),
            stale_track=bool(requires_track and intent.session_id and (
                not self.track_valid(intent.session_id, intent.track_id) or
                (self._motion_evidence is not None and (
                    not self._motion_evidence[3] or self._motion_evidence[1] != intent.session_id or
                    self._motion_evidence[2] != intent.track_id or
                    not 0 <= now-self._motion_evidence[0] <= self.max_observation_age)))),
            stale_person_zoom_policy=(intent.target_kind == 'PERSON' and intent.session_id is not None
                                      and intent.session_id == self._face_session),
            bbox_frame_edge=(self.zoom_direction > 0 and
                             self._body_at_frame_edge(intent.session_id, intent.track_id)),
            stale_observation=(not math.isfinite(intent.frame_timestamp)
                               or now < intent.frame_timestamp or now > intent.expiry))
        for key, reason in (('stale_command_generation', 'STALE_COMMAND_GENERATION'),
                            ('stale_session', 'STALE_SESSION'), ('stale_track', 'STALE_TRACK'),
                            ('bbox_frame_edge', 'BBOX_FRAME_EDGE'),
                            ('stale_person_zoom_policy', 'STALE_PERSON_ZOOM_POLICY'),
                            ('stale_observation', 'STALE_OBSERVATION')):
            if flags[key]:
                return reason, flags
        return None, flags

    def validate_rpc_intent(self, fields):
        """Called at the actual wire boundary, after camera queue/speed setup."""
        if not fields.get('zoom_intent_id'):
            return True  # manual commands retain their own session/policy guards
        intent = self._intent
        if intent is None or fields['zoom_intent_id'] != intent.command_id:
            latency.profiler.event('ZOOM_COMMAND_REJECTED',
                command_rejection_reason='STALE_COMMAND_GENERATION', stale_command_generation=True)
            return False
        now = time.monotonic()
        reason, flags = self.intent_rejection(intent, now)
        latency.profiler.event('ZOOM_DISPATCH_VALIDATION', metric='observation_age_at_rpc_dispatch_ms',
                               duration_ms=(now-intent.frame_timestamp)*1000, observation_age_ms=(now-intent.frame_timestamp)*1000,
                               frame_id=intent.frame_id, **flags)
        latency.profiler.event('ZOOM_PLANNING_TO_RPC', metric='zoom_planning_to_rpc_ms',
                               duration_ms=(now-intent.planned_at)*1000)
        if reason:
            self._dispatch_rejection = (intent.command_id, reason)
            latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason=reason, **flags)
        return reason is None

    def motion_feedback(self, person, frame_shape, received, session, track_id):
        """Fresh body evidence maintains motion safety, never changes an existing plan."""
        now = time.monotonic()
        self._observe_motor_idle()
        valid = (person is not None and session is not None and self.session_valid(session)
                 and self.track_valid(session, track_id) and 0 <= now-received <= self.max_observation_age)
        if valid:
            x1, y1, x2, y2 = person['bbox']
            h, w = frame_shape[:2]
            valid = (all(math.isfinite(v) for v in (x1,y1,x2,y2)) and x2>x1 and y2>y1
                     and 0 <= y1 < h and y2 > 0 and .05*w < (x1+x2)/2 < .95*w
                     and person.get('track_id', track_id) == track_id)
        with self._lock:
            previous = self._body_edge
            if valid and (previous is None or received >= previous[0]):
                held = previous is not None and previous[1:3] == (session, track_id) and previous[3]
                margin = self.frame_edge_release_margin if held else self.frame_edge_margin
                self._body_edge = (received, session, track_id,
                                   self._touches_frame_edge(person, frame_shape, margin))
            self._motion_evidence = (received, session, track_id, bool(valid))
            active = self._intent
            compatible = (active is not None and active.session_id == session and active.track_id == track_id)
            if valid and compatible and self.busy:
                self._last_publish = now
        if self.recovery_active and self._recovery_scope == 'TARGET':
            # Missing body is the condition recovery owns, not a cancellation.
            # Only a validated reacquisition can hand the motor back to tracking.
            if valid:
                self.request_stop(reason='BBOX_FOUND')
            return bool(valid)
        if self.busy and (not valid or not compatible):
            self.request_stop(reason='STALE_TRACK' if person is not None else 'TARGET_LOST')
        elif self.busy and self.zoom_direction > 0 and self._body_at_frame_edge(session, track_id):
            # Runs on every tracked frame, including frames without a face result.
            # request_stop also invalidates START waiting in the camera queue.
            self.request_stop(reason='BBOX_FRAME_EDGE')
        return bool(valid and (not self.busy or compatible))

    def continue_face_recovery(self):
        with self._lock:
            intent, evidence = self._intent, self._motion_evidence
            if not self.recovery_active or self._recovery_scope != 'FACE' or intent is None or evidence is None:
                return False
            received, session, track, valid = evidence
            return bool(valid and session == intent.session_id and track == intent.track_id
                and self.session_valid(session) and self.track_valid(session, track)
                and 0 <= time.monotonic()-received <= self.max_observation_age)

    def continue_absolute_face_miss(self):
        """A short detector miss is not a lost track; require fresh safe body feedback."""
        now = time.monotonic()
        with self._lock:
            intent, evidence = self._intent, self._motion_evidence
            if not self.absolute_face_active or intent is None or evidence is None:
                return False
            received, session, track, valid = evidence
            if (not valid or session != intent.session_id or track != intent.track_id
                    or not self.session_valid(session) or not self.track_valid(session, track)
                    or not 0 <= now-received <= self.max_observation_age):
                return False
            if self._face_miss_since is None:
                self._face_miss_since = now
            return now-self._face_miss_since <= self.face_miss_grace

    def _replan_latest(self, intent):
        with self._mail_lock:
            candidate = copy.deepcopy(self._latest_spatial)
        if not candidate:
            return False
        c = candidate.get('correlation', {})
        observed = candidate.get('observation_time', float('-inf'))
        if (c.get('lock_session_id') != intent.session_id or c.get('target_track_id') != intent.track_id
                or candidate.get('target_name', 'TARGET') != intent.target_kind
                or observed <= intent.frame_timestamp
                or not 0 <= time.monotonic()-observed <= self.max_observation_age
                or not self.session_valid(intent.session_id) or not self.track_valid(intent.session_id, intent.track_id)):
            return False
        c['replanning'] = True
        self.replanning_count += 1
        latency.profiler.count('zoom_request_replanning_count')
        latency.profiler.event('ZOOM_REQUEST_REPLANNED', previous_frame_id=intent.frame_id,
                               frame_id=c.get('frame_id'), replanning_count=self.replanning_count)
        self.submit_observation(**candidate)
        return True

    def observe_focus_wait(self, waiting):
        if waiting and self._focus_wait_started is None:
            self._focus_wait_started = latency.profiler.event('ZOOM_AF_WAIT_STARTED', af_state='BUSY')
        elif not waiting and self._focus_wait_started is not None:
            latency.profiler.elapsed('ZOOM_AF_WAIT_ENDED', self._focus_wait_started, 'af_waiting_duration_ms', af_state='RELEASED')
            self._focus_wait_started = None

    def _debug(self, message, force=False):
        now = time.monotonic()
        if self.debug and (force or now - self._last_log >= self.debug_interval):
            self._last_log = now
            print(f"[AUTO ZOOM t={now - self._log_origin:.3f}] {message}", flush=True)

    def _status(self, message):
        if self.status_callback is not None:
            try:
                self.status_callback(message)
            except Exception:
                pass

    def set_enabled(self, enabled, wait=True):
        with self._mail_lock:
            self._pending = None
        with self._lock:
            # Failed STOP must be resolved before permitting another START.
            if enabled and not self.enabled and not self.busy:
                self._recovery_spent = 0.0
            self.enabled = bool(enabled) and not (self.busy and not self.enabled)
            latency.profiler.event('ZOOM_TOGGLE', auto_zoom_enabled=self.enabled,
                requested_enabled=bool(enabled), max_observation_age_ms=self.max_observation_age*1000,
                feedback_timeout_ms=self.feedback_timeout*1000)
            self._previous_ratio = None
            self._previous_time = None
            if enabled and not self.busy:
                self._plan_key = None
                self._set_phase('IDLE')
        if not enabled:
            if wait:
                self.stop(reason="DISABLED")
            else:
                self.request_stop(reason="DISABLED")

    @staticmethod
    def _touches_frame_edge(target, frame_shape, margin):
        if target is None or frame_shape is None:
            return False
        x1, y1, x2, y2 = target['bbox']
        h, w = frame_shape[:2]
        return (x1 <= margin*w or x2 >= (1-margin)*w
                or y1 <= margin*h or y2 >= (1-margin)*h)

    def _body_at_frame_edge(self, session, track_id):
        evidence = self._body_edge
        return bool(evidence is not None and session is not None
                    and evidence[1:3] == (session, track_id) and evidence[3])

    @staticmethod
    def calculate_height_ratio(frame_shape, target) -> Optional[float]:
        if frame_shape is None or target is None or frame_shape[0] <= 0:
            return None
        bbox = target.get("bbox")
        if bbox is None:
            return None
        ratio = float(bbox[3] - bbox[1]) / frame_shape[0]
        return ratio if math.isfinite(ratio) and ratio > 0 else None

    def choose_step(self, current_ratio, target_ratio):
        relative_error = abs(target_ratio - current_ratio) / target_ratio
        if relative_error > self.medium_error:
            return self.step_medium
        if relative_error > self.short_error:
            return self.short_pulse
        return self.step_small

    def submit_observation(self, **observation):
        """Latest-only mailbox: inference never waits for camera HTTP requests."""
        if self._closed.is_set():
            return
        observation = copy.deepcopy(observation)
        observation.setdefault('publish_time', time.monotonic())
        observation['_queued_ns'] = time.perf_counter_ns()
        with self._mail_lock:
            if 'stop_reason' in observation:
                self._pending_stop = observation
            else:
                if self._pending is not None:
                    latency.profiler.count('dropped_zoom_observations')
                    latency.profiler.event('ZOOM_OBSERVATION_DROPPED',
                        **dict(self._pending.get('correlation', {}), reason='MAILBOX_REPLACED'))
                self._pending = observation
            if observation.get('target') is not None:
                self._latest_spatial = copy.deepcopy(observation)
            elif 'stop_reason' in observation:
                self._latest_spatial = None
            if self._dispatcher is None:
                self._dispatcher = threading.Thread(target=self._dispatch, name='zoom-supervisor', daemon=True)
                self._dispatcher.start()
        self._mail_event.set()

    @property
    def control_pending(self):
        with self._mail_lock:
            return self._pending_stop is not None or self._pending is not None or self._dispatching

    def request_stop(self, reason="REQUESTED"):
        with self._lock:
            if (reason == 'TARGET_LOST' and self.recovery_active
                    and self._recovery_scope == 'TARGET'):
                latency.profiler.event('ZOOM_STOP_SUPPRESSED', stop_initiator=threading.current_thread().name,
                    stop_reason=reason, decision_suppression_reason='TARGET_RECOVERY_OWNS_MOTION')
                return
            if self.busy:
                self._cancel_reason = reason
                self._cancel_initiator = threading.current_thread().name
                self._cancel.set()  # invalidate queued START before the dispatcher performs STOP
        self.submit_observation(stop_reason=reason)

    def _dispatch(self):
        while not self._closed.is_set():
            self._mail_event.wait()
            if self._closed.is_set():
                return
            self._mail_event.clear()
            with self._mail_lock:
                if self._pending_stop is not None:
                    observation, self._pending_stop = self._pending_stop, None
                    self._pending = None  # safety STOP cannot be overwritten by framing
                else:
                    observation, self._pending = self._pending, None
                self._dispatching = observation is not None
            if observation is None:
                continue
            queued_ns = observation.pop('_queued_ns', None)
            # A control-only STOP carries no replacement spatial/session evidence.
            # Keep the current binding so subsequent edge checks remain scoped.
            self._correlation = observation.pop('correlation',
                self._correlation if 'stop_reason' in observation else {})
            latency.context.set(self._correlation)
            latency.profiler.elapsed('ZOOM_OBSERVATION_DEQUEUED', queued_ns, 'zoom_observation_queue_wait_ms')
            session = self._correlation.get('lock_session_id')
            if session and not self.session_valid(session):
                latency.record_stale('ZOOM_SESSION_MISMATCH', category=(
                    'control_stop_session_mismatch' if 'stop_reason' in observation else 'ptz_observation'))
                self.stop(reason='STALE_SESSION')
                with self._mail_lock:
                    self._dispatching = False
                continue
            started = time.perf_counter()
            try:
                if 'stop_reason' in observation:
                    if observation['stop_reason'] in ('BBOX_FOUND', 'REID_NOT_CONFIRMED', 'TARGET_CHANGED'):
                        self._recovery_spent = 0.0
                        self._recovery_blocked = False
                        self._recovery_feedback = None
                    self.stop(reason=observation['stop_reason'])
                elif observation.pop('head_recovery', False):
                    self.head_recovery(**observation)
                elif observation.pop('recovery', False):
                    self.recover_missing(**observation)
                else:
                    self.update(**observation)
            except Exception as exc:
                self.set_enabled(False)
                self._status(f'ZOOM CONTROL ERROR: {exc}')
            finally:
                self.last_control_ms = (time.perf_counter()-started)*1000
                with self._mail_lock:
                    self._dispatching = False

    @property
    def absolute_face_active(self):
        with self._lock:
            return self.busy and self._mode == 'absolute' and self._target_name == 'FACE'

    @property
    def recovery_active(self):
        with self._lock:
            return self.busy and self._mode == 'recovery'

    @property
    def target_recovery_active(self):
        return self.recovery_active and self._recovery_scope == 'TARGET'

    @latency.measured('ZOOM_RECOVERY_DECISION', 'zoom_recovery_decision_ms')
    def recover_missing(self, observation_time, absent_since, zoom_level, speed=None, publish_time=None):
        self._recovery_hold_reason = 'ACTIVE_SEGMENT' if self.recovery_active else 'RECOVERY_NOT_READY'
        result = self._recover_missing(observation_time, absent_since, zoom_level, speed, publish_time)
        fields = dict(self._correlation)
        fields.update(frame_id=fields.get('frame_id'), lock_session_id=fields.get('lock_session_id'),
            person_track_valid=False, person_ratio=None, face_detected=False,
            face_latched=bool(self._face_session and self._face_session == fields.get('lock_session_id')),
            face_ratio=None, zoom_owner='TARGET_RECOVERY', zoom_state='TARGET_RECOVERY',
            zoom_direction_requested=-1, decision='REQUESTED' if result else 'CONTINUE' if self.recovery_active else 'HOLD',
            decision_suppression_reason=None if result else self._recovery_hold_reason,
            observation_age_ms=(time.monotonic()-observation_time)*1000)
        latency.profiler.event('ZOOM_CONTROL_DECISION', **fields)
        return result

    def _recover_missing(self, observation_time, absent_since, zoom_level, speed=None, publish_time=None):
        """Fresh no-bbox evidence permits bounded OUT only, never target zoom-in."""
        now = time.monotonic()
        self._observe_motor_idle()
        published = now if publish_time is None else publish_time
        visual_recovery = self.pulse_recovery_without_telemetry and not self._has_telemetry()
        budget = min(self.recovery_max_duration, self.pulse_recovery_budget) if visual_recovery else self.recovery_max_duration
        timestamps = (observation_time, absent_since, published)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in timestamps):
            reason = 'RECOVERY_INVALID_TIMESTAMP'
        elif not absent_since <= observation_time <= published <= now:
            reason = 'RECOVERY_TIMESTAMP_ORDER'
        elif now-observation_time > self.max_observation_age:
            reason = 'RECOVERY_OBSERVATION_TOO_OLD'
        elif now-published > self.feedback_timeout:
            reason = 'RECOVERY_PUBLISH_TOO_OLD'
        elif self._correlation.get('lock_session_id') and not self.session_valid(self._correlation['lock_session_id']):
            reason = 'RECOVERY_SESSION_MISMATCH'
        elif not visual_recovery and (not isinstance(zoom_level, (int, float)) or not math.isfinite(zoom_level)):
            reason = 'RECOVERY_INVALID_ZOOM_ESTIMATE'
        else:
            reason = None
        if reason:
            self._recovery_hold_reason = reason
            latency.profiler.count('zoom_recovery_rejected_'+reason)
            if (reason != getattr(self, '_last_recovery_reason', None)
                    or now-getattr(self, '_last_recovery_log', 0.) >= .5):
                self._last_recovery_reason, self._last_recovery_log = reason, now
                latency.profiler.event('ZOOM_RECOVERY_FRESHNESS_REJECTED', reason=reason,
                    observation_timestamp=observation_time, publish_timestamp=published,
                    absent_since=absent_since, now_timestamp=now,
                    max_observation_age_ms=self.max_observation_age*1000,
                    feedback_timeout_ms=self.feedback_timeout*1000)
            self.stop(reason=reason)
            return False
        if not self.enabled or observation_time <= self._last_observation:
            return False
        self._last_observation = observation_time
        self._last_publish = published
        if self.busy and not self.recovery_active:
            self.stop(reason='TARGET_LOST')
            return False
        if ((not visual_recovery and zoom_level <= self.min_zoom+self.min_zoom_epsilon)
                or self._recovery_spent >= budget):
            self._recovery_hold_reason = 'RECOVERY_BUDGET_EXHAUSTED' if self._recovery_spent >= budget else 'RECOVERY_WIDE_LIMIT_OR_BUDGET'
            self.stop(reason='RECOVERY_WIDE_LIMIT')
            return False
        if observation_time-absent_since < self.recovery_delay or self.busy:
            return False
        if observation_time < self.last_zoom_end_time+max(self.wait_after_zoom, self.recovery_interval):
            return False
        if self._last_direction > 0 and (now-self._last_start < self.direction_hold
                or now-self.last_zoom_end_time < self.direction_cooldown):
            return False
        if self._has_telemetry():
            telemetry = self.ptz.telemetry()
            if telemetry is None:
                return False
            zoom_level = telemetry['zoom']
            if zoom_level <= self.min_zoom+self.min_zoom_epsilon:
                return False
        if self._recovery_blocked:
            self._recovery_hold_reason = 'RECOVERY_NO_PROGRESS_OR_FAULT'
            latency.profiler.event('ZOOM_DECISION', decision='HOLD',
                decision_suppression_reason='RECOVERY_NO_PROGRESS_OR_FAULT', zoom_owner='TARGET_RECOVERY')
            return False
        previous = self._recovery_feedback
        if previous is not None and self._has_telemetry():
            before, ended = previous
            if telemetry.get('timestamp', 0) <= ended or telemetry.get('zoom_motion_state') != 'Idle':
                self._recovery_hold_reason = 'WAIT_RECOVERY_IDLE_FEEDBACK'
                return False  # STOP ACK alone cannot authorize another segment.
            self._recovery_feedback = None
            if telemetry['zoom'] >= before-.01:
                self._recovery_blocked = True
                self._recovery_hold_reason = 'RECOVERY_NO_MEASURED_PROGRESS'
                latency.profiler.event('ZOOM_DECISION', decision='HOLD',
                    decision_suppression_reason='RECOVERY_NO_MEASURED_PROGRESS', zoom_owner='TARGET_RECOVERY')
                return False
        if self._has_telemetry() and (self._lens_fault(telemetry) or getattr(self.ptz, '_motor_fault', False) is True):
            self._recovery_blocked = True
            return False
        self._recovery_scope = 'TARGET'
        duration = min(self.recovery_pulse, budget-self._recovery_spent)
        started = self._start_zoom(PTZ_ZOOM_OUT, -1, duration, speed,
                                  'RECOVERY NO BBOX -> ZOOM OUT', mode='recovery')
        if started:
            self._recovery_spent += duration
            latency.profiler.event('ZOOM_RECOVERY_BUDGET', strategy='PULSE_VISUAL' if visual_recovery else 'LENS_TELEMETRY',
                spent_seconds=self._recovery_spent, budget_seconds=budget, pulse_seconds=duration,
                lens_position=None if visual_recovery else zoom_level)
            self._debug(f'[RECOVERY] no_bbox={observation_time-absent_since:.2f}s '
                        f'zoom_estimate={zoom_level} pulse={duration*1000:.0f}ms', force=True)
        return started

    @latency.measured('ZOOM_DECISION_EVAL', 'zoom_decision_ms')
    def update(self, frame_shape, target, target_ratio, tolerance, speed=None,
               target_name="TARGET", filtered_ratio=None, min_ratio=None,
               max_ratio=None, observation_time=None, state_name=None, publish_time=None):
        self._observe_motor_idle()
        ratio = filtered_ratio if filtered_ratio is not None else self.calculate_height_ratio(frame_shape, target)
        lower = target_ratio-tolerance if min_ratio is None and target_ratio is not None else min_ratio
        upper = target_ratio+tolerance if max_ratio is None and target_ratio is not None else max_ratio
        direction = (1 if ratio < lower else -1 if ratio > upper else 0) if ratio is not None and lower is not None and upper is not None else 0
        previous_request = self._action_request_ns
        result = self._update(frame_shape, target, target_ratio, tolerance, speed, target_name,
            filtered_ratio, min_ratio, max_ratio, observation_time, state_name, publish_time)
        requested = self._action_request_ns != previous_request
        session = self._correlation.get('lock_session_id')
        fields = dict(self._correlation)
        fields.update(frame_id=fields.get('frame_id'), lock_session_id=session,
            person_track_valid=bool(target is not None) if target_name == 'PERSON' else fields.get('person_track_valid', False),
            person_ratio=ratio if target_name == 'PERSON' else fields.get('person_ratio'),
            face_detected=bool(target is not None) if target_name == 'FACE' else fields.get('face_visible', False),
            face_latched=bool(session and session == self._face_session),
            face_ratio=ratio if target_name == 'FACE' else fields.get('face_ratio'),
            zoom_owner='FACE' if session and session == self._face_session else target_name,
            zoom_state=state_name or self.phase, zoom_direction_requested=direction,
            decision='REQUESTED' if requested else 'CONTINUE' if self.busy else 'HOLD',
            decision_suppression_reason=None if requested else result,
            observation_age_ms=(time.monotonic()-observation_time)*1000 if observation_time is not None else 0.)
        lens = self.ptz.telemetry() if self._has_telemetry() else None
        fields.update(current_zoom_value=(lens or {}).get('zoom'),
            zoom_motion_state=(lens or {}).get('zoom_motion_state'),
            telemetry_status='available' if lens else 'unavailable',
            target_bbox=tuple(target['bbox']) if target is not None and target.get('bbox') is not None else None)
        latency.profiler.event('ZOOM_CONTROL_DECISION', **fields)
        return result

    def _update(self, frame_shape, target, target_ratio, tolerance, speed=None,
               target_name="TARGET", filtered_ratio=None, min_ratio=None,
               max_ratio=None, observation_time=None, state_name=None, publish_time=None):
        now = time.monotonic()
        if target_name == 'PERSON' and self._face_session is not None and self._face_session == self._correlation.get('lock_session_id'):
            latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason='PERSON_FRAMING_FORBIDDEN_IN_FACE_SESSION')
            return 'FACE_SESSION_HOLD'
        if not self.enabled:
            return "DISABLED"
        if self.recovery_active:
            self.stop(reason='BBOX_FOUND')
            return 'WAITING FOR POST_RECOVERY_FRAME'
        observed = now if observation_time is None else observation_time
        published = now if publish_time is None else publish_time
        if (not math.isfinite(observed) or not math.isfinite(published)
                or observed > published or published > now):
            self.stop(reason="INVALID_TIMESTAMP")
            return "INVALID TIMESTAMP"
        age, update_age = now-observed, now-published
        if age > self.max_observation_age or update_age > self.feedback_timeout:
            reason = 'STALE_OBSERVATION' if age > self.max_observation_age else 'WORKER_TIMEOUT'
            self._debug(f'[WATCHDOG] reason={reason} observation_age={age:.3f} '
                        f'update_age={update_age:.3f} max_age={self.max_observation_age:.3f} '
                        f'worker_timeout={self.feedback_timeout:.3f}')
            self.stop(reason=reason)
            return reason
        ratio = filtered_ratio if filtered_ratio is not None else self.calculate_height_ratio(frame_shape, target)
        if target is None or ratio is None or not math.isfinite(ratio) or ratio <= 0:
            self.stop(reason='FACE_LOST' if target_name == 'FACE' else 'TARGET_LOST')
            return 'WAITING FOR FRESH TARGET'
        session = self._correlation.get('lock_session_id')
        if session is None or self.session_valid(session):
            self._recovery_spent = 0.0
            self._recovery_blocked = False
            self._recovery_feedback = None
        if target_ratio is None or target_ratio <= 0:
            self.stop(reason='INVALID_TARGET')
            return 'INVALID TARGET RATIO'
        if observed <= self._last_observation and not self._correlation.get('replanning'):
            return self.last_action
        self._last_observation = observed
        self._last_publish = published
        if target_name == 'FACE':
            self._face_miss_since = None
        lower = target_ratio-tolerance if min_ratio is None else min_ratio
        upper = target_ratio+tolerance if max_ratio is None else max_ratio
        # Use raw geometry, not a smoothed size ratio. A small/off-centre bbox
        # can hit a side before reaching the desired height. Body bounds remain
        # authoritative even after framing switches to the face.
        edge = (self._touches_frame_edge(target, frame_shape,
                    max(.05, self.frame_edge_margin) if target_name == 'FACE' else self.frame_edge_margin)
                or self._body_at_frame_edge(self._correlation.get('lock_session_id'),
                                            self._correlation.get('target_track_id')))
        if edge and ((self.busy and self.zoom_direction > 0) or ratio < lower):
            self.stop(reason='BBOX_FRAME_EDGE')
            if self.busy and not self.enabled:
                return self.last_action  # preserve STOP failure and disabled state
            self.last_action = 'BBOX FRAME EDGE: HOLD ZOOM IN'
            self._status(self.last_action)
            return self.last_action
        key = (self._correlation.get('lock_session_id'), target_name)
        if key != self._plan_key and not self.busy:
            self._plan_key = key
            self._major_done = False
            self.correction_count = 0
            self._held_bbox = None
            self._set_phase('TARGET_LOCKED')
        if lower <= ratio <= upper:
            self.stop(reason='HOLD_ZONE')
            self._held_bbox = tuple(target['bbox'])
            latency.profiler.event('ZOOM_FEEDBACK', current_face_ratio=ratio if target_name=='FACE' else None,
                current_person_ratio=ratio if target_name!='FACE' else None, target_ratio=target_ratio,
                zoom_prediction_error=ratio-target_ratio, action='HOLD')
            if self.phase != 'ZOOM_SETTLING' or self._stability_reported:
                self._set_phase('ZOOM_HOLD')
            self.last_action = f'{target_name} SIZE OK'
            return self.last_action
        direction = 1 if ratio < lower else -1
        visual_camera = self._has_telemetry()
        relative_error = abs(target_ratio-ratio)/target_ratio
        # Brake earlier for faces: their final framing latch stops at 0.18,
        # below the nominal 0.25 control target.
        far = relative_error > (0.5 if target_name == 'FACE' else self.continuous_error)
        if self.busy:
            if direction != self.zoom_direction:
                self.stop(reason='DIRECTION_CHANGE')
            elif target_name != self._target_name:
                self.stop(reason='TARGET_KIND_CHANGED')
            elif self._zoom_target is None and visual_camera and self._fast_run and not far:
                self.stop(reason='SLOW_APPROACH')
            return self.last_action
        if self.phase == 'ZOOM_SETTLING':
            if not self._stability_reported:
                if now-self.last_zoom_end_time > self.absolute_settle_timeout:
                    self._set_phase('ERROR', 'VISUAL_SETTLING_TIMEOUT')
                    latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason='VISUAL_SETTLING_TIMEOUT')
                return 'WAIT_VISUAL_STABILITY'
            self._set_phase('ZOOM_CORRECTING')
            latency.profiler.event('ZOOM_FEEDBACK', current_face_ratio=ratio if target_name=='FACE' else None,
                current_person_ratio=ratio if target_name!='FACE' else None, target_ratio=target_ratio,
                zoom_prediction_error=ratio-target_ratio, correction_count=self.correction_count)
        if self.phase == 'ERROR':
            return 'ERROR: RESET TARGET OR AUTO ZOOM TO RETRY'
        # A new START requires an image captured after the previous STOP + settle.
        if observed < self.last_zoom_end_time+self.wait_after_zoom:
            return 'WAITING FOR POST_SETTLE_FRAME'
        reverse = self._last_direction and self._last_direction != direction
        outside_error = (lower-ratio if direction > 0 else ratio-upper)/target_ratio
        if reverse and (now-self._last_start < self.direction_hold
                        or now-self.last_zoom_end_time < self.direction_cooldown
                        or outside_error < self.reverse_error):
            return 'DIRECTION_HOLD'
        if direction < 0 and not self.allow_zoom_out:
            return f'{target_name} TOO LARGE'
        telemetry = None
        if self._has_telemetry():
            telemetry = self.ptz.telemetry()
            if telemetry is None:
                latency.profiler.event('ZOOM_COMMAND_DEFERRED', command_rejection_reason='LENS_TELEMETRY_UNAVAILABLE')
                return 'WAITING FOR LENS TELEMETRY'
            current = telemetry['zoom']
            calibration = self.estimator.calibration
            if calibration is not None and telemetry.get('camera_id') != calibration.camera_id:
                latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason='CALIBRATION_CAMERA_NOT_VERIFIED')
                return 'WAITING FOR VERIFIED CALIBRATION CAMERA'
            if ((direction < 0 and current <= self.min_zoom+self.min_zoom_epsilon)
                    or (direction > 0 and current >= 37.)):
                return 'ZOOM LIMIT'
        if target_name == 'FACE':
            speed = min(50 if far else 25, getattr(self.ptz, 'speed', 100) if speed is None else speed)
        self._zoom_target = None
        self._plan = None
        self._planning_evidence = (observed, tuple(target['bbox']), ratio,
                                   (telemetry or {}).get('timestamp'), now)
        latency.profiler.event('ZOOM_OBSERVATION_AT_PLANNING', metric='observation_age_at_planning_ms',
            duration_ms=(now-observed)*1000, frame_id=self._correlation.get('frame_id'),
            observation_age_ms=(now-observed)*1000,
            frame_telemetry_timestamp_difference_ms=((telemetry['timestamp']-observed)*1000
                                                    if telemetry and telemetry.get('timestamp') is not None else None))
        absolute_supported = getattr(self.ptz, 'supports_absolute_zoom', False) is True and self.control_mode == 'auto'
        if absolute_supported:
            if self._held_bbox is not None:
                old = self._held_bbox
                box = target['bbox']
                significant = (abs((box[0]+box[2]-old[0]-old[2])/2) > .15*frame_shape[1] or
                               abs((box[1]+box[3]-old[1]-old[3])/2) > .15*frame_shape[0] or
                               abs((box[3]-box[1])/max(1, old[3]-old[1])-1) > .25)
                if significant:
                    self._major_done = False
                    self.correction_count = 0
                    self._held_bbox = None
                    latency.profiler.event('ZOOM_TARGET_GEOMETRY_CHANGED')
            if self._major_done and self.correction_count >= self.max_corrections:
                self._set_phase('ZOOM_HOLD', 'CORRECTION_LIMIT')
                latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason='CORRECTION_LIMIT')
                return 'CORRECTION_LIMIT'
            self._set_phase('ZOOM_CORRECTING' if self._major_done else 'ZOOM_PLANNING')
            try:
                self._plan = self.estimator.estimate_target_zoom(current, ratio, target_ratio,
                    target_kind=target_name, correction=self._major_done)
            except ValueError as exc:
                self._set_phase('ERROR', str(exc))
                latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason=str(exc))
                return 'INVALID_ZOOM_PLAN'
            latency.profiler.event('ZOOM_POSITION_ESTIMATED', **self._plan.fields())
            latency.profiler.event('ZOOM_PLAN', **self._plan.fields(),
                current_zoom_position=telemetry.get('zoom_position_raw'),
                zoom_position_source=telemetry.get('zoom_position_source'),
                current_face_ratio=ratio if target_name=='FACE' else None,
                current_person_ratio=ratio if target_name!='FACE' else None,
                target_ratio=target_ratio, estimated_zoom_position=self._plan.requested_zoom_position,
                command_coordinate='PositionABS_normalized', correction_count=self.correction_count,
                af_state=telemetry.get('focus_motion_state'))
            if self._plan.requested_zoom_position is not None:
                self._zoom_target = self._plan.target_zoom_value
                if abs(self._zoom_target-current) <= .3:
                    self._set_phase('ZOOM_HOLD', 'POSITION_DEADBAND_OR_SATURATION')
                    return 'POSITION_DEADBAND_OR_SATURATION'
            else:
                latency.profiler.event('ZOOM_FALLBACK', command_rejection_reason='BELOW_VERIFIED_POSITIONABS_RANGE')
        elif self.control_mode == 'pulse':
            latency.profiler.event('ZOOM_FALLBACK', command_rejection_reason='PULSE_BASELINE_SELECTED', prediction_method='PULSE_FEEDBACK')
        elif self._has_telemetry():
            latency.profiler.event('ABSOLUTE_ZOOM_UNSUPPORTED',
                command_rejection_reason='CAPABILITY_UNSUPPORTED_OR_UNAVAILABLE', prediction_method='PULSE_FEEDBACK')
        self._fast_run = bool(visual_camera and far and self._zoom_target is None)
        self._target_name = target_name
        duration = (self.absolute_timeout if self._zoom_target is not None else
                    self.continuous_timeout if self._fast_run else self.choose_step(ratio, target_ratio))
        self._debug(f'[ZOOM] state={state_name or target_name} ratio={ratio:.3f} '
                    f'target={lower:.3f}-{upper:.3f} error={target_ratio-ratio:.3f} '
                    f'command={"ZOOM_IN" if direction > 0 else "ZOOM_OUT"} '
                    f'pulse={duration*1000:.0f}ms observation_age={age:.3f}')
        self._start_zoom(PTZ_ZOOM_IN if direction > 0 else PTZ_ZOOM_OUT,
                         direction, duration, speed,
                         f'VISUAL {target_name} {"IN" if direction > 0 else "OUT"}')
        return self.last_action

    def _start_zoom(self, command, direction, duration, speed, description, mode="pulse"):
        with self._lock:
            if not self.enabled or self.busy:
                return False
            if self.orchestrator is not None and not self.orchestrator.acquire("zoom"):
                latency.profiler.event('ZOOM_COMMAND_DEFERRED', command_rejection_reason=(
                    'MANUAL_OVERRIDE' if self.orchestrator.manual else
                    'OWNER_'+str(self.orchestrator.owner) if self.orchestrator.owner else 'LENS_SETTLING'))
                return False
            self._generation += 1
            generation = self._generation
            self._cancel = threading.Event()
            self._cancel_reason = None
            self._cancel_initiator = None
            self.busy = True
            self.zoom_direction = direction
            self._last_direction = direction
            self._last_start = time.monotonic()
            if mode == 'recovery':
                self._zoom_target = None
                self._plan = None
            self._mode = 'absolute' if self._zoom_target is not None else mode
            self.last_action = description
            if description == "LOST TARGET -> ZOOM OUT":
                self._last_feedback = time.monotonic()
            correlation = dict(self._correlation, command_id='cmd_'+uuid4().hex,
                               zoom_domain='RECOVERY' if mode == 'recovery' else self._target_name,
                               recovery_scope=self._recovery_scope if mode == 'recovery' else None)
            if self._has_telemetry():
                status = self.ptz.telemetry() or {}
                correlation['current_zoom_position'] = status.get('zoom_position_raw')
            evidence = self._planning_evidence if mode != 'recovery' else None
            observed, bbox, ratio, lens_stamp, planned_at = evidence or (
                self._last_observation, None, None, (status or {}).get('timestamp') if self._has_telemetry() else None,
                time.monotonic())
            intent = ZoomIntent(correlation['command_id'], correlation.get('frame_id'), observed, bbox, ratio,
                correlation.get('lock_session_id'), correlation.get('target_track_id'), lens_stamp,
                generation, observed+self.max_observation_age, correlation['zoom_domain'],
                self._plan.target_zoom_value if self._plan else self._zoom_target,
                self._plan.requested_zoom_position if self._plan else None, planned_at)
            self._intent = intent
            self._dispatch_rejection = None
            correlation.update(zoom_intent_id=intent.command_id, zoom_intent_generation=intent.generation,
                frame_timestamp=intent.frame_timestamp, zoom_intent_bbox=intent.bbox,
                zoom_intent_ratio=intent.ratio, lens_telemetry_timestamp=intent.lens_timestamp,
                zoom_intent_expiry=intent.expiry)
            self._face_miss_since = None
            self._motor_before = None
            session = intent.session_id
            valid_plan = self.intent_rejection(intent)[0] is None
            if valid_plan and session and session not in self._first_plan_sessions:
                self._first_plan_sessions.append(session)
                latency.profiler.elapsed('FIRST_VALID_ZOOM_PLAN', correlation.get('locked_ns'), 'lock_to_first_valid_zoom_plan_ms', **correlation)
            self._action_request_ns = latency.profiler.event('ZOOM_REQUEST_CREATED', **correlation,
                                   zoom_position_requested=self._plan.requested_zoom_position if self._plan else None,
                                   target_zoom_value=self._zoom_target,
                                   command_kind='absolute' if self._zoom_target is not None else 'pulse',
                                   command_type='PositionABS' if self._zoom_target is not None else 'ZoomTele' if direction > 0 else 'ZoomWide',
                                   zoom_direction=direction, zoom_pulse_duration_ms=duration*1000)
            self._action_correlation = correlation
            latency.profiler.elapsed('LOCK_TO_ZOOM_REQUEST', correlation.get('locked_ns'), 'lock_to_zoom_request_ms', **correlation)
            latency.profiler.event('RECOVERY_ZOOM_STARTED' if mode == 'recovery' else
                'FACE_ZOOM_STARTED' if self._target_name == 'FACE' else 'PERSON_ZOOM_STARTED', **correlation)
            latency.profiler.event('ZOOM_ACTION_ACCEPTED', **correlation,
                                   correction_count=self.correction_count,
                                   correction=bool(self._plan and self._plan.correction))
            self._set_phase('ZOOM_MOVING')
            self._stability_ratios = []
            self._stability_reported = False
            self._motor_thread = threading.Thread(target=self._zoom_worker,
                             args=(generation, self._cancel, command, duration, speed, correlation, intent),
                             name='zoom-watchdog', daemon=True)
            self._motor_thread.start()
        return True

    def _zoom_worker(self, generation, cancel, command, duration, speed, correlation=None, intent=None):
        latency.context.set(correlation or {})
        error = None
        position_before = None
        movement_started_ns = None
        last_status_stamp = None
        stable_positions = 0
        acknowledged_at = None
        # Serialize I/O separately; never hold the shared state lock over HTTP.
        with self._command_lock:
            with self._lock:
                session = (correlation or {}).get('lock_session_id')
                reason, flags = self.intent_rejection(intent)
                if reason:
                    latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason=reason,
                        observation_age_ms=(time.monotonic()-intent.frame_timestamp)*1000, **flags)
                    if generation == self._generation:
                        self.busy = False
                        self._set_phase('IDLE', reason)
                        if self.orchestrator is not None:
                            self.orchestrator.release("zoom")
                        if reason == 'STALE_OBSERVATION':
                            self._replan_latest(intent)
                    return
                self._started = time.monotonic()
                self.zoom_motor_active = True
            started = time.perf_counter()
            try:
                latency.profiler.event('ZOOM_WORKER_DISPATCH', metric='zoom_planning_dispatch_latency_ms',
                    duration_ms=(time.monotonic()-intent.planned_at)*1000,
                    observation_age_ms=(time.monotonic()-intent.frame_timestamp)*1000,
                    planning_dispatch_latency_ms=(time.monotonic()-intent.planned_at)*1000)
                latency.profiler.event('ZOOM_STARTED', evidence='command_dispatch')
                emit('ZOOM_START', direction=self.zoom_direction, duration=duration)
                print(f'[ZOOM] direction={self.zoom_direction} target={self._zoom_target} timeout={duration:.3f}s')
                if self._has_telemetry() and self.ptz.telemetry() is None:
                    raise RuntimeError('Lens telemetry is stale')
                if self._has_telemetry():
                    self._motor_before = dict(self.ptz.telemetry())
                    if self._lens_fault(self._motor_before) or getattr(self.ptz, '_motor_fault', False) is True:
                        raise RuntimeError('Motor fault before START')
                    position_before = self._motor_before['zoom']
                latency.profiler.event('ZOOM_POSITION_BEFORE', zoom_position_before=position_before)
                if self._zoom_target is not None:
                    latency.profiler.count('absolute_zoom_command_attempt_count')
                    success, response = self.ptz.move_to_zoom_position(intent.command_position, speed)
                else:
                    success, response = self.ptz.command(command, speed)
                if success:
                    acknowledged_at = time.monotonic()
                    if intent.command_position is not None:
                        with self._lock:
                            if generation == self._generation:
                                if self._major_done:
                                    self.correction_count += 1
                                self._major_done = True
                    latency.profiler.event('ZOOM_COMMAND_ACK', command_kind=self._mode)
                    latency.profiler.event('ZOOM_RPC_ACK', command_kind=self._mode)
                if not success:
                    error = f'ZOOM START FAILED: {response}'
            except Exception as exc:
                error = f'ZOOM START ERROR: {exc}'
            self.last_ptz_ms = (time.perf_counter()-started)*1000
        if error:
            rejected = self._dispatch_rejection
            if rejected and rejected[0] == intent.command_id:
                # Wire guard proved START was not sent; do not latch a motor fault for stale geometry.
                with self._lock:
                    if generation != self._generation:
                        return
                    self.busy = self.zoom_motor_active = False
                    self._intent = None
                    self._set_phase('IDLE', rejected[1])
                    if self.orchestrator is not None:
                        self.orchestrator.release('zoom')
                if rejected[1] == 'STALE_OBSERVATION':
                    self._replan_latest(intent)
                return
            self.stop(generation, reason='START_FAILED')
            self._status(error)
        else:
            reason = 'CANCELLED'
            while not cancel.wait(0.01):
                with self._lock:
                    if generation != self._generation:
                        return
                    now = time.monotonic()
                    if self.orchestrator is not None and self.orchestrator.manual:
                        reason = 'MANUAL_OVERRIDE'
                        break
                    if session and not self.session_valid(session):
                        reason = 'STALE_SESSION'
                        break
                    requires_track = not (intent.target_kind == 'RECOVERY' and self._recovery_scope == 'TARGET')
                    if requires_track and session and not self.track_valid(session, intent.track_id):
                        reason = 'STALE_TRACK'
                        break
                    evidence = self._motion_evidence
                    if requires_track and evidence is not None and session:
                        received, tracked_session, track, valid = evidence
                        if (not valid or tracked_session != session or track != intent.track_id
                                or not 0 <= now-received <= self.max_observation_age):
                            reason = 'STALE_TRACK'
                            break
                    if self._face_miss_since is not None and now-self._face_miss_since > self.face_miss_grace:
                        reason = 'FACE_MISS_SAFETY_TIMEOUT'
                        break
                    if self._has_telemetry():
                        telemetry = self.ptz.telemetry()
                        if telemetry is None:
                            reason = 'STALE_LENS_TELEMETRY'
                            break
                        if self._lens_fault(telemetry) or getattr(self.ptz, '_motor_fault', False) is True:
                            reason = 'MOTOR_FAULT'
                            break
                        z = telemetry['zoom']
                        moving_state = telemetry.get('zoom_motion_state') == 'Moving'
                        intermediate_change = (position_before is not None and abs(z-position_before) > .01
                            and (self._zoom_target is None or abs(z-self._zoom_target) > .3))
                        if movement_started_ns is None and (moving_state or intermediate_change):
                            movement_started_ns = latency.profiler.event('ZOOM_MOVING',
                                evidence='measured_motion_state' if moving_state else 'estimated_position_change',
                                actual_zoom_value=z)
                        if self._zoom_target is not None and telemetry.get('timestamp') != last_status_stamp:
                            last_status_stamp = telemetry.get('timestamp')
                            motion = telemetry.get('zoom_motion_state')
                            near = abs(z-self._zoom_target) <= .3
                            post_ack = telemetry.get('timestamp', 0) > (acknowledged_at or now)
                            stable_positions = stable_positions+1 if near and post_ack else 0
                            latency.profiler.event('ZOOM_STATUS', after_ack=post_ack, actual_zoom_position=telemetry.get('zoom_position_raw'),
                                actual_zoom_value=z, motor_state=motion, af_state=telemetry.get('focus_motion_state'),
                                zoom_value_error=z-self._zoom_target, zoom_error=None,
                                actual_position_coordinate='PTZZoomHD', command_coordinate='PositionABS_normalized')
                            completed = near and post_ack and (motion == 'Idle' or (motion is None and stable_positions >= 3))
                            if completed:
                                evidence = 'measured_idle_and_target' if motion == 'Idle' else 'estimated_position_plateau'
                                latency.profiler.event('ZOOM_MOVEMENT_COMPLETE', evidence=evidence)
                                latency.profiler.event('ZOOM_TARGET_REACHED', zoom_position_after=telemetry.get('zoom_position_raw'),
                                    actual_zoom_value=z, target_zoom_value=self._zoom_target, evidence=evidence)
                                latency.profiler.elapsed('ZOOM_MOTOR_DURATION', movement_started_ns,
                                    'zoom_motor_duration_ms', evidence='estimated_from_telemetry_observations')
                                latency.profiler.elapsed('ZOOM_COMPLETED', self._action_request_ns,
                                    'zoom_total_ms', evidence=evidence, zoom_value_error=z-self._zoom_target)
                                if motion == 'Idle':
                                    self.zoom_motor_active = False  # no redundant STOP for an observed completed absolute move
                                reason = 'POSITION_REACHED'
                                break
                        if ((self.zoom_direction > 0 and z >= 37.) or
                                (self.zoom_direction < 0 and z <= self.min_zoom+self.min_zoom_epsilon)):
                            reason = 'ZOOM_LIMIT'
                            break
                    if now-self._started >= duration:
                        reason = 'ABSOLUTE_TIMEOUT' if self._zoom_target is not None else 'PULSE_COMPLETE'
                        break
                    if now-self._last_publish >= self.feedback_timeout:
                        reason = 'WORKER_TIMEOUT'
                        self._debug(f'[WATCHDOG] reason={reason} update_age={now-self._last_publish:.3f} '
                                    f'threshold={self.feedback_timeout:.3f}', force=True)
                        break
            if cancel.is_set():
                reason = self._cancel_reason or reason
            self.stop(generation, reason=reason)
        while True:
            with self._lock:
                if generation != self._generation or not self.busy:
                    return
            delay = (.5 if self._stop_budget.exhausted else
                     max(.1, self._stop_budget.next_at-time.monotonic()))
            if self._closed.wait(delay):
                return  # shutdown performs a final bounded STOP attempt
            self.stop(generation)

    def stop(self, generation=None, reason="REQUESTED"):
        with latency.correlated(**self._action_correlation):
            return self._stop(generation, reason)

    def _stop(self, generation=None, reason="REQUESTED"):
        estimate = error = None
        with self._command_lock:
            with self._lock:
                if generation is not None and generation != self._generation:
                    return
                if not self.busy:
                    return
                if reason == 'TARGET_LOST' and self._mode == 'recovery' and self._recovery_scope == 'TARGET':
                    return
                self._cancel.set()
                self.last_stop_reason = reason
                initiator = self._cancel_initiator or threading.current_thread().name
                active = self.zoom_motor_active
                if (active and reason != 'APPLICATION_CLOSE' and not self._stop_budget.ready()
                        and getattr(self.ptz, 'motor_state', None) != 'READY'):
                    return  # retry exhaustion retains busy/ownership without log spam
                was_absolute = self._mode == 'absolute'
                started, direction = self._started, self.zoom_direction
            if was_absolute and active and reason != 'POSITION_REACHED':
                latency.profiler.count('absolute_zoom_cancellation_count')
                latency.profiler.event('ABSOLUTE_ZOOM_CANCELLED', cancellation_reason=reason, motor_stopped=None)
            emit('ZOOM_STOP', reason=reason)
            latency.profiler.event('ZOOM_STOP_REQUESTED', decision_reason=reason, command_type='STOP', motor_stopped=None,
                stop_initiator=initiator, stop_reason=reason)
            print(f'[ZOOM] STOP_REQUEST reason={reason}')
            if active:
                begin = time.perf_counter()
                try:
                    with latency.correlated(decision_reason=reason, stop_initiator=initiator, stop_reason=reason):
                        success, response = self.ptz.stop()
                except Exception as exc:
                    success, response = False, str(exc)
                self.last_ptz_ms = (time.perf_counter()-begin)*1000
                if not success:
                    self._stop_budget.failed()
                    error = f'STOP FAILED; AUTO ZOOM DISABLED: {response}'
                else:
                    self._stop_budget.reset()
                    estimate = (direction, time.monotonic()-started)
            before = self._motor_before or {}
            after = self.ptz.telemetry() if self._has_telemetry() else None
            ended = time.monotonic()
            after_valid = bool(after and after.get('timestamp', 0) > (started or ended))
            fields = dict(self._action_correlation, zoom_before=before.get('zoom'),
                zoom_after=after.get('zoom') if after_valid else None,
                zoom_status_before=before.get('zoom_motion_state'),
                zoom_status_after=after.get('zoom_motion_state') if after_valid else None,
                actual_zoom_delta=(after['zoom']-before['zoom'] if after_valid and 'zoom' in before else None),
                command_duration_ms=(ended-started)*1000 if started is not None else None,
                stop_initiator=initiator, stop_reason=reason,
                telemetry_status='available' if after_valid else 'unavailable', motor_stopped=None)
            latency.profiler.event('ZOOM_ACTION_ENDED', **fields)
            if not error:
                self._idle_pending = dict(fields, stop_ack_time=ended)
                if self._mode == 'recovery':
                    self._recovery_feedback = (before.get('zoom'), ended) if 'zoom' in before else None
                    if reason in ('MOTOR_FAULT', 'START_FAILED', 'STALE_LENS_TELEMETRY') or (not self._has_telemetry() and not self.pulse_recovery_without_telemetry):
                        self._recovery_blocked = True
            with self._lock:
                if error:
                    self.enabled = False
                    self.last_action = error
                else:
                    self._generation += 1
                    self.busy = self.zoom_motor_active = False
                    self._intent = None
                    self._face_miss_since = None
                    self.zoom_direction = 0
                    self._started = None
                    self._zoom_target = None
                    self.last_zoom_end_time = time.monotonic()
                    self.last_action = 'STOPPED'
                    if reason in ('START_FAILED', 'ABSOLUTE_TIMEOUT', 'STALE_LENS_TELEMETRY', 'MOTOR_FAULT'):
                        self._set_phase('ERROR', reason)
                    elif was_absolute and reason in ('POSITION_REACHED', 'HOLD_ZONE', 'FACE_FRAMING_HOLD', 'PERSON_FRAMING_HOLD'):
                        self._set_phase('ZOOM_SETTLING', reason)
                    else:
                        self._set_phase('IDLE', reason)
                    if self.orchestrator is not None:
                        self.orchestrator.release('zoom', self.wait_after_zoom)
        if error:
            self._status(error)
        if estimate is not None and self.zoom_callback is not None:
            try:
                self.zoom_callback(*estimate)
            except Exception:
                pass

    def zoom_out_step(self, duration=0.30, speed=None):
        if time.monotonic() - self.last_zoom_end_time < self.wait_after_zoom:
            return False
        return self._start_zoom(PTZ_ZOOM_OUT, -1, duration, speed,
                                "LOST TARGET -> ZOOM OUT")

    def close(self, timeout=5.):
        timeout = 5. if timeout is None else max(0., timeout)
        deadline = time.monotonic()+timeout
        self.observe_focus_wait(False)
        self.enabled = False
        self._closed.set()
        self._cancel.set()
        self._mail_event.set()
        self.stop(reason='APPLICATION_CLOSE')
        for worker in (self._dispatcher, self._motor_thread):
            if worker is not None and worker is not threading.current_thread():
                worker.join(max(0., deadline-time.monotonic()))
        return not any(w is not None and w.is_alive() for w in (self._dispatcher, self._motor_thread))

    def observe_stability(self, ratio, frame_id, received, target_kind=None):
        """Visual size stability is an estimate, never a motor-stopped assertion."""
        with self._lock:
            if target_kind is not None and target_kind != self._target_name:
                return
            if (self._stability_reported or self.busy or self._action_request_ns is None
                    or frame_id == self._stability_frame or ratio is None
                    or received < self.last_zoom_end_time+self.wait_after_zoom):
                return
            self._stability_frame = frame_id
            self._stability_ratios.append(float(ratio))
            self._stability_ratios = self._stability_ratios[-3:]
            if len(self._stability_ratios) == 3:
                low, high = min(self._stability_ratios), max(self._stability_ratios)
                if low > 0 and (high-low)/low <= .02:
                    self._stability_reported = True
                    with latency.correlated(**dict(self._action_correlation, frame_id=frame_id)):
                        latency.profiler.elapsed('ZOOM_VISUALLY_STABLE', self._action_request_ns,
                            'zoom_visual_stabilization_ms', evidence='estimated_bbox_ratio',
                            face_or_person_ratio_after=ratio, motor_stopped='unavailable')
