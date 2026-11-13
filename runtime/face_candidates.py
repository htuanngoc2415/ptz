"""Real-frame best shots with configurable, multi-factor quality and bounded storage."""
from dataclasses import dataclass
import cv2
import numpy as np


@dataclass
class Candidate:
    job: object
    face: dict
    score: float
    crop: object
    metrics: dict
    result: dict
    queued_ns: int | None = None


class BestShots:
    def __init__(self, cfg):
        self.cfg = cfg
        self.items = []
        self.best = None
        self.last_sample = float('-inf')
        self.started = None
        self.last_used = float('-inf')
        self.last_signature = None

    def add(self, job, face, result, stable=True):
        if self.started is None:
            self.started = job.received
        if job.received-self.last_sample < self.cfg.FACE_CANDIDATE_SPACING:
            return
        x1,y1,x2,y2 = face['bbox']
        crop = job.image[y1:y2,x1:x2]
        points = np.asarray(face.get('landmarks', []), dtype=float)
        if crop.size == 0 or points.shape != (5,2) or not np.isfinite(points).all():
            return
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        eyes = np.linalg.norm(points[1]-points[0])
        if eyes < 2 or not ((points[:,0] >= x1)&(points[:,0] <= x2)&
                           (points[:,1] >= y1)&(points[:,1] <= y2)).all():
            return
        pose = max(0., 1.-abs(points[2,0]-points[:2,0].mean())/eyes-
                   abs(points[1,1]-points[0,1])/eyes)
        metrics = dict(size=min(1., min(crop.shape[:2])/self.cfg.FACE_RECOGNITION_MIN_SIZE),
            sharpness=min(1., float(cv2.Laplacian(gray, cv2.CV_64F).var())/
                          self.cfg.FACE_RECOGNITION_MIN_SHARPNESS), pose=pose,
            illumination=max(0.,1.-abs(float(gray.mean())-127.5)/127.5),
            contrast=min(1.,float(gray.std())/40.), stability=float(stable),
            alignment=min(1., eyes/max(1.,x2-x1)*3.))
        weights = self.cfg.FACE_QUALITY_WEIGHTS
        score = sum(metrics[k]*v for k,v in weights.items())/sum(weights.values())
        candidate = Candidate(job, dict(face), score, crop.copy(), metrics, dict(result))
        self.last_sample = job.received
        self.items.append(candidate)
        self.items.sort(key=lambda c:c.score, reverse=True)
        del self.items[self.cfg.FACE_CANDIDATE_LIMIT:]
        if self.best is None or score > self.best.score:
            self.best = candidate

    def pop(self, now):
        fallback = self.started is not None and now-self.started >= self.cfg.FACE_QUALITY_FALLBACK_TIMEOUT
        self.items = [c for c in self.items if c.job.received > self.last_used and now-c.job.received <= self.cfg.FACE_CANDIDATE_MAX_AGE]
        for candidate in self.items:
            if candidate.job.received > self.last_used and (candidate.score >= self.cfg.FACE_QUALITY_MIN_SCORE or fallback):
                signature = cv2.resize(cv2.cvtColor(candidate.crop, cv2.COLOR_BGR2GRAY),(16,16)).astype(float)
                if (self.last_signature is not None and
                    candidate.job.received-self.last_used < self.cfg.FACE_DUPLICATE_RETRY_TIMEOUT and
                    np.mean(np.abs(signature-self.last_signature)) < self.cfg.FACE_DUPLICATE_PIXEL_DELTA):
                    continue
                self.last_signature = signature
                self.last_used = candidate.job.received
                self.items.remove(candidate)
                return candidate
        return None
