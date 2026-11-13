#!/usr/bin/env python3

from __future__ import annotations

from typing import Optional


# ============================================================
# BBOX UTILITIES
# ============================================================

def bbox_width(
    detection: dict
) -> int:

    x1, y1, x2, y2 = (
        detection["bbox"]
    )

    return max(
        0,
        x2 - x1
    )


def bbox_height(
    detection: dict
) -> int:

    x1, y1, x2, y2 = (
        detection["bbox"]
    )

    return max(
        0,
        y2 - y1
    )


def bbox_area(
    detection: dict
) -> int:

    return (
        bbox_width(detection)
        *
        bbox_height(detection)
    )


def bbox_center(
    detection: dict
) -> tuple[float, float]:

    x1, y1, x2, y2 = (
        detection["bbox"]
    )

    return (
        (x1 + x2) / 2.0,
        (y1 + y2) / 2.0,
    )


# ============================================================
# TARGET SELECTOR
# ============================================================

def select_target(
    detections: list[dict],
    frame_shape,
    mode: str = "center",
) -> Optional[dict]:
    """
    Select one detection as tracking target.

    Supported modes
    ---------------
    highest_confidence
        Select detection with highest YOLO confidence.

    largest
        Select largest bounding box.

    center
        Select object whose bbox center is closest
        to frame center.
    """

    if not detections:
        return None

    if frame_shape is None:
        return None

    mode = str(
        mode
    ).lower()

    # ========================================================
    # HIGHEST CONFIDENCE
    # ========================================================

    if mode == "highest_confidence":

        return max(
            detections,
            key=lambda det:
                float(
                    det.get(
                        "confidence",
                        0.0
                    )
                )
        )

    # ========================================================
    # LARGEST
    # ========================================================

    if mode == "largest":

        return max(
            detections,
            key=bbox_area
        )

    # ========================================================
    # CENTER
    # ========================================================

    frame_h = frame_shape[0]
    frame_w = frame_shape[1]

    frame_cx = (
        frame_w / 2.0
    )

    frame_cy = (
        frame_h / 2.0
    )

    def center_distance_squared(
        detection
    ):

        cx, cy = bbox_center(
            detection
        )

        dx = (
            cx
            -
            frame_cx
        )

        dy = (
            cy
            -
            frame_cy
        )

        return (
            dx * dx
            +
            dy * dy
        )

    return min(
        detections,
        key=center_distance_squared
    )


# ============================================================
# PERSON TARGET
# ============================================================

def select_person_target(
    detections: list[dict],
    frame_shape,
    mode: str = "center",
) -> Optional[dict]:
    """
    Select target person.

    Separate wrapper is intentionally provided so the
    application can later add person-specific selection
    logic without changing generic select_target().
    """

    return select_target(
        detections=detections,
        frame_shape=frame_shape,
        mode=mode,
    )


# ============================================================
# FACE TARGET
# ============================================================

def select_face_target(
    detections: list[dict],
    frame_shape,
    mode: str = "largest",
) -> Optional[dict]:
    """
    Select target face.

    Normally face detections are already restricted to the
    selected person's ROI, so "largest" is a reasonable
    default.
    """

    return select_target(
        detections=detections,
        frame_shape=frame_shape,
        mode=mode,
    )
