"""Bounded-cost ROI quality analysis. Thresholds belong to the caller."""
import cv2
import numpy as np


class ImageQualityAnalyzer:
    def __init__(self, dark=90, delta=45, shadow=0.35, shadow_level=60):
        self.dark, self.delta, self.shadow = dark, delta, shadow
        self.shadow_level = shadow_level

    def analyze(self, frame, target):
        if frame is None or target is None:
            return None
        h, w = frame.shape[:2]
        box = np.asarray(target.get('bbox', []), dtype=float)
        if box.shape != (4,) or not np.isfinite(box).all():
            return None
        x1, y1, x2, y2 = box.astype(int)
        x1, x2 = np.clip([x1, x2], 0, w)
        y1, y2 = np.clip([y1, y2], 0, h)
        if x2-x1 < 8 or y2-y1 < 8:
            return None
        crop = frame[y1:y2, x1:x2]
        scale = min(1., 256 / max(crop.shape[:2]))
        roi = cv2.cvtColor(cv2.resize(crop, None, fx=scale, fy=scale), cv2.COLOR_BGR2GRAY)
        small = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY)
        mask = np.ones(small.shape, bool)
        mask[int(y1*90/h):int(np.ceil(y2*90/h)), int(x1*160/w):int(np.ceil(x2*160/w))] = False
        bg = small[mask]
        p10, p50, p90 = np.percentile(roi, [10, 50, 90])
        metrics = dict(person_mean_luma=float(roi.mean()), background_mean_luma=float(bg.mean()) if bg.size else None,
                       shadow_ratio=float(np.mean(roi < self.shadow_level)), highlight_ratio=float(np.mean(roi >= 245)),
                       p10=float(p10), p50=float(p50), p90=float(p90), local_contrast=float(p90-p10),
                       laplacian_variance=float(cv2.Laplacian(roi, cv2.CV_64F).var()))
        metrics['backlit'] = bool(bg.size and metrics['person_mean_luma'] < self.dark
                                 and metrics['background_mean_luma']-metrics['person_mean_luma'] > self.delta
                                 and metrics['shadow_ratio'] >= self.shadow)
        return metrics
