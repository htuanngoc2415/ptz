#!/usr/bin/env python3

import cv2
import config as cfg

from ai import (
    PersonDetector,
    FaceDetector,
    select_person_target,
    select_face_target,
    detect_face_inside_person,
    calculate_person_sharpness,
    calculate_face_sharpness,
)


# ============================================================
# CONFIG
# ============================================================

PERSON_MODEL_PATH = cfg.PERSON_MODEL_PATH
FACE_MODEL_PATH = cfg.FACE_DETECT_MODEL

DEVICE = 0

PERSON_CONF = 0.35
FACE_CONF = 0.40


# ============================================================
# MODEL
# ============================================================

person_detector = PersonDetector(
    model_path=PERSON_MODEL_PATH,
    conf_threshold=PERSON_CONF,
    device=DEVICE,
)

face_detector = FaceDetector(
    model_path=FACE_MODEL_PATH,
    conf_threshold=FACE_CONF,
    input_size=cfg.FACE_DET_INPUT_SIZE,
    nms_threshold=cfg.FACE_DET_NMS,
)


# ============================================================
# CAMERA
# ============================================================

cap = cv2.VideoCapture(
    0
)


while True:

    ret, frame = cap.read()

    if not ret:
        break

    # ========================================================
    # PERSON
    # ========================================================

    persons = person_detector.detect(
        frame
    )

    person = select_person_target(
        persons,
        frame.shape,
        mode="center",
    )

    # ========================================================
    # FACE INSIDE PERSON
    # ========================================================

    faces = []

    face = None

    if person is not None:

        faces = detect_face_inside_person(
            face_detector,
            frame,
            person,
        )

        face = select_face_target(
            faces,
            frame.shape,
            mode="largest",
        )

    # ========================================================
    # SHARPNESS
    # ========================================================

    person_sharpness = (
        calculate_person_sharpness(
            frame,
            person
        )
    )

    face_sharpness = (
        calculate_face_sharpness(
            frame,
            face
        )
    )

    # ========================================================
    # DRAW ALL PERSONS
    # ========================================================

    for det in persons:

        x1, y1, x2, y2 = (
            det["bbox"]
        )

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            (255, 0, 0),
            2,
        )

        for point in det.get("landmarks", []):
            cv2.circle(
                frame,
                tuple(map(int, point)),
                2,
                (255, 0, 255),
                -1,
            )

    # ========================================================
    # DRAW TARGET PERSON
    # ========================================================

    if person is not None:

        x1, y1, x2, y2 = (
            person["bbox"]
        )

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            (0, 255, 0),
            3,
        )

        cv2.putText(
            frame,
            (
                "TARGET PERSON "
                f"{person_sharpness:.0f}"
            ),
            (
                x1,
                max(
                    20,
                    y1 - 10
                )
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )

    # ========================================================
    # DRAW FACES
    # ========================================================

    for det in faces:

        x1, y1, x2, y2 = (
            det["bbox"]
        )

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            (0, 255, 255),
            2,
        )

    # ========================================================
    # DRAW TARGET FACE
    # ========================================================

    if face is not None:

        x1, y1, x2, y2 = (
            face["bbox"]
        )

        cv2.rectangle(
            frame,
            (x1, y1),
            (x2, y2),
            (0, 0, 255),
            3,
        )

        cv2.putText(
            frame,
            (
                "TARGET FACE "
                f"{face_sharpness:.0f}"
            ),
            (
                x1,
                max(
                    20,
                    y1 - 10
                )
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 0, 255),
            2,
        )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    cv2.putText(
        frame,
        (
            f"Person AI: "
            f"{person_detector.get_last_inference_ms():.1f} ms"
        ),
        (20, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )

    cv2.putText(
        frame,
        (
            f"Face AI: "
            f"{face_detector.get_last_inference_ms():.1f} ms"
        ),
        (20, 60),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.6,
        (255, 255, 255),
        2,
    )

    # ========================================================
    # DISPLAY
    # ========================================================

    cv2.imshow(
        "AI Test",
        frame
    )

    key = (
        cv2.waitKey(1)
        &
        0xFF
    )

    if key == ord("q"):
        break


cap.release()

cv2.destroyAllWindows()
