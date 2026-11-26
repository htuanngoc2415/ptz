"""Short image-space prediction for the existing pan/tilt path, not identity evidence."""
import math
import numpy as np
from runtime.camera_motion import CameraMotionEstimator


class TargetPrediction:
    def __init__(self):
        self.previous=None
        self.estimator=CameraMotionEstimator(max_gap=.3)

    def reset(self):
        self.previous=None

    def observe(self, frame, box, people, timestamp, binding, now):
        previous=self.previous
        self.previous=None
        if box is None or not binding[0] or not 0<=now-timestamp<=.25:
            return None
        boxes=[tuple(p['bbox']) for p in people]
        self.previous=(frame.copy(),tuple(box),boxes,timestamp,binding)
        if previous is None:return None
        old,old_box,old_people,old_time,old_binding=previous
        dt=timestamp-old_time
        if binding!=old_binding or not 0<dt<=.3 or old.shape!=frame.shape:return None
        motion=self.estimator.estimate(old,frame,old_time,timestamp,old_people,boxes)
        # During ongoing camera movement future image velocity is not known.
        if not motion.valid or motion.confidence<.7 or motion.classification!='LOW_IMAGE_MOTION':return None
        a=(np.array(old_box[:2])+old_box[2:])/2
        b=(np.array(box[:2])+box[2:])/2
        transform=np.asarray(motion.transform)
        velocity=(b-(transform[:,:2]@a+transform[:,2]))/dt
        h,w=frame.shape[:2]
        if not np.isfinite(velocity).all() or abs(velocity[0])>w or abs(velocity[1])>h:return None
        return dict(velocity=tuple(map(float,velocity)),timestamp=timestamp,binding=binding,
                    camera_motion='LOW_IMAGE_MOTION',confidence=motion.confidence)


def predict_box(box,shape,evidence,now,binding):
    """Compensate only measured observation age. Never invent motor-response latency."""
    if not evidence or evidence.get('binding')!=binding:return box,None
    age=now-evidence['timestamp']
    if not 0<=age<=.25 or evidence.get('confidence',0)<.7 or evidence.get('camera_motion')!='LOW_IMAGE_MOTION':return box,None
    vx,vy=evidence['velocity'];h,w=shape[:2]
    if not all(math.isfinite(x) for x in (vx,vy)):return box,None
    horizon=min(.15,age)
    offsets=[];edges=[]
    for i,(v,limit) in enumerate(((vx,w),(vy,h))):
        center=(box[i]+box[i+2])/2
        shift=max(-.05*limit,min(.05*limit,v*horizon))
        # Prediction may stop at center, but cannot reverse a motor command.
        if (center-limit/2)*(center+shift-limit/2)<0:shift=limit/2-center
        offsets.append(shift)
        edge=box[i+2] if v>0 else box[i]
        if abs(v)>1:edges.append(max(0.,(limit-edge)/v if v>0 else -edge/v))
    dx,dy=offsets
    return (box[0]+dx,box[1]+dy,box[2]+dx,box[3]+dy),dict(
        prediction_horizon_s=horizon,target_velocity=(vx,vy),time_to_edge_s=min(edges) if edges else None,
        prediction_basis='GMC_VALIDATED_OBSERVATION_AGE_ONLY')
