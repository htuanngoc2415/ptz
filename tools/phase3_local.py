"""Offline CPU localization experiment. Never an identity/control authority.

All thresholds are provisional engineering guards, not calibrated accuracy claims.
Features inside a bbox may belong to background/occluders: risk input and short
anchor lifetime are necessary, but cannot guarantee that drift is detected.
"""
from dataclasses import dataclass
import math
import cv2
import numpy as np


@dataclass(frozen=True)
class Options:
    enabled: bool = False
    max_gap: float = .15
    max_anchor_age: float = .8
    min_points: int = 10
    min_survival: float = .5
    fb_error: float = 1.2
    residual: float = 2.
    min_iou_correction: float = .4


def iou(a, b):
    a, b = np.asarray(a), np.asarray(b)
    d = np.maximum(0, np.minimum(a[2:], b[2:])-np.maximum(a[:2], b[:2]))
    inter = float(np.prod(d))
    return inter / max(1e-9, np.prod(a[2:]-a[:2])+np.prod(b[2:]-b[:2])-inter)


def warp_box(box, matrix):
    x1,y1,x2,y2 = box
    pts = np.array([[x1,y1],[x2,y1],[x1,y2],[x2,y2]]) @ matrix[:,:2].T + matrix[:,2]
    return np.r_[pts.min(0), pts.max(0)]


