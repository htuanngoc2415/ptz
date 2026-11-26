"""Offline event-driven ReID PoC. No threads, models or camera authority.

Call offer for detector observations; take the bounded request in the existing
worker and feed it to ShadowScorer(batched=True). A skipped event is not a fresh
identity observation. Priority never extends the source timestamp deadline.
"""
from dataclasses import dataclass
import copy
import math

from ai.overlap_policy import overlap_geometry
from ai.reid_target import iou


PRIORITY = dict(INITIAL_ACQUISITION=3, APPROACHING_OVERLAP=5, ACTIVE_OVERLAP=4,
    POST_OVERLAP_SEPARATION=6, TRACK_ID_CHANGE=5, TRACKING_UNCERTAIN=3,
    TARGET_REACQUISITION=4, PERIODIC_IDENTITY_CHECK=1)


def crop_quality(frame, persons, cfg):
    """Reuse focus analysis. This proxy cannot certify full-body visibility."""
    from ai.quality.focus_quality import FocusQualityAnalyzer
    focus = FocusQualityAnalyzer(cfg.FOCUS_LAPLACIAN, cfg.FOCUS_TENENGRAD,
                                cfg.FOCUS_EDGE_DENSITY, cfg.FOCUS_MIN_ROI_SIZE)
    h, w = frame.shape[:2]
    result = []
    for p in persons:
        p = dict(p)
        box = p['bbox']
        valid = (len(box) == 4 and all(math.isfinite(x) for x in box)
                 and 0 < box[0] < box[2] < w and 0 < box[1] < box[3] < h)
        body_like = valid and (box[3]-box[1])/(box[2]-box[0]) >= cfg.REID_FULL_BODY_ASPECT
        q = focus.analyze(frame, p) if valid else None
        p['crop_quality'] = ('measured_clear' if q and q['good'] and body_like and not p.get('occluded')
                             else 'unknown')
        p['phase2_quality'] = dict(focus=q, interior_bbox=valid, full_body_like=body_like,
                                  full_body_visibility='unknown')
        result.append(p)
    return result


@dataclass(frozen=True)
class ReIDRequest:
    session: str
    generation: int
    frame_id: int
    timestamp: float
    events: tuple
    priority: int
    quality: int
    frame: object
    persons: tuple


