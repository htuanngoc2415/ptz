"""Experimental target-centric policy. Not imported by production; no motor API.

BBox overlap is geometry, never visibility truth. Extra evidence is required
before emitting localization through overlap. All similarities are heuristics.
"""
from dataclasses import dataclass,field
import math
import numpy as np
from ai.overlap_policy import overlap_geometry
from ai.reid_target import iou


def vector(v):
    try:a=np.asarray(v,dtype=float)
    except (TypeError,ValueError):return None
    return a if a.shape==(512,) and np.isfinite(a).all() and abs(np.linalg.norm(a)-1)<1e-3 else None


@dataclass(frozen=True)
class Observation:
    key: str
    track_id: int | None
    bbox: tuple
    confidence: float
    embedding: object
    parts: dict = field(default_factory=dict)
    support: float = 0.  # fraction of bbox in uncontaminated evidence regions, NOT true visibility
    evidence_kind: str = 'unknown'
    quality: bool = False


class OcclusionAssociation:
    def __init__(self,reference,session):
        self.reference=vector(reference)
        if self.reference is None:raise ValueError('Invalid reference embedding')
        self.session=session;self.last_frame=-1;self.last_time=None
        self.anchor=None;self.box=None;self.observed=None;self.velocity=(0.,0.)
        self.pending=None;self.count=0;self.identity_known=False
        self.state='SEARCHING';self.hypotheses=[]

    def result(self,reason,selected=None,tracking='UNOBSERVED',uncertainty=1.,geometry=False):
        return dict(state=self.state,identity_known=self.identity_known,reason=reason,
            bbox=selected.bbox if selected else None,selected_candidate=selected.key if selected else None,
            track_id=selected.track_id if selected else None,tracking_status=tracking,
            prediction_uncertainty=uncertainty,geometric_overlap=geometry,
            actual_occlusion='UNKNOWN',support=selected.support if selected else None,
            control_eligible=selected is not None and self.state=='LOCKED',
            hypotheses=self.hypotheses[:4],gallery_updated=False,physical_commands=0)

    def observe(self,session,frame_id,timestamp,now,people,camera_motion='STATIC',camera_transform=None):
        if session!=self.session or frame_id<=self.last_frame or not all(math.isfinite(x) for x in (timestamp,now)) or not 0<=now-timestamp<=.25 or (self.last_time is not None and timestamp<=self.last_time):
            return self.result('STALE_SESSION_OR_ORDER')
        self.last_frame=frame_id;self.last_time=timestamp
        if len({p.key for p in people})!=len(people):return self.result('DUPLICATE_OBSERVATION')
        self.hypotheses=[]
        if not people:
            self.pending=None;self.count=0
            self.state='OCCLUDED_UNOBSERVED' if self.observed is not None and timestamp-self.observed<=.8 else 'LOST_LOCALIZATION' if self.identity_known else 'SEARCHING'
            return self.result('DETECTOR_ABSENCE_IDENTITY_NOT_DISPROVEN')
        for p in people:
            b=p.bbox
            if len(b)!=4 or not all(math.isfinite(x) for x in b) or b[2]<=b[0] or b[3]<=b[1] or not math.isfinite(p.confidence) or not 0<=p.confidence<=1 or vector(p.embedding) is None:
                return self.result('INCOMPLETE_COMPETITOR_EVIDENCE')
        ranked=sorted([(float(vector(p.embedding)@self.reference),p) for p in people],key=lambda x:x[0],reverse=True)
        for score,p in ranked:
            anchor_score=float(vector(p.embedding)@vector(self.anchor.embedding)) if self.anchor else None
            self.hypotheses.append(dict(candidate=p.key,reference_similarity=score,anchor_similarity=anchor_score))
        score,best=ranked[0];margin=score-ranked[1][0] if len(ranked)>1 else 2.
        overlap=any(overlap_geometry(best.bbox,p.bbox)['overlap']>=.15 for p in people if p is not best)
        if score<(.70 if self.identity_known else .65) or margin<.08:
            self.state='AMBIGUOUS';self.pending=None;self.count=0
            return self.result('APPEARANCE_CONFLICT',geometry=overlap)
        if not math.isfinite(best.confidence) or best.confidence<.35:
            self.state='VISIBLE_UNVERIFIED';return self.result('LOW_DETECTION_CONFIDENCE')
        if self.anchor is not None and float(vector(best.embedding)@vector(self.anchor.embedding))<.70:
            self.state='AMBIGUOUS';return self.result('ANCHOR_APPEARANCE_CONFLICT',geometry=overlap)
        if self.anchor is None or best.track_id!=self.anchor.track_id or self.observed is None or timestamp-self.observed>.8:
            # New/rebound identity needs repeated clean reference evidence. Neither
            # geometric proximity nor a reused ByteTrack ID can seed the anchor.
            if overlap or not best.quality:
                self.pending=None;self.count=0
                self.state='VISIBLE_UNVERIFIED'
                return self.result('INITIAL_OR_REBOUND_NEEDS_CLEAR_EVIDENCE',geometry=overlap)
            continuous=self.pending and self.pending[0]==best.track_id and 0<timestamp-self.pending[2]<=1. and iou(self.pending[1],best.bbox)>=.4
            self.count=self.count+1 if continuous else 1
            self.pending=(best.track_id,best.bbox,timestamp)
            if self.count<3:
                self.state='VISIBLE_UNVERIFIED';return self.result('CONFIRMING_REFERENCE')
            self.anchor=best;self.identity_known=True;self.count=0;self.pending=None
        elif overlap:
            anchor_score=float(vector(best.embedding)@vector(self.anchor.embedding))
            part_scores=[]
            for key,v in best.parts.items():
                a=vector(self.anchor.parts.get(key));b=vector(v)
                if a is not None and b is not None:part_scores.append(float(a@b))
            trusted_parts=len(part_scores)>=2 and min(part_scores)>=.70
            competitors=[]
            for _,other in ranked[1:]:
                similarities=[float(vector(v)@vector(self.anchor.parts[k])) for k,v in other.parts.items()
                              if k in self.anchor.parts and vector(v) is not None and vector(self.anchor.parts[k]) is not None]
                if similarities:competitors.append(float(np.mean(similarities)))
            if competitors and part_scores and np.mean(part_scores)-max(competitors)<.08:
                self.state='AMBIGUOUS';return self.result('PART_APPEARANCE_CONFLICT',geometry=True)
            if not (anchor_score>=.70 and trusted_parts and best.support>=.15 and best.evidence_kind in ('isolated_parts','instance_mask_parts')):
                self.state='PARTIAL_UNVERIFIED'
                return self.result('GEOMETRY_ONLY_VISIBILITY_UNKNOWN',geometry=True)
            predicted=self.box
            if camera_motion!='STATIC':
                if camera_transform is None:
                    self.state='PARTIAL_UNVERIFIED';return self.result('CAMERA_MOTION_UNCERTAIN',geometry=True)
                H=np.asarray(camera_transform)
                if H.shape!=(2,3) or not np.isfinite(H).all():return self.result('INVALID_CAMERA_TRANSFORM',geometry=True)
                from tools.phase3_local import warp_box
                predicted=warp_box(self.box,H)
            if iou(predicted,best.bbox)<.4:
                self.state='AMBIGUOUS';return self.result('LOCALIZATION_DISAGREEMENT',geometry=True)
        elif not best.quality:
            self.state='VISIBLE_UNVERIFIED';return self.result('POOR_CROP')
        self.box=tuple(best.bbox);self.observed=timestamp;self.state='LOCKED'
        # Anchor/gallery never update on overlap, even when localization is trusted.
        if not overlap:self.anchor=best
        return self.result('TRUSTED_VISIBLE_SUPPORT' if overlap else 'CLEAR_REFERENCE',best,
            'PARTIAL_OBSERVED' if overlap else 'OBSERVED',.35 if overlap else .1,overlap)
