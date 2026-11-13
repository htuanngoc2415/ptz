#!/usr/bin/env python3

from __future__ import annotations
from event_logging import event_print as print

import sys
import time
import traceback
from typing import Optional

import numpy as np
from telemetry import latency
from runtime.scheduling import scheduled


# ============================================================
# OPTIONAL ULTRALYTICS IMPORT
# ============================================================

try:
    from ultralytics import YOLO

except ImportError as exc:

    print(
        "[AI] Không import được ultralytics.\n"
        "Cài bằng:\n"
        "pip install ultralytics\n"
        f"\nChi tiết: {exc}",
        file=sys.stderr,
    )

    YOLO = None


# ============================================================
# GENERIC YOLO DETECTOR
# ============================================================

class YOLODetector:
    """
    Generic Ultralytics YOLO detector.

    Can be used for:
        - Person detection
        - Face detection
        - Vehicle detection
        - Other YOLO detection models

    Detection output format:

        {
            "bbox": (x1, y1, x2, y2),
            "confidence": 0.93,
            "class_id": 0,
            "type": "PERSON"
        }

    All bbox coordinates are integer pixel coordinates.
    """

    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.60,
        class_id: Optional[int] = 0,
        device=0,
        imgsz: int = 640,
        name: str = "OBJECT",
        verbose: bool = False,
    ):

        if YOLO is None:
            raise RuntimeError(
                "Ultralytics chưa được cài đặt."
            )

        self.model_path = model_path

        self.conf_threshold = float(
            conf_threshold
        )

        self.iou_threshold = float(
            iou_threshold
        )

        self.class_id = class_id

        self.device = device

        self.imgsz = int(imgsz)

        self.name = str(
            name
        ).upper()

        self.verbose = bool(
            verbose
        )

        self.model = None

        self.last_inference_ms = 0.0
        self.last_error = None
        self.fatal_error = None

        self.load_model()

    # ========================================================
    # LOAD MODEL
    # ========================================================

    def load_model(
        self
    ) -> None:

        print(
            f"[AI] Loading {self.name} model:"
        )

        print(
            f"     {self.model_path}"
        )

        start = time.perf_counter()

        self.model = YOLO(
            self.model_path
        )
        self.model.add_callback('on_predict_start', lambda predictor: self._instrument_predictor(predictor))

        elapsed = (
            time.perf_counter()
            -
            start
        )

        print(
            f"[AI] {self.name} model loaded "
            f"in {elapsed:.2f} s"
        )

    def _instrument_predictor(self, predictor=None):
        predictor = predictor if predictor is not None else getattr(self.model, 'predictor', None)
        if predictor is None or getattr(predictor, '_latency_instrumented', False):
            return
        end2end = getattr(predictor.model, 'end2end', None)
        self.postprocess_info = dict(end2end=end2end,
            mode='END_TO_END_NO_TRADITIONAL_NMS' if end2end else 'NMS_OR_SIX_COLUMN_OUTPUT',
            confidence=self.conf_threshold, configured_iou=self.iou_threshold,
            agnostic_nms=getattr(predictor.args,'agnostic_nms',None), class_filter=self.class_id)
        latency.profiler.event('PERSON_POSTPROCESS_MODE', **self.postprocess_info)
        for method, stage in [('preprocess', 'YOLO_PREPROCESS'), ('inference', 'YOLO_INFERENCE'),
                              ('postprocess', 'YOLO_POSTPROCESS')]:
            original = getattr(predictor, method)
            def timed(*args, _original=original, _stage=stage, **kwargs):
                with latency.span(_stage, _stage.lower()+'_ms', model='YOLO', device=str(self.device), timing='cpu_wall'):
                    with latency.torch_gpu_span(_stage.lower()+'_gpu_ms', getattr(predictor, 'device', 'cpu')):
                        return _original(*args, **kwargs)
            setattr(predictor, method, timed)
        predictor._latency_instrumented = True

    def _inference_failure(self, exc, stage, frame):
        """Latch accelerator failures: never spin on an unhealthy CUDA context."""
        from telemetry.events import emit
        self.last_error = str(exc)
        accelerator_failure = any(token in self.last_error.lower() for token in ('cuda', 'cublas', 'cudnn'))
        fields = dict(module=self.name, device=str(self.device), model_path=self.model_path,
            imgsz=self.imgsz, frame_shape=list(frame.shape) if frame is not None else None,
            error=self.last_error, error_stage=stage)
        if accelerator_failure:
            self.fatal_error = self.last_error
            emit('CUDA_ERROR', **fields, traceback=traceback.format_exc())
            diagnostics = {}
            try:
                import torch
                device = int(self.device) if str(self.device).isdecimal() else self.device
                diagnostics.update(allocated_bytes=torch.cuda.memory_allocated(device),
                    reserved_bytes=torch.cuda.memory_reserved(device))
                try:
                    free, total = torch.cuda.mem_get_info(device)
                    diagnostics.update(driver_free_bytes=free, driver_total_bytes=total)
                except Exception as diagnostic_error:
                    diagnostics['driver_memory_status'] = type(diagnostic_error).__name__
            except Exception as diagnostic_error:
                diagnostics['allocator_memory_status'] = type(diagnostic_error).__name__
            emit('CUDA_MEMORY', **fields, **diagnostics)
            emit('INFERENCE_BLOCKED', **fields, recovery='RESTART_PROCESS_OR_EXPLICIT_CPU_START')
        print(f'[AI] {self.name} inference error ({stage}): {exc}', file=sys.stderr)
        if accelerator_failure:
            print(f'[AI] {self.name}: CUDA inference blocked; restart the application after resolving memory pressure. '
                  'Explicit CPU mode: PTZ_DEVICE=cpu bash run_camera.sh', file=sys.stderr)

    # ========================================================
    # DETECT
    # ========================================================

    @scheduled('detection')
    @latency.measured('YOLO', 'yolo_total_ms')
    def detect(
        self,
        frame_bgr: np.ndarray
    ) -> list[dict]:
        """
        Run YOLO detection.

        Parameters
        ----------
        frame_bgr:
            OpenCV BGR image.

        Returns
        -------
        list[dict]

        Example:
            [
                {
                    "bbox": (100, 100, 300, 500),
                    "confidence": 0.92,
                    "class_id": 0,
                    "type": "PERSON"
                }
            ]
        """

        if self.fatal_error is not None:
            self.last_error = self.fatal_error
            return []
        self.last_error = None
        if frame_bgr is None:
            return []

        if frame_bgr.size == 0:
            return []

        if self.model is None:
            return []

        start = time.perf_counter()

        try:

            self._instrument_predictor()
            results = self.model.predict(
                source=frame_bgr,
                conf=self.conf_threshold,
                iou=self.iou_threshold,
                imgsz=self.imgsz,
                device=self.device,
                verbose=self.verbose,
            )

        except Exception as exc:
            self._inference_failure(exc, 'predict', frame_bgr)
            return []

        self.last_inference_ms = (
            time.perf_counter()
            -
            start
        ) * 1000.0

        detections = []

        if not results:
            return detections

        result = results[0]

        boxes = getattr(
            result,
            "boxes",
            None
        )

        if boxes is None:
            return detections

        frame_h, frame_w = (
            frame_bgr.shape[:2]
        )

        for source_index, box in enumerate(boxes):

            try:

                class_id = int(
                    box.cls[0].item()
                )

                confidence = float(
                    box.conf[0].item()
                )

                # --------------------------------------------
                # CLASS FILTER
                # --------------------------------------------

                if (
                    self.class_id is not None
                    and
                    class_id != self.class_id
                ):
                    continue

                # --------------------------------------------
                # BBOX
                # --------------------------------------------

                xyxy = (
                    box.xyxy[0]
                    .detach()
                    .cpu()
                    .numpy()
                )

                x1, y1, x2, y2 = (
                    map(
                        int,
                        xyxy
                    )
                )

                # Clamp coordinates.
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
                    continue

                if y2 <= y1:
                    continue

                detections.append(
                    {
                        "source_detection_index": source_index,
                        "bbox": (
                            x1,
                            y1,
                            x2,
                            y2,
                        ),

                        "confidence":
                            confidence,

                        "class_id":
                            class_id,

                        "type":
                            self.name,
                    }
                )

            except Exception as exc:
                if any(token in str(exc).lower() for token in ('cuda', 'cublas', 'cudnn')):
                    self._inference_failure(exc, 'result_to_cpu', frame_bgr)
                    return []  # never return partially parsed detections after CUDA failure
                print(f'[AI] Parse {self.name} detection error: {exc}', file=sys.stderr)

        return detections

    # ========================================================
    # INFORMATION
    # ========================================================

    def get_last_inference_ms(
        self
    ) -> float:

        return float(
            self.last_inference_ms
        )
        
