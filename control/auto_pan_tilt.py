"""Fresh, identity-authorized body centering using short serialized motor pulses."""
import math
import threading
import time
from telemetry import latency
from uuid import uuid4
from .pan_policy import plan_pan
from runtime.stop_retry import StopRetryBudget


class AutoPanTiltController:
    def __init__(self, ptz, orchestrator, cfg, status=lambda message: None):
        self.ptz, self.orchestrator, self.cfg, self.status = ptz, orchestrator, cfg, status
        if getattr(cfg, 'AUTO_PAN_TILT_ADAPTIVE', False):
            if not (0 < cfg.AUTO_PAN_TILT_PULSE <= cfg.AUTO_PAN_TILT_MAX_PULSE <= .5
                    and cfg.AUTO_PAN_TILT_DEADZONE < cfg.AUTO_PAN_TILT_FAR_ERROR <= .5):
                raise ValueError('Invalid adaptive pan/tilt pulse limits')
        self.enabled = bool(cfg.AUTO_PAN_TILT_ENABLED and getattr(ptz, 'supports_pan_tilt', False))
        self.session_valid = lambda session, track: False
        self._condition = threading.Condition(threading.RLock())
        self._latest = None
        self._closed = False
        self._axis = None
        self._ready_after = 0.
        self._fault = False
        self._stop_budget = StopRetryBudget()
        self.shutdown_unconfirmed = False
        self.busy = False
        self._active_fields = {}
        self._last_stop = 0.
        self._log_key = None
        self._log_time = 0.

        self.ptz.pan_tilt_intent_valid = self.validate_intent
        self._thread = threading.Thread(target=self._run, name='auto-pan-tilt', daemon=True)
        latency.profiler.event('PAN_TILT_CONFIG', auto_pan_enabled=self.enabled,
            pulse_ms=cfg.AUTO_PAN_TILT_PULSE*1000, settle_ms=cfg.AUTO_PAN_TILT_SETTLE*1000,
            max_speed=cfg.AUTO_PAN_TILT_MAX_SPEED, deadzone=cfg.AUTO_PAN_TILT_DEADZONE,
            release=cfg.AUTO_PAN_TILT_RELEASE,
            adaptive=getattr(cfg, 'AUTO_PAN_TILT_ADAPTIVE', False),
            max_pulse_ms=getattr(cfg, 'AUTO_PAN_TILT_MAX_PULSE', cfg.AUTO_PAN_TILT_PULSE)*1000,
            max_observation_age_ms=cfg.MAX_CONTROL_OBSERVATION_AGE*1000)
        self._thread.start()

    def set_enabled(self, enabled, reason="USER_TOGGLE"):
        with self._condition:
            self.enabled = bool(enabled and getattr(self.ptz, 'supports_pan_tilt', False) and not self._fault)
            latency.profiler.event('PAN_TILT_TOGGLE', auto_pan_enabled=self.enabled,
                                   requested_enabled=bool(enabled), decision_reason=reason)
            self._latest = None
            self._axis = None
            self._condition.notify_all()

    def cancel(self):
        with self._condition:
            self._latest = None
            self._axis = None
            self._condition.notify_all()

    def _direction(self, box, shape):
        plan = plan_pan(box, shape, self.cfg, previous_axis=self._axis)
        self._axis = plan.axis
        return (plan.direction, plan.speed) if plan.action == 'MOVE' else None

    def _pulse(self, box, shape):
        return plan_pan(box, shape, self.cfg, previous_axis=self._axis).duration

    def _decision(self, reason, fields):
        # Count every evaluation; emit at most twice a second per stable decision.
        latency.profiler.count('pan_tilt_decisions_'+reason)
        now = time.monotonic()
        key = (reason, fields.get('command_direction'))
        if key != self._log_key or now-self._log_time >= .5:
            self._log_key, self._log_time = key, now
            latency.profiler.event('PAN_TILT_DECISION', **dict(fields,
                decision_reason=reason, auto_pan_enabled=self.enabled,
                motor_owner=self.orchestrator.owner,
                cooldown_remaining_ms=max(0., self._ready_after-now)*1000))

    def submit(self, person, shape, received, session, track, metadata=None):
        with self._condition:
            now = time.monotonic()
            box = person.get('bbox') if person else None
            fields = dict(metadata or {}, lock_session_id=session, target_track_id=track,
                target_bbox=box, observation_age_ms=(now-received)*1000,
                command_direction=None, command_speed=None, command_duration_ms=None,
                command_status='GATED', command_id=None, stop_reason=None,
                target_center=None, normalized_error_x=None, normalized_error_y=None)
            if not self.enabled or self._closed or self._fault:
                reason = 'AUTO_PAN_DISABLED'
            elif self.orchestrator.manual:
                reason = 'MANUAL_OVERRIDE'
            elif not session or not self.session_valid(session, track):
                reason = 'TARGET_UNCERTAIN'
            elif (person is None or box is None or len(box) != 4
                    or not all(math.isfinite(v) for v in box)
                    or shape[0] <= 0 or shape[1] <= 0
                    or box[2] <= box[0] or box[3] <= box[1]
                    or box[2] <= 0 or box[3] <= 0 or box[0] >= shape[1] or box[1] >= shape[0]):
                reason = 'NO_VALID_TARGET'
            elif not math.isfinite(received) or not 0 <= now-received <= self.cfg.MAX_CONTROL_OBSERVATION_AGE:
                reason = 'STALE_OBSERVATION'
            else:
                reason = None
            if reason:
                self._decision(reason, fields)
                self.cancel()
                return False
            if self._latest and received <= self._latest[0]:
                self._decision('OUT_OF_ORDER_OBSERVATION', fields)
                return self._latest[3] is not None
            direction = self._direction(box, shape)
            if direction:
                h,w = shape[:2]
                center = ((box[0]+box[2])/2, (box[1]+box[3])/2)
                duration = self._pulse(box, shape)
                fields.update(target_center=center, normalized_error_x=center[0]/w-.5,
                    normalized_error_y=center[1]/h-.5, command_direction=direction[0],
                    command_speed=direction[1], command_duration_ms=duration*1000,
                    command_status='READY')
                # Keep tuple layout used by wire validation; plan fields are immutable copies.
                self._latest = (received, session, track, direction, duration, fields)
                reason = ('COOLDOWN' if now < self._ready_after else
                          'MOTOR_BUSY' if self.orchestrator.blocked() else 'TARGET_LOCKED')
            else:
                self._latest = None
                reason = 'DEADZONE'
            self._decision(reason, fields)
            self._condition.notify_all()
            return direction is not None

    def validate_intent(self, fields):
        with self._condition:
            item = self._latest
            now = time.monotonic()
            return bool(self.enabled and not self._closed and not self._fault and item
                and getattr(self.ptz, '_motor_fault', False) is not True
                and not self.orchestrator.manual
                and self.orchestrator.owner == 'pan_tilt'
                and 0 <= now-item[0] <= self.cfg.MAX_CONTROL_OBSERVATION_AGE
                and 0 <= now-fields.get('observation_time', float('-inf')) <= self.cfg.MAX_CONTROL_OBSERVATION_AGE
                and item[1] == fields.get('lock_session_id')
                and item[2] == fields.get('target_track_id')
                and item[3][0] == fields.get('pan_tilt_command')
                and self.session_valid(item[1], item[2]))

    def _stop(self, reason="PULSE_COMPLETE", fields=None):
        try:
            ok, message = self.ptz.stop()
        except Exception as exc:
            ok, message = False, type(exc).__name__
        latency.profiler.event('PAN_TILT_STOP', **dict(fields or {},
            stop_reason=reason, acknowledged=bool(ok), rpc_result=str(message)))
        if not ok:
            first = not self._fault
            self._fault = True
            self.enabled = False
            self._stop_budget.failed()
            if first or self._stop_budget.attempts == self._stop_budget.limit:
                latency.profiler.event('MOTOR_FAULT_LATCHED', motor_owner=self.orchestrator.owner,
                    fault_state='FAULT_LATCHED' if self._stop_budget.exhausted else 'STOP_UNCONFIRMED',
                    retry_attempt=self._stop_budget.attempts)
                self.status(f'Auto Pan/Tilt STOP unconfirmed: {message}; movement blocked')
            return False
        if self._fault:
            latency.profiler.event('MOTOR_FAULT_RECOVERED', motor_owner=self.orchestrator.owner,
                                   evidence='STOP_RPC_ACK', physical_motor_stopped=None)
        self._fault = False
        self._stop_budget.reset()
        self.busy = False
        self._last_stop = time.monotonic()
        self._ready_after = self._last_stop+self.cfg.AUTO_PAN_TILT_SETTLE
        self.orchestrator.release('pan_tilt', self.cfg.AUTO_PAN_TILT_SETTLE)
        return True

    def _run(self):
        while True:
            with self._condition:
                if self._closed:
                    break
                if self._fault:
                    # Sleep interruptibly without retrying after budget exhaustion.
                    externally_confirmed = getattr(self.ptz, 'motor_state', None) == 'READY'
                    if not externally_confirmed and not self._stop_budget.ready():
                        delay = .5 if self._stop_budget.exhausted else max(.01, self._stop_budget.next_at-time.monotonic())
                        self._condition.wait(timeout=delay)
                        continue
                else:
                    self._condition.wait(timeout=.03)
                    if self._closed:
                        break
                item = self._latest
            if self._fault:
                # Ownership survives retry exhaustion and reconnect success.
                self._stop('STOP_RETRY', self._active_fields)
                continue
            if (not item or not self.enabled or time.monotonic() < self._ready_after
                    or item[0] <= self._last_stop
                    or time.monotonic()-item[0] > self.cfg.MAX_CONTROL_OBSERVATION_AGE):
                continue
            if not self.orchestrator.acquire('pan_tilt'):
                self._decision('MANUAL_OVERRIDE' if self.orchestrator.manual else 'MOTOR_BUSY', item[5])
                continue
            if getattr(self.ptz, '_motor_fault', False) is True:
                # A persisted backend fault can exist without a local owner.
                # Discard this intent; recovery requires a fresh observation.
                self.cancel()
                self.orchestrator.release('pan_tilt')
                self._decision('MOTOR_FAULT', item[5])
                continue
            fields = dict(item[5], command_id='pan_'+uuid4().hex, motor_owner='pan_tilt', lock_session_id=item[1], target_track_id=item[2],
                          observation_time=item[0], pan_tilt_command=item[3][0])
            self._active_fields = fields
            stop_reason = 'PULSE_COMPLETE'
            dispatched = None
            try:
                if not self.validate_intent(fields):
                    self.orchestrator.release('pan_tilt')
                    continue
                self.busy = True
                dispatched = time.monotonic()
                latency.profiler.event('PAN_TILT_DISPATCH', **dict(fields,
                    command_status='COMMAND_DISPATCHED', observation_age_ms=(dispatched-item[0])*1000,
                    detection_to_dispatch_ms=(dispatched-fields['detection_completed_timestamp'])*1000
                        if fields.get('detection_completed_timestamp') is not None else None,
                    inter_pulse_wait_ms=(dispatched-self._last_stop)*1000 if self._last_stop else None))
                latency.profiler.count('pan_tilt_command_count')
                with latency.correlated(**fields):
                    ok, message = self.ptz.command(*item[3])
                latency.profiler.event('PAN_TILT_RPC_RESULT', **dict(fields,
                    acknowledged=bool(ok), rpc_result=str(message),
                    command_status='ACK' if ok else 'COMMAND_FAILED'))
                if not ok and self.validate_intent(fields):
                    self.enabled = False
                    self.status(f'Auto Pan/Tilt disabled: {message}')
                deadline = time.monotonic()+item[4]
                with self._condition:
                    while ok and time.monotonic() < deadline and self.validate_intent(fields):
                        self._condition.wait(timeout=min(.02, max(0., deadline-time.monotonic())))
                if not ok:
                    stop_reason = 'COMMAND_FAILED'
                elif time.monotonic() < deadline:
                    stop_reason = 'MANUAL_OVERRIDE' if self.orchestrator.manual else 'AUTHORIZATION_OR_GEOMETRY_CHANGED'
            except Exception as exc:
                stop_reason = 'COMMAND_FAILED'
                latency.profiler.event('PAN_TILT_RPC_RESULT', **dict(fields,
                    command_status='COMMAND_FAILED', exception=type(exc).__name__))
                self.enabled = False
                self.status(f'Auto Pan/Tilt error: {type(exc).__name__}')
            finally:
                if self.busy:
                    latest = self._latest
                    displacement = None
                    if (latest and latest[1:3] == item[1:3] and latest[0] > dispatched
                            and time.monotonic()-latest[0] <= self.cfg.MAX_CONTROL_OBSERVATION_AGE
                            and self.session_valid(item[1], item[2])):
                        before, after = item[5]['target_center'], latest[5]['target_center']
                        displacement = (after[0]-before[0], after[1]-before[1])
                    self._stop(stop_reason, fields)
                    latency.profiler.event('PAN_TILT_COMMAND_ENDED', **dict(fields,
                        stop_reason=stop_reason, image_displacement_px=displacement,
                        displacement_evidence='target_image_motion_not_motor_telemetry',
                        command_active_duration_ms=(time.monotonic()-dispatched)*1000 if dispatched else None))

        # Exactly one final attempt, with fault retained if it is unconfirmed.
        if self.busy or self._fault:
            self._stop('APPLICATION_CLOSE', self._active_fields)
        self.shutdown_unconfirmed = self._fault
        if self.shutdown_unconfirmed:
            latency.profiler.event('SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR',
                shutdown_phase='auto_pan_tilt', motor_owner=self.orchestrator.owner,
                fault_state='STOP_UNCONFIRMED', physical_motor_stopped=None)
            self.status('STOP chưa được xác nhận. Cần kiểm tra camera trực tiếp hoặc dùng cơ chế dừng độc lập.')

    def close(self, timeout=None):
        # Legacy callers passing None also get a finite join.
        timeout = 6. if timeout is None else max(0., timeout)
        with self._condition:
            self._closed = True
            self.enabled = False
            self._latest = None
            self._condition.notify_all()
        self._thread.join(timeout)
        if self._thread.is_alive():
            latency.profiler.event('SHUTDOWN_STOP_TIMEOUT', shutdown_phase='auto_pan_tilt',
                                   motor_owner=self.orchestrator.owner)
        return not self._thread.is_alive()
