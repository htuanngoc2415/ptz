"""Headless AI orchestration. Context supplies services and UI notification callbacks.

No Tk imports: the desktop adapter remains compatible during incremental migration.
"""
import time
import cv2
import config as cfg
from event_logging import event_print as print
from ai import calculate_face_sharpness, calculate_person_sharpness
from ai.face.identity_confirmation import IdentityConfirmation
from ai.quality.image_quality import ImageQualityAnalyzer
from ai.quality.focus_quality import FocusQualityAnalyzer
from control.acquisition_trial import AcquisitionTrial
from control.face_framing import FaceFramingLock
from control.zoom_policy import ZoomPolicy
from control import TargetState
from app.state import TargetSnapshot
from telemetry.events import emit
from telemetry import latency
from runtime.scheduling import scheduler
from runtime.face_worker import FaceWorker
from runtime.target_session import TargetSession
from runtime.byte_tracking import ByteTrackPersonTracker
from ai.reid_target import iou, inside


class InferenceWorker:
    """Compatibility context adapter; UI callbacks are the only presentation boundary."""
    def __init__(self, context):
        object.__setattr__(self, '_context', context)

    def __getattr__(self, name):
        return getattr(self._context, name)

    def __setattr__(self, name, value):
        setattr(self._context, name, value)

    def reset_tracking_worker(self):
        """Only the AI thread mutates tracking state; UI requests resets via Event."""
        if hasattr(self, 'auto_pan_tilt'):
            self.auto_pan_tilt.cancel()
        if hasattr(self, 'person_tracker'):
            self.person_tracker.reset()
        if hasattr(self, 'target_session'):
            self.target_session.publish(None)
        if hasattr(self, 'face_worker'):
            self.face_worker.invalidate()
        self._identity_logged_session = None
        self._usable_logged_session = None
        if hasattr(self, 'face_framing'):
            self.face_framing.reset()
        self.face_framing = FaceFramingLock(cfg.AUTO_ZOOM_FACE_MIN_RATIO,
            cfg.FACE_FINAL_CONFIRM_FRAMES, cfg.FACE_FINAL_LOST_TIMEOUT_SEC)
        if hasattr(self, 'acquisition_trial'):
            self.acquisition_trial.finish("RESET", time.monotonic())
        self.acquisition_trial = AcquisitionTrial(cfg.ACQUISITION_DEADLINE_SEC)
        self.reid_target.reset(preserve_reacquire=self.reid_target.reacquiring)
        self.target_state.reset()
        self.zoom_policy = ZoomPolicy(context=cfg.CONTEXT_FRAMING)
        self.face_zoom_locked = False
        self.face_recognition_result = None
        self.last_face_recognition_time = 0.0
        self.face_display_recognition = None
        self.face_identity = IdentityConfirmation(cfg.FACE_ID_CONFIRM_COUNT, cfg.FACE_ID_CONFIRM_TIMEOUT)
        self.auto_focus.reset()
        with self.ai_lock:
            self.ai_result.update(person_target=None, face_target=None, control=None,
                                  recognition=None, face_panel=None, display_recognition=None, reid=self.reid_target.snapshot())

    def handle_detector_failure(self):
        error = getattr(self.person_detector, 'last_error', None)
        if not isinstance(error, str) or not error:
            self._detector_failure_reported = None
            return False
        # Failed inference is unavailable evidence, never a target-lost observation
        # that could authorize recovery zoom-out or apply stale face results.
        if (getattr(self, '_detector_failure_reported', None) != error
                or getattr(self.auto_zoom, 'enabled', False) is True):
            self._detector_failure_reported = error
            self.auto_zoom.request_stop(reason='DETECTOR_ERROR')
            self.auto_zoom.set_enabled(False, wait=False)
            self.reid_target.invalidate(error)
            self.reset_tracking_worker()
            self.set_status('PERSON inference failed; Auto Zoom disabled. '
                            'Restart after resolving GPU memory, or use PTZ_DEVICE=cpu.')
            self.notify_auto_zoom_disabled()
            latency.profiler.event('DETECTOR_CONTROL_BLOCKED', error=error,
                inference_blocked=bool(getattr(self.person_detector, 'fatal_error', None)))
        return True

    def consume_identity(self, result):
        """Identity is historical evidence for this session; never update spatial state."""
        job = result['job']
        if (not self.auto_zoom.session_valid(job.lock_session_id) or
                job.lock_session_id != self.reid_target.lock_session_id or
                job.track_id != self.reid_target.track_id):
            latency.profiler.event('IDENTITY_OBSERVATION_REJECTED', **job.correlation(),
                reason='TRACK_MISMATCH' if job.track_id != self.reid_target.track_id else 'SESSION_MISMATCH')
            return
        recognition = result['recognition']
        if recognition is None:
            return
        self.face_recognition_result = recognition
        if not recognition['matched']:
            self.face_display_recognition = None
        if recognition['confirmed']:
            self.face_display_recognition = dict(recognition)
            if self._identity_logged_session != job.lock_session_id:
                self._identity_logged_session = job.lock_session_id
                with latency.correlated(**job.correlation()):
                    latency.profiler.elapsed('IDENTITY_CONFIRMED', self.reid_target.locked_ns,
                        'lock_to_identity_confirmed_ms', identity=recognition['identity'], similarity=recognition['score'])
                latency.profiler.count('identity_confirmation_count')
                self.acquisition_trial.finish('CONFIRMED', time.monotonic())
        latency.profiler.event('IDENTITY_OBSERVATION_APPLIED', **job.correlation(),
                               identity=recognition['identity'], confirmed=recognition['confirmed'])
        if result.get('usable_ns') and self._usable_logged_session != job.lock_session_id:
            self._usable_logged_session = job.lock_session_id
            latency.profiler.event('FIRST_USABLE_FACE', metric='time_to_usable_face_ms',
                duration_ms=(result['usable_ns']-self.reid_target.locked_ns)/1e6, **job.correlation())

    def start_person_zoom_on_lock(self, person, frame_shape, frame_id, received):
        """Latest-only submission before face work/quality/GUI. Camera ownership is checked by the supervisor."""
        if not hasattr(self, 'zoom_policy'):
            self.zoom_policy = ZoomPolicy(context=cfg.CONTEXT_FRAMING)
        session = self.reid_target.lock_session_id
        reason = None
        if person is None:
            reason = 'MISSING_PERSON_BBOX'
        elif not self.auto_zoom.enabled:
            reason = 'DISABLED'
        elif not self.reid_target.locked or self.reid_target.crossing_hold or self.tracking_reset.is_set():
            reason = 'TARGET_NOT_VALIDATED'
        elif self.zoom_policy.session == session and self.zoom_policy.face_mode:
            reason = 'FACE_OWNER'
        elif (getattr(getattr(self, 'face_framing', None), 'locked', False) is True
                or getattr(getattr(self, 'target_state', None), 'face_latched', False) is True):
            reason = 'FACE_LATCHED'
        if cfg.CONTEXT_FRAMING and person is not None:
            h,w = frame_shape[:2]
            x1,y1,x2,y2 = person['bbox']
            if (y2-y1)/h < cfg.AUTO_ZOOM_PERSON_MIN_RATIO and (x1 < .1*w or x2 > .9*w or y1 < .1*h or y2 > .9*h):
                reason = 'CONTEXT_EDGE_HOLD'
        if reason:
            latency.profiler.event('ZOOM_CONTROL_DECISION', frame_id=frame_id, lock_session_id=session,
                person_track_valid=person is not None and self.reid_target.locked,
                person_ratio=((person['bbox'][3]-person['bbox'][1])/frame_shape[0] if person else None),
                face_detected=False, face_latched=reason in ('FACE_OWNER', 'FACE_LATCHED'), face_ratio=None,
                zoom_owner='FACE' if reason in ('FACE_OWNER', 'FACE_LATCHED') else 'PERSON',
                zoom_state='PERSON_APPROACH', zoom_direction_requested=0, decision='HOLD',
                decision_suppression_reason=reason, observation_age_ms=(time.monotonic()-received)*1000)
            return False
        self.auto_zoom.submit_observation(
            correlation=dict(latency.context.get(), frame_id=frame_id,
                lock_session_id=self.reid_target.lock_session_id, target_track_id=self.reid_target.track_id,
                locked_ns=self.reid_target.locked_ns, zoom_state='PERSON_APPROACH',
                decision_reason='PERSON_APPROACH', person_track_valid=True, face_visible=False),
            frame_shape=frame_shape, target=person, target_name='PERSON',
            target_ratio=cfg.AUTO_ZOOM_PERSON_TARGET_RATIO, tolerance=cfg.PERSON_RATIO_TOLERANCE,
            min_ratio=cfg.AUTO_ZOOM_PERSON_MIN_RATIO, max_ratio=cfg.AUTO_ZOOM_PERSON_MAX_RATIO,
            observation_time=received, speed=self.get_motor_speed(), state_name='TARGET_LOCKED')
        return True

    def disable_reid_auto_zoom(self):
        self.auto_zoom.set_enabled(False, wait=False)
        self.set_status(f"Auto Zoom blocked: {self.reid_target.error}")
        self.notify_auto_zoom_disabled()

    def log_face_recognition(self, key, message):
        """Log immediately on identity/outcome changes; rate-limit repeated status."""
        now = time.monotonic()
        if message == "WAIT | target_not_locked" and not cfg.LOG_IDLE_NO_PERSON:
            return
        # WAIT reasons can fluctuate every frame: rate-limit all WAIT messages together.
        changed = key != getattr(self, '_face_log_key', None) and key[0] != 'WAIT'
        if changed or now-getattr(self, '_face_log_time', float('-inf')) >= cfg.FACE_RECOGNITION_LOG_INTERVAL:
            print(f"[RECOGNITION] {message}", flush=True)
            self._face_log_key = key
            self._face_log_time = now

    def ai_worker(self):
        if not hasattr(self, 'person_detector'):
            from app.application import initialize_models
            try:
                initialize_models(self)
            except Exception as exc:
                with self.target_request_lock:
                    self.ai_initialization_error = str(exc)
                    self.pending_target_path = None
                self._set_target_panel(error=f"AI initialization failed: {exc}")
                print(f'[AI] Model initialization ERROR: {exc}')
                emit('MODEL_INIT_ERROR', error=str(exc))
                if hasattr(self, 'auto_pan_tilt'):
                    self.auto_pan_tilt.cancel()
                self.reid_target.invalidate(exc)
                self.disable_reid_auto_zoom()
                return
        print('[TARGET] Selection mode: '+('YOLO + OSNet every frame; spatial tracking disabled'
            if cfg.TARGET_CONTINUOUS_REID else 'ByteTrack + periodic/event-driven OSNet verification'))
        self.person_tracker = ByteTrackPersonTracker(cfg)
        self.target_session = TargetSession()
        self.zoom_policy = ZoomPolicy(context=cfg.CONTEXT_FRAMING)
        self.auto_zoom.track_valid = self.target_session.matches
        self.auto_zoom.face_miss_grace = cfg.AUTO_ZOOM_FACE_LOST_TIMEOUT
        self.auto_zoom.session_valid = lambda session: (self.running and not self.tracking_reset.is_set()
            and self.target_session.matches(session))
        if hasattr(self, 'auto_pan_tilt'):
            self.auto_pan_tilt.session_valid = lambda session, track: (
                self.running and not self.tracking_reset.is_set()
                and self.target_session.matches(session, track))
        self.face_worker = FaceWorker(self.face_detector, self.face_recognizer, self.face_matcher, cfg,
                                      session_valid=self.auto_zoom.session_valid, track_valid=self.target_session.matches)
        if hasattr(self, 'ptz'):
            self.ptz.session_valid = self.auto_zoom.session_valid
        image_quality = ImageQualityAnalyzer(cfg.IMAGE_DARK_THRESHOLD, cfg.IMAGE_BACKGROUND_DELTA,
                                             cfg.IMAGE_SHADOW_RATIO, cfg.IMAGE_SHADOW_LEVEL)
        focus_quality = FocusQualityAnalyzer(cfg.FOCUS_LAPLACIAN, cfg.FOCUS_TENENGRAD,
                                             cfg.FOCUS_EDGE_DENSITY, cfg.FOCUS_MIN_ROI_SIZE)
        last_stale_log = float('-inf')
        # OSNet loading/target extraction never blocks Tk's UI thread.
        self.reid_target.reload_if_changed(self.person_detector)
        self.reset_tracking_worker()
        if not self.reid_target.valid:
            self.disable_reid_auto_zoom()
        if self.reid_target.target_data:
            self._set_target_panel(target=self.reid_target.target_data)

        interval = (
            1.0
            /
            max(
                1.0,
                cfg.AI_MAX_FPS,
            )
        )

        last_perf_log = float('-inf')
        last_control_state = None
        absent_since = None
        recovery_entered = False
        last_sequence = -1
        last_frame_time = None
        while self.running:

            loop_start = time.monotonic()
            if self.acquisition_trial.expire(loop_start):
                # This is a diagnostic deadline, not permission to override the
                # operator.  Keep tracking/zoom enabled unless explicitly configured.
                if cfg.ACQUISITION_TIMEOUT_DISABLE_AUTO_ZOOM:
                    self.auto_zoom.set_enabled(False, wait=False)
                    self.auto_zoom.request_stop(reason="ACQUISITION_TIMEOUT")
                    self.notify_auto_zoom_disabled()
                    self.set_status("Trial timeout: Auto Zoom disabled by config.")
                else:
                    self.set_status("Trial timeout recorded; Auto Zoom remains ON.")
            if self.tracking_reset.is_set():
                self.tracking_reset.clear()
                absent_since = None
                recovery_entered = False
                self.reset_tracking_worker()
            # Consume one UI request atomically.  Heavy target detection/ReID is
            # deliberately owned by this worker, never Tk's event thread.
            target_request_lock = getattr(self, "target_request_lock", None)
            requested_target = None
            if target_request_lock is not None:
                with target_request_lock:
                    requested_target = self.pending_target_path
                    self.pending_target_path = None
            if requested_target:
                try:
                    target = self.reid_target.load_target_from_path(requested_target, self.person_detector)
                    print(f"[TARGET] PERSON_DETECTED bbox={target['bbox']} persons={target['persons']} selected=largest")
                    print("[TARGET] EMBEDDING_READY")
                    self.auto_zoom.request_stop(reason="TARGET_REPLACED")
                    self.reid_target.replace_target(target)
                    self.digital_ptz.reset()
                    self.reset_tracking_worker()
                    self._set_target_panel(target=target)
                    self.set_status("TARGET READY")
                    print(f"[TARGET] REPLACED path={target['path']}")
                except Exception as exc:
                    # load_target_from_path mutates nothing before success: old target remains active.
                    print(f"[TARGET] LOAD_FAILED reason={exc}")
                    print("[TARGET] Previous target preserved")
                    self._set_target_panel(error=str(exc))
                    self.set_status(f"TARGET ERROR: {exc}")

            retry = self.reid_retry.is_set()
            if retry:
                self.reid_retry.clear()
                print("[REID] Retrying target initialization (Auto Zoom remains OFF)")
            if self.reid_target.reload_if_changed(
                    self.person_detector, loop_start, force=retry,
                    before_reload=lambda: self.auto_zoom.request_stop(reason="TARGET_CHANGED")):
                self.auto_zoom.request_stop(reason="TARGET_CHANGED")
                self.reset_tracking_worker()
                if not self.reid_target.valid:
                    self.disable_reid_auto_zoom()
                elif retry:
                    self.set_status("Target ReID ready. Enable Auto Zoom to start confirmation.")

            # ------------------------------------------------
            # GET FRAME
            # ------------------------------------------------

            if hasattr(self, 'frames'):
                packet = self.frames.latest(after=last_sequence, consumer="detection", timeout=0.1)
                if not self.running:
                    break
                self.current_frame = packet
                frame_rgb = packet.image if packet else None
                frame_time = packet.receive_timestamp if packet else last_frame_time
                frame_sequence = packet.frame_id if packet else last_sequence
            else:  # Compatibility for existing headless adapters.
                with self.frame_lock:
                    frame_rgb = self.latest_frame_rgb
                    frame_time = self.latest_frame_time
                    frame_sequence = self.frame_sequence

            if frame_rgb is None or frame_sequence == last_sequence:
                if frame_time is not None and loop_start-frame_time > cfg.FACE_LOST_HARD_TIMEOUT:
                    absent_since = None
                    latency.profiler.gate('perception_gate', 'WATCHDOG')
                    self.auto_zoom.request_stop(reason="WATCHDOG")
                    self.reid_target.mark_lost()
                    self.reset_tracking_worker()
                if not hasattr(self, 'frames'):
                    time.sleep(0.02)

                continue

            if last_frame_time is not None and frame_time-last_frame_time > cfg.FACE_LOST_HARD_TIMEOUT:
                absent_since = None
                latency.profiler.event('FRAME_GAP', gap_ms=(frame_time-last_frame_time)*1000)
                self.auto_zoom.request_stop(reason="FRAME_GAP")
                self.reid_target.mark_lost()
                self.reset_tracking_worker()
            last_frame_time = frame_time
            if last_sequence >= 0:
                latency.profiler.count('dropped_frames', max(0, frame_sequence-last_sequence-1))
            last_sequence = frame_sequence
            received_ns = getattr(packet, 'received_ns', None) if hasattr(self, 'frames') else None
            latency.context.set(dict(frame_id=frame_sequence, received_ns=received_ns,
                acquisition_id=self.reid_target.acquisition_id, lock_session_id=self.reid_target.lock_session_id,
                target_track_id=self.reid_target.track_id, track_generation=self.reid_target.track_generation))
            latency.profiler.elapsed('FRAME_INFERENCE_AGE', received_ns, 'frame_age_since_receive_ms')
            if loop_start-frame_time > cfg.MAX_CONTROL_OBSERVATION_AGE:
                latency.profiler.gate('perception_gate', 'STALE_OBSERVATION')
                self.auto_zoom.request_stop(reason='STALE_OBSERVATION')
                if loop_start-last_stale_log >= cfg.PERF_LOG_INTERVAL_SEC:
                    emit('FRAME_STALE', frame_id=frame_sequence, frame_age_ms=(loop_start-frame_time)*1000)
                    last_stale_log = loop_start
                continue
            latency.profiler.gate('perception_gate', None)
            frame_bgr = cv2.cvtColor(
                frame_rgb,
                cv2.COLOR_RGB2BGR,
            )

            ai_start = (
                time.perf_counter()
            )

            # =================================================
            # PERSON DETECTION
            # =================================================

            capture_age_ms = (time.monotonic()-frame_time)*1000
            yolo_started = time.perf_counter()
            reid_ms = face_ms = 0.0
            def report_perf(state):
                nonlocal last_perf_log
                if cfg.ENABLE_PERF_LOG and time.monotonic()-last_perf_log >= cfg.PERF_LOG_INTERVAL_SEC:
                    last_perf_log = time.monotonic()
                    print(f"[PERF] frame={frame_sequence} capture_age={capture_age_ms:.1f}ms "
                          f"yolo={yolo_ms:.1f}ms reid={reid_ms:.1f}ms face={face_ms:.1f}ms "
                          f"control={self.auto_zoom.last_control_ms:.1f}ms "
                          f"ptz_last={self.auto_zoom.last_ptz_ms:.1f}ms "
                          f"observation_age={(time.monotonic()-frame_time)*1000:.1f}ms "
                          f"max_age={cfg.MAX_CONTROL_OBSERVATION_AGE*1000:.0f}ms state={state}")

            with scheduler.use('detection'):
                if hasattr(self, 'frames'):
                    fresh = self.frames.latest(after=frame_sequence, consumer='detection')
                    if fresh is not None:
                        latency.profiler.count('dropped_frames', max(0, fresh.frame_id-frame_sequence-1))
                        latency.profiler.count('superseded_detection_frames')
                        latency.profiler.event('DETECTION_JOB_DROPPED', frame_id=frame_sequence, reason='REFRESH_BEFORE_INFERENCE')
                        packet = fresh
                        self.current_frame = fresh
                        frame_rgb, frame_sequence, frame_time = fresh.image, fresh.frame_id, fresh.receive_timestamp
                        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                        last_sequence, last_frame_time = frame_sequence, frame_time
                        capture_age_ms = (time.monotonic()-frame_time)*1000
                        self.reid_target.frame_id = frame_sequence
                        self.reid_target.frame_age_ms = capture_age_ms
                        latency.context.set(dict(latency.context.get(), frame_id=frame_sequence, received_ns=fresh.received_ns))
                        latency.profiler.event('DETECTION_FRAME_REFRESHED', frame_id=frame_sequence)
                if not 0 <= time.monotonic()-frame_time <= cfg.MAX_CONTROL_OBSERVATION_AGE:
                    latency.profiler.gate('perception_gate', 'STALE_BEFORE_INFERENCE')
                    latency.profiler.count('stale_inference_jobs_skipped')
                    latency.profiler.event('INFERENCE_JOB_SKIPPED', branch='detection', reason='STALE_BEFORE_INFERENCE')
                    self.auto_zoom.request_stop(reason='STALE_OBSERVATION')
                    continue
                persons = self.person_detector.detect(frame_bgr)
            detection_completed = time.monotonic()
            latency.profiler.count('detection_frames')
            latency.profiler.event('PERSON_DETECTION_RESULT', num_persons=len(persons))
            if self.handle_detector_failure():
                absent_since = None
                continue

            yolo_ms = (time.perf_counter()-yolo_started)*1000
            if time.monotonic() - frame_time > cfg.MAX_CONTROL_OBSERVATION_AGE:
                absent_since = None
                latency.profiler.gate('perception_gate', 'STALE_OBSERVATION')
                self.auto_zoom.request_stop(reason="STALE_OBSERVATION")
                latency.record_stale('DETECTION_FRAME_TOO_OLD')
                if time.monotonic()-last_stale_log >= cfg.PERF_LOG_INTERVAL_SEC:
                    age_ms = (time.monotonic()-frame_time)*1000
                    emit('PERSON_RESULT_STALE', frame_id=frame_sequence,
                         frame_age_ms=age_ms, inference_ms=yolo_ms, persons=len(persons))
                    self.set_status(f'AI quá chậm: {age_ms:.0f} ms; kết quả không dùng để điều khiển')
                    last_stale_log = time.monotonic()
                report_perf("STALE_OBSERVATION")
                continue

            if cfg.TARGET_CONTINUOUS_REID:
                # Per-frame labels exclude neighbours; none represent spatial tracks.
                for index, detection in enumerate(persons):
                    detection['track_id'] = -(index+1)
            else:
                persons = self.person_tracker.update(persons, frame_time, frame=frame_bgr, frame_id=frame_sequence)
            if persons and self.auto_zoom.enabled:
                self.acquisition_trial.start(frame_time)

            self.reid_target.frame_id = frame_sequence
            self.reid_target.frame_age_ms = (time.monotonic()-frame_time)*1000
            self.reid_target.backlight_state = "STATIC"
            person = None
            faces = []
            face = None
            face_result = None
            control_frame_time = frame_time
            initial_zoom_submitted = False
            pan_needed = False
            try:
                reid_started = time.perf_counter()
                person = self.reid_target.select_person(frame_bgr, persons, frame_time)
                if not cfg.TARGET_CONTINUOUS_REID:
                    self.person_tracker.record_selection(frame_bgr, self.reid_target, person, frame_time,
                        getattr(self.person_detector, 'postprocess_info', None))
                # Acquisition/recovery can block on OSNet. Recheck before publishing
                # motor authorization or enqueueing the first zoom/pan command.
                if not 0 <= time.monotonic()-frame_time <= cfg.MAX_CONTROL_OBSERVATION_AGE:
                    self.target_session.publish(None)
                    self.auto_zoom.request_stop(reason='STALE_RESULT')
                    if hasattr(self, 'auto_pan_tilt'):
                        self.auto_pan_tilt.cancel()
                    latency.record_stale('SELECTION_DEADLINE_EXPIRED')
                    continue
                binding = (self.reid_target.lock_session_id, self.reid_target.track_id,
                           self.reid_target.track_generation)
                previous_binding = getattr(self, '_profile_binding', None)
                if previous_binding != binding:
                    latency.profiler.event('TARGET_BINDING_CHANGED', previous_binding=previous_binding,
                        lock_session_id=binding[0], target_track_id=binding[1], track_generation=binding[2],
                        evidence='runtime_binding_change_not_ground_truth_ID_switch')
                    self._profile_binding = binding
                self.target_session.publish(self.reid_target.lock_session_id
                    if self.reid_target.locked and not self.reid_target.crossing_hold else None, self.reid_target.track_id)
                if self.reid_target.crossing_hold:
                    self.auto_zoom.request_stop(reason='IDENTITY_UNCERTAIN')
                if self.reid_target.just_locked:
                    self.acquisition_trial.mark("TARGET_LOCKED", time.monotonic())
                    self.face_identity.reset()
                    self.face_display_recognition = None
                    self.face_recognition_result = None
                    self.last_face_recognition_time = 0.0
                reid_ms = (time.perf_counter()-reid_started)*1000
                latency.context.set(dict(latency.context.get(), acquisition_id=self.reid_target.acquisition_id,
                                         lock_session_id=self.reid_target.lock_session_id,
                                         target_track_id=self.reid_target.track_id, track_generation=self.reid_target.track_generation))
                self.auto_zoom.motion_feedback(person, frame_bgr.shape, frame_time,
                    self.reid_target.lock_session_id, self.reid_target.track_id)
                if hasattr(self, 'auto_pan_tilt'):
                    pan_needed = self.auto_pan_tilt.submit(person, frame_bgr.shape, frame_time,
                        self.reid_target.lock_session_id, self.reid_target.track_id,
                        metadata=dict(frame_id=frame_sequence, detection_completed_timestamp=detection_completed,
                            target_uid=getattr(self.reid_target, 'target_uid', None),
                            tracking_state=getattr(self.reid_target, 'tracking_status', self.reid_target.stage),
                            identity_state=getattr(self.reid_target, 'identity_status', None),
                            auto_zoom_enabled=self.auto_zoom.enabled))
                    if pan_needed:
                        self.auto_zoom.request_stop(reason='PAN_TILT_CENTERING')
                if person is not None and not pan_needed:
                    initial_zoom_submitted = self.start_person_zoom_on_lock(person, frame_bgr.shape, frame_sequence, frame_time)
                face_started = time.perf_counter()
                face_result = None
                if self.reid_target.locked and not self.reid_target.crossing_hold:
                    identity_result = self.face_worker.take_identity()
                    while identity_result is not None:
                        self.consume_identity(identity_result)
                        identity_result = self.face_worker.take_identity()
                    face_result = self.face_worker.take()
                    if face_result:
                        job = face_result['job']
                        compatible = (self.auto_zoom.session_valid(job.lock_session_id) and
                            job.lock_session_id == self.reid_target.lock_session_id and
                            job.track_id == self.reid_target.track_id and
                            (person is None or (job.person is not None and
                             iou(person['bbox'], job.person['bbox']) >= cfg.REID_TRACK_IOU)))
                        candidate_face = face_result['face']
                        if candidate_face and person is not None:
                            compatible = compatible and inside(candidate_face, person)
                        if compatible:
                            faces, face = face_result['faces'], candidate_face
                            # Keep the source observation time; never re-stamp old face coordinates.
                            if face is not None:
                                control_frame_time = min(frame_time, job.received)
                            if face_result.get('usable_ns') and self._usable_logged_session != job.lock_session_id:
                                self._usable_logged_session = job.lock_session_id
                                latency.profiler.event('FIRST_USABLE_FACE', metric='time_to_usable_face_ms',
                                    duration_ms=(face_result['usable_ns']-self.reid_target.locked_ns)/1e6,
                                    **job.correlation())
                        else:
                            face_result = None
                            latency.record_stale(('SESSION_MISMATCH' if job.lock_session_id != self.reid_target.lock_session_id else 'TRACK_MISMATCH' if job.track_id != self.reid_target.track_id else 'FACE_GEOMETRY_OR_AGE'))
                    if person is not None or self.reid_target.face_box is not None:
                        self.face_worker.lens_unstable = (self.auto_zoom.busy is True or self.auto_focus.busy is True or
                            time.monotonic()-self.auto_zoom.last_zoom_end_time < cfg.FACE_LENS_SETTLE_TIME)
                        self.face_worker.submit(frame_sequence, packet.receive_timestamp if hasattr(self, 'frames') else frame_time,
                            frame_bgr, person, None if cfg.TARGET_CONTINUOUS_REID else self.reid_target.face_box, persons,
                            self.reid_target.acquisition_id, self.reid_target.lock_session_id, self.reid_target.track_id)
                else:
                    self.face_display_recognition = None
                    self.face_recognition_result = None
                    self.face_worker.invalidate()
                face_ms = (time.perf_counter()-face_started)*1000
                lost = self.reid_target.finish_frame(person, face, face_observed=face_result is not None)
                if lost:
                    self.auto_zoom.request_stop(reason="TARGET_LOST")
                    self.reset_tracking_worker()
                    person = face = None
            except Exception as exc:
                if hasattr(self, 'auto_pan_tilt'):
                    self.auto_pan_tilt.cancel()
                self.reid_target.invalidate(exc)
                self.disable_reid_auto_zoom()
                self.reset_tracking_worker()
                person = face = None

            if (time.monotonic() - frame_time > cfg.MAX_CONTROL_OBSERVATION_AGE
                    or self.tracking_reset.is_set()):
                absent_since = None
                latency.profiler.gate('perception_gate', 'STALE_OBSERVATION')
                self.auto_zoom.request_stop(reason="STALE_OBSERVATION")
                latency.record_stale(('TRACKING_RESET' if self.tracking_reset.is_set() else 'CONTROL_DEADLINE_EXPIRED'))
                if time.monotonic()-last_stale_log >= cfg.PERF_LOG_INTERVAL_SEC:
                    emit('PERCEPTION_RESULT_STALE', frame_id=frame_sequence,
                         frame_age_ms=(time.monotonic()-frame_time)*1000,
                         yolo_ms=yolo_ms, reid_ms=reid_ms, face_ms=face_ms)
                    self.set_status('AI xử lý trễ; tạm dừng zoom để chờ ảnh mới')
                    last_stale_log = time.monotonic()
                report_perf("STALE_OBSERVATION")
                continue

            latency.profiler.gate('perception_gate', None)
            latency.profiler.gate('target_visibility', 'OCCLUDED_HOLD' if self.reid_target.crossing_hold else
                'TARGET_MISSING' if person is None and face is None else None)
            quality_frame = face_result['job'].image if face is not None and face_result else frame_bgr
            quality_metrics = image_quality.analyze(quality_frame, face or person)
            focus_metrics = (focus_quality.analyze(quality_frame, face or person) or {'good': False, 'reason': 'insufficient_roi'}) if cfg.FOCUS_QUALITY_MODE == 'combined' else None

            raw_face_ratio = ((face['bbox'][3]-face['bbox'][1])/frame_bgr.shape[0]
                              if face is not None else None)
            self.face_framing.update(raw_face_ratio, frame_time,
                                     self.auto_zoom.enabled, self.reid_target.locked, face_observed=face_result is not None)
            face_present = face is not None
            if face_result is not None and face_present != getattr(self, '_face_present', False):
                print(f"[FACE] {'DETECTED' if face_present else 'LOST'} ratio={raw_face_ratio}")
                emit('FACE_DETECTED' if face_present else 'FACE_LOST', frame_id=frame_sequence, ratio=raw_face_ratio)
            if face_result is not None:
                self._face_present = face_present

            detector_failed = isinstance(getattr(self.person_detector, 'last_error', None), str) and bool(self.person_detector.last_error)
            no_bbox = not persons and face is None and not detector_failed
            # Recovery is based on the selected target, not arbitrary detections.
            # Unrelated people must not prevent widening the search area.
            target_missing = person is None and face is None and not detector_failed
            if (person is not None and self.reid_target.locked and not self.reid_target.crossing_hold) and self.auto_zoom.recovery_active and self.auto_zoom.target_recovery_active:
                self.auto_zoom.request_stop(reason="BBOX_FOUND")
            if target_missing:
                if absent_since is None:
                    absent_since = frame_time
            else:
                absent_since = None
                recovery_entered = False
            if (target_missing and self.reid_target.valid and self.auto_zoom.enabled
                    and not self.auto_focus.busy and self.zoom_direction == 0
                    and frame_time-absent_since >= cfg.LOST_ZOOM_OUT_DELAY
                    and not recovery_entered):
                # Recovery abandons old identity continuity: any reappearing person
                # must pass ReID acquisition again before IN is allowed.
                if self.reid_target.locked:
                    self.reid_target.mark_lost()
                    self.reset_tracking_worker()
                else:
                    # Preserve a new candidate's confirmation count while OUT
                    # widens the view; only an old lock needs abandoning.
                    self.face_framing.reset()
                person = face = None
                recovery_entered = True
                print('[RECOVERY] Target missing or unmatched: widening view; reacquisition requires ReID')

            # =================================================
            # STATE MACHINE
            # =================================================

            control = (
                self.target_state.update(
                    frame_shape=frame_bgr.shape,
                    person_target=person,
                    face_target=None if cfg.CONTEXT_FRAMING else face,
                    face_observed=face_result is not None,
                    now=frame_time,
                )
            )

            searching_at_person_scale = (person is not None and face is None and control.target_type == 'PERSON'
                and control.person_ratio is not None
                and cfg.AUTO_ZOOM_PERSON_MIN_RATIO <= control.person_ratio <= cfg.AUTO_ZOOM_PERSON_MAX_RATIO)
            if searching_at_person_scale and not getattr(self, '_person_search_hold', False):
                latency.profiler.event('PERSON_FRAMED_FACE_SEARCH', strategy='HOLD_AND_RETRY_HEAD_ROI',
                    additional_zoom_budget=0, target_track_id=self.reid_target.track_id)
            self._person_search_hold = searching_at_person_scale
            self.auto_zoom.observe_stability(
                control.face_ratio if face is not None else control.person_ratio,
                face_result['job'].frame_id if face is not None and face_result else frame_sequence,
                control_frame_time, target_kind="FACE" if face is not None else "PERSON")
            if control.state != last_control_state:
                print(f"[CONTROL STATE] {last_control_state} -> {control.state.value}")
                latency.profiler.event('CONTROL_STATE_TRANSITION',
                    previous_state=last_control_state.value if last_control_state else None, state=control.state.value)
                last_control_state = control.state

            # =================================================
            # FACE SIZE READINESS
            # =================================================
            was_face_ready = self.face_zoom_locked
            self.face_zoom_locked = (
                control.state == TargetState.TRACK_FACE
                and control.face_ratio is not None
                and cfg.AUTO_ZOOM_FACE_MIN_RATIO <= control.face_ratio
                <= cfg.AUTO_ZOOM_FACE_MAX_RATIO
                and frame_time - self.auto_zoom.last_zoom_end_time
                >= cfg.AUTO_ZOOM_SETTLE_TIME
                and not self.auto_zoom.busy
                and not self.auto_zoom.control_pending
            )
            if face_result is not None and was_face_ready != self.face_zoom_locked:
                self.auto_focus.unlock()
            if control.target_lost:
                self.face_recognition_result = None
            if control.target_lost:
                self.face_display_recognition = None

            # =================================================
            # SHARPNESS
            # =================================================
            #
            # Khi có face, luôn đo sharpness trên FACE.
            # Điều này không phụ thuộc state machine đang còn
            # ở ZOOM_PERSON hay đã chuyển sang ZOOM_FACE.
            # =================================================

            if face is not None:

                sharpness = (
                    calculate_face_sharpness(
                        quality_frame,
                        face,
                    )
                )

            else:

                sharpness = (
                    calculate_person_sharpness(
                        frame_bgr,
                        person,
                    )
                )

            # =================================================
            # OPTICAL AUTO ZOOM
            # =================================================

            decision = self.zoom_policy.decide(self.reid_target.lock_session_id, person, face,
                control, frame_bgr.shape, frame_time, face_observed=face_result is not None, face_received=control_frame_time)
            latency.context.set(dict(latency.context.get(), **{k:v for k,v in decision.items() if k != 'action'}))
            latency.profiler.event('ZOOM_POLICY_DECISION', **decision,
                decision=decision['action'], decision_suppression_reason=(decision['decision_reason']
                    if decision['action'] in ('HOLD', 'WAIT_FACE_SAMPLE') else None),
                zoom_direction_requested=(0 if decision['action'] in ('HOLD', 'WAIT_FACE_SAMPLE') else
                    -1 if decision['action'] in ('HEAD_RECOVERY', 'RECOVERY') else None),
                observation_age_ms=(time.monotonic()-control_frame_time)*1000)
            latency.profiler.gate('zoom_policy', decision['decision_reason']
                if decision['action'] in ('HOLD', 'WAIT_FACE_SAMPLE') else None)
            if self.zoom_policy.face_mode:
                initial_zoom_submitted = False  # fresh face takeover supersedes early person framing
                self.auto_zoom.enter_face_mode(self.reid_target.lock_session_id)
            self.auto_zoom.observe_focus_wait(self.auto_zoom.enabled and self.auto_focus.busy and decision['action']=='NORMAL')
            if pan_needed:
                self.auto_zoom.request_stop(reason='PAN_TILT_CENTERING')
            elif decision['action'] == 'WAIT_FACE_SAMPLE':
                if self.auto_zoom.busy:
                    self.auto_zoom.heartbeat(frame_time, self.reid_target.lock_session_id)
            elif decision['action'] == 'HEAD_RECOVERY' and self.auto_zoom.enabled:
                self.auto_zoom.submit_observation(correlation=dict(latency.context.get(), target_track_id=self.reid_target.track_id),
                    head_recovery=True, observation_time=frame_time, speed=self.get_motor_speed())
            elif (decision['action'] == 'HOLD'
                  and decision['decision_reason'] == 'ZOOM_HOLD_FACE_TEMPORARILY_LOST'
                  and (self.auto_zoom.continue_absolute_face_miss() is True
                       or self.auto_zoom.continue_face_recovery() is True)):
                latency.profiler.event('ZOOM_FACE_MISS_CONTINUED', **decision)
            elif decision['action'] == 'HOLD':
                if self.auto_zoom.busy or self.auto_zoom.control_pending:
                    self.auto_zoom.request_stop(reason=decision['decision_reason'])
                latency.profiler.event('ZOOM_DECISION', **decision)
            elif (not initial_zoom_submitted and self.auto_zoom.enabled and not self.auto_focus.busy
                  and (self.orchestrator.owner == 'zoom' or not self.orchestrator.blocked())):

                if (target_missing and (no_bbox or recovery_entered)
                        and self.reid_target.valid and not self.tracking_reset.is_set()
                        and self.zoom_direction == 0):
                    self.auto_zoom.submit_observation(
                        correlation=dict(latency.context.get(), decision_reason="ZOOM_OUT_TARGET_RECOVERY", zoom_state="TARGET_RECOVERY"),
                        recovery=True, observation_time=frame_time,
                        absent_since=absent_since, zoom_level=self.get_zoom_level(),
                        speed=self.get_motor_speed())
                elif (not self.reid_target.valid or not self.reid_target.locked
                        or self.tracking_reset.is_set() or control.target_lost):
                    self.auto_zoom.request_stop(reason="REID_NOT_CONFIRMED")
                else:
                    # Call even with no target or a locked face: both must stop motion.
                    ratio = control.face_ratio if control.target_type == "FACE" else control.person_ratio
                    lower = cfg.AUTO_ZOOM_FACE_MIN_RATIO if control.target_type == "FACE" else cfg.AUTO_ZOOM_PERSON_MIN_RATIO
                    upper = cfg.AUTO_ZOOM_FACE_MAX_RATIO if control.target_type == "FACE" else cfg.AUTO_ZOOM_PERSON_MAX_RATIO
                    needs_motion = (control.control_target is not None and ratio is not None
                                    and not lower <= ratio <= upper)
                    # Do not enqueue redundant HOLDs: AF/recognition can use a truly idle lens.
                    if needs_motion or self.auto_zoom.busy or self.auto_zoom.control_pending or self.auto_zoom.phase == "ZOOM_SETTLING":
                        self.auto_zoom.submit_observation(
                            correlation=dict(latency.context.get(), frame_id=(face_result['job'].frame_id if control.target_type == 'FACE' and face is not None and face_result else frame_sequence), target_track_id=self.reid_target.track_id,
                                             locked_ns=self.reid_target.locked_ns),
                            frame_shape=frame_bgr.shape,
                            target=control.control_target,
                            target_ratio=control.target_ratio,
                            tolerance=control.tolerance,
                            speed=self.get_motor_speed(),
                            target_name=control.target_type or "TARGET",
                            filtered_ratio=(control.face_ratio
                                            if control.target_type == "FACE"
                                            else control.person_ratio),
                            min_ratio=(cfg.AUTO_ZOOM_FACE_MIN_RATIO
                                       if control.target_type == "FACE" else cfg.AUTO_ZOOM_PERSON_MIN_RATIO),
                            max_ratio=(cfg.AUTO_ZOOM_FACE_MAX_RATIO
                                       if control.target_type == "FACE" else cfg.AUTO_ZOOM_PERSON_MAX_RATIO),
                            observation_time=control_frame_time if control.target_type == 'FACE' else frame_time,
                            state_name=control.state.value,
                        )

            # =================================================
            # AUTO FOCUS
            # =================================================
            #
            # AF must also be available during face acquisition: blur can
            # prevent face detection and otherwise deadlock the zoom/AF loop.
            allow_focus = (
                self.reid_target.locked and not self.tracking_reset.is_set()
                and not pan_needed
               
                and (self.orchestrator.owner == 'focus' or not self.orchestrator.blocked())
                and (face is not None or person is not None)
                and not self.auto_zoom.busy
                and not self.auto_zoom.control_pending
                and self.zoom_direction == 0
                and time.monotonic() - self.auto_zoom.last_zoom_end_time
                >= cfg.AUTO_ZOOM_SETTLE_TIME
                and not control.target_lost
            )

            self.auto_focus.update(
                sharpness=sharpness,
                allow_focus=allow_focus,
                frame_time=control_frame_time,
                roi_kind="FACE" if face is not None else "PERSON",
                quality=focus_metrics,
            )
            if face is not None:
                self.acquisition_trial.mark("FACE_DETECTED", time.monotonic())
            if self.auto_focus.busy:
                self.acquisition_trial.mark("FOCUSING", time.monotonic())
            if self.auto_zoom.busy:
                self.acquisition_trial.mark("ZOOMING", time.monotonic())

            # Face inference and temporal identity confirmation run in face_worker.
            # Motor motion is deliberately not a recognition gate.

            if self.reid_target.locked:
                if self.reid_target.crossing_hold:
                    stage = 'OCCLUDED_HOLD'
                elif self.face_framing.locked:
                    stage = "FACE_FINAL_LOCK"
                elif self.face_recognition_result and self.face_recognition_result.get("confirmed"):
                    stage = "VERIFIED"
                elif control.state in (TargetState.FACE_LOST_HOLD, TargetState.FACE_RECOVERY):
                    stage = control.state.value
                elif control.target_type == "FACE":
                    stage = "FACE_CONTROL"
                elif self.reid_target.just_locked:
                    stage = "TARGET_LOCKED"
                else:
                    stage = "PERSON_ZOOM"
                if stage == "PERSON_ZOOM" and self.reid_target.stage != stage:
                    print("[ZOOM] PERSON_CONTROL")
                self.reid_target.set_stage(stage)
            reid_debug = self.reid_target.snapshot()

            # =================================================
            # DIGITAL PTZ
            # =================================================
            #
            # Face có ưu tiên tuyệt đối cho digital crop.
            # Nếu chưa thấy face mới dùng person.
            # =================================================

            # Perception above always uses frame_bgr (the original full frame).
            # Digital PTZ is display-only: face has priority, then briefly held
            # face centre, then the full-frame target person.  FACE_FINAL_LOCK
            # stops optical zoom only; it must not freeze this ROI.
            dptz_now = time.monotonic()
            if cfg.CONTEXT_FRAMING:
                digital_target, dptz_mode = person, "PERSON_FOLLOW" if person is not None else "HOLD"
            elif face is not None:
                self._dptz_last_face = dict(face)
                self._dptz_last_face_time = dptz_now
                digital_target, dptz_mode = face, "FACE_FOLLOW"
            elif (getattr(self, "_dptz_last_face", None) is not None
                  and dptz_now - getattr(self, "_dptz_last_face_time", 0.0) <= cfg.DIGITAL_PTZ_FACE_HOLD_SEC):
                digital_target, dptz_mode = self._dptz_last_face, "HOLD"
            elif person is not None:
                digital_target, dptz_mode = person, "PERSON_FOLLOW"
            else:
                digital_target, dptz_mode = None, "HOLD"
            if dptz_mode != getattr(self, "_dptz_mode", None):
                print(f"[DPTZ] mode={dptz_mode}")
                self._dptz_mode = dptz_mode
            self.digital_ptz.update(frame_shape=frame_bgr.shape, target=digital_target, now=dptz_now)

            report_perf(control.state.value)

            # =================================================
            # AI TIME
            # =================================================

            ai_ms = (
                time.perf_counter()
                -
                ai_start
            ) * 1000.0

            # =================================================
            # SAVE RESULT
            # =================================================

            if time.monotonic()-getattr(self, '_last_metrics', float('-inf')) >= cfg.PERF_LOG_INTERVAL_SEC:
                self._last_metrics = time.monotonic()
                emit('FRAME_METRICS', frame_id=frame_sequence, frame_age_ms=(self._last_metrics-frame_time)*1000,
                     yolo_ms=yolo_ms, reid_ms=reid_ms, face_ms=face_ms, state=self.reid_target.stage,
                     image_quality=quality_metrics, focus_quality=focus_metrics)
            self.target_snapshot = TargetSnapshot(
                target_uid=getattr(self.reid_target, 'target_uid', None),
                tracking_status=getattr(self.reid_target, 'tracking_status', None),
                identity_status=getattr(self.reid_target, 'identity_status', None),
                acquisition_id=self.reid_target.acquisition_id, lock_session_id=self.reid_target.lock_session_id,
                identity_frame_id=(self.face_recognition_result or {}).get('frame_id'),
                state=self.reid_target.stage, frame_id=frame_sequence,
                track_id=self.reid_target.track_id or None,
                bbox=person['bbox'] if person else None, face_bbox=face['bbox'] if face else None,
                person_confirmed=self.reid_target.locked, person_similarity=self.reid_target.top1,
                raw_similarity=(person or {}).get('raw_similarity'),
                normalized_similarity=(person or {}).get('normalized_similarity'),
                identity_similarity=(self.face_recognition_result or {}).get('score'), face_detected=face is not None,
                last_seen_ts=self.reid_target.last_seen, lost_since=self.reid_target.lost_since,
                identity=(self.face_recognition_result or {}).get('final_identity'),
                illumination_quality=quality_metrics, focus_quality=focus_metrics)
            face_panel = self.face_worker.panel(self.reid_target.lock_session_id, self.reid_target.track_id)
            if cfg.FACE_PIPELINE_MODE == 'optimized' and (face_panel is None or not face_panel['recognition']):
                self.face_display_recognition = None
            with self.ai_lock:

                self.ai_result = {
                    "frame_shape": frame_bgr.shape,
                    "frame_received": frame_time,
                    "face_panel": face_panel,
                    "target_state": self.target_snapshot.to_dict(),
                    "persons": persons,
                    "faces": faces,

                    "person_target": person,
                    "face_target": face,

                    "control": control,

                    "sharpness": sharpness,

                    "recognition": self.face_recognition_result,
                    "display_recognition": self.face_display_recognition,
                    "reid": reid_debug,

                    #"ai_ms": ai_ms,
                }

            # =================================================
            # UI TEXT
            # =================================================

            self.update_ai_text(
                persons,
                faces,
                control,
                sharpness,
                ai_ms,
                reid_debug,
            )

            # =================================================
            # FPS LIMIT
            # =================================================

            elapsed = (
                time.monotonic()
                -
                loop_start
            )

            sleep_time = (
                interval
                -
                elapsed
            )

            if sleep_time > 0:

                time.sleep(
                    sleep_time
                )

    # ========================================================
    # LOST TARGET
    # ========================================================