# ============================================================
# PERSON DETECTOR
# ============================================================

class PersonDetector(
    YOLODetector
):
    """
    YOLO Person Detector.
    """

    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.35,
        iou_threshold: float = 0.60,
        class_id: int = 0,
        device=0,
        imgsz: int = 640,
    ):

        super().__init__(
            model_path=model_path,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            class_id=class_id,
            device=device,
            imgsz=imgsz,
            name="PERSON",
        )
        
# ============================================================
# FACE DETECTOR
# ============================================================

class FaceDetector(
    YOLODetector
):
    """
    YOLO Face Detector.
    """

    def __init__(
        self,
        model_path: str,
        conf_threshold: float = 0.40,
        iou_threshold: float = 0.50,
        class_id: int = 0,
        device=0,
        imgsz: int = 640,
    ):

        super().__init__(
            model_path=model_path,
            conf_threshold=conf_threshold,
            iou_threshold=iou_threshold,
            class_id=class_id,
            device=device,
            imgsz=imgsz,
            name="FACE",
        )
        
# ============================================================
# FACE DETECTION INSIDE PERSON
# ============================================================

def detect_face_inside_person(
    face_detector: FaceDetector,
    frame_bgr: np.ndarray,
    person_target: Optional[dict],
    upper_body_ratio: float = 0.55,
    roi_expand_x: float = 0.10,
    roi_expand_top: float = 0.05,
) -> list[dict]:
    """
    Detect faces only inside the selected person's upper-body ROI.

    Parameters
    ----------
    face_detector:
        FaceDetector instance.

    frame_bgr:
        Full camera frame.

    person_target:
        Selected person detection.

        Expected format:

        {
            "bbox": (x1, y1, x2, y2),
            ...
        }

    upper_body_ratio:
        Fraction of person height used for face search.

        Example:
            0.55

        means only search approximately the upper 55% of
        the person's bounding box.

    roi_expand_x:
        Horizontal expansion relative to person width.

    roi_expand_top:
        Extra area above the person bbox relative to person
        height.

    Returns
    -------
    list[dict]

    Returned face bounding boxes use FULL FRAME coordinates,
    not ROI coordinates.
    """

    if frame_bgr is None:
        return []

    if frame_bgr.size == 0:
        return []

    if person_target is None:
        return []

    bbox = person_target.get(
        "bbox"
    )

    if bbox is None:
        return []

    frame_h, frame_w = (
        frame_bgr.shape[:2]
    )

    px1, py1, px2, py2 = bbox

    # --------------------------------------------------------
    # CLAMP PERSON BBOX
    # --------------------------------------------------------

    px1 = max(
        0,
        min(
            frame_w - 1,
            int(px1)
        )
    )

    py1 = max(
        0,
        min(
            frame_h - 1,
            int(py1)
        )
    )

    px2 = max(
        0,
        min(
            frame_w,
            int(px2)
        )
    )

    py2 = max(
        0,
        min(
            frame_h,
            int(py2)
        )
    )

    if px2 <= px1:
        return []

    if py2 <= py1:
        return []

    person_width = (
        px2 - px1
    )

    person_height = (
        py2 - py1
    )

    # --------------------------------------------------------
    # FACE SEARCH ROI
    # --------------------------------------------------------
    #
    # Person bbox:
    #
    #       ┌──────────────┐
    #       │              │
    #       │     HEAD     │  ← search here
    #       │              │
    #       ├──────────────┤
    #       │              │
    #       │     BODY     │
    #       │              │
    #       └──────────────┘
    #
    # --------------------------------------------------------

    expand_x = int(
        person_width
        *
        roi_expand_x
    )

    expand_top = int(
        person_height
        *
        roi_expand_top
    )

    roi_x1 = (
        px1
        -
        expand_x
    )

    roi_x2 = (
        px2
        +
        expand_x
    )

    roi_y1 = (
        py1
        -
        expand_top
    )

    roi_y2 = (
        py1
        +
        int(
            person_height
            *
            upper_body_ratio
        )
    )

    # --------------------------------------------------------
    # CLAMP ROI
    # --------------------------------------------------------

    roi_x1 = max(
        0,
        roi_x1
    )

    roi_y1 = max(
        0,
        roi_y1
    )

    roi_x2 = min(
        frame_w,
        roi_x2
    )

    roi_y2 = min(
        frame_h,
        roi_y2
    )

    if roi_x2 <= roi_x1:
        return []

    if roi_y2 <= roi_y1:
        return []

    # --------------------------------------------------------
    # CROP ROI
    # --------------------------------------------------------

    person_roi = frame_bgr[
        roi_y1:roi_y2,
        roi_x1:roi_x2
    ]

    if person_roi.size == 0:
        return []

    # --------------------------------------------------------
    # FACE DETECTION
    # --------------------------------------------------------

    roi_faces = (
        face_detector.detect(
            person_roi
        )
    )

    full_frame_faces = []

    # --------------------------------------------------------
    # CONVERT ROI COORDINATES -> FULL FRAME COORDINATES
    # --------------------------------------------------------

    for face in roi_faces:

        fx1, fy1, fx2, fy2 = (
            face["bbox"]
        )

        full_x1 = (
            fx1
            +
            roi_x1
        )

        full_y1 = (
            fy1
            +
            roi_y1
        )

        full_x2 = (
            fx2
            +
            roi_x1
        )

        full_y2 = (
            fy2
            +
            roi_y1
        )

        converted_face = dict(
            face
        )

        converted_face["bbox"] = (
            full_x1,
            full_y1,
            full_x2,
            full_y2,
        )

        converted_face[
            "parent_person_bbox"
        ] = (
            px1,
            py1,
            px2,
            py2,
        )

        converted_face[
            "search_roi"
        ] = (
            roi_x1,
            roi_y1,
            roi_x2,
            roi_y2,
        )

        full_frame_faces.append(
            converted_face
        )

    return full_frame_faces        


# The public face API now uses SCRFD ONNX. Keep these names here as aliases so
# existing direct imports from ai.detector do not silently load face.pt.
LegacyYOLOFaceDetector = FaceDetector
legacy_detect_face_inside_person = detect_face_inside_person

from .face.face_detector import (  # noqa: E402  (compatibility export)
    FaceDetector,
    detect_face_inside_person,
)
