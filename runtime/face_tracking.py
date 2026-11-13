"""Bounded optical-flow face tracking. Detector landmarks alone feed biometrics."""
import cv2
import numpy as np


class FaceTracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.reset()

    def reset(self):
        self.gray = self.face = self.points = None
        self.detected = float('-inf')
        self.scale = 1.
        self.shape = None

    def correct(self, image, face, received):
        self.reset()
        if face is None:
            return
        self.shape = image.shape[:2]
        self.scale = min(1., self.cfg.FACE_TRACK_MAX_DIMENSION/max(self.shape))
        self.gray = self._gray(image)
        self.face = dict(face)
        x1,y1,x2,y2 = face['bbox']
        mask = np.zeros_like(self.gray)
        x1,y1,x2,y2 = (int(v*self.scale) for v in (x1,y1,x2,y2))
        mask[max(0,y1):y2, max(0,x1):x2] = 255
        self.points = cv2.goodFeaturesToTrack(self.gray, 40, .01, 4, mask=mask)
        self.detected = received

    def _gray(self, image):
        if self.scale < 1.:
            image = cv2.resize(image, (max(1,round(image.shape[1]*self.scale)),
                                       max(1,round(image.shape[0]*self.scale))))
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    def update(self, image, received):
        if self.face is None or self.points is None or image.shape[:2] != self.shape or received-self.detected > self.cfg.FACE_TRACK_MAX_AGE:
            self.reset()
            return None
        gray = self._gray(image)
        new, status, _ = cv2.calcOpticalFlowPyrLK(self.gray, gray, self.points, None, maxLevel=self.cfg.FACE_TRACK_PYRAMID_LEVELS)
        if new is None:
            self.reset()
            return None
        back, back_status, _ = cv2.calcOpticalFlowPyrLK(gray, self.gray, new, None, maxLevel=self.cfg.FACE_TRACK_PYRAMID_LEVELS)
        if back is None:
            self.reset()
            return None
        good = ((status.ravel() == 1) & (back_status.ravel() == 1) &
                (np.linalg.norm(back-self.points, axis=2).ravel() < self.cfg.FACE_TRACK_FB_ERROR))
        confidence = float(good.mean())
        if good.sum() < 4 or confidence < self.cfg.FACE_TRACK_MIN_CONFIDENCE:
            self.reset()
            return None
        matrix, inliers = cv2.estimateAffinePartial2D(self.points[good], new[good], method=cv2.RANSAC)
        if matrix is None or inliers is None or float(inliers.mean()) < self.cfg.FACE_TRACK_MIN_CONFIDENCE:
            self.reset()
            return None
        scale = float(np.linalg.norm(matrix[0,:2]))
        if not 1.-self.cfg.FACE_TRACK_MAX_SCALE_CHANGE <= scale <= 1.+self.cfg.FACE_TRACK_MAX_SCALE_CHANGE:  # large geometry changes require SCRFD
            self.reset()
            return None
        matrix[:,2] /= self.scale
        x1,y1,x2,y2 = self.face['bbox']
        corners = np.array([[x1,y1,1],[x2,y1,1],[x2,y2,1],[x1,y2,1]], np.float32) @ matrix.T
        box = tuple(int(v) for v in np.rint(np.r_[corners.min(axis=0),corners.max(axis=0)]))
        h,w = image.shape[:2]
        if not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
            self.reset()
            return None
        self.face = dict(self.face, bbox=box, tracked=True, tracking_confidence=confidence,
                         detection_timestamp=self.detected, timestamp=received)
        self.gray, self.points = gray, new[good].reshape(-1,1,2)
        return dict(self.face)
