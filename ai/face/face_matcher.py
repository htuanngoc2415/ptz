"""Cosine matching against multiple templates per identity."""

from __future__ import annotations

import numpy as np


class FaceMatcher:
    def __init__(self, database, threshold):
        self.database = database
        self.threshold = float(threshold)

    def match(self, query_embedding):
        query = np.asarray(query_embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if query.size != 512 or not np.isfinite(norm) or norm < 1e-12:
            return {"identity": "unknown", "score": 0.0, "matched": False,
                    "nearest_identity": None, "reason": "invalid_embedding"}
        query = query / norm
        source = getattr(self.database, "embeddings", self.database)
        ranking = []
        best_identity, best_score = None, -1.0
        for identity, values in source.items():
            templates = np.asarray(values, dtype=np.float32)
            if templates.ndim == 1:
                templates = templates[None, :]
            if templates.ndim != 2 or templates.shape[1] != 512 or templates.size == 0:
                continue
            score = float(np.max(templates @ query))
            if np.isfinite(score):
                ranking.append((score, str(identity)))
            if np.isfinite(score) and (best_identity is None or score > best_score):
                best_identity, best_score = str(identity), score
        ranking.sort(key=lambda item: item[0], reverse=True)
        second_score = ranking[1][0] if len(ranking) > 1 else None
        matched = best_identity is not None and best_score >= self.threshold
        return {
            "identity": best_identity if matched else "unknown",
            "nearest_identity": best_identity,
            "reason": "matched" if matched else "below_threshold" if best_identity else "empty_database",
            "score": float(best_score if best_identity is not None else 0.0),
            "matched": bool(matched),
            "second_score": second_score,
            "margin": best_score - second_score if second_score is not None else None,
        }
