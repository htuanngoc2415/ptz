"""ONNX-only face detection, alignment and recognition pipeline."""

from .face_alignment import align_face
from .face_database import FaceDatabase
from .face_detector import FaceDetector, detect_face_inside_person
from .face_matcher import FaceMatcher
from .face_recognizer import FaceRecognizer

__all__ = [
    "FaceDetector",
    "detect_face_inside_person",
    "align_face",
    "FaceRecognizer",
    "FaceDatabase",
    "FaceMatcher",
]
