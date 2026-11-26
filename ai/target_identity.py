"""Phase 1 identity memory. Single owner, no inference, threads or motor authority.

Scores are cosine measurements, not calibrated probabilities. All admission
thresholds come from the existing ReID policy. Callers supply observation time
and consumption time in the SAME clock domain (media PTS for offline replay).
"""
from collections import deque
from dataclasses import dataclass, replace
from enum import Enum
import math
from uuid import uuid4

import numpy as np

from ai.reid_target import iou
from ai.overlap_policy import overlap_geometry


class IdentityState(str, Enum):
    CONFIRMED_VISIBLE = 'CONFIRMED_VISIBLE'
    VISIBLE_UNVERIFIED = 'VISIBLE_UNVERIFIED'
    AMBIGUOUS = 'AMBIGUOUS'
    OCCLUDED_PREDICTED = 'OCCLUDED_PREDICTED'
    LOST_IN_FOV = 'LOST_IN_FOV'
    OUT_OF_FOV = 'OUT_OF_FOV'


def embedding_tuple(value):
    a = np.asarray(value, dtype=np.float64)
    if a.shape != (512,) or not np.isfinite(a).all() or abs(np.linalg.norm(a)-1) > 1e-4:
        raise ValueError('Expected normalized finite 512-dimensional embedding')
    return tuple(float(x) for x in a)


@dataclass(frozen=True)
class IdentityPolicy:
    acquire: float
    keep: float
    reacquire: float
    margin: float
    confirmations: int
    confirmation_iou: float
    max_gap: float
    max_age: float
    detection_threshold: float
    overlap_threshold: float
    gallery_enabled: bool = False
    gallery_capacity: int = 8  # memory bound, not a confidence threshold

    @classmethod
    def from_config(cls, cfg):
        return cls(cfg.REID_ACQUIRE_THRESHOLD, cfg.REID_KEEP_THRESHOLD,
                   cfg.REID_REACQUIRE_THRESHOLD, cfg.REID_MARGIN_THRESHOLD,
                   cfg.REID_CONFIRM_FRAMES, cfg.REID_CONFIRM_IOU,
                   cfg.REID_CONFIRM_MAX_GAP, cfg.MAX_CONTROL_OBSERVATION_AGE,
                   cfg.PERSON_CONF_THRESHOLD, cfg.REID_CROSSING_OVERLAP,
                   getattr(cfg, 'IDENTITY_MEMORY_GALLERY_ENABLED', False))

    def __post_init__(self):
        values = (self.acquire, self.keep, self.reacquire, self.margin,
                  self.confirmation_iou, self.max_gap, self.max_age,
                  self.detection_threshold, self.overlap_threshold)
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite policy')
        if not (0 <= self.keep <= self.acquire <= self.reacquire <= 1
                and 0 <= self.margin <= 2 and 0 <= self.confirmation_iou <= 1
                and 0 <= self.detection_threshold <= 1 and 0 <= self.overlap_threshold <= 1
                and self.max_gap > 0 and self.max_age > 0
                and self.confirmations >= 1 and self.gallery_capacity >= 1):
            raise ValueError('Invalid identity policy')


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    track_id: int | None
    bbox: tuple
    confidence: float
    embedding: tuple | None
    evidence_frame_id: int
    evidence_timestamp: float
    evidence_session: str
    occluded: bool = False
    policy_rejection: str | None = None
    quality_approved: bool = False  # Explicit external crop review; unknown fails closed.
    camera_state: tuple | None = None
    crop_quality: str = 'unknown'  # measured_clear is a proxy, never a GT/body-visibility label.


