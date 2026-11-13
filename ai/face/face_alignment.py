"""Five-point similarity alignment compatible with 112x112 ArcFace models."""

from __future__ import annotations

import cv2
import numpy as np


ARCFACE_TEMPLATE_112 = np.array([
    [38.2946, 51.6963],
    [73.5318, 51.5014],
    [56.0252, 71.7366],
    [41.5493, 92.3655],
    [70.7299, 92.2041],
], dtype=np.float32)


def align_face(image: np.ndarray, landmarks, output_size=(112, 112)) -> np.ndarray:
    if image is None or image.size == 0:
        raise ValueError("image is empty")
    points = np.asarray(landmarks, dtype=np.float32)
    if points.shape != (5, 2) or not np.isfinite(points).all():
        raise ValueError("landmarks must have finite shape (5, 2)")
    output_w, output_h = map(int, output_size)
    if output_w <= 0 or output_h <= 0:
        raise ValueError("output_size must be positive")
    target = ARCFACE_TEMPLATE_112.copy()
    target[:, 0] *= output_w / 112.0
    target[:, 1] *= output_h / 112.0
    matrix, _ = cv2.estimateAffinePartial2D(points, target, method=cv2.LMEDS)
    if matrix is None or not np.isfinite(matrix).all():
        raise ValueError("could not estimate face similarity transform")
    return cv2.warpAffine(
        image, matrix, (output_w, output_h),
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0,
    )
