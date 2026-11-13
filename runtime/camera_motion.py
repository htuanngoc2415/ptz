"""CPU-only observational camera motion. No tracker, identity or motor decisions."""
from dataclasses import dataclass,asdict
import datetime,math,time
import cv2
import numpy as np


@dataclass
class MotionEstimate:
    valid: bool = False
    reason: str = 'UNINITIALIZED'
    transform: object = None  # previous -> current, original-image pixels
    dx: float | None = None
    dy: float | None = None
    matches: int = 0
    inliers: int = 0
    inlier_ratio: float = 0.
    reprojection_error: float | None = None
    confidence: float = 0.  # quality heuristic, not a calibrated probability
    classification: str = 'UNKNOWN'
    scale: float | None = None
    rotation_degrees: float | None = None
    latency_ms: float = 0.

    def payload(self):return asdict(self)


class CameraMotionEstimator:
    def __init__(self, downscale=2, max_gap=.5):
        self.downscale=max(1,int(downscale));self.max_gap=float(max_gap)

    @staticmethod
    def mask(shape, boxes, osd_boxes=()):
        h,w=shape;mask=np.full((h,w),255,np.uint8)
        mask[:max(2,h//30)]=0;mask[-max(2,h//30):]=0
        mask[:,:max(2,w//50)]=0;mask[:,-max(2,w//50):]=0
        for box in list(boxes)+list(osd_boxes):
            if len(box)!=4 or not np.isfinite(box).all():continue
            x1,y1,x2,y2=box
            pad=5
            x1,x2=np.clip([int(x1)-pad,int(x2)+pad+1],0,w)
            y1,y2=np.clip([int(y1)-pad,int(y2)+pad+1],0,h)
            if x2>x1 and y2>y1:mask[y1:y2,x1:x2]=0
        return mask

    def estimate(self, previous,current,previous_time,current_time,previous_boxes=(),current_boxes=(),osd_boxes=()):
        begin=time.perf_counter();result=MotionEstimate()
        try:
            self._estimate(result,previous,current,previous_time,current_time,previous_boxes,current_boxes,osd_boxes)
        except (cv2.error,ValueError,TypeError,OverflowError) as exc:
            result=MotionEstimate(reason='ESTIMATION_ERROR_'+type(exc).__name__)
        result.latency_ms=(time.perf_counter()-begin)*1000
        return result

    def _estimate(self,r,previous,current,pt,ct,pboxes,cboxes,osd):
        if not math.isfinite(pt) or not math.isfinite(ct) or not 0<ct-pt<=self.max_gap:
            r.reason='INVALID_TIME_GAP';return
        if previous is None or current is None or previous.shape!=current.shape or min(previous.shape[:2])<32:
            r.reason='INVALID_FRAME_SHAPE';return
        h,w=previous.shape[:2];factor=max(self.downscale,w/640)
        size=(max(16,round(w/factor)),max(16,round(h/factor)))
        sx,sy=size[0]/w,size[1]/h
        gray=lambda im:cv2.resize(cv2.cvtColor(im,cv2.COLOR_BGR2GRAY) if im.ndim==3 else im,size)
        a,b=gray(previous),gray(current)
        if a.dtype!=np.uint8 or b.dtype!=np.uint8:r.reason='INVALID_FRAME_TYPE';return
        ma=cv2.resize(self.mask((h,w),pboxes,osd),size,interpolation=cv2.INTER_NEAREST)
        mb=cv2.resize(self.mask((h,w),cboxes,osd),size,interpolation=cv2.INTER_NEAREST)
        if min(cv2.Laplacian(a,cv2.CV_64F).var(),cv2.Laplacian(b,cv2.CV_64F).var())<8:
            r.reason='LOW_TEXTURE_OR_BLUR';return
        points=cv2.goodFeaturesToTrack(a,400,.01,7,mask=ma,blockSize=7)
        if points is None or len(points)<16:r.reason='TOO_FEW_FEATURES';return
        q,ok,_=cv2.calcOpticalFlowPyrLK(a,b,points,None,winSize=(21,21),maxLevel=3)
        if q is None:r.reason='FLOW_FAILED';return
        back,okback,_=cv2.calcOpticalFlowPyrLK(b,a,q,None,winSize=(21,21),maxLevel=3)
        if back is None:r.reason='FLOW_FAILED';return
        pp=points[:,0];qq=q[:,0];good=(ok[:,0]>0)&(okback[:,0]>0)&(np.linalg.norm(back[:,0]-pp,axis=1)<1.2)
        good &= np.isfinite(qq).all(axis=1)
        inside=(qq[:,0]>=0)&(qq[:,0]<size[0])&(qq[:,1]>=0)&(qq[:,1]<size[1])
        for i in np.flatnonzero(good&inside):
            if mb[int(qq[i,1]),int(qq[i,0])]==0:good[i]=False
        good &= inside;pp,qq=pp[good],qq[good];r.matches=len(pp)
        if len(pp)<16:r.reason='TOO_FEW_MATCHES';return
        H,inliers=cv2.estimateAffinePartial2D(pp,qq,method=cv2.RANSAC,ransacReprojThreshold=2.,maxIters=1000,confidence=.99)
        if H is None or not np.isfinite(H).all():r.reason='INVALID_TRANSFORM';return
        keep=inliers.ravel().astype(bool);r.inliers=int(keep.sum());r.inlier_ratio=r.inliers/r.matches
        # Convert an affine warp in resized coordinates back to original pixels.
        D=np.diag([sx,sy,1.]);full=np.vstack([H,[0,0,1.]])
        H=(np.linalg.inv(D)@full@D)[:2]
        predicted=(pp@full[:2,:2].T+full[:2,2]);residual=(predicted-qq)/np.array([sx,sy])
        r.reprojection_error=float(np.median(np.linalg.norm(residual[keep],axis=1))) if keep.any() else None
        r.scale=float(np.sqrt(np.linalg.det(H[:,:2]))) if np.linalg.det(H[:,:2])>0 else 0.
        r.rotation_degrees=float(np.degrees(np.arctan2(H[1,0],H[0,0])))
        grid={(min(3,int(x/size[0]*4)),min(2,int(y/size[1]*3))) for x,y in pp[keep]}
        spread=np.ptp(pp[keep],axis=0)/np.array(size) if keep.any() else np.zeros(2)
        if r.inliers<12 or r.inlier_ratio<.55:r.reason='LOW_INLIER_SUPPORT';return
        if len(grid)<4 or min(spread)<.2:r.reason='LOCALIZED_FEATURES';return
        if r.reprojection_error is None or r.reprojection_error>2.5:r.reason='HIGH_RESIDUAL';return
        if not .85<=r.scale<=1.18 or abs(r.rotation_degrees)>12 or abs(H[0,2])>w*.35 or abs(H[1,2])>h*.35:
            r.reason='IMPLAUSIBLE_GEOMETRY';return
        r.valid=True;r.reason='VALID';r.transform=H.tolist();r.dx=float(H[0,2]);r.dy=float(H[1,2])
        r.confidence=float(r.inlier_ratio*min(1,r.inliers/60)*max(0,1-r.reprojection_error/3))
        r.classification=('SCALE_CHANGE' if abs(r.scale-1)>.015 else 'ROTATION' if abs(r.rotation_degrees)>1. else
                          'TRANSLATION' if math.hypot(r.dx,r.dy)>2 else 'LOW_IMAGE_MOTION')


class MotionObserver:
    """Opt-in diagnostic sidecar; resets on session/frame discontinuity."""
    def __init__(self,cfg):
        self.estimator=CameraMotionEstimator(getattr(cfg,'CAMERA_MOTION_DOWNSCALE',2))
        self.osd=getattr(cfg,'CAMERA_MOTION_OSD_BOXES',())
        self.previous=None;self.recorder=None
        if getattr(cfg,'CAMERA_MOTION_RECORD_ENABLED',False):
            from .motion_recording import MotionRecorder
            self.recorder=MotionRecorder(cfg)

    def reset(self):self.previous=None

    def observe(self,frame,received,frame_id,boxes,session,command=None,capture_timestamp=None,detections=None):
        if frame is None:return None
        previous=self.previous
        self.previous=(frame.copy(),received,frame_id,[list(b) for b in boxes],session)
        if previous is None:result=MotionEstimate(reason='FIRST_FRAME')
        elif session!=previous[4]:result=MotionEstimate(reason='SESSION_CHANGED')
        elif frame_id is None or previous[2] is None or frame_id<=previous[2]:result=MotionEstimate(reason='FRAME_ORDER')
        else:result=self.estimator.estimate(previous[0],frame,previous[1],received,previous[3],boxes,self.osd)
        metadata=dict(frame_id=frame_id,previous_frame_id=previous[2] if previous else None,
            capture_timestamp=capture_timestamp,receive_timestamp=received,session=session,
            observation_monotonic=time.monotonic(),
            observation_wall_time=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ptz_command_active=None,expected_camera_moving=None,zoom_transition=None,settling_estimate=None,
            observed_global_motion=result.payload(),motion_estimation_confidence=result.confidence,
            timestamp_basis='decoded_frame_arrival_monotonic',capture_clock='unavailable' if capture_timestamp is None else 'caller_supplied')
        if command:
            for k in ('ptz_command_active','expected_camera_moving','zoom_transition','settling_estimate'):metadata[k]=command.get(k)
        if self.recorder:
            self.recorder.submit(frame,detections if detections is not None else [{'bbox':b} for b in boxes],metadata)
        return metadata

    def close(self):
        if self.recorder:self.recorder.close()
