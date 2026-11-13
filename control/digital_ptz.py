import time
import threading
from functools import wraps

def synchronized(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return call

import cv2


class DigitalPTZController:

    def __init__(
        self,
        enabled=False,
        min_zoom=1.0,
        max_zoom=2.5,
        target_height_ratio=0.70,
        center_smoothing=0.45,
        deadzone_x=0.10,
        deadzone_y=0.10,
        zoom_smoothing=0.25,
        lost_hold_time=0.60,
        return_smoothing=0.08,
    ):

        self._lock = threading.RLock()
        self.enabled = bool(enabled)

        self.min_zoom = float(min_zoom)
        self.max_zoom = float(max_zoom)

        self.target_height_ratio = float(
            target_height_ratio
        )

        self.center_smoothing = float(
            center_smoothing
        )
        self.deadzone_x = max(0.0, float(deadzone_x))
        self.deadzone_y = max(0.0, float(deadzone_y))

        self.zoom_smoothing = float(
            zoom_smoothing
        )

        self.lost_hold_time = float(
            lost_hold_time
        )

        self.return_smoothing = float(
            return_smoothing
        )

        self.center_x = None
        self.center_y = None

        self.zoom = 1.0

        self.last_target_time = None

    # ========================================================
    # ENABLE
    # ========================================================

    @synchronized
    def set_enabled(self, enabled):

        self.enabled = bool(enabled)

        if not self.enabled:
            self.reset()

    # ========================================================
    # RESET
    # ========================================================

    @synchronized
    def reset(self):

        self.center_x = None
        self.center_y = None

        self.zoom = 1.0

        self.last_target_time = None

    # ========================================================
    # HELPER
    # ========================================================

    @staticmethod
    def _lerp(current, target, alpha):

        if current is None:
            return float(target)

        return (
            current
            +
            (target - current)
            *
            alpha
        )

    # ========================================================
    # UPDATE
    # ========================================================

    @synchronized
    def update(
        self,
        frame_shape,
        target,
        now=None,
        target_height_ratio=None,
        hold=False,
    ):

        if now is None:
            now = time.monotonic()

        if hold and self.enabled:
            return
        self._source_shape = tuple(frame_shape[:2])
        frame_h = int(frame_shape[0])
        frame_w = int(frame_shape[1])

        # ----------------------------------------------------
        # DISABLED
        # ----------------------------------------------------

        if not self.enabled:

            self.center_x = (
                frame_w / 2.0
            )

            self.center_y = (
                frame_h / 2.0
            )

            self.zoom = 1.0

            return

        # ----------------------------------------------------
        # TARGET EXISTS
        # ----------------------------------------------------

        if target is not None:

            bbox = target.get("bbox")

            if bbox is None:
                return

            x1, y1, x2, y2 = bbox

            x1 = max(
                0,
                min(
                    frame_w - 1,
                    int(x1)
                )
            )

            x2 = max(
                0,
                min(
                    frame_w,
                    int(x2)
                )
            )

            y1 = max(
                0,
                min(
                    frame_h - 1,
                    int(y1)
                )
            )

            y2 = max(
                0,
                min(
                    frame_h,
                    int(y2)
                )
            )

            target_w = max(
                1,
                x2 - x1
            )

            target_h = max(
                1,
                y2 - y1
            )

            # ================================================
            # TARGET CENTER
            # ================================================

            target_cx = (
                x1 + x2
            ) / 2.0

            target_cy = (
                y1 + y2
            ) / 2.0

            # ================================================
            # DESIRED DIGITAL ZOOM
            # ================================================

            ratio = (
                self.target_height_ratio
                if target_height_ratio is None
                else float(target_height_ratio)
            )

            desired_target_height = (
                frame_h
                *
                ratio
            )

            desired_zoom = (
                desired_target_height
                /
                target_h
            )

            desired_zoom = max(
                self.min_zoom,
                min(
                    self.max_zoom,
                    desired_zoom
                )
            )

            # ================================================
            # SMOOTH ZOOM
            # ================================================

            self.zoom = (
                self._lerp(
                    self.zoom,
                    desired_zoom,
                    self.zoom_smoothing,
                )
            )

            self.zoom = max(
                self.min_zoom,
                min(
                    self.max_zoom,
                    self.zoom
                )
            )

            # ================================================
            # SMOOTH TARGET CENTER
            # ================================================

            # Target coordinates are always in original-frame space.  A dead
            # zone avoids chasing normal face/person bbox noise.
            if self.center_x is None or abs(target_cx - self.center_x) > frame_w * self.deadzone_x:
                self.center_x = self._lerp(self.center_x, target_cx, self.center_smoothing)
            if self.center_y is None or abs(target_cy - self.center_y) > frame_h * self.deadzone_y:
                self.center_y = self._lerp(self.center_y, target_cy, self.center_smoothing)

            self.last_target_time = now

            return

        # ----------------------------------------------------
        # TARGET LOST
        # ----------------------------------------------------

        if self.last_target_time is None:

            self._return_to_full_frame(
                frame_w,
                frame_h,
            )

            return

        lost_time = (
            now
            -
            self.last_target_time
        )

        if (
            lost_time
            <=
            self.lost_hold_time
        ):

            return

        self._return_to_full_frame(
            frame_w,
            frame_h,
        )

    # ========================================================
    # RETURN TO FULL FRAME
    # ========================================================

    def _return_to_full_frame(
        self,
        frame_w,
        frame_h,
    ):

        frame_cx = (
            frame_w / 2.0
        )

        frame_cy = (
            frame_h / 2.0
        )

        self.center_x = (
            self._lerp(
                self.center_x,
                frame_cx,
                self.return_smoothing,
            )
        )

        self.center_y = (
            self._lerp(
                self.center_y,
                frame_cy,
                self.return_smoothing,
            )
        )

        self.zoom = (
            self._lerp(
                self.zoom,
                1.0,
                self.return_smoothing,
            )
        )

        if abs(
            self.zoom - 1.0
        ) < 0.01:

            self.zoom = 1.0

    # ========================================================
    # CALCULATE CROP
    # ========================================================

    def _calculate_crop(
        self,
        frame_w,
        frame_h,
    ):

        zoom = max(
            1.0,
            float(self.zoom)
        )

        crop_w = int(
            round(
                frame_w / zoom
            )
        )

        crop_h = int(
            round(
                frame_h / zoom
            )
        )

        crop_w = max(
            2,
            min(
                frame_w,
                crop_w
            )
        )

        crop_h = max(
            2,
            min(
                frame_h,
                crop_h
            )
        )

        # ----------------------------------------------------
        # REQUESTED CENTER
        # ----------------------------------------------------

        cx = (
            frame_w / 2.0
            if self.center_x is None
            else float(self.center_x)
        )

        cy = (
            frame_h / 2.0
            if self.center_y is None
            else float(self.center_y)
        )

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Clamp CENTER rather than clamping x1/x2 separately.
        #
        # This guarantees a fixed crop size.
        # ----------------------------------------------------

        half_w = (
            crop_w / 2.0
        )

        half_h = (
            crop_h / 2.0
        )

        min_cx = half_w
        max_cx = (
            frame_w - half_w
        )

        min_cy = half_h
        max_cy = (
            frame_h - half_h
        )

        cx = max(
            min_cx,
            min(
                max_cx,
                cx
            )
        )

        cy = max(
            min_cy,
            min(
                max_cy,
                cy
            )
        )

        # ----------------------------------------------------
        # INTEGER CROP
        # ----------------------------------------------------

        x1 = int(
            round(
                cx - half_w
            )
        )

        y1 = int(
            round(
                cy - half_h
            )
        )

        # Final boundary safety.

        x1 = max(
            0,
            min(
                frame_w - crop_w,
                x1
            )
        )

        y1 = max(
            0,
            min(
                frame_h - crop_h,
                y1
            )
        )

        x2 = (
            x1 + crop_w
        )

        y2 = (
            y1 + crop_h
        )

        return (
            x1,
            y1,
            x2,
            y2,
        )

    # ========================================================
    # RENDER
    # ========================================================

    def render(self, frame_bgr):
        import copy
        with self._lock:
            snapshot = copy.copy(self)
        source_shape = getattr(snapshot, '_source_shape', frame_bgr.shape[:2])
        if snapshot.center_x is not None:
            snapshot.center_x *= frame_bgr.shape[1] / source_shape[1]
        if snapshot.center_y is not None:
            snapshot.center_y *= frame_bgr.shape[0] / source_shape[0]
        return snapshot._render_snapshot(frame_bgr)

    def _render_snapshot(
       self,
       frame_bgr,
    ):

        # ====================================================
        # FRAME SIZE
        # ====================================================

        frame_h, frame_w = frame_bgr.shape[:2]

        # ====================================================
        # NO DIGITAL PTZ
        # ====================================================

        if (
            not self.enabled
            or self.zoom <= 1.001
            or self.center_x is None
            or self.center_y is None
        ):

            transform = self._identity_transform(
                frame_w,
                frame_h,
            )

            return (
                frame_bgr,
                transform,
            )

        # ====================================================
        # CURRENT DIGITAL ZOOM
        # ====================================================

        zoom = max(
            1.0,
            float(self.zoom),
        )

        # ====================================================
        # CALCULATE ROI SIZE
        #
        # Example:
        #
        # frame = 1920x1080
        # zoom  = 2.0
        #
        # ROI = 960x540
        # ====================================================

        crop_w = int(
            round(
                frame_w / zoom
            )
        )

        crop_h = int(
            round(
                frame_h / zoom
            )
        )

        # Safety clamp

        crop_w = max(
            2,
            min(
                frame_w,
                crop_w,
            ),
        )

        crop_h = max(
            2,
            min(
                frame_h,
                crop_h,
            ),
        )

        # ====================================================
        # ROI CENTER
        #
        # self.center_x / center_y phải là tâm bbox Face
        # nếu Face đang được sử dụng làm digital_target.
        # ====================================================

        cx = float(
            self.center_x
        )

        cy = float(
            self.center_y
        )

    # ====================================================
    # CALCULATE TOP-LEFT FROM FACE CENTER
    #
    # Mục tiêu:
    #
    #           ROI
    #   ┌─────────────────┐
    #   │                 │
    #   │        +        │
    #   │     face_c      │
    #   │                 │
    #   └─────────────────┘
    #
    # face center == ROI center
    # ====================================================

        x1 = int(
            round(
                cx
                -
                crop_w / 2.0
            )
        )

        y1 = int(
            round(
                cy
                -
                crop_h / 2.0
            )
        )

    # ====================================================
    # CLAMP ROI INTO ORIGINAL FRAME
    #
    # Nếu ROI vượt khỏi frame thì bắt buộc phải dịch ROI.
    # Trường hợp này face có thể không nằm chính giữa tuyệt
    # đối vì bên ngoài frame không tồn tại pixel.
    # ====================================================

        max_x1 = max(
            0,
            frame_w - crop_w,
        )

        max_y1 = max(
            0,
            frame_h - crop_h,
        )

        x1 = max(
            0,
            min(
                max_x1,
                x1,
            ),
        )

        y1 = max(
            0,
            min(
                max_y1,
                y1,
            ),
        )

    # ====================================================
    # BOTTOM-RIGHT
    # ====================================================

        x2 = (
            x1
            +
            crop_w
        )

        y2 = (
            y1
            +
            crop_h
        )

    # ====================================================
    # CROP ROI
    # ====================================================

        crop = frame_bgr[
            y1:y2,
            x1:x2,
        ]

    # Safety check

        if crop.size == 0:

            transform = self._identity_transform(
                frame_w,
                frame_h,
            )

            return (
                frame_bgr,
                transform,
            )

    # ====================================================
    # RESIZE ROI BACK TO ORIGINAL FRAME SIZE
    #
    # ROI nhỏ:
    #       crop_w x crop_h
    #
    # trở thành:
    #       frame_w x frame_h
    # ====================================================

        rendered = cv2.resize(
            crop,
            (
                frame_w,
                frame_h,
            ),
            interpolation=cv2.INTER_LINEAR,
        )

    # ====================================================
    # CALCULATE TRANSFORM
    # ====================================================

        scale_x = (
            frame_w
            /
            crop_w
        )

        scale_y = (
            frame_h
            /
            crop_h
        )

        transform = {
            "enabled": True,

            "zoom": scale_x,

            "crop_x1": x1,
            "crop_y1": y1,

            "crop_x2": x2,
            "crop_y2": y2,

            "crop_w": crop_w,
            "crop_h": crop_h,

            "output_w": frame_w,
            "output_h": frame_h,

            "scale_x": scale_x,
            "scale_y": scale_y,
        }

    # ====================================================
    # RETURN
    # ====================================================

        return (
            rendered,
            transform,
        )

    # ========================================================
    # TRANSFORM POINT
    # ========================================================

    @staticmethod
    def transform_point(
        x,
        y,
        transform,
    ):

        if not transform.get(
            "enabled",
            False
        ):

            return (
                int(round(x)),
                int(round(y)),
            )

        tx = (
            (
                x
                -
                transform["crop_x1"]
            )
            *
            transform["scale_x"]
        )

        ty = (
            (
                y
                -
                transform["crop_y1"]
            )
            *
            transform["scale_y"]
        )

        return (
            int(round(tx)),
            int(round(ty)),
        )

    # ========================================================
    # TRANSFORM BBOX
    # ========================================================

    def transform_bbox(
        self,
        bbox,
        transform,
    ):

        if bbox is None:
            return None

        x1, y1, x2, y2 = bbox

        nx1, ny1 = (
            self.transform_point(
                x1,
                y1,
                transform,
            )
        )

        nx2, ny2 = (
            self.transform_point(
                x2,
                y2,
                transform,
            )
        )

        output_w = (
            transform.get(
                "output_w"
            )
        )

        output_h = (
            transform.get(
                "output_h"
            )
        )

        if output_w is not None:

            nx1 = max(
                0,
                min(
                    output_w - 1,
                    nx1
                )
            )

            nx2 = max(
                0,
                min(
                    output_w - 1,
                    nx2
                )
            )

        if output_h is not None:

            ny1 = max(
                0,
                min(
                    output_h - 1,
                    ny1
                )
            )

            ny2 = max(
                0,
                min(
                    output_h - 1,
                    ny2
                )
            )

        return (
            nx1,
            ny1,
            nx2,
            ny2,
        )

    # ========================================================
    # TRANSFORM DETECTION
    # ========================================================

    def transform_detection(
        self,
        detection,
        transform,
    ):

        if detection is None:
            return None

        result = dict(
            detection
        )

        result["bbox"] = (
            self.transform_bbox(
                detection["bbox"],
                transform,
            )
        )

        if detection.get("landmarks") is not None:

            result["landmarks"] = [
                self.transform_point(
                    point[0],
                    point[1],
                    transform,
                )
                for point in detection["landmarks"]
            ]

        return result

    # ========================================================
    # TRANSFORM DETECTIONS
    # ========================================================

    def transform_detections(
        self,
        detections,
        transform,
    ):

        if detections is None:
            return []

        return [
            self.transform_detection(
                detection,
                transform,
            )
            for detection
            in detections
        ]

    # ========================================================
    # IDENTITY TRANSFORM
    # ========================================================

    @staticmethod
    def _identity_transform(
        frame_w,
        frame_h,
    ):

        return {
            "enabled": False,

            "zoom": 1.0,

            "crop_x1": 0,
            "crop_y1": 0,
            "crop_x2": frame_w,
            "crop_y2": frame_h,

            "crop_w": frame_w,
            "crop_h": frame_h,

            "output_w": frame_w,
            "output_h": frame_h,

            "scale_x": 1.0,
            "scale_y": 1.0,
        }

    # ========================================================
    # GET ZOOM
    # ========================================================

    @synchronized
    def get_zoom(self):

        return float(
            self.zoom
        )