@dataclass(frozen=True)
class Phase2Policy:
    filtering: bool = False
    memory: bool = False
    association: bool = False
    capacity: int = 32
    ttl: float = 3.
    minor_target_cover: float = .05
    minor_competitor_cover: float = .25
    minor_iou: float = .05

    @classmethod
    def from_config(cls, cfg):
        return cls(getattr(cfg, 'IDENTITY_CANDIDATE_FILTERING_ENABLED', False),
                   getattr(cfg, 'IDENTITY_DISTRACTOR_MEMORY_ENABLED', False),
                   getattr(cfg, 'IDENTITY_MULTI_CUE_ENABLED', False),
                   minor_target_cover=cfg.REID_MINOR_TARGET_COVER,
                   minor_competitor_cover=cfg.REID_MINOR_COMPETITOR_COVER,
                   minor_iou=cfg.REID_MINOR_IOU)

    def __post_init__(self):
        if self.capacity < 1 or not math.isfinite(self.ttl) or self.ttl <= 0:
            raise ValueError('Invalid distractor memory bounds')
        if self.memory and not self.filtering or self.association and not (self.filtering and self.memory):
            raise ValueError('Memory requires filtering; association requires filtering and memory')


@dataclass(frozen=True)
class DistractorEvidence:
    candidate: Candidate
    reference_similarity: float
    count: int
    trajectory: tuple
    reason: str = 'REPEATED_CLEAR_NEGATIVE_WITH_POSITIVE_TARGET'


@dataclass(frozen=True)
class TrustedObservation:
    frame_id: int
    timestamp: float
    candidate: Candidate
    reference_similarity: float
    margin: float | None


