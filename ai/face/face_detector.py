"""SCRFD inference using ONNX Runtime (no InsightFace package dependency)."""

from __future__ import annotations
from event_logging import event_print as print

import math
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
from telemetry import latency
import onnxruntime as ort
from .ort_memory import session_options, provider_options


def _providers(requested: Optional[Sequence[str]]) -> list[str]:
    available = ort.get_available_providers()
    if requested is not None:
        selected = [name for name in requested if name in available]
    else:
        selected = [
            name for name in ("CUDAExecutionProvider", "CPUExecutionProvider")
            if name in available
        ]
    if not selected:
        raise RuntimeError(f"No usable ONNX Runtime provider. Available: {available}")
    return selected


class FaceDetector:
    """Decode SCRFD score, distance-box and five-landmark output heads."""

    def __init__(
        self,
        model_path,
        providers=None,
        input_size=(640, 640),
        conf_threshold=0.5,
        nms_threshold=0.4,
    ):
        self.model_path = str(Path(model_path))
        self.input_size = (int(input_size[0]), int(input_size[1]))
        self.conf_threshold = float(conf_threshold)
        self.nms_threshold = float(nms_threshold)
        options = session_options()
        selected = _providers(providers)
        self.session = ort.InferenceSession(
            self.model_path,
            sess_options=options,
            providers=selected,
            provider_options=provider_options(selected),
        )
        self.input = self.session.get_inputs()[0]
        self.outputs = self.session.get_outputs()
        self.output_names = [item.name for item in self.outputs]
        self.last_inference_ms = 0.0
        print(f"[FaceDetector] Provider: {self.session.get_providers()[0]}")

    @staticmethod
    def _letterbox(image: np.ndarray, size: tuple[int, int]):
        dst_w, dst_h = size
        src_h, src_w = image.shape[:2]
        scale = min(dst_w / src_w, dst_h / src_h)
        new_w = max(1, int(round(src_w * scale)))
        new_h = max(1, int(round(src_h * scale)))
        resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        left = (dst_w - new_w) // 2
        top = (dst_h - new_h) // 2
        canvas = np.zeros((dst_h, dst_w, 3), dtype=np.uint8)
        canvas[top:top + new_h, left:left + new_w] = resized
        return canvas, scale, left, top

    @staticmethod
    def _distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
        return np.column_stack((
            points[:, 0] - distance[:, 0],
            points[:, 1] - distance[:, 1],
            points[:, 0] + distance[:, 2],
            points[:, 1] + distance[:, 3],
        ))

    @staticmethod
    def _distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
        return (points[:, None, :] + distance.reshape(-1, 5, 2)).astype(np.float32)

    @staticmethod
    def _feature_info(count: int, input_w: int, input_h: int) -> tuple[int, int, int]:
        """Infer (stride, anchors, grid width) from an SCRFD head length."""
        candidates = []
        for anchors in (1, 2):
            cells = count / anchors
            for stride in (4, 8, 16, 32, 64, 128):
                grid_w = math.ceil(input_w / stride)
                grid_h = math.ceil(input_h / stride)
                if grid_w * grid_h == cells:
                    candidates.append((stride, anchors, grid_w))
        if not candidates:
            raise ValueError(f"Cannot infer SCRFD stride/anchors for output length {count}")
        return candidates[0]

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
        x1, y1, x2, y2 = boxes.T
        areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
        order = scores.argsort()[::-1]
        keep = []
        while order.size:
            index = int(order[0])
            keep.append(index)
            if order.size == 1:
                break
            rest = order[1:]
            xx1 = np.maximum(x1[index], x1[rest])
            yy1 = np.maximum(y1[index], y1[rest])
            xx2 = np.minimum(x2[index], x2[rest])
            yy2 = np.minimum(y2[index], y2[rest])
            inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
            union = areas[index] + areas[rest] - inter
            iou = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
            order = rest[iou <= threshold]
        return keep

    @staticmethod
    def _as_2d(value: np.ndarray) -> np.ndarray:
        value = np.asarray(value)
        if value.ndim == 3 and value.shape[0] == 1:
            value = value[0]
        return value.reshape(value.shape[0], -1)

    def detect(self, image: np.ndarray) -> list[dict]:
        if image is None or not isinstance(image, np.ndarray) or image.size == 0:
            return []
        if image.ndim != 3 or image.shape[2] != 3:
            return []

        with latency.span('SCRFD_PREPROCESS', 'scrfd_preprocess_ms'):
            input_w, input_h = self.input_size
            padded, scale, pad_x, pad_y = self._letterbox(image, self.input_size)
            blob = cv2.dnn.blobFromImage(
                padded, 1.0 / 128.0, self.input_size,
                (127.5, 127.5, 127.5), swapRB=True,
            )
        start = time.perf_counter()
        try:
            with latency.span('SCRFD_INFERENCE', 'scrfd_inference_ms', model='SCRFD', device=self.session.get_providers()[0], timing='cpu_wall_ort_run'):
                raw = self.session.run(self.output_names, {self.input.name: blob})
        except Exception as exc:
            print(f"[FaceDetector] inference error: {exc}", file=sys.stderr)
            return []
        self.last_inference_ms = (time.perf_counter() - start) * 1000.0

        with latency.span('SCRFD_POSTPROCESS', 'scrfd_postprocess_ms'):
            by_width: dict[int, list[np.ndarray]] = {1: [], 4: [], 10: []}
            for value in raw:
                array = self._as_2d(value)
                if array.shape[1] in by_width:
                    by_width[array.shape[1]].append(array)
            if not all(len(by_width[key]) == 3 for key in (1, 4, 10)):
                shapes = [np.asarray(value).shape for value in raw]
                print(f"[FaceDetector] unsupported SCRFD outputs: {shapes}", file=sys.stderr)
                return []

            score_by_count = {value.shape[0]: value for value in by_width[1]}
            box_by_count = {value.shape[0]: value for value in by_width[4]}
            kps_by_count = {value.shape[0]: value for value in by_width[10]}
            all_boxes, all_scores, all_kps = [], [], []

            for count, score_head in score_by_count.items():
                if count not in box_by_count or count not in kps_by_count:
                    continue
                stride, anchors, grid_w = self._feature_info(count, input_w, input_h)
                grid_h = math.ceil(input_h / stride)
                yy, xx = np.mgrid[:grid_h, :grid_w]
                centers = np.stack((xx, yy), axis=-1).reshape(-1, 2).astype(np.float32)
                centers = (centers * stride).repeat(anchors, axis=0)

                scores = score_head[:, 0]
                selected = np.flatnonzero(scores >= self.conf_threshold)
                if selected.size == 0:
                    continue
                boxes = self._distance2bbox(
                    centers[selected], box_by_count[count][selected] * stride,
                )
                landmarks = self._distance2kps(
                    centers[selected], kps_by_count[count][selected] * stride,
                )
                all_boxes.append(boxes)
                all_scores.append(scores[selected])
                all_kps.append(landmarks)

            if not all_boxes:
                return []
            boxes = np.concatenate(all_boxes)
            scores = np.concatenate(all_scores)
            landmarks = np.concatenate(all_kps)
            keep = self._nms(boxes, scores, self.nms_threshold)
            src_h, src_w = image.shape[:2]
            results = []
            for index in keep:
                box = boxes[index].copy()
                points = landmarks[index].copy()
                box[[0, 2]] = (box[[0, 2]] - pad_x) / scale
                box[[1, 3]] = (box[[1, 3]] - pad_y) / scale
                points[:, 0] = (points[:, 0] - pad_x) / scale
                points[:, 1] = (points[:, 1] - pad_y) / scale
                box[[0, 2]] = np.clip(box[[0, 2]], 0, src_w)
                box[[1, 3]] = np.clip(box[[1, 3]], 0, src_h)
                points[:, 0] = np.clip(points[:, 0], 0, max(0, src_w - 1))
                points[:, 1] = np.clip(points[:, 1], 0, max(0, src_h - 1))
                x1, y1, x2, y2 = box
                if x2 <= x1 or y2 <= y1 or not np.isfinite(points).all():
                    continue
                results.append({
                    "bbox": (int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))),
                    "confidence": float(scores[index]),
                    "landmarks": points.astype(np.float32),
                    "class_id": 0,
                    "type": "FACE",
                })
            return results

    def get_last_inference_ms(self) -> float:
        """Compatibility with the existing YOLO detector timing API."""
        return float(self.last_inference_ms)


