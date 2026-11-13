#!/usr/bin/env python3
"""Run detection, landmark alignment, embedding and matching on one image."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config as cfg
from ai.face import FaceDatabase, FaceDetector, FaceMatcher, FaceRecognizer, align_face


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--database", default=cfg.FACE_DATABASE_PATH)
    parser.add_argument("--threshold", type=float, default=cfg.FACE_RECOGNITION_THRESHOLD)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--save")
    return parser.parse_args()


def main():
    args = parse_args()
    image = cv2.imread(args.image)
    if image is None:
        print(f"Cannot read image: {args.image}", file=sys.stderr)
        return 1
    detector = FaceDetector(
        cfg.FACE_DETECT_MODEL,
        input_size=cfg.FACE_DET_INPUT_SIZE,
        conf_threshold=cfg.FACE_DET_CONF,
        nms_threshold=cfg.FACE_DET_NMS,
    )
    recognizer = FaceRecognizer(cfg.FACE_RECOGNITION_MODEL)
    database = FaceDatabase(args.database)
    matcher = FaceMatcher(database, args.threshold)
    faces = detector.detect(image)
    print(f"Detected faces: {len(faces)}")
    for index, face in enumerate(faces):
        print(f"\nFace {index}:")
        print(f"BBox: {face['bbox']}")
        print(f"Confidence: {face['confidence']:.6f}")
        print(f"Landmarks:\n{face['landmarks']}")
        try:
            aligned = align_face(image, face["landmarks"])
            embedding = recognizer.extract_embedding(aligned)
            match = matcher.match(embedding)
            print(f"Embedding shape: {embedding.shape}")
            print(f"Embedding norm: {np.linalg.norm(embedding):.6f}")
            print(f"Identity: {match['identity']}")
            print(f"Similarity: {match['score']:.6f}")
        except Exception as exc:
            print(f"Recognition error: {exc}")
            match = {"identity": "unknown", "score": 0.0}
        x1, y1, x2, y2 = face["bbox"]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        for point in face["landmarks"]:
            cv2.circle(image, tuple(np.rint(point).astype(int)), 2, (0, 0, 255), -1)
        cv2.putText(
            image, f"{match['identity']} | {match['score']:.2f}",
            (x1, max(20, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
        )
    if args.save:
        if not cv2.imwrite(args.save, image):
            print(f"Could not save: {args.save}", file=sys.stderr)
            return 1
        print(f"Saved visualization: {args.save}")
    if args.show:
        cv2.imshow("Face recognition", image)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    if not database.embeddings:
        print(f"WARNING: database is missing or empty: {args.database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
