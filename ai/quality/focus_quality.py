"""Noise-reduced, scale-bounded focus evidence, never a lens position estimate."""
import cv2
import numpy as np


class FocusQualityAnalyzer:
    def __init__(self, laplacian=45., tenengrad=150., edge_density=0.025, min_size=32):
        self.laplacian, self.tenengrad = laplacian, tenengrad
        self.edge_density, self.min_size = edge_density, min_size

    def analyze(self, frame, target):
        if target is None:
            return None
        h, w = frame.shape[:2]
        x1,y1,x2,y2 = map(int, target['bbox'])
        roi = frame[max(0,y1):min(h,y2), max(0,x1):min(w,x2)]
        if roi.size == 0 or min(roi.shape[:2]) < self.min_size:
            return None
        scale = min(1., 160/max(roi.shape[:2]))
        gray = cv2.cvtColor(cv2.resize(roi, None, fx=scale, fy=scale), cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3,3), .6)
        lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        dx, dy = (cv2.Sobel(gray, cv2.CV_32F, a, b) for a,b in ((1,0),(0,1)))
        ten = float(np.mean(dx*dx+dy*dy))
        edge = float(np.mean(cv2.Canny(gray, 40, 100) > 0))
        return dict(laplacian=lap, tenengrad=ten, edge_density=edge,
                    good=lap >= self.laplacian and ten >= self.tenengrad and edge >= self.edge_density)
