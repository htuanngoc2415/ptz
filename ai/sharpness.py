#!/usr/bin/env python3

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np


# ============================================================
# BASIC SHARPNESS
# ============================================================

def calculate_sharpness(
    image_bgr: np.ndarray
) -> float:
    """
    Calculate image sharpness using Variance of Laplacian.

    Larger value generally means sharper edges.

    Example:

        40    -> blurry
        150   -> medium
        350+  -> relatively sharp

    IMPORTANT:
        These values are NOT universal.

        They depend on:
            - resolution
            - ROI size
            - lighting
            - image content
            - noise
            - compression
            - zoom level

        The current project uses approximately 350 as the
        autofocus lock threshold based on existing testing.
    """

    if image_bgr is None:
        return 0.0

    if image_bgr.size == 0:
        return 0.0

    # --------------------------------------------------------
    # GRAYSCALE
    # --------------------------------------------------------

    if len(image_bgr.shape) == 2:

        gray = image_bgr

    else:

        gray = cv2.cvtColor(
            image_bgr,
            cv2.COLOR_BGR2GRAY
        )

    # Very small ROIs produce unreliable Laplacian values.
    h, w = gray.shape[:2]

    if w < 8 or h < 8:
        return 0.0

    # --------------------------------------------------------
    # LAPLACIAN
    # --------------------------------------------------------

    laplacian = cv2.Laplacian(
        gray,
        cv2.CV_64F
    )

    variance = (
        laplacian.var()
    )

    return float(
        variance
    )


# ============================================================
# ROI SHARPNESS
# ============================================================

def calculate_roi_sharpness(
    frame_bgr: np.ndarray,
    bbox,
    padding_ratio: float = 0.0,
) -> float:
    """
    Calculate Variance of Laplacian inside bbox.

    bbox:
        (x1, y1, x2, y2)

    padding_ratio:
        Optional inward padding.

        Example:

            padding_ratio = 0.1

        removes 10% from each side before calculating
        sharpness. This can reduce influence from bbox
        boundaries/background.
    """

    if frame_bgr is None:
        return 0.0

    if frame_bgr.size == 0:
        return 0.0

    if bbox is None:
        return 0.0

    frame_h, frame_w = (
        frame_bgr.shape[:2]
    )

    x1, y1, x2, y2 = bbox

    x1 = int(x1)
    y1 = int(y1)
    x2 = int(x2)
    y2 = int(y2)

    # --------------------------------------------------------
    # CLAMP
    # --------------------------------------------------------

    x1 = max(
        0,
        min(
            frame_w - 1,
            x1
        )
    )

    y1 = max(
        0,
        min(
            frame_h - 1,
            y1
        )
    )

    x2 = max(
        0,
        min(
            frame_w,
            x2
        )
    )

    y2 = max(
        0,
        min(
            frame_h,
            y2
        )
    )

    if x2 <= x1:
        return 0.0

    if y2 <= y1:
        return 0.0

    # --------------------------------------------------------
    # OPTIONAL INWARD PADDING
    # --------------------------------------------------------

    if padding_ratio > 0.0:

        width = (
            x2 - x1
        )

        height = (
            y2 - y1
        )

        pad_x = int(
            width
            *
            padding_ratio
        )

        pad_y = int(
            height
            *
            padding_ratio
        )

        new_x1 = (
            x1 + pad_x
        )

        new_y1 = (
            y1 + pad_y
        )

        new_x2 = (
            x2 - pad_x
        )

        new_y2 = (
            y2 - pad_y
        )

        if (
            new_x2 > new_x1
            and
            new_y2 > new_y1
        ):

            x1 = new_x1
            y1 = new_y1
            x2 = new_x2
            y2 = new_y2

    # --------------------------------------------------------
    # CROP
    # --------------------------------------------------------

    roi = frame_bgr[
        y1:y2,
        x1:x2
    ]

    if roi.size == 0:
        return 0.0

    return calculate_sharpness(
        roi
    )


# ============================================================
# PERSON SHARPNESS
# ============================================================

def calculate_person_sharpness(
    frame_bgr: np.ndarray,
    person_target: Optional[dict],
) -> float:
    """
    Calculate sharpness of selected person.

    A small inward padding is used to reduce background edges.
    """

    if person_target is None:
        return 0.0

    bbox = person_target.get(
        "bbox"
    )

    if bbox is None:
        return 0.0

    return calculate_roi_sharpness(
        frame_bgr=frame_bgr,
        bbox=bbox,
        padding_ratio=0.05,
    )


# ============================================================
# FACE SHARPNESS
# ============================================================

def calculate_face_sharpness(
    frame_bgr: np.ndarray,
    face_target: Optional[dict],
) -> float:
    """
    Calculate sharpness of selected face.

    Face sharpness is kept separate from person sharpness
    because later they should use different thresholds.
    """

    if face_target is None:
        return 0.0

    bbox = face_target.get(
        "bbox"
    )

    if bbox is None:
        return 0.0

    return calculate_roi_sharpness(
        frame_bgr=frame_bgr,
        bbox=bbox,
        padding_ratio=0.05,
    )
