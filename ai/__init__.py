"""
AI package for PTZ camera system.

Responsibilities:
    - YOLO person detection
    - ONNX SCRFD face detection
    - ONNX ArcFace recognition
    - Target selection
    - Face detection inside selected person ROI
    - Sharpness calculation

Motor control and state-machine logic are intentionally kept
outside this package.
"""

from .detector import (
    YOLODetector,
    PersonDetector,
)

from .face import (
    FaceDetector,
    detect_face_inside_person,
    align_face,
    FaceRecognizer,
    FaceDatabase,
    FaceMatcher,
)

from .target_selector import (
    select_target,
    select_person_target,
    select_face_target,
)

from .sharpness import (
    calculate_sharpness,
    calculate_roi_sharpness,
    calculate_person_sharpness,
    calculate_face_sharpness,
)


__all__ = [
    # detector
    "YOLODetector",
    "PersonDetector",
    "FaceDetector",
    "detect_face_inside_person",
    "align_face",
    "FaceRecognizer",
    "FaceDatabase",
    "FaceMatcher",

    # target selector
    "select_target",
    "select_person_target",
    "select_face_target",

    # sharpness
    "calculate_sharpness",
    "calculate_roi_sharpness",
    "calculate_person_sharpness",
    "calculate_face_sharpness",
]
