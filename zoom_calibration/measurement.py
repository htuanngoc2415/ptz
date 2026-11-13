"""Raw image measurements and conservative, explicitly confirmed association."""
from collections import deque
import math
import statistics

def ratio(bbox, height):
    if len(bbox) != 4 or not all(math.isfinite(v) for v in (*bbox, height)) or height <= 0 or bbox[3] <= bbox[1] or bbox[2] <= bbox[0]:
        raise ValueError('Invalid frame or bbox dimensions')
    return (bbox[3] - bbox[1]) / height

def gains(before, after, pulse):
    if not all(math.isfinite(v) for v in (before, after, pulse)) or before <= 0 or after <= 0 or pulse <= 0:
        raise ValueError('Positive ratios and pulse required')
    log = math.log(after / before)
    return dict(delta_ratio=after-before, relative_gain=after/before-1,
                log_ratio_gain=log, effective_gain_per_ms=log/pulse)

def iou(a, b):
    w = max(0, min(a[2], b[2])-max(a[0], b[0]))
    h = max(0, min(a[3], b[3])-max(a[1], b[1]))
    intersection = w*h
    return intersection / max(1, (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection)

class TargetMeasurement:
    """Identity is never reacquired automatically after loss/ambiguity.

    Spatial association is conservative evidence, not biometric identification.
    A fresh click is required to confirm the same target after any invalid gap.
    """
    def __init__(self, cfg):
        self.cfg = cfg
        self.history = deque(maxlen=cfg['filter_window'])
        self.bbox = None
        self.target_id = 0
        self.latched = True
        self.center = None

    def select(self, detection):
        ratio(detection['bbox'], 1)  # Reject malformed detector output before confirmation.
        if not math.isfinite(detection['confidence']):
            raise ValueError('Invalid detection confidence')
        self.target_id += 1
        self.bbox = tuple(detection['bbox'])
        self.center = None
        self.history.clear()
        self.latched = False

    def invalidate(self):
        """Require explicit identity confirmation after an unobserved interval."""
        self.latched = True
        self.history.clear()
        self.center = None

    def update(self, detections, width, height):
        result = dict(target_id=self.target_id or None, tracking_valid=False,
                      measurement_valid=False, invalid_reason='TARGET_NOT_CONFIRMED', bbox_clipped=False)
        if self.latched or self.bbox is None:
            return result
        try:
            if width <= 0 or not math.isfinite(width):
                raise ValueError('Invalid frame width')
            for detection in detections:
                ratio(detection['bbox'], height)
                if not math.isfinite(detection['confidence']):
                    raise ValueError('Invalid confidence')
        except (ValueError, TypeError, KeyError, IndexError):
            self.invalidate()
            result['invalid_reason'] = 'MALFORMED_DETECTION_OR_FRAME'
            return result
        ranked = sorted(((iou(self.bbox, d['bbox']), i, d) for i, d in enumerate(detections)), reverse=True)
        if not ranked or ranked[0][0] < .25 or (len(ranked)>1 and ranked[0][0]-ranked[1][0]<.15):
            self.latched = True
            result['invalid_reason'] = 'TRACKING_LOST_OR_AMBIGUOUS_CONFIRM_TARGET'
            return result
        d = ranked[0][2]
        b = tuple(d['bbox'])
        raw = ratio(b, height)
        center = ((b[0]+b[2])/2/width, (b[1]+b[3])/2/height)
        clipped = b[0]<=2 or b[1]<=2 or b[2]>=width-2 or b[3]>=height-2
        reason = ''
        if d['confidence'] < self.cfg['confidence']:
            reason = 'LOW_CONFIDENCE'
        elif clipped:
            reason = 'BBOX_CLIPPED_PERSON_RANGE_EXCEEDED'
        elif (b[2]-b[0])/(b[3]-b[1])>.85:
            reason = 'SUSPECTED_PARTIAL_BODY_OR_POSE'
        elif self.center and math.dist(center, self.center)>self.cfg['max_center_motion']:
            reason = 'EXCESSIVE_TARGET_MOTION'
        elif self.history and abs(raw/statistics.median(x[4] for x in self.history)-1)>self.cfg['outlier_fraction']:
            reason = 'UNEXPLAINED_SIZE_OUTLIER'
        self.bbox = b
        self.center = center
        if reason:
            self.latched = True
        else:
            self.history.append((*b, raw))
        filtered = tuple(statistics.median(x[i] for x in self.history) for i in range(4)) if self.history else None
        result.update(bbox_x1=b[0], bbox_y1=b[1], bbox_x2=b[2], bbox_y2=b[3],
                      bbox_height=b[3]-b[1], bbox_width=b[2]-b[0],
                      person_ratio_raw=raw, person_ratio_filtered=ratio(filtered,height) if filtered else None,
                      bbox_filtered=filtered, confidence=d['confidence'], tracking_valid=not bool(reason),
                      bbox_clipped=clipped, measurement_valid=not bool(reason), invalid_reason=reason)
        return result
