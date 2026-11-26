"""Motion IDs with fail-closed appearance verification and immutable target gallery."""
from contextvars import copy_context
import copy
from concurrent.futures import Future
import threading
import math
import time
import traceback
from uuid import uuid4
from ai.reid_target import ReIDTargetSelector, iou
from telemetry import latency
from ai.overlap_policy import OverlapMemory, overlap_geometry, minor_contact
from ai.approach_risk import ApproachRisk


class HybridReIDTargetSelector(ReIDTargetSelector):
    def reset(self, preserve_reacquire=False):
        super().reset(preserve_reacquire)
        self.verification_epoch = uuid4().hex
        if not preserve_reacquire or not getattr(self, 'target_uid', None):
            self.target_uid = 'target_'+uuid4().hex
        self.tracking_status = 'LOST' if preserve_reacquire else 'MISSING'
        self.identity_status = 'UNCERTAIN'
        self.negative_verifications = 0
        self.transition_reason = 'RESET'
        self.last_positive_verify = None
        self.pending_id = None
        self.pending_box = None
        self.pending_count = 0
        self._confirmation_track = None
        self._confirmation_time = None
        self._pending_time = None
        self._overlap_memory = OverlapMemory()
        self._approach_risk = ApproachRisk()
        self._preoverlap_risk = False
        self._hold_scan_at = float("-inf")
        self._weak_overlap_active = False
        self._previous_observation_time = None

    def _hold(self, reason, missing=False):
        if not self.crossing_hold:
            self._hold_scan_at = float("-inf")
        self.crossing_hold = True
        self.verification_epoch = uuid4().hex
        self.tracking_status = 'MISSING' if missing else 'TRACKED'
        self.identity_status = 'UNCERTAIN'
        self.transition_reason = reason
        self.set_stage('OCCLUDED_HOLD')

    def _start_verification(self, frame, persons, now):
        # One bounded job. The background scorer sees a snapshot, never live state.
        if getattr(self, '_verification', None) is not None:
            return
        if getattr(self.cfg,'REID_CANDIDATE_AWARE',False) and not self._preoverlap_risk:
            target = next((p for p in persons if p.get('track_id')==self.track_id),None)
            if target is None:return
            persons = [p for p in persons if p is target or
                overlap_geometry(target['bbox'],p['bbox'])['overlap']>0 or
                (self.person_box is not None and iou(self.person_box,p['bbox'])>=self.cfg.REID_TRACK_IOU)]
        scorer = copy.copy(self)
        scorer._score_trigger = 'TARGET_LOCKED_PERIODIC_VERIFY'
        epoch = self.verification_epoch
        scorer._score_valid = lambda: (self.verification_epoch == epoch
            and time.monotonic()-now <= self.cfg.REID_VERIFY_RESULT_MAX_AGE)
        observations = copy.deepcopy(persons)
        image = frame.copy()
        future = Future()
        token = (self.verification_epoch, self.lock_session_id, self.track_id)
        self._verification = (future, token, now, self.frame_id, observations)
        self.last_verify = now
        self.identity_status = 'VERIFYING'
        self.transition_reason = 'PERIODIC_VERIFY'
        latency.profiler.count('verification_only_count')
        def run():
            try:
                scores = scorer._scores(image, observations)
                latency.profiler.event('REID_VERIFICATION_COMPLETED',
                    frame_id=scorer.frame_id, target_uid=scorer.target_uid,
                    lock_session_id=scorer.lock_session_id, tracker_id=scorer.track_id,
                    capture_timestamp=None, receive_timestamp=now,
                    inference_timestamp=time.monotonic())
                future.set_result(scores)
            except Exception as exc:
                # Log at the producer, even if a later session rejects the future.
                latency.profiler.event('REID_VERIFICATION_EXCEPTION',
                    frame_id=scorer.frame_id, lock_session_id=token[1],
                    target_track_id=token[2], reid_phase='TARGET_LOCKED_PERIODIC_VERIFY',
                    error_type=type(exc).__name__, error_message=str(exc),
                    traceback=traceback.format_exc(),
                    superseded=self.verification_epoch != token[0])
                future.set_exception(exc)
        context = copy_context()
        threading.Thread(target=lambda: context.run(run), name='reid-verification', daemon=True).start()

    def _take_verification(self, now):
        job = getattr(self, '_verification', None)
        if job is None or not job[0].done():
            return
        future, token, captured, frame_id, persons = job
        self._verification = None
        if (token != (self.verification_epoch, self.lock_session_id, self.track_id)
                or not 0 <= now-captured <= self.cfg.REID_VERIFY_RESULT_MAX_AGE):
            latency.profiler.count('stale_reid_results')
            latency.profiler.count('stale_category_reid_result')
            reason = ('WORKER_RESULT_SUPERSEDED' if token[0] != self.verification_epoch else
                      'SESSION_MISMATCH' if token[1] != self.lock_session_id else
                      'TRACK_MISMATCH' if token[2] != self.track_id else 'DEADLINE_EXPIRED')
            latency.profiler.count('stale_reason_'+reason)
            latency.profiler.event('REID_VERIFICATION_DISCARDED', frame_id=frame_id,
                transition_reason='STALE_RESULT', result_age_ms=(now-captured)*1000)
            return
        try:
            scores = future.result()
            if len(scores) != len(persons) or not all(math.isfinite(v) for v in scores):
                raise ValueError('Invalid verification scores')
        except Exception:
            self.transition_reason = 'VERIFY_ERROR'
            return  # Unavailable evidence is not a negative identity observation.
        index = next(i for i,p in enumerate(persons) if p['track_id'] == self.track_id)
        score = scores[index]
        others = [v for i,v in enumerate(scores) if i != index]
        self.top1 = score
        self.top2 = max(others) if others else None
        self.margin = score-self.top2 if others else None
        latency.profiler.event('TARGET_VERIFICATION', frame_id=frame_id,
            target_uid=self.target_uid, lock_session_id=self.lock_session_id,
            target_track_id=self.track_id, similarity=score, margin=self.margin,
            result_age_ms=(now-captured)*1000)
        if others and self.margin < self.cfg.REID_MARGIN_THRESHOLD:
            self._hold('AMBIGUOUS_IDENTITY')
        elif score >= self.cfg.REID_KEEP_THRESHOLD:
            self.negative_verifications = 0
            self.last_positive_verify = captured
            self._overlap_memory.verified_at = captured
            self._overlap_memory.verified_ids = tuple(p.get('track_id') for p in persons)
            self.identity_status = 'VERIFIED'
        else:
            self.negative_verifications += 1
            self.identity_status = 'VERIFYING'
            if self.negative_verifications >= self.cfg.REID_NEGATIVE_CONFIRMATIONS:
                self._hold('LOW_REID_SCORE')

    def _candidate_overlap(self, candidate, persons):
        box = candidate['bbox']
        rows = []
        unsafe = False
        for p in persons:
            b = p['bbox']
            intersection = max(0, min(box[2],b[2])-max(box[0],b[0])) * max(0, min(box[3],b[3])-max(box[1],b[1]))
            overlap = intersection/max(1, min((box[2]-box[0])*(box[3]-box[1]), (b[2]-b[0])*(b[3]-b[1])))
            # Only identical copies of one tracker observation are redundant.
            same = p is candidate or (p.get('track_id') == candidate.get('track_id')
                                     and tuple(b) == tuple(box))
            unsafe |= (not same and overlap >= self.cfg.REID_CROSSING_OVERLAP
                       and not minor_contact(overlap_geometry(box, b), self.cfg))
            rows.append(dict(track_id=p.get('track_id'), bbox=b, iou=iou(box,b),
                overlap_ratio=overlap, similarity=p.get('reid_score'), confidence=p.get('confidence')))
        now = getattr(self, '_diagnostic_observation_time', self.observation_time) or 0.
        if unsafe and now-getattr(self, '_last_overlap_log', float('-inf')) >= .5:
            self._last_overlap_log = now
            latency.profiler.event('TARGET_OVERLAP_EVIDENCE', frame_id=self.frame_id,
                target_uid=self.target_uid, target_track_id=candidate.get('track_id'),
                observation_timestamp=now, candidates=rows, similarity=self.top1,
                last_positive_evidence_timestamp=self.last_positive_verify,
                margin=self.margin, threshold=self.cfg.REID_CROSSING_OVERLAP,
                decision_reason='PERSON_OVERLAP', identity_status=self.identity_status)
        if candidate.get('track_id') == self.track_id:
            blocked, reason = self._overlap_memory.assess(candidate,persons,self,now,
                getattr(self,'_previous_observation_time',self.observation_time))
            self._weak_overlap_active = reason == 'WEAK_OVERLAP_CONTINUITY'
            self.overlap_reason = reason
            if unsafe:
                latency.profiler.count('overlap_policy_'+reason)
            return blocked
        self.overlap_reason = 'GEOMETRY_OVERLAP' if unsafe else 'NO_AMBIGUOUS_OVERLAP'
        return bool(unsafe)

    def acquisition_allowed(self, candidate, persons):
        # Same geometry gate before confirmation as during continuity/recovery.
        allowed = not self._candidate_overlap(candidate, self._observed_persons)
        if allowed:
            now = self._diagnostic_observation_time
            if (candidate.get('track_id') != self._confirmation_track
                    or self._confirmation_time is None
                    or not 0 <= now-self._confirmation_time <= self.cfg.REID_CONFIRM_MAX_GAP):
                self.confirm = 0
                self.candidate_box = None
            self._confirmation_track = candidate.get('track_id')
            self._confirmation_time = now
        if not allowed:
            self.transition_reason = 'ACQUISITION_OVERLAP'
        return allowed

    def select_person(self, frame, persons, now):
        before = self.stage
        counts = dict(acquire=self.confirm, recovery=self.pending_count)
        result = self._select_person(frame, persons, now)
        if getattr(self.cfg, 'REID_OVERLAP_DIAGNOSTICS', False):
            # Bounded sampling; decisions remain independent of diagnostics.
            if now-getattr(self, '_last_decision_log', float('-inf')) >= .5:
                self._last_decision_log = now
                target = result or next((p for p in persons if p.get('track_id') == self.track_id), None)
                if target is None and persons:
                    target = max(persons, key=lambda p: p.get('reid_score', float('-inf')))
                pairs = [] if target is None else [dict(track_id=p.get('track_id'), bbox=p['bbox'],
                    **overlap_geometry(target['bbox'], p['bbox'])) for p in persons if p is not target]
                latency.profiler.event('OVERLAP_DECISION', frame_id=self.frame_id,
                    observation_timestamp=now, target_track_id=self.track_id,
                    candidate_track_id=target.get('track_id') if target else None,
                    target_bbox=target['bbox'] if target else None, competitors=pairs,
                    continuity_confidence='trusted' if result is not None and self.locked else 'unconfirmed',
                    previous_observation_timestamp=self._previous_observation_time,
                    last_positive_verify=self.last_positive_verify,
                    negative_verifications=self.negative_verifications,
                    confirmation_admitted=(self.confirm > counts['acquire'] or self.pending_count > counts['recovery']),
                    similarity=self.top1, margin=self.margin,
                    confirmation_before=counts,
                    confirmation_after=dict(acquire=self.confirm, recovery=self.pending_count),
                    candidate_accepted=result is not None, overlap_reason=getattr(self, 'overlap_reason', None),
                    decision_reason=self.transition_reason, previous_state=before, new_state=self.stage,
                    detection_exists=bool(persons), target_track_exists=any(p.get('track_id')==self.track_id for p in persons),
                    identity_confirmed=self.locked and not self.crossing_hold,
                    predicted_only=False, genuinely_lost=not self.locked and self.reacquiring,
                    ptz_control_allowed=result is not None and self.locked and not self.crossing_hold)
        return result

    def _select_person(self, frame, persons, now):
        # Low-score detections maintain ByteTrack motion, never authorize a target.
        eligible = [p for p in persons if p.get('confidence', 1.) >= self.cfg.PERSON_CONF_THRESHOLD]
        if self.observation_time is not None and now <= self.observation_time:
            latency.profiler.count('stale_reason_OUT_OF_ORDER_RESULT')
            return None
        self._previous_observation_time = self.observation_time
        self._observed_persons = persons
        self._diagnostic_observation_time = now
        if self.locked:
            self._take_verification(now)
        gate_candidates = list(eligible)
        if self.locked:
            # A low-score observed current track may maintain identity continuity;
            # it must not be skipped by the high-confidence acquisition precheck.
            current_gate = next((p for p in persons if p.get('track_id') == self.track_id),None)
            if current_gate is not None and not any(p is current_gate for p in gate_candidates):
                gate_candidates.append(current_gate)
        if gate_candidates and all(self._candidate_overlap(p, persons) for p in gate_candidates):
            # No candidate can pass the existing geometry gate. OSNet cannot
            # authorize any result here; avoid repeated full-batch GPU work.
            self.observation_time = now
            self.just_locked = False
            self.confirm = self.pending_count = 0
            self.candidate_box = self.pending_box = self.pending_id = None
            if self.locked:
                missing = not any(p.get('track_id') == self.track_id for p in persons)
                self._hold('TRACK_MISSING' if missing else 'PERSON_OVERLAP', missing)
            latency.profiler.count('reid_skipped_all_candidates_overlap')
            return None
        if not self.locked:
            result = super().select_person(frame, eligible, now)
            if result is not None:
                self.last_verify = now
                self.last_positive_verify = now
                self.tracking_status = 'TRACKED'
                self.identity_status = 'VERIFIED'
            return result
        if self.observation_time is not None and now <= self.observation_time:
            latency.profiler.count('stale_reason_OUT_OF_ORDER_RESULT')
            return None
        self.observation_time = now
        self.just_locked = False
        current = next((p for p in persons if p.get('track_id') == self.track_id), None)
        self._preoverlap_risk = self._approach_risk.assess(current,persons,
            (self.lock_session_id,self.track_id),now)
        overlapping = current is not None and self._candidate_overlap(current, persons)
        jump = (current is not None and self.person_box is not None
                and iou(current['bbox'], self.person_box) < self.cfg.REID_TRACK_IOU)
        if current is None or overlapping or jump:
            self._hold('TRACK_MISSING' if current is None else
                       'PERSON_OVERLAP' if overlapping else 'MOTION_DISCONTINUITY', current is None)
        if (not self.crossing_hold and self.last_positive_verify is not None
                and now-self.last_positive_verify > self.cfg.REID_IDENTITY_MAX_AGE):
            self._hold('VERIFY_TIMEOUT')
        if not self.crossing_hold:
            self.person_box = current['bbox']
            self.tracking_status = 'TRACKED'
            weak_due = (getattr(self,'_weak_overlap_active',False) and self._overlap_memory.since is not None
                        and self.last_verify < self._overlap_memory.since)
            approach_due = self._preoverlap_risk and now-self.last_verify >= .2
            if approach_due:
                latency.profiler.event('TARGET_APPROACH_REVERIFY',frame_id=self.frame_id,
                    target_track_id=self.track_id,lock_session_id=self.lock_session_id,
                    evidence='geometry_risk_not_identity_truth')
            if weak_due or approach_due or now-self.last_verify >= self.cfg.REID_VERIFY_INTERVAL:
                self._start_verification(frame, persons, now)
            return current
        recovering = True
        if not eligible:
            self.pending_count = 0
            self.pending_id = self.pending_box = None
            return None
        if overlapping and getattr(self.cfg,'REID_CANDIDATE_AWARE',False):
            if now-self._hold_scan_at < self.cfg.REID_HOLD_SCAN_INTERVAL:
                latency.profiler.count('reid_hold_geometry_throttled')
                return None
            self._hold_scan_at = now
        # Global retry still considers every eligible person, including distant
        # candidates. No hard motion window can make the true target unreachable.
        scores = self._scores(frame, eligible)
        if len(scores) != len(eligible) or not all(math.isfinite(s) for s in scores):
            raise ValueError('Invalid hybrid ReID scores')
        order = sorted(range(len(scores)), key=scores.__getitem__, reverse=True)
        candidate = eligible[order[0]]
        self.top1 = scores[order[0]]
        self.top2 = scores[order[1]] if len(order) > 1 else None
        self.margin = self.top1-self.top2 if self.top2 is not None else None
        threshold = self.cfg.REID_REACQUIRE_THRESHOLD if recovering else self.cfg.REID_ACQUIRE_THRESHOLD
        if (self.top1 < threshold or self._candidate_overlap(candidate, persons)
                or (self.margin is not None and self.margin < self.cfg.REID_MARGIN_THRESHOLD)):
            self.pending_count = 0
            self.pending_id = self.pending_box = None
            return None
        changed = candidate.get('track_id') != self.track_id
        if recovering or changed:
            continuous = (self.pending_id == candidate.get('track_id') and self.pending_box is not None
                          and iou(self.pending_box, candidate['bbox']) >= self.cfg.REID_CONFIRM_IOU
                          and self._pending_time is not None
                          and 0 <= now-self._pending_time <= self.cfg.REID_CONFIRM_MAX_GAP)
            self.pending_count = self.pending_count+1 if continuous else 1
            self.pending_id, self.pending_box = candidate.get('track_id'), candidate['bbox']
            self._pending_time = now
            if self.pending_count < self.cfg.REID_CONFIRM_FRAMES:
                return None
            # Rotate authorization even when a recovered ByteTrack ID is unchanged.
            if changed:
                latency.profiler.count('target_tracker_id_changes')
                latency.profiler.event('TARGET_TRACKER_REBOUND', target_uid=self.target_uid,
                    previous_tracker_id=self.track_id, next_tracker_id=candidate['track_id'],
                    frame_id=self.frame_id, transition_reason='TRACK_ID_CHANGED',
                    similarity=self.top1, margin=self.margin)
            self.track_id = candidate['track_id']
            self.track_generation += 1
            self.lock_session_id = 'lock_'+uuid4().hex
            self.locked_ns = time.perf_counter_ns()
            self.just_locked = True
            self.face_box = None
            self.face_missing = 0
        self.pending_count = 0
        self.pending_id = self.pending_box = None
        self.last_verify = now
        self.last_positive_verify = now
        self.negative_verifications = 0
        self.identity_status = 'VERIFIED'
        self.tracking_status = 'TRACKED'
        self.transition_reason = 'TARGET_REACQUIRED'
        self.verification_epoch = uuid4().hex
        self.crossing_hold = False
        self.person_box = candidate['bbox']
        candidate['reid_score'] = self.top1
        candidate['appearance_supported'] = True
        self.set_stage('TARGET_LOCKED')
        return candidate

    def finish_frame(self, person, face, face_observed=True):
        # Stale face evidence must never extend an unverified body identity.
        return super().finish_frame(person, face if person is not None else None, face_observed)

    def snapshot(self):
        result = super().snapshot()
        result.update(target_uid=self.target_uid, lock_session_id=self.lock_session_id,
                      tracking_status=self.tracking_status, identity_status=self.identity_status,
                      transition_reason=self.transition_reason,
                      negative_verifications=self.negative_verifications)
        result['loss_state'] = ('TEMP_LOST' if self.locked and self.crossing_hold else
                                'LOST' if self.reacquiring else 'TRACKING' if self.locked else 'SEARCHING')
        return result