class LocalTracker:
    def __init__(self, options=Options()):
        self.options = options
        self.reset()

    def reset(self):
        self.box = self.points = self.gray = None
        self.timestamp = self.anchor_time = self.frame_id = self.session = None
        self.status, self.confidence = 'NO_ANCHOR', 0.
        self.diagnostics = {}

    def invalidate(self, reason):
        self.status, self.confidence = reason, 0.
        self.box = self.points = self.gray = None
        return self.get_tracking_state()

    @staticmethod
    def valid_box(box, shape):
        if box is None or len(box) != 4 or not np.isfinite(box).all(): return False
        x1,y1,x2,y2 = box; h,w = shape[:2]
        return 1 <= x1 < x2 < w-1 and 1 <= y1 < y2 < h-1 and min(x2-x1,y2-y1)>=12

    @staticmethod
    def gray_frame(frame):
        if frame is None or frame.dtype != np.uint8: raise ValueError('INVALID_FRAME')
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame.copy()

    def initialize(self, frame, bbox, timestamp, provenance):
        self.reset()
        if not self.options.enabled: return self.invalidate('DISABLED')
        p = provenance
        # Caller asserts a separately reviewed experiment anchor or independently
        # verified identity detection. Track ID/IoU/OSNet score alone do not qualify.
        if p.get('kind') not in ('manual_experiment_anchor','trusted_detection') or not p.get('reviewer') or not p.get('source_sha256') or not p.get('session'):
            return self.invalidate('UNTRUSTED_ANCHOR')
        if not math.isfinite(timestamp) or p.get('timestamp') != timestamp or not isinstance(p.get('frame_id'),int):
            return self.invalidate('INVALID_PROVENANCE_TIME')
        if p.get('risk',False): return self.invalidate('ASSOCIATION_RISK')
        gray = self.gray_frame(frame)
        if not self.valid_box(bbox,gray.shape): return self.invalidate('BOUNDARY')
        box=np.asarray(bbox,dtype=float); x1,y1,x2,y2=box; pad=(box[2:]-box[:2])*.12
        mask=np.zeros_like(gray); lo=np.ceil(box[:2]+pad).astype(int); hi=np.floor(box[2:]-pad).astype(int)
        mask[lo[1]:hi[1],lo[0]:hi[0]]=255
        points=cv2.goodFeaturesToTrack(gray,100,.015,4,mask=mask,blockSize=5)
        if points is None or len(points)<self.options.min_points: return self.invalidate('FEATURE_LOSS')
        self.box,self.points,self.gray=box,points,gray
        self.timestamp=self.anchor_time=timestamp
        self.frame_id,self.session=p['frame_id'],p['session']
        self.provenance=dict(p); self.seed_count=len(points)
        if not self._distributed(points[:,0],box): return self.invalidate('FEATURE_CONCENTRATION')
        self.status,self.confidence='ANCHORED',1.
        return self.get_tracking_state()

    @staticmethod
    def _distributed(points,box):
        span=(points.max(0)-points.min(0))/(box[2:]-box[:2])
        grid=np.clip(((points-box[:2])/(box[2:]-box[:2])*3).astype(int),0,2)
        return bool(min(span)>=.3 and len(set(map(tuple,grid)))>=4)

    def update(self, frame, timestamp, frame_id, session, risk=False):
        if not self.options.enabled: return self.invalidate('DISABLED')
        if self.box is None: return self.get_tracking_state()
        if session != self.session: return self.invalidate('SESSION_CHANGED')
        if not math.isfinite(timestamp) or timestamp<=self.timestamp or frame_id<=self.frame_id: return self.invalidate('OUT_OF_ORDER')
        if timestamp-self.timestamp>self.options.max_gap: return self.invalidate('FRAME_GAP')
        if timestamp-self.anchor_time>self.options.max_anchor_age: return self.invalidate('ANCHOR_EXPIRED')
        if risk: return self.invalidate('OCCLUSION_OR_CROSSING_RISK')
        gray=self.gray_frame(frame)
        if gray.shape!=self.gray.shape: return self.invalidate('RESOLUTION_CHANGED')
        try:
            q,ok,_=cv2.calcOpticalFlowPyrLK(self.gray,gray,self.points,None,winSize=(21,21),maxLevel=3)
            if q is None: return self.invalidate('FLOW_FAILED')
            back,okback,_=cv2.calcOpticalFlowPyrLK(gray,self.gray,q,None,winSize=(21,21),maxLevel=3)
            if back is None: return self.invalidate('FLOW_FAILED')
            p=self.points[:,0]; q=q[:,0]; fb=np.linalg.norm(back[:,0]-p,axis=1)
            good=(ok[:,0]>0)&(okback[:,0]>0)&np.isfinite(q).all(1)&(fb<self.options.fb_error)
            p,q=p[good],q[good]
            if len(p)<self.options.min_points: return self.invalidate('FEATURE_LOSS')
            M,inside=cv2.estimateAffinePartial2D(p,q,method=cv2.RANSAC,ransacReprojThreshold=self.options.residual,maxIters=1000,confidence=.99)
            if M is None or not np.isfinite(M).all(): return self.invalidate('TRANSFORM_FAILED')
            inside=inside.ravel().astype(bool); p,q=p[inside],q[inside]
            survival=len(p)/self.seed_count
            self.diagnostics=dict(survival=survival,points=len(p),fb_median=float(np.median(fb[good])))
            if len(p)<self.options.min_points or survival<self.options.min_survival: return self.invalidate('FEATURE_SURVIVAL')
            scale=float(np.linalg.norm(M[:,0])); rotation=abs(math.degrees(math.atan2(M[1,0],M[0,0])))
            box=warp_box(self.box,M)
            jump=np.linalg.norm((box[:2]+box[2:]-self.box[:2]-self.box[2:])/2)
            if not .92<=scale<=1.08 or rotation>8 or jump>.3*min(self.box[2:]-self.box[:2]): return self.invalidate('SUDDEN_JUMP_OR_SCALE')
            if not self.valid_box(box,gray.shape): return self.invalidate('BOUNDARY')
            if not self._distributed(q,box): return self.invalidate('FEATURE_CONCENTRATION')
            residual=float(np.median(np.linalg.norm(p@M[:,:2].T+M[:,2]-q,axis=1)))
            self.diagnostics=dict(survival=survival,fb_median=float(np.median(fb[good])),residual=residual,scale=scale,rotation=rotation,points=len(q))
            self.box,self.points,self.gray=box,q.astype(np.float32).reshape(-1,1,2),gray
            self.timestamp,self.frame_id=timestamp,frame_id
            self.confidence=float(survival*max(0,1-residual/self.options.residual))
            self.status='LOCAL_VISUAL_ONLY'
        except cv2.error:
            return self.invalidate('FLOW_ERROR')
        return self.get_tracking_state()

    def correct(self, frame, detection):
        p=detection.get('provenance',{})
        if self.box is None: return self.invalidate('REANCHOR_REQUIRES_INITIALIZE')
        if p.get('timestamp')!=self.timestamp or p.get('frame_id')!=self.frame_id or p.get('session')!=self.session or p.get('source_sha256')!=self.provenance['source_sha256']:
            return self.invalidate('CORRECTION_NOT_ALIGNED')
        bbox=detection.get('bbox')
        if not self.valid_box(bbox,frame.shape) or p.get('risk') or iou(self.box,bbox)<self.options.min_iou_correction:
            return self.invalidate('CORRECTION_DISAGREEMENT')
        error=float(np.linalg.norm((self.box[:2]+self.box[2:]-np.asarray(bbox[:2])-np.asarray(bbox[2:]))/2))
        state=self.initialize(frame,bbox,self.timestamp,p)
        self.diagnostics['pre_correction_center_error']=error
        return self.get_tracking_state()

    def get_tracking_state(self):
        return dict(bbox=None if self.box is None else self.box.tolist(),bbox_source='KLT_POC',
                    localization_confidence=self.confidence,identity_status='UNVERIFIED_LOCALIZATION_ONLY',
                    tracking_status=self.status,frame_id=self.frame_id,capture_timestamp=None,
                    observation_timestamp=self.timestamp,anchor_timestamp=self.anchor_time,
                    is_valid_for_control_estimation=False,control_authorized=False,
                    diagnostics=dict(self.diagnostics))
