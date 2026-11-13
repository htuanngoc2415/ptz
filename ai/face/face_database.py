"""Persistent multi-template face embedding database."""

from __future__ import annotations
from event_logging import event_print as print

from pathlib import Path

import numpy as np


class FaceDatabase:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.embeddings: dict[str, np.ndarray] = {}
        if self.path is not None and self.path.exists():
            try:
                self.load(self.path)
            except (OSError, ValueError) as exc:
                print(f"[FaceDatabase] could not load {self.path}: {exc}")
                self.embeddings = {}

    def load(self, path=None):
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("database path is not set")
        if not target.exists():
            self.embeddings = {}
            return self
        loaded = {}
        with np.load(target, allow_pickle=False) as archive:
            for identity in archive.files:
                values = np.asarray(archive[identity], dtype=np.float32)
                if values.ndim == 1:
                    values = values[None, :]
                if values.ndim != 2 or values.shape[1] != 512:
                    continue
                norms = np.linalg.norm(values, axis=1, keepdims=True)
                valid = np.isfinite(values).all(axis=1) & (norms[:, 0] > 1e-12)
                if np.any(valid):
                    loaded[identity] = values[valid] / norms[valid]
        self.path = target
        self.embeddings = loaded
        return self

    def save(self, path=None):
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("database path is not set")
        target.parent.mkdir(parents=True, exist_ok=True)
        arrays = {}
        for identity, values in self.embeddings.items():
            array = np.asarray(values, dtype=np.float32)
            if array.ndim == 1:
                array = array[None, :]
            if array.ndim != 2 or array.shape[1] != 512:
                raise ValueError(f"Invalid embeddings for {identity}: {array.shape}")
            arrays[str(identity)] = array
        np.savez_compressed(target, **arrays)
        self.path = target

    def add(self, identity: str, embedding):
        value = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if value.size != 512:
            raise ValueError("embedding must have shape (512,)")
        norm = float(np.linalg.norm(value))
        if not np.isfinite(norm) or norm < 1e-12:
            raise ValueError("embedding has zero/invalid norm")
        value = (value / norm)[None, :]
        key = str(identity)
        self.embeddings[key] = (
            np.concatenate((self.embeddings[key], value), axis=0)
            if key in self.embeddings else value
        )

    def __len__(self):
        return len(self.embeddings)

    @property
    def total_embeddings(self):
        return sum(values.shape[0] for values in self.embeddings.values())