class EventReID:
    def __init__(self, cfg, session, *, enabled=None):
        self.enabled = getattr(cfg, 'IDENTITY_EVENT_REID_ENABLED', False) if enabled is None else enabled
        self.session = session
        self.generation = 0
        self.interval = cfg.IDENTITY_SHADOW_INTERVAL
        self.periodic = min(cfg.REID_VERIFY_INTERVAL, cfg.REID_CONFIRM_MAX_GAP/2)
        self.max_age = cfg.MAX_CONTROL_OBSERVATION_AGE
        self.max_gap = cfg.REID_CONFIRM_MAX_GAP
        self.overlap = cfg.REID_CROSSING_OVERLAP
        self.previous = None
        self.pending = None
        self.last_take = float('-inf')
        self.was_overlap = False
        self.last_events = ()

    def reset(self, session):
        self.generation += 1
        self.session, self.pending, self.previous = session, None, None
        self.was_overlap = False
        self.last_events = ()
        self.last_take = float('-inf')

    def offer(self, *, frame, persons, frame_id, timestamp, now, session, manager):
        if (not self.enabled or session != self.session or manager.session_id != session
                or not all(math.isfinite(v) for v in (timestamp, now))
                or not 0 <= now-timestamp <= self.max_age):
            return False
        if self.previous and (frame_id <= self.previous[0] or timestamp <= self.previous[1]):
            return False
        # Metadata only: never inspect cached OSNet embeddings to choose inference frames.
        people = [dict(candidate_id=p['candidate_id'], track_id=p.get('track_id'),
                       bbox=tuple(p['bbox']), confidence=p['confidence'],
                       crop_quality=p.get('crop_quality', 'unknown'),
                       occluded=p.get('occluded', False)) for p in persons]
        for p in people:
            b = p['bbox']
            if (len(b) != 4 or not all(math.isfinite(v) for v in b)
                    or b[2] <= b[0] or b[3] <= b[1]):
                return False
        active, approaching = False, False
        previous = {p['track_id']: p for p in self.previous[2]} if self.previous else {}
        dt = timestamp-self.previous[1] if self.previous else None
        for i, a in enumerate(people):
            for b in people[i+1:]:
                geometry = overlap_geometry(a['bbox'], b['bbox'])
                active |= geometry['overlap'] >= self.overlap
                # Bbox expansion gives advance warning even on the first frame.
                def expanded(box):
                    dx, dy = (box[2]-box[0])/2, (box[3]-box[1])/4
                    return (box[0]-dx, box[1]-dy, box[2]+dx, box[3]+dy)
                approaching |= geometry['overlap'] < self.overlap and iou(expanded(a['bbox']), expanded(b['bbox'])) > 0
                if dt and 0 < dt <= self.max_gap and all(p['track_id'] in previous for p in (a,b)):
                    projected = []
                    for p in (a,b):
                        old, box = previous[p['track_id']]['bbox'], p['bbox']
                        vx = ((box[0]+box[2])-(old[0]+old[2]))/(2*dt)
                        vy = ((box[1]+box[3])-(old[1]+old[3]))/(2*dt)
                        projected.append(tuple(v+(vx if j%2 == 0 else vy)*self.max_gap
                                               for j,v in enumerate(box)))
                    approaching |= not active and overlap_geometry(*projected)['overlap'] >= self.overlap
        events = []
        if manager.last_trusted is None:
            events.append('INITIAL_ACQUISITION')
        else:
            ids = {p['track_id'] for p in people}
            if manager.last_trusted.candidate.track_id not in ids:
                events.append('TRACK_ID_CHANGE')
            if manager.state.value != 'CONFIRMED_VISIBLE':
                events.extend(('TRACKING_UNCERTAIN', 'TARGET_REACQUISITION'))
        if approaching:
            events.append('APPROACHING_OVERLAP')
        if active:
            events.append('ACTIVE_OVERLAP')
        if self.was_overlap and not active:
            events.append('POST_OVERLAP_SEPARATION')
        if timestamp-self.last_take >= self.periodic:
            events.append('PERIODIC_IDENTITY_CHECK')
        self.previous = (frame_id, timestamp, people)
        self.was_overlap = active
        self.last_events = tuple(events)
        if self.pending and not 0 <= now-self.pending.timestamp <= self.max_age:
            self.pending = None
        # Strongly contaminated crops are not useful fresh identity evidence.
        clear = sum(p['crop_quality'] == 'measured_clear' and not p['occluded']
            and all(overlap_geometry(p['bbox'], q['bbox'])['overlap'] < self.overlap
                    for q in people if p is not q) for p in people)
        if not events or not clear:
            return False
        priority = max(PRIORITY[e] for e in events)
        # A fresh but poor image cannot evict an important clean image.
        rank = (priority, clear)
        if self.pending and rank < (self.pending.priority, self.pending.quality):
            return False
        self.pending = ReIDRequest(session, self.generation, frame_id, timestamp, tuple(events), priority, clear,
                                   frame.copy(), tuple(copy.deepcopy(people)))
        return True

    def take(self, now, session):
        request = self.pending
        if request is None:
            return None
        if session != self.session or request.session != session or not 0 <= now-request.timestamp <= self.max_age:
            self.pending = None
            return None
        if now-self.last_take < self.interval:
            return None
        self.pending = None
        self.last_take = now
        return request

    def valid_result(self, request, now, manager):
        return (self.enabled and request.generation == self.generation
                and request.session == self.session == manager.session_id
                and math.isfinite(now) and 0 <= now-request.timestamp <= self.max_age
                and request.frame_id > manager.last_frame_id
                and (manager.last_timestamp is None or request.timestamp > manager.last_timestamp))
