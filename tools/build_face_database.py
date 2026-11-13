#!/usr/bin/env python3
"""Build an NPZ database while retaining every valid image embedding."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config as cfg
from ai.face import FaceDatabase, FaceDetector, FaceRecognizer, align_face

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(PROJECT_ROOT / "data" / "face_database"))
    parser.add_argument("--output", default=cfg.FACE_DATABASE_PATH)
    parser.add_argument("--min-face-size", type=int, default=30)
    return parser.parse_args()


def main():
    args = parse_args()
    root = Path(args.root)
    detector = FaceDetector(
        cfg.FACE_DETECT_MODEL,
        input_size=cfg.FACE_DET_INPUT_SIZE,
        conf_threshold=cfg.FACE_DET_CONF,
        nms_threshold=cfg.FACE_DET_NMS,
    )
    recognizer = FaceRecognizer(cfg.FACE_RECOGNITION_MODEL)
    database = FaceDatabase()
    total_images = total_valid = 0

    if not root.is_dir():
        print(f"Database root does not exist: {root}", file=sys.stderr)
        return 1

    for identity_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        images = sorted(path for path in identity_dir.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
        valid = 0
        for path in images:
            total_images += 1
            image = cv2.imread(str(path))
            if image is None:
                print(f"WARNING [{identity_dir.name}] cannot read: {path.name}")
                continue
            faces = detector.detect(image)
            if not faces:
                print(f"WARNING [{identity_dir.name}] no face: {path.name}")
                continue
            if len(faces) > 1:
                print(f"WARNING [{identity_dir.name}] {len(faces)} faces; selecting largest: {path.name}")
            face = max(
                faces,
                key=lambda item: (item["bbox"][2] - item["bbox"][0]) * (item["bbox"][3] - item["bbox"][1]),
            )
            x1, y1, x2, y2 = face["bbox"]
            if min(x2 - x1, y2 - y1) < args.min_face_size:
                print(f"WARNING [{identity_dir.name}] face too small: {path.name} {face['bbox']}")
                continue
            try:
                aligned = align_face(image, face["landmarks"])
                database.add(identity_dir.name, recognizer.extract_embedding(aligned))
            except Exception as exc:
                print(f"WARNING [{identity_dir.name}] failed {path.name}: {exc}")
                continue
            valid += 1
            total_valid += 1
        print(f"\nIdentity: {identity_dir.name}\nImages: {len(images)}\nValid embeddings: {valid}")

    database.save(args.output)
    print(f"\nTotal identities: {len(database)}")
    print(f"Total images: {total_images}")
    print(f"Total embeddings: {total_valid}")
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
