"""Phase 2B OFFLINE ONLY: bounded observed localization, never new identity.

Wraps the unchanged strict manager. Flags default OFF; no runtime imports this
module. Provisional output never changes trusted history, gallery, or selection.
"""
from dataclasses import dataclass
import math
import numpy as np
from ai.target_identity import embedding_tuple
from ai.reid_target import iou
from ai.overlap_policy import overlap_geometry


@dataclass(frozen=True)
class ContinuityPolicy:
    quality_continuation: bool = False
    low_similarity_continuation: bool = False


class ContinuityVerifier:
    def __init__(self, manager, flags=None):
        self.manager = manager
        self.flags = flags or ContinuityPolicy()
        if manager.policy.gallery_enabled:
            raise ValueError('Phase2B offline requires gallery adaptation OFF')
        self.risk_latched = False
        self.last_risk = None

    def interrupt(self):
        self.manager.interrupt()
        self.risk_latched = True
        self.last_risk = 'EXPLICIT_RESET'

    @staticmethod
    def expanded(box):
        dx=(box[2]-box[0])/2;dy=(box[3]-box[1])/4
        return (box[0]-dx,box[1]-dy,box[2]+dx,box[3]+dy)

    def observe(self, **kwargs):
        m=self.manager
        if not (self.flags.quality_continuation or self.flags.low_similarity_continuation):
            return m.observe(**kwargs)
        candidates=tuple(kwargs['candidates']);kwargs=dict(kwargs,candidates=candidates)
        anchor=m.last_trusted;velocity=m.velocity;history=tuple(m.history)
        result=m.observe(**kwargs)
        if result['action']=='REJECT':
            return result  # rejected evidence cannot change wrapper risk state either
        now,ts=kwargs['now'],kwargs['timestamp']
        result=dict(result,localization_candidate=result['selected_candidate'],
                    localization_bbox=result['bbox'],provisional=False,identity_updated=result['selected_candidate'] is not None,
                    continuity_state='CONFIRMED_CLEAR' if result['selected_candidate'] else 'AMBIGUOUS',
                    continuation_reason=None,physical_commands=0)
        # A strict confirmation clears prior uncertainty; the underlying manager still
        # requires its original appearance/margin/geometry/multi-observation gates.
        if result['selected_candidate'] is not None:
            selected=next(c for c in candidates if c.candidate_id==result['selected_candidate'])
            approaching=any(iou(self.expanded(selected.bbox),self.expanded(c.bbox))>0
                            for c in candidates if c is not selected)
            self.risk_latched=approaching
            self.last_risk='APPROACHING_COMPETITOR' if approaching else None
            if approaching:result['continuity_state']='CONFIRMED_AT_RISK'
            return result
        def deny(reason,state='AMBIGUOUS',latch=False):
            if latch:self.risk_latched=True;self.last_risk=reason
            result.update(continuation_reason=reason,continuity_state=state)
            return result
        if not candidates:
            return deny('NO_OBSERVED_LOCALIZATION','OCCLUDED_PREDICTED' if kwargs.get('absence')=='occluded' else 'LOST',True)
        if anchor is None:return deny('NO_TRUSTED_ANCHOR','REVERIFYING')
        # Provenance/finite/normalization of every competitor must be valid.
        for c in candidates:
            if (c.evidence_session!=m.session_id or c.evidence_frame_id!=kwargs['frame_id']
                or c.evidence_timestamp!=ts or len(c.bbox)!=4
                or not all(math.isfinite(v) for v in c.bbox)
                or c.bbox[2]<=c.bbox[0] or c.bbox[3]<=c.bbox[1]
                or not math.isfinite(c.confidence) or not 0<=c.confidence<=1):
                return deny('INVALID_COMPETITOR_EVIDENCE',latch=True)
            try:embedding_tuple(c.embedding)
            except (TypeError,ValueError):return deny('INVALID_COMPETITOR_EVIDENCE',latch=True)
        same=[c for c in candidates if c.track_id is not None and c.track_id==anchor.candidate.track_id]
        if len(same)!=1:return deny('TRACK_ID_CHANGED_OR_DUPLICATED','REVERIFYING',True)
        c=same[0];others=[p for p in candidates if p is not c]
        if c.occluded:return deny('OCCLUDED_OBSERVATION','OCCLUDED_PREDICTED',True)
        if c.policy_rejection or c.confidence<m.policy.detection_threshold:
            return deny('UNSAFE_DETECTION',latch=True)
        if any(overlap_geometry(c.bbox,p.bbox)['overlap']>0 for p in others):
            return deny('OVERLAP_RISK','CONFIRMED_AT_RISK',True)
        if any(iou(self.expanded(c.bbox),self.expanded(p.bbox))>0 for p in others):
            return deny('APPROACHING_COMPETITOR','CONFIRMED_AT_RISK',True)
        if self.risk_latched:return deny('REVERIFY_AFTER_'+str(self.last_risk),'REVERIFYING')
        budget=min(m.policy.max_age,m.policy.max_gap)
        if not (0<ts-anchor.timestamp<=budget and 0<now-anchor.timestamp<=budget):
            return deny('ANCHOR_EXPIRED','LOST',True)
        if len(history)<2 or velocity is None or not all(math.isfinite(v) for v in velocity):
            return deny('INSUFFICIENT_TRUSTED_MOTION','REVERIFYING')
        if (history[-1].candidate.track_id!=history[-2].candidate.track_id
            or not 0<history[-1].timestamp-history[-2].timestamp<=m.policy.max_gap):
            return deny('UNSTABLE_TRUSTED_MOTION','REVERIFYING')
        dt=ts-anchor.timestamp
        predicted=tuple(v+(velocity[0] if i%2==0 else velocity[1])*dt for i,v in enumerate(anchor.candidate.bbox))
        anchor_iou=iou(c.bbox,anchor.candidate.bbox);motion_iou=iou(c.bbox,predicted)
        if min(anchor_iou,motion_iou)<m.policy.confirmation_iou:
            return deny('GEOMETRY_OR_MOTION_CONFLICT','REVERIFYING',True)
        anchor_score=float(np.dot(c.embedding,anchor.candidate.embedding))
        ref=max(float(np.dot(c.embedding,v)) for v in m.reference_views)
        rival_ref=max((max(float(np.dot(p.embedding,v)) for v in m.reference_views) for p in others),default=-1.)
        rival_anchor=max((float(np.dot(p.embedding,anchor.candidate.embedding)) for p in others),default=-1.)
        if ref-rival_ref<m.policy.margin or anchor_score-rival_anchor<m.policy.margin:
            return deny('COMPETITOR_APPEARANCE_CONFLICT',latch=True)
        if anchor_score<m.policy.reacquire:
            return deny('TRUSTED_APPEARANCE_CONFLICT','REVERIFYING',True)
        for audit in result['candidate_audit']:
            if audit['candidate_id']==c.candidate_id and audit.get('classification_reason') in ('CONFIRMED_DISTRACTOR','DISTRACTOR_TARGET_CONFLICT'):
                return deny('DISTRACTOR_MEMORY_CONFLICT',latch=True)
        quality=(self.flags.quality_continuation and result['reason']=='UNCERTAIN_CROP_QUALITY'
                 and c.crop_quality!='measured_clear' and ref>=m.policy.keep)
        similarity=(self.flags.low_similarity_continuation and result['reason']=='LOW_REFERENCE_SIMILARITY'
                    and c.crop_quality=='measured_clear')
        if not (quality or similarity):return deny('GATE_NOT_IN_ABLATION','REVERIFYING')
        # No selected_candidate or bbox: existing controller consumers cannot mistake
        # provisional localization for verified identity. No predicted box is emitted.
        result.update(localization_candidate=c.candidate_id,localization_bbox=tuple(c.bbox),
            provisional=True,identity_updated=False,identity_confidence='unverified',
            localization_confidence='observed_provisional',visibility_confidence='unknown',
            continuity_state='REVERIFYING',continuation_reason='BOUNDED_QUALITY_CONTINUATION' if quality else 'BOUNDED_SIMILARITY_CONTINUATION',
            continuation_evidence=dict(anchor_frame=anchor.frame_id,anchor_age_s=now-anchor.timestamp,
              budget_s=budget,anchor_iou=anchor_iou,motion_iou=motion_iou,anchor_similarity=anchor_score,
              reference_similarity=ref,reference_margin=ref-rival_ref,anchor_margin=anchor_score-rival_anchor))
        assert m.last_trusted is anchor and len(m.history)==len(history)
        assert not result['ptz_authorized'] and not m.gallery
        return result