def detect_face_inside_person(
    face_detector: FaceDetector,
    frame_bgr: np.ndarray,
    person_target: Optional[dict],
    upper_body_ratio: float = 0.60,
    roi_expand_x: float = 0.10,
    roi_expand_top: float = 0.05,
) -> list[dict]:
    """Detect in a configurable person ROI and return full-frame coordinates."""
    if frame_bgr is None or frame_bgr.size == 0 or not person_target:
        return []
    bbox = person_target.get("bbox")
    if bbox is None:
        return []
    roi_started = latency.profiler.event('FACE_ROI_PREPARE_START')
    frame_h, frame_w = frame_bgr.shape[:2]
    px1, py1, px2, py2 = map(float, bbox)
    px1, py1 = max(0, px1), max(0, py1)
    px2, py2 = min(frame_w, px2), min(frame_h, py2)
    if px2 <= px1 or py2 <= py1:
        return []
    width, height = px2 - px1, py2 - py1
    ratio = min(1.0, max(0.1, float(upper_body_ratio)))
    x1 = max(0, int(math.floor(px1 - width * roi_expand_x)))
    y1 = max(0, int(math.floor(py1 - height * roi_expand_top)))
    x2 = min(frame_w, int(math.ceil(px2 + width * roi_expand_x)))
    y2 = min(frame_h, int(math.ceil(py1 + height * ratio)))
    if x2 <= x1 or y2 <= y1:
        return []
    crop = frame_bgr[y1:y2, x1:x2]
    latency.profiler.elapsed('FACE_ROI_PREPARE_END', roi_started, 'face_roi_prepare_ms')
    faces = face_detector.detect(crop)
    converted = []
    for face in faces:
        item = dict(face)
        fx1, fy1, fx2, fy2 = item["bbox"]
        item["bbox"] = (fx1 + x1, fy1 + y1, fx2 + x1, fy2 + y1)
        item["landmarks"] = np.asarray(item["landmarks"], np.float32) + np.array([x1, y1], np.float32)
        item["parent_person_bbox"] = tuple(map(int, (px1, py1, px2, py2)))
        item["search_roi"] = (x1, y1, x2, y2)
        converted.append(item)
    return converted
