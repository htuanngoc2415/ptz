"""Conservative, evidence-bounded treatment of minor geometric contact."""
from ai.reid_target import iou


def overlap_geometry(a,b):
    area=lambda box:max(0,box[2]-box[0])*max(0,box[3]-box[1])
    inter=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return dict(iou=iou(a,b),overlap=inter/max(1,min(area(a),area(b))),target_cover=inter/max(1,area(a)),
                competitor_cover=inter/max(1,area(b)))

def minor_contact(geometry, cfg):
    """Both crops must remain mostly uncontaminated; containment is not minor."""
    return (getattr(cfg, 'REID_MINOR_OVERLAP_ENABLED', False)
            and geometry['target_cover'] <= cfg.REID_MINOR_TARGET_COVER
            and geometry['competitor_cover'] <= cfg.REID_MINOR_COMPETITOR_COVER
            and geometry['iou'] <= cfg.REID_MINOR_IOU)


class OverlapMemory:
    def __init__(self):self.reset()
    def reset(self):
        self.key=None;self.since=None;self.ids=();self.verified_at=None;self.verified_ids=()
    def assess(self,candidate,persons,selector,now,previous_observation):
        cfg=selector.cfg;box=candidate['bbox']
        conflicts=[(p,overlap_geometry(box,p['bbox'])) for p in persons
            if p is not candidate and not (p.get('track_id')==candidate.get('track_id') and tuple(p['bbox'])==tuple(box))]
        conflicts=[(p,g) for p,g in conflicts if g['overlap']>=cfg.REID_CROSSING_OVERLAP
                   and not minor_contact(g, cfg)]
        key=(selector.lock_session_id,selector.track_id)
        if key!=self.key:self.reset();self.key=key
        if not conflicts:self.reset();self.key=key;return False,'NO_OVERLAP'
        if not getattr(cfg,'REID_TEMPORAL_OVERLAP_ENABLED',False):return True,'GEOMETRY_OVERLAP_LEGACY'
        old=selector.person_box;positive=selector.last_positive_verify
        stable=(selector.locked and not selector.crossing_hold and candidate.get('track_id')==selector.track_id
            and old is not None and previous_observation is not None and 0<=now-previous_observation<=cfg.MAX_CONTROL_OBSERVATION_AGE
            and positive is not None and 0<=now-positive<=cfg.REID_WEAK_EVIDENCE_MAX_AGE
            and selector.negative_verifications==0 and selector.identity_status in ('VERIFIED','VERIFYING')
            and iou(old,box)>=cfg.REID_TRACK_IOU)
        if not stable:return True,'OVERLAP_WITHOUT_TRUSTED_CONTINUITY'
        old_area=max(1,(old[2]-old[0])*(old[3]-old[1]));area=(box[2]-box[0])*(box[3]-box[1])
        if not cfg.REID_WEAK_AREA_MIN<=area/old_area<=cfg.REID_WEAK_AREA_MAX:return True,'MERGE_SPLIT_RISK'
        for other,g in conflicts:
            if (g['target_cover']>cfg.REID_WEAK_TARGET_COVER or g['iou']>cfg.REID_WEAK_IOU
                    or iou(old,other['bbox'])>=iou(old,box)-cfg.REID_ASSOCIATION_RISK_MARGIN):
                return True,'IDENTITY_THREATENING_OVERLAP'
        if any(p.get('track_id') is None for p,g in conflicts):return True,'UNIDENTIFIED_CONFLICT'
        ids=tuple(sorted(p['track_id'] for p,g in conflicts))
        if self.since is None:self.since=now;self.ids=ids
        elif self.ids!=ids:return True,'OVERLAP_CANDIDATES_CHANGED'
        verified=(self.verified_at is not None and self.verified_at>=self.since
            and 0<=now-self.verified_at<=cfg.REID_WEAK_EVIDENCE_MAX_AGE
            and set(ids)<=set(self.verified_ids))
        if now-self.since<=cfg.REID_WEAK_OVERLAP_GRACE or verified:return False,'WEAK_OVERLAP_CONTINUITY'
        return True,'WEAK_OVERLAP_UNVERIFIED_TIMEOUT'
