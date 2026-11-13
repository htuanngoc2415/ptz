"""ArcFace ONNX embedding extraction."""

from __future__ import annotations
from event_logging import event_print as print

import sys
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
from telemetry import latency
import onnxruntime as ort
from .ort_memory import session_options, provider_options


def _providers(requested: Optional[Sequence[str]]) -> list[str]:
    available = ort.get_available_providers()
    selected = (
        [name for name in requested if name in available]
        if requested is not None else
        [name for name in ("CUDAExecutionProvider", "CPUExecutionProvider") if name in available]
    )
    if not selected:
        raise RuntimeError(f"No usable ONNX Runtime provider. Available: {available}")
    return selected


class FaceRecognizer:
    def __init__(self, model_path, providers=None):
        self.model_path = str(Path(model_path))
        options = session_options()
        selected = _providers(providers)
        self.session = ort.InferenceSession(self.model_path, sess_options=options,
                                            providers=selected,
                                            provider_options=provider_options(selected))
        self.input = self.session.get_inputs()[0]
        self.output = self.session.get_outputs()[0]
        shape = self.input.shape
        if len(shape) != 4 or tuple(shape[-2:]) != (112, 112):
            raise ValueError(f"Expected ArcFace input [N,3,112,112], got {shape}")
        print(f"[FaceRecognizer] Provider: {self.session.get_providers()[0]}")

    def extract_embedding(self, aligned_face: np.ndarray) -> np.ndarray:
        if aligned_face is None or aligned_face.size == 0:
            raise ValueError("aligned_face is empty")
        with latency.span('ARCFACE_PREPROCESS', 'arcface_preprocess_ms'):
            if aligned_face.shape[:2] != (112, 112):
                aligned_face = cv2.resize(aligned_face, (112, 112), interpolation=cv2.INTER_LINEAR)
            # blobFromImage performs BGR->RGB, (pixel - 127.5) / 127.5, and NCHW.
            blob = cv2.dnn.blobFromImage(
                aligned_face, 1.0 / 127.5, (112, 112),
                (127.5, 127.5, 127.5), swapRB=True,
            )
        try:
            with latency.span('ARCFACE_INFERENCE', 'arcface_inference_ms', model='ArcFace', device=self.session.get_providers()[0], timing='cpu_wall_ort_run'):
                output = self.session.run([self.output.name], {self.input.name: blob})[0]
        except Exception as exc:
            print(f"[FaceRecognizer] inference error: {exc}", file=sys.stderr)
            raise RuntimeError("ArcFace ONNX inference failed") from exc
        with latency.span('ARCFACE_POSTPROCESS', 'arcface_postprocess_ms'):
            embedding = np.asarray(output, dtype=np.float32).reshape(-1)
            if embedding.size != 512:
                raise ValueError(f"Expected 512-D embedding, got {embedding.shape}")
            norm = float(np.linalg.norm(embedding))
            if not np.isfinite(norm) or norm < 1e-12:
                raise ValueError("embedding has zero/invalid norm")
            return embedding / norm
