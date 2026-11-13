"""Offline-only ByteTrack experiment. Never imported by the application pipeline.

Current STrack is XYAH, not BoT-SORT XYWH. Only translation is admitted:
mean[x,y] += t; Jacobian is I; covariance is propagated with I P I^T plus
bounded image-fit uncertainty on position. Aspect/height/velocities are unchanged.
Scale/rotation estimates fall back; no generic R-kron warp of XYAH is used.
"""
import numpy as np
from .byte_tracking import ByteTrackPersonTracker,IndexedByteTracker


def translation_for(estimate):
    if estimate is None or not estimate.valid or not np.isfinite(estimate.confidence) or estimate.confidence<.35:return None
    try:H=np.asarray(estimate.transform,dtype=float)
    except (ValueError,TypeError):return None
    if H.shape!=(2,3) or not np.isfinite(H).all():return None
    if not np.allclose(H[:,:2],np.eye(2),atol=.01,rtol=0):return None
    return H[:,2].copy()


def warp_xyah(track,translation,error=0.):
    t=np.asarray(translation,dtype=float)
    if t.shape!=(2,) or not np.isfinite(t).all() or not np.isfinite(error):raise ValueError('invalid translation')
    mean=track.mean.copy();cov=track.covariance.copy()
    mean[:2]+=t
    cov[:2,:2]+=np.eye(2)*min(4.,max(0.,float(error))**2)
    track.mean=mean;track.covariance=(cov+cov.T)/2


class TranslationByteTracker(IndexedByteTracker):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.estimate=None;self.applied=0

    def multi_predict(self,stracks):
        super().multi_predict(stracks)
        translation=translation_for(self.estimate)
        if translation is None:return
        # This hook runs exactly once after prediction, before high/low-score
        # association. Unconfirmed tracks also need the same coordinate change.
        seen=set()
        for track in list(stracks)+[t for t in self.tracked_stracks if not t.is_activated]:
            if id(track) in seen or track.mean is None:continue
            seen.add(id(track));warp_xyah(track,translation,self.estimate.reprojection_error or 0.)
        self.applied+=1


class ShadowBranch:
    def __init__(self,cfg,enabled=False):
        self.adapter=ByteTrackPersonTracker(cfg)
        self.enabled=enabled
        if enabled:self.adapter.tracker=TranslationByteTracker(self.adapter.tracker.args,frame_rate=cfg.AI_MAX_FPS)

    def update(self,detections,received,frame_id,estimate=None):
        if self.enabled:self.adapter.tracker.estimate=estimate
        # No image is passed to native BYTETracker.update: native GMC cannot run
        # a second warp. No GT labels/identity data enter this call.
        return self.adapter.update(detections,received,frame_id=frame_id)


def safe_handling_proposal(*,age_s,session_valid,image_quality_good,gmc_valid,prediction_age_s,
                           reject_stale=False,gate_gallery=False,reject_invalid_gmc=True,bound_prediction=False,
                           max_age=.8,max_prediction_age=1.):
    """Independent offline decisions only; does not call control or mutate gallery."""
    fresh=bool(session_valid and np.isfinite(age_s) and 0<=age_s<=max_age)
    return {'allow_new_ptz_observation':fresh if reject_stale else None,
            'allow_gallery_update':bool(fresh and image_quality_good) if gate_gallery else None,
            'allow_gmc':bool(session_valid and gmc_valid) if reject_invalid_gmc else None,
            'allow_predicted_target':bool(session_valid and np.isfinite(prediction_age_s) and 0<=prediction_age_s<=max_prediction_age) if bound_prediction else None}