class TargetIdentityManager:
    def __init__(self, reference, policy, *, session_id=None, target_uid=None,
                 reference_views=(), phase2=None):
        self.policy = policy
        self.phase2 = phase2 or Phase2Policy()
        self._session_id = session_id or 'identity_session_'+uuid4().hex
        self._target_uid = target_uid or 'target_'+uuid4().hex
        self._original = embedding_tuple(reference)
        self.reference_views = (self._original,) + tuple(embedding_tuple(e) for e in reference_views)
        self.gallery = deque(maxlen=policy.gallery_capacity)
        self.history = deque(maxlen=128)
        self.tracklets = deque(maxlen=128)
        # Unverified alternatives, never silently promoted to known negatives.
        self.distractor_history = deque(maxlen=128)
        self.last_trusted = None
        self.velocity = None
        self.state = IdentityState.LOST_IN_FOV
        self.last_frame_id = -1
        self.last_timestamp = None
        self._pending = None
        self._pending_count = 0
        self.active_track_id = None
        self._resume_anchor_valid = False
        self.distractors = deque(maxlen=self.phase2.capacity)

    def _classify_candidates(self, candidates, frame_id, timestamp):
        """Bounded negative evidence; track ID alone never links two observations.

        A negative needs repeated, clear, separated observations and a co-observed
        strong target, plus appearance/geometry/time agreement. History expires
        from its last clean supporting observation, never from a HOLD.
        """
        self.distractors = deque((e for e in self.distractors
            if 0 < timestamp-e.candidate.evidence_timestamp <= self.phase2.ttl),
            maxlen=self.phase2.capacity)
        values = {}
        for c in candidates:
            try:
                if (c.evidence_session != self.session_id or c.evidence_frame_id != frame_id
                        or c.evidence_timestamp != timestamp or len(c.bbox) != 4
                        or not all(math.isfinite(v) for v in c.bbox)
                        or c.bbox[2] <= c.bbox[0] or c.bbox[3] <= c.bbox[1]
                        or not math.isfinite(c.confidence) or not 0 <= c.confidence <= 1):
                    continue
                emb = embedding_tuple(c.embedding)
                c = replace(c, bbox=tuple(c.bbox), embedding=emb)
                score = max(float(np.dot(emb, v)) for v in self.reference_views)
                values[c.candidate_id] = (c, score)
            except (ValueError, TypeError):
                continue
        entries = list(self.distractors)
        used = set()
        labels = {}
        complete = len(values) == len(candidates)
        for key, (c, score) in values.items():
            rivals = [(p, s) for k, (p, s) in values.items() if k != key]
            clean = (c.crop_quality == 'measured_clear' and not c.occluded
                     and not c.policy_rejection and c.confidence >= self.policy.detection_threshold)
            separated = all(overlap_geometry(c.bbox, p.bbox)['overlap'] == 0 for p, _ in rivals)
            margin = score-max((s for _, s in rivals), default=-1.)
            status, why = 'IDENTITY_UNCERTAIN', 'INSUFFICIENT_CLEAR_IDENTITY_EVIDENCE'
            matches = []
            if self.phase2.memory:
                for i, e in enumerate(entries):
                    old = e.candidate
                    similarity = float(np.dot(c.embedding, old.embedding))
                    if (i not in used and old.evidence_session == self.session_id
                            and similarity >= self.policy.reacquire
                            and iou(c.bbox, old.bbox) >= self.policy.confirmation_iou):
                        matches.append((similarity, i, e))
            matches.sort(key=lambda x: x[0], reverse=True)
            match = matches[0] if matches and (len(matches) == 1 or
                matches[0][0]-matches[1][0] >= self.policy.margin) else None
            if match and match[2].count >= self.policy.confirmations and clean:
                status = 'CONFIRMED_DISTRACTOR' if score < self.policy.keep else 'IDENTITY_UNCERTAIN'
                why = 'CONFIRMED_DISTRACTOR' if score < self.policy.keep else 'DISTRACTOR_TARGET_CONFLICT'
            elif clean and complete and score >= self.policy.acquire and margin >= self.policy.margin:
                status, why = 'TARGET_CANDIDATE', 'REFERENCE_AND_MARGIN_SUPPORTED'
            anchor_conflict = (self.last_trusted is not None
                and 0 < timestamp-self.last_trusted.timestamp <= self.policy.max_gap
                and float(np.dot(c.embedding, self.last_trusted.candidate.embedding)) >= self.policy.keep)
            positive_other = any(s >= self.policy.reacquire and p.crop_quality == 'measured_clear'
                and not p.occluded and not p.policy_rejection and p.confidence >= self.policy.detection_threshold
                and all(s-other_s >= self.policy.margin for other_key, (_, other_s) in values.items()
                        if other_key != p.candidate_id)
                and all(overlap_geometry(p.bbox, other.bbox)['overlap'] == 0
                        for other_key, (other, _) in values.items() if other_key != p.candidate_id)
                for p, s in rivals)
            if (self.phase2.memory and complete and clean and separated and positive_other
                    and score < self.policy.keep and not anchor_conflict):
                prior = match[2] if match else None
                continuous = prior and timestamp-prior.candidate.evidence_timestamp <= self.policy.max_gap
                count = prior.count+1 if continuous else 1
                trajectory = ((prior.trajectory if continuous else ()) +
                              ((frame_id, timestamp, c.track_id, c.bbox),))[-8:]
                evidence = DistractorEvidence(c, score, count, trajectory)
                if match:
                    entries[match[1]] = evidence
                    used.add(match[1])
                else:
                    entries.append(evidence)
                    used.add(len(entries)-1)
                if count >= self.policy.confirmations:
                    status, why = 'CONFIRMED_DISTRACTOR', 'CONFIRMED_DISTRACTOR'
            labels[key] = dict(identity_class=status, classification_reason=why,
                               crop_quality=c.crop_quality, reference_similarity=score)
        self.distractors = deque(entries[-self.phase2.capacity:], maxlen=self.phase2.capacity)
        return labels

    def _phase2_minor_contact(self, c, other, labels):
        """Only a previously confirmed negative can explain a minor contact."""
        if not self.phase2.association or self.last_trusted is None:
            return False
        a, b = labels.get(c.candidate_id, {}), labels.get(other.candidate_id, {})
        g = overlap_geometry(c.bbox, other.bbox)
        return (a.get('identity_class') == 'TARGET_CANDIDATE'
            and a.get('reference_similarity', -1) >= self.policy.reacquire
            and b.get('identity_class') == 'CONFIRMED_DISTRACTOR'
            and c.crop_quality == other.crop_quality == 'measured_clear'
            and 0 < c.evidence_timestamp-self.last_trusted.timestamp <= self.policy.max_gap
            and float(np.dot(c.embedding, self.last_trusted.candidate.embedding)) >= self.policy.reacquire
            and iou(c.bbox, self.last_trusted.candidate.bbox) >= self.policy.confirmation_iou
            and g['target_cover'] <= self.phase2.minor_target_cover
            and g['competitor_cover'] <= self.phase2.minor_competitor_cover
            and g['iou'] <= self.phase2.minor_iou)

    @property
    def session_id(self):
        return self._session_id

    @property
    def target_uid(self):
        return self._target_uid

    @property
    def original_reference(self):
        return self._original  # immutable tuple, including when caller mutates input

    def _break_confirmation(self):
        self._pending = None
        self._pending_count = 0

    def interrupt(self):
        """Lose localization continuity without forgetting the target's identity."""
        self._break_confirmation()
        self._resume_anchor_valid = False
        self.active_track_id = None
        self.state = IdentityState.LOST_IN_FOV

    def _result(self, action, reason, now, *, selected=None, audits=(), top1=None, top2=None):
        age = max(0., now-self.last_trusted.timestamp) if self.last_trusted else None
        return dict(target_uid=self.target_uid, session_id=self.session_id,
                    action=action, reason=reason, state=self.state.value,
                    selected_candidate=selected.candidate_id if selected else None,
                    track_id=selected.track_id if selected else None,
                    bbox=selected.bbox if selected else None,
                    top1=top1, top2=top2, margin=top1-top2 if top2 is not None else None,
                    identity_confidence='confirmed' if selected else 'unverified',
                    localization_confidence='observed_bbox' if selected else 'unknown',
                    visibility_confidence='clear' if selected else self.state.value,
                    uncertainty_age_s=age, uncertainty_calibrated=False,
                    candidate_audit=list(audits), gallery_size=len(self.gallery),
                    ptz_authorized=False)

    def observe(self, *, session_id, frame_id, timestamp, now, candidates,
                absence='unknown'):
        # Invalid jobs cannot mutate identity, pending counts, history or gallery.
        if session_id != self.session_id:
            return self._result('REJECT', 'SESSION_MISMATCH', now)
        if not all(math.isfinite(v) for v in (timestamp, now)) or not 0 <= now-timestamp <= self.policy.max_age:
            return self._result('REJECT', 'STALE_OR_FUTURE_OBSERVATION', now)
        if frame_id <= self.last_frame_id or (self.last_timestamp is not None and timestamp <= self.last_timestamp):
            return self._result('REJECT', 'OUT_OF_ORDER_OBSERVATION', now)
        if absence not in ('unknown', 'occluded', 'out_of_fov'):
            raise ValueError('Unknown absence evidence')
        candidates = tuple(candidates)
        ids = [c.candidate_id for c in candidates]
        if len(ids) != len(set(ids)):
            return self._result('REJECT', 'DUPLICATE_CANDIDATE_ID', now)
        self.last_frame_id, self.last_timestamp = frame_id, timestamp
        previous_state = self.state
        self.active_track_id = None
        if not candidates:
            self._resume_anchor_valid = False
            self._break_confirmation()
            self.state = (IdentityState.OUT_OF_FOV if absence == 'out_of_fov' else
                          IdentityState.OCCLUDED_PREDICTED if absence == 'occluded' else IdentityState.LOST_IN_FOV)
            # No synthetic box is emitted: an occlusion hypothesis is not a localization.
            return self._result('HOLD', 'NO_OBSERVATION_'+absence.upper(), now)
        audits, usable = [], []
        labels = self._classify_candidates(candidates, frame_id, timestamp) if self.phase2.filtering else {}
        for c in candidates:
            reason, score, gallery_score, vector = None, None, None, None
            if (c.evidence_session != self.session_id or c.evidence_frame_id != frame_id
                    or c.evidence_timestamp != timestamp):
                reason = 'EVIDENCE_FRAME_OR_SESSION_MISMATCH'
            elif (len(c.bbox) != 4 or not all(math.isfinite(v) for v in c.bbox)
                  or c.bbox[2] <= c.bbox[0] or c.bbox[3] <= c.bbox[1]
                  or not math.isfinite(c.confidence) or not 0 <= c.confidence <= 1):
                reason = 'INVALID_GEOMETRY_OR_CONFIDENCE'
            else:
                try:
                    vector = embedding_tuple(c.embedding)
                    score = max(float(np.dot(vector, v)) for v in self.reference_views)
                    gallery_score = max((float(np.dot(vector, v)) for v in self.gallery), default=None)
                except (TypeError, ValueError):
                    reason = 'NO_VALID_APPEARANCE'
            if reason is None:
                if c.policy_rejection:
                    reason = 'POLICY:'+c.policy_rejection
                elif c.occluded:
                    reason = 'OCCLUDED_CROP'
                elif c.confidence < self.policy.detection_threshold:
                    reason = 'LOW_DETECTION_CONFIDENCE'
                elif any(other is not c and overlap_geometry(c.bbox, other.bbox)['overlap'] >= self.policy.overlap_threshold
                         and not self._phase2_minor_contact(c, other, labels)
                         for other in candidates if len(other.bbox) == 4 and all(math.isfinite(v) for v in other.bbox)):
                    reason = 'PERSON_OVERLAP'
                if self.phase2.filtering and reason is None:
                    label = labels.get(c.candidate_id, {})
                    if label.get('classification_reason') in ('CONFIRMED_DISTRACTOR', 'DISTRACTOR_TARGET_CONFLICT'):
                        reason = label['classification_reason']
                    elif c.crop_quality != 'measured_clear':
                        reason = 'UNCERTAIN_CROP_QUALITY'
                    elif (self.phase2.association and self.last_trusted is not None
                          and 0 < timestamp-self.last_trusted.timestamp <= self.policy.max_gap
                          and float(np.dot(vector, self.last_trusted.candidate.embedding)) < self.policy.reacquire):
                        reason = 'APPEARANCE_HISTORY_CONFLICT'
            audit = dict(candidate_id=c.candidate_id, track_id=c.track_id,
                         reference_similarity=score, gallery_similarity=gallery_score,
                         reason=reason, accepted=False)
            audits.append(audit)
            if self.phase2.filtering:
                audit.update(labels.get(c.candidate_id, dict(identity_class='IDENTITY_UNCERTAIN',
                             classification_reason='INVALID_EVIDENCE')))
            if vector is not None:
                usable.append((score, c, vector, audit))
        ranked = sorted(usable, key=lambda row: row[0], reverse=True)
        top1 = ranked[0][0] if ranked else None
        top2 = ranked[1][0] if len(ranked) > 1 else None
        reason = None
        if not ranked:
            reason = 'NO_VALID_APPEARANCE'
        elif any(a['reason'] in ('NO_VALID_APPEARANCE', 'EVIDENCE_FRAME_OR_SESSION_MISMATCH',
                                'INVALID_GEOMETRY_OR_CONFIDENCE') for a in audits):
            reason = 'INCOMPLETE_COMPETITOR_EVIDENCE'
        else:
            score, best, vector, audit = ranked[0]
            # Do not retain mutable arrays/lists supplied by the caller.
            best = replace(best, bbox=tuple(best.bbox), embedding=vector,
                           camera_state=tuple(best.camera_state) if best.camera_state is not None else None)
            continuing = (previous_state == IdentityState.CONFIRMED_VISIBLE and self.last_trusted is not None
                          and timestamp-self.last_trusted.timestamp <= self.policy.max_gap
                          and best.track_id == self.last_trusted.candidate.track_id
                          and iou(best.bbox, self.last_trusted.candidate.bbox) >= self.policy.confirmation_iou)
            # A brief rejected crop is not automatically a new identity search.
            # Anchor to the last ACCEPTED observation, never to a chain of HOLDs.
            # A changed/unknown MOT ID or real absence still requires reacquisition.
            resume = (previous_state in (IdentityState.AMBIGUOUS, IdentityState.VISIBLE_UNVERIFIED)
                      and self._resume_anchor_valid
                      and self.last_trusted is not None
                      and 0 < timestamp-self.last_trusted.timestamp <= self.policy.max_gap
                      and best.track_id is not None
                      and best.track_id == self.last_trusted.candidate.track_id
                      and iou(best.bbox, self.last_trusted.candidate.bbox) >= self.policy.confirmation_iou
                      and float(np.dot(vector, self.last_trusted.candidate.embedding)) >= self.policy.reacquire)
            threshold = self.policy.keep if continuing or resume else self.policy.reacquire if self.last_trusted else self.policy.acquire
            audit['reference_threshold'] = threshold
            audit['continuity_mode'] = 'continuing' if continuing else 'bounded_resume' if resume else 'reacquire' if self.last_trusted else 'acquire'
            if audit['reason']:
                reason = audit['reason']
            elif score < threshold:
                reason = 'LOW_REFERENCE_SIMILARITY'
            elif top2 is not None and top1-top2 < self.policy.margin:
                reason = 'AMBIGUOUS_MARGIN'
            elif continuing:
                self._break_confirmation()
            else:
                prev = self._pending
                continuous = (prev is not None and 0 < timestamp-prev[0] <= self.policy.max_gap
                              and iou(best.bbox, prev[1].bbox) >= self.policy.confirmation_iou
                              and float(np.dot(vector, prev[2])) >= self.policy.reacquire)
                # Identity continuity needs appearance + geometry + time, not an equal MOT ID.
                self._pending_count = self._pending_count+1 if continuous else 1
                self._pending = (timestamp, best, vector)
                if self._pending_count < self.policy.confirmations:
                    reason = 'AWAITING_MULTI_OBSERVATION_CONFIRMATION'
        if reason:
            if reason != 'AWAITING_MULTI_OBSERVATION_CONFIRMATION':
                self._break_confirmation()
            self.state = (IdentityState.VISIBLE_UNVERIFIED if reason == 'AWAITING_MULTI_OBSERVATION_CONFIRMATION'
                          else IdentityState.AMBIGUOUS)
            for a in audits:
                if a['reason'] is None:
                    a['reason'] = reason
            return self._result('HOLD', reason, now, audits=audits, top1=top1, top2=top2)
        reacquired = self.last_trusted is not None and (previous_state != IdentityState.CONFIRMED_VISIBLE
                      or best.track_id != self.last_trusted.candidate.track_id)
        trusted = TrustedObservation(frame_id, timestamp, best, score, top1-top2 if top2 is not None else None)
        if self.last_trusted:
            dt = timestamp-self.last_trusted.timestamp
            a, b = self.last_trusted.candidate.bbox, best.bbox
            self.velocity = ((b[0]+b[2]-a[0]-a[2])/(2*dt), (b[1]+b[3]-a[1]-a[3])/(2*dt))
        if not self.tracklets or self.tracklets[-1]['track_id'] != best.track_id or reacquired:
            self.tracklets.append(dict(track_id=best.track_id, first=timestamp, last=timestamp))
        else:
            self.tracklets[-1]['last'] = timestamp
        self.last_trusted = trusted
        self._resume_anchor_valid = True
        self.history.append(trusted)
        self.active_track_id = best.track_id
        self.state = IdentityState.CONFIRMED_VISIBLE
        self._break_confirmation()
        # No mean embedding and no self-training from occluded/unreviewed crops.
        # Require strong ORIGINAL-reference support, even after a gallery is built.
        if (self.policy.gallery_enabled and best.quality_approved and not best.occluded
                and score >= self.policy.reacquire
                and all(overlap_geometry(best.bbox, c.bbox)['overlap'] == 0
                        for c in candidates if c.candidate_id != best.candidate_id)):
            if vector not in self.gallery:
                self.gallery.append(vector)
        for _, other, emb, _ in ranked[1:]:
            self.distractor_history.append(dict(frame_id=frame_id, timestamp=timestamp,
                candidate=other, embedding=emb, status='UNVERIFIED_ALTERNATIVE'))
        for a in audits:
            a['accepted'] = a['candidate_id'] == best.candidate_id
            a['reason'] = 'CONFIRMED_MULTI_CUE' if a['accepted'] else a['reason'] or 'NOT_SELECTED'
        return self._result('REACQUIRE' if reacquired else 'MATCH', 'CONFIRMED_MULTI_CUE', now,
                            selected=best, audits=audits, top1=top1, top2=top2)
