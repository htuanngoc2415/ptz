"""Latest-only spatial face branch plus one bounded, event-driven biometric worker.

Session/track tokens guard both branches; biometric completion never drives motors.
"""
from dataclasses import dataclass
from collections import deque
import threading
import time
from event_logging import event_print as print

from telemetry import latency
from runtime.scheduling import scheduler, inference_resource


@dataclass(frozen=True)
class FaceJob:
    frame_id: int
    received: float
    image: object
    person: dict | None
    previous_face: tuple | None
    persons: tuple
    acquisition_id: str
    lock_session_id: str
    track_id: int
    generation: int
    queued_ns: int
    lens_unstable: bool = False
    track_generation: int | None = None

    def correlation(self):
        return dict(frame_id=self.frame_id, acquisition_id=self.acquisition_id,
                    lock_session_id=self.lock_session_id, target_track_id=self.track_id, track_id=self.track_id,
                    track_generation=self.track_generation, face_generation=self.generation,
                    face_observation_id=f'{self.lock_session_id}:{self.track_id}:{self.generation}:{self.frame_id}',
                    frame_received_timestamp=self.received)


class FaceWorker:
    def __init__(self, detector, recognizer, matcher, cfg, session_valid=None, track_valid=None):
        from ai.face.identity_confirmation import IdentityConfirmation
        self.detector, self.recognizer, self.matcher, self.cfg = detector, recognizer, matcher, cfg
        self.session_valid = session_valid or (lambda session: True)
        self.track_valid = track_valid or (lambda session, track: True)
        self.identity = IdentityConfirmation(cfg.FACE_ID_CONFIRM_COUNT, cfg.FACE_ID_CONFIRM_TIMEOUT)
        self.condition = threading.Condition()
        self.generation = 0
        self.session = None
        self.track_id = None
        self.identity_results = deque(maxlen=32)
        self._last_identity_frame = {}
        self.pending = self.result = None
        self.last_submitted_frame = None
        self.completed_frame = None
        self.closed = False
        self.last_attempt = float('-inf')
        self.identity_session = None
        from runtime.face_tracking import FaceTracker
        from runtime.face_candidates import BestShots
        self.tracker = FaceTracker(cfg)
        self.shots = BestShots(cfg)
        self.spatial_session = None
        self.state = 'UNIDENTIFIED'
        self.cache = None
        self.recognition_pending = None
        self.recognition_busy = False
        self.face_visible = False
        self.last_associated = float('-inf')
        self.last_detection = float('-inf')
        self.lens_unstable = False
        self.previous_lens_unstable = False
        self.recognition_thread = threading.Thread(target=self._recognition_run, name='face-identity', daemon=True)
        self.recognition_thread.start()
        self.thread = threading.Thread(target=self._run, name='face-spatial', daemon=True)
        self.thread.start()

    def invalidate(self):
        with self.condition:
            self.generation += 1
            self.session = None
            self.track_id = None
            self.last_submitted_frame = None
            self.identity_results.clear()
            self._last_identity_frame.clear()
            self.cache = None
            self.state = 'UNIDENTIFIED'
            if self.recognition_pending is not None:
                self.recognition_busy = False
            self.recognition_pending = None
            self.pending = self.result = None
            self.condition.notify_all()

    def submit(self, frame_id, received, image, person, previous_face, persons,
               acquisition_id, lock_session_id, track_id):
        with self.condition:
            if self.closed:
                return
            if self.session != lock_session_id or self.track_id != track_id:
                self.generation += 1
                self.cache = None
                self.state = 'UNIDENTIFIED'
                if self.recognition_pending is not None:
                    self.recognition_busy = False
                self.recognition_pending = None
                self.identity_results.clear()
                self._last_identity_frame.clear()
                self.last_submitted_frame = None
            if self.last_submitted_frame is not None and frame_id <= self.last_submitted_frame:
                latency.profiler.event('IDENTITY_OBSERVATION_REJECTED', reason='DUPLICATE_OBSERVATION',
                                       frame_id=frame_id, lock_session_id=lock_session_id, target_track_id=track_id)
                return
            self.session = lock_session_id
            self.track_id = track_id
            if self.pending is not None:
                latency.profiler.count('dropped_face_jobs')
                latency.profiler.count('stale_reason_WORKER_RESULT_SUPERSEDED')
                latency.profiler.event('FACE_JOB_DROPPED', **self.pending.correlation(), reason='MAILBOX_REPLACED')
            self.last_submitted_frame = frame_id
            self.pending = FaceJob(frame_id, received, image, dict(person) if person else None,
                                   previous_face, tuple(dict(p) for p in persons), acquisition_id,
                                   lock_session_id, track_id, self.generation, time.perf_counter_ns(), self.lens_unstable,
                                   latency.context.get().get('track_generation'))
            self.condition.notify_all()

    def rejection_reason(self, job):
        if (self.closed or job.generation != self.generation or job.lock_session_id != self.session
                or not self.session_valid(job.lock_session_id)):
            return 'SESSION_MISMATCH'
        if job.track_id != self.track_id or not self.track_valid(job.lock_session_id, job.track_id):
            return 'TRACK_MISMATCH'
        return None

    def valid(self, job):
        """Motor/spatial freshness; never used to discard a completed identity."""
        return (self.rejection_reason(job) is None and
                0 <= time.monotonic()-job.received <= self.cfg.MAX_CONTROL_OBSERVATION_AGE)

    def reject(self, job, reason):
        latency.profiler.count('recognition_rejected_count')
        latency.profiler.count('recognition_rejected_'+reason)
        latency.profiler.event('IDENTITY_OBSERVATION_REJECTED', **job.correlation(), reason=reason)

    def take_identity(self):
        """Independent bounded observations: a fresh visual result cannot overwrite identity."""
        with self.condition:
            while self.identity_results:
                result = self.identity_results.popleft()
                reason = self.rejection_reason(result['job'])
                if reason:
                    self.reject(result['job'], reason)
                else:
                    return result
        return None

    def take(self):
        with self.condition:
            result, self.result = self.result, None
            face = result.get('face') if result else None
            tracker_valid = (not face or not face.get('tracked') or
                (face.get('tracking_confidence', 0.) >= self.cfg.FACE_TRACK_MIN_CONFIDENCE and
                 0 <= time.monotonic()-face.get('detection_timestamp', float('-inf')) <= self.cfg.FACE_TRACK_MAX_AGE))
            if result and self.valid(result['job']) and tracker_valid:
                return result
            if result:
                latency.profiler.event('FACE_SPATIAL_RESULT_REJECTED', **result['job'].correlation(), reason='STALE_FOR_MOTOR')
                latency.record_stale(self.rejection_reason(result['job']) or 'FACE_SPATIAL_AGE_OR_TRACKER_QUALITY')
        return None

    def close(self, timeout=5.):
        with self.condition:
            self.closed = True
            self.pending = self.result = None
            self.identity_results.clear()
            self.condition.notify_all()
        self.thread.join(timeout)
        self.recognition_thread.join(timeout)
        return not self.thread.is_alive() and not self.recognition_thread.is_alive()

    def _process(self, job):
        from ai.face.face_detector import detect_face_inside_person
        from ai.reid_target import inside, iou, face_ready
        from ai.sharpness import calculate_face_sharpness
        from ai.quality.focus_quality import FocusQualityAnalyzer
        import numpy as np
        cfg = self.cfg
        if not self.valid(job):
            self.reject(job, self.rejection_reason(job) or 'STALE_FOR_MOTOR')
            return None
        if job.person is not None and job.person.get('track_id', job.track_id) != job.track_id:
            self.reject(job, 'TRACK_MISMATCH')
            return None
        key = (job.lock_session_id, job.track_id, job.generation)
        with self.condition:
            if self.spatial_session != key:
                from runtime.face_candidates import BestShots
                self.tracker.reset()
                self.shots = BestShots(cfg)
                self.spatial_session = key
                self.last_associated = float('-inf')
                self.last_detection = float('-inf')
                self.last_attempt = float('-inf')
                self.state = 'COLLECTING'
        if self.previous_lens_unstable and not job.lens_unstable:
            self.tracker.reset()
            self.last_detection = float('-inf')
        self.previous_lens_unstable = job.lens_unstable
        optimized = getattr(cfg, 'FACE_PIPELINE_MODE', 'optimized') == 'optimized'
        if optimized and getattr(cfg, 'FACE_TRACKING_ENABLED', True) and self.tracker.face is not None and not job.lens_unstable and (job.received-self.last_detection < 1./cfg.FACE_DETECTION_HZ or self.recognition_busy):
            with latency.span('FACE_TRACKING', 'face_tracking_ms'):
                tracked = self.tracker.update(job.image, job.received)
            latency.profiler.count('face_tracker_frames')
            if tracked is not None:
                safe = job.person is not None and inside(tracked, job.person) and not any(
                    inside(tracked, p) for p in job.persons if p.get('track_id') != job.track_id)
                if safe:
                    return dict(job=job, faces=[tracked], face=tracked, recognition=None,
                                sharpness=None, focus_quality=None)
                self.tracker.reset()
                latency.profiler.count('face_association_conflicts')
            latency.profiler.count('face_tracker_losses')
        with scheduler.use('face', valid=lambda: self.valid(job),
                           resource=inference_resource(self.detector)) as granted:
            if not granted:
                self.reject(job, self.rejection_reason(job) or 'STALE_FOR_MOTOR')
                return None
            with self.condition:
                if not self.valid(job):
                    self.reject(job, self.rejection_reason(job) or 'STALE_FOR_MOTOR')
                    return None
            if job.person is not None:
                latency.profiler.count('scrfd_calls')
                faces = detect_face_inside_person(self.detector, job.image, job.person,
                                                 upper_body_ratio=cfg.FACE_PERSON_ROI_TOP_RATIO)
            elif job.previous_face is not None:
                latency.profiler.count('scrfd_calls')
                # Close-up fallback only around the last associated face, never whole-frame acquisition.
                faces = detect_face_inside_person(self.detector, job.image,
                            {'bbox': job.previous_face}, upper_body_ratio=1.)
            else:
                faces = []
        def head_associated(face, person):
            if not inside(face, person):
                return False
            x1,y1,x2,y2 = face['bbox']
            px1,py1,px2,py2 = person['bbox']
            return (px1 <= x1 < x2 <= px2 and py1 <= y1 < y2 <= py2 and
                    (y1+y2)/2 <= py1+cfg.FACE_PERSON_ROI_TOP_RATIO*(py2-py1))
        eligible = [f for f in faces if job.person is None or head_associated(f, job.person)]
        # Track IDs distinguish two overlapping people even when bboxes coincide.
        def other_person(person):
            if job.person is None:
                return True
            if person.get('track_id') is not None and job.person.get('track_id') is not None:
                return person['track_id'] != job.person['track_id']
            return person['bbox'] != job.person['bbox']
        eligible = [f for f in eligible if not any(inside(f, p) for p in job.persons if other_person(p))]
        if job.previous_face is not None and not getattr(cfg, 'TARGET_CONTINUOUS_REID', False):
            ranked = sorted(eligible, key=lambda f: iou(f['bbox'], job.previous_face), reverse=True)
            face = ranked[0] if ranked and iou(ranked[0]['bbox'], job.previous_face) >= cfg.REID_FACE_IOU else None
            if len(ranked) > 1 and iou(ranked[0]['bbox'], job.previous_face)-iou(ranked[1]['bbox'], job.previous_face) < cfg.REID_ASSOC_MARGIN:
                face = None
        else:
            face = eligible[0] if len(eligible) == 1 and job.person is not None else None
        if face is not None:
            with self.condition:
                if (self.cache is not None and job.received-self.last_associated > cfg.FACE_REACQUIRE_REVERIFY_TIMEOUT):
                    from runtime.face_candidates import BestShots
                    self.shots = BestShots(cfg)
                    self.cache = None
                    self.identity.reset()
                    self.state = 'REVERIFY'
                    latency.profiler.count('identity_reverify_count')
                self.last_associated = job.received
        self.last_detection = job.received
        self.face_visible = face is not None
        if getattr(cfg, 'FACE_TRACKING_ENABLED', True):
            self.tracker.correct(job.image, face, job.received)
        else:
            self.tracker.reset()
        result = dict(job=job, faces=faces, face=face, recognition=None, sharpness=None, focus_quality=None)
        latency.profiler.event('FACE_DETECTION_RESULT', face_detected=face is not None)
        if face is None:
            if faces:
                self.reject(job, 'FACE_ASSOCIATION_AMBIGUOUS')
            else:
                latency.profiler.event('FACE_LOST', **job.correlation(), evidence='detector_miss')
            return result
        latency.profiler.event('FACE_DETECTED', **job.correlation())
        latency.profiler.event('FACE_ASSOCIATED', **job.correlation(), evidence='head_geometry_temporal_exclusion')
        with latency.span('FACE_QUALITY', 'face_quality_ms'):
            sharpness = calculate_face_sharpness(job.image, face)
            quality = FocusQualityAnalyzer(cfg.FOCUS_LAPLACIAN, cfg.FOCUS_TENENGRAD,
                         cfg.FOCUS_EDGE_DENSITY, cfg.FOCUS_MIN_ROI_SIZE).analyze(job.image, face)
            ready = face_ready(face, sharpness, cfg) and np.isfinite(sharpness) and sharpness >= cfg.FACE_RECOGNITION_MIN_SHARPNESS
        result.update(sharpness=sharpness, focus_quality=quality)
        x1, y1, x2, y2 = face['bbox']
        latency.profiler.event('FACE_QUALITY_RESULT', face_bbox_width=x2-x1, face_bbox_height=y2-y1,
                              face_quality_score=sharpness, face_quality_status='sufficient' if ready else 'insufficient')
        if ready:
            result['usable_ns'] = latency.profiler.event('FACE_QUALITY_PASSED', **job.correlation())
        # Publish current geometry before ArcFace starts. Recognition completion must
        # neither delay motor feedback nor publish the same bbox a second time.
        with self.condition:
            if self.valid(job):
                self.result = dict(result)
                result['visual_published'] = True
                self.condition.notify_all()
        if not ready and getattr(cfg, 'FACE_PIPELINE_MODE', 'optimized') == 'baseline':
            self.reject(job, 'LOW_FACE_QUALITY')
            return result
        if getattr(cfg, 'FACE_PIPELINE_MODE', 'optimized') == 'baseline':
            if time.monotonic()-self.last_attempt >= cfg.FACE_RECOGNITION_INTERVAL:
                return self._recognize(job, result)
            return result
        with self.condition:
            if self.rejection_reason(job) is None:
                with latency.span('FACE_CANDIDATE_QUALITY', 'face_candidate_quality_ms'):
                    self.shots.add(job, face, result, stable=not job.lens_unstable)
                retry = cfg.FACE_UNKNOWN_RETRY_INTERVAL if self.state == 'UNKNOWN' else cfg.FACE_RECOGNITION_INTERVAL
                if (self.cache is None) and not self.recognition_busy and time.monotonic()-self.last_attempt >= retry:
                    candidate = self.shots.pop(job.received)
                    if candidate is not None:
                        candidate.queued_ns = latency.profiler.event('RECOGNITION_CANDIDATE_QUEUED',
                            **candidate.job.correlation())
                        self.recognition_pending = candidate
                        self.recognition_busy = True
                        self.state = 'RECOGNIZING'
                        self.condition.notify_all()
        return result

    def _recognize(self, job, result):
        from ai.face.face_alignment import align_face
        cfg = self.cfg
        face = result['face']
        sharpness = result['sharpness']
        x1,y1,x2,y2 = face['bbox']
        now = time.monotonic()
        recognition_queued = time.monotonic()
        with scheduler.use('recognition', valid=lambda: (
                self.rejection_reason(job) is None and
                0 <= time.monotonic()-job.received <= cfg.FACE_CANDIDATE_MAX_AGE and
                time.monotonic()-recognition_queued <= cfg.MAX_CONTROL_OBSERVATION_AGE),
                           resource=inference_resource(self.recognizer)) as granted:
            if not granted:
                self.reject(job, self.rejection_reason(job) or 'STALE_RECOGNITION_QUEUE')
                return result
            with self.condition:
                reason = self.rejection_reason(job)
                if reason:
                    self.reject(job, reason)
                    return None
                self.last_attempt = now
            latency.profiler.count('arcface_calls')
            latency.profiler.count('recognition_attempts')
            result['recognition_started_timestamp'] = time.monotonic()
            latency.profiler.event('ARCFACE_STARTED', **job.correlation(), recognition_started_timestamp=result['recognition_started_timestamp'])
            with latency.span('FACE_RECOGNITION', 'face_recognition_total_ms'):
                with latency.span('FACE_ALIGN', 'face_align_ms'):
                    aligned = align_face(job.image, face['landmarks'])
                embedding = self.recognizer.extract_embedding(aligned)
                with latency.span('FACE_MATCH', 'face_matching_ms'):
                    match = self.matcher.match(embedding)
        result['recognition_finished_timestamp'] = time.monotonic()
        latency.profiler.event('ARCFACE_COMPLETED', **job.correlation(),
            recognition_started_timestamp=result['recognition_started_timestamp'],
            recognition_finished_timestamp=result['recognition_finished_timestamp'])
        with self.condition:
            reason = self.rejection_reason(job)
            if reason:
                self.reject(job, reason)
                return None
            identity_session = (job.lock_session_id, job.track_id, job.generation)
            if job.frame_id <= self._last_identity_frame.get(identity_session, -1):
                self.reject(job, 'DUPLICATE_OBSERVATION')
                return None
            self._last_identity_frame[identity_session] = job.frame_id
            if self.identity_session != identity_session:
                self.identity.reset()
                self.identity_session = identity_session
            result['recognition'] = dict(self.identity.update(match, job.received), **job.correlation())
            rec = result['recognition']
            self.state = 'IDENTIFIED' if rec['confirmed'] else 'CANDIDATE' if rec['matched'] else 'UNKNOWN'
            if rec['confirmed']:
                self.cache = dict(rec)
            status = 'MATCH' if rec['confirmed'] else 'CANDIDATE' if rec['matched'] else 'UNKNOWN'
            second = rec.get('second_score')
            second_text = 'n/a' if second is None else f'{second:.3f}'
            print(f"[RECOGNITION] {status} identity={rec['identity']} similarity={rec['score']:.3f} "
                  f"frame={job.frame_id} session={job.lock_session_id} sharpness={sharpness:.1f} "
                  f"size={x2-x1}x{y2-y1} ratio={(y2-y1)/job.image.shape[0]:.3f} "
                  f"second={second_text} confirm={rec['confirm_count']}/{cfg.FACE_ID_CONFIRM_COUNT}")
            if len(self.identity_results) == self.identity_results.maxlen:
                self.reject(self.identity_results[0]['job'], 'IDENTITY_MAILBOX_OVERFLOW')
            self.identity_results.append(result)
            latency.profiler.count('recognition_accepted_count')
            latency.profiler.event('IDENTITY_OBSERVATION_ACCEPTED', **job.correlation(),
                identity=rec['identity'], confirmed=rec['confirmed'],
                outcome='KNOWN_CANDIDATE' if rec['matched'] else 'UNKNOWN',
                reason=None if rec['matched'] else 'LOW_SIMILARITY',
                recognition_started_timestamp=result['recognition_started_timestamp'],
                recognition_finished_timestamp=result['recognition_finished_timestamp'],
                frame_age_ms=(time.monotonic()-job.received)*1000)
            latency.profiler.event('IDENTITY_OBSERVATION', identity=rec['identity'], similarity=rec['score'],
                                   confirmed=rec['confirmed'], confirmation_count=rec['confirm_count'])
        return result

    def _recognition_run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.recognition_pending is not None)
                if self.closed:
                    return
                candidate, self.recognition_pending = self.recognition_pending, None
            try:
                with latency.correlated(**candidate.job.correlation()):
                    latency.profiler.elapsed('RECOGNITION_CANDIDATE_DEQUEUED', candidate.queued_ns,
                        'recognition_mailbox_wait_ms')
                    self._recognize(candidate.job, candidate.result)
            except Exception as exc:
                latency.profiler.event('FACE_RECOGNITION_ERROR', error=type(exc).__name__)
                with self.condition:
                    if self.rejection_reason(candidate.job) is None:
                        self.state = 'COLLECTING'
            finally:
                with self.condition:
                    self.recognition_busy = False
                    if self.state == 'RECOGNIZING':
                        self.state = 'COLLECTING'
                    self.condition.notify_all()

    def panel(self, session, track):
        with self.condition:
            if session != self.session or track != self.track_id or not self.session_valid(session):
                return None
            best = self.shots.best if self.spatial_session == (session, track, self.generation) else None
            return dict(state=self.state, recognition=dict(self.cache) if self.cache else None,
                        crop=best.crop if best else None, frame_id=best.job.frame_id if best else None,
                        session=session, track=track,
                        face_visible=((self.tracker.face is not None and
                            0 <= time.monotonic()-self.tracker.detected <= self.cfg.FACE_TRACK_MAX_AGE)
                            if getattr(self.cfg, 'FACE_TRACKING_ENABLED', True) else
                            (self.face_visible and 0 <= time.monotonic()-self.last_detection <= self.cfg.MAX_CONTROL_OBSERVATION_AGE)))

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                job, self.pending = self.pending, None
            with latency.correlated(**job.correlation()):
                latency.profiler.elapsed('FACE_QUEUE_EXIT', job.queued_ns, 'face_mailbox_wait_ms')
                try:
                    result = self._process(job)
                except Exception as exc:
                    latency.profiler.event('FACE_BRANCH_ERROR', error=type(exc).__name__)
                    self.reject(job, 'INFERENCE_ERROR')
                    continue
                with self.condition:
                    self.completed_frame = job.frame_id
                    if result is not None and self.rejection_reason(job) is None and not result.get('visual_published'):
                        self.result = result
                        self.condition.notify_all()
                    elif result is not None and self.rejection_reason(job) is not None:
                        latency.record_stale(self.rejection_reason(job) or 'FACE_RESULT_EXPIRED')
                    self.condition.notify_all()
