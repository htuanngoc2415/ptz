#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time
import math
from collections import deque
from statistics import median
from typing import Optional


# ============================================================
# STATE
# ============================================================

class TargetState(str, Enum):
    SEARCH_PERSON = "SEARCH_PERSON"
    ZOOM_PERSON = "ZOOM_PERSON"
    SEARCH_FACE = "SEARCH_FACE"
    ZOOM_FACE = "ZOOM_FACE"
    TRACK_FACE = "TRACK_FACE"
    LOST_TARGET = "LOST_TARGET"
    FACE_LOST_HOLD = "FACE_LOST_HOLD"
    FACE_RECOVERY = "FACE_RECOVERY"


# ============================================================
# RESULT
# ============================================================

@dataclass
class StateMachineResult:
    state: TargetState

    control_target: Optional[dict]

    target_type: Optional[str]

    target_ratio: Optional[float]

    tolerance: Optional[float]

    person_ratio: Optional[float]

    face_ratio: Optional[float]

    person_ready_count: int

    face_ready_count: int

    face_lost_count: int

    target_lost: bool = False


# ============================================================
# STATE MACHINE
# ============================================================

class TargetStateMachine:
    """
    Hierarchical target controller.

    Flow:

        SEARCH_PERSON
            ↓
        ZOOM_PERSON
            ↓
        SEARCH_FACE
            ↓
        ZOOM_FACE
            ↓
        TRACK_FACE

    Face lost:
        retain face framing for the session; hold on face loss, recover only when both are lost.

    Person lost:
        LOST_TARGET.

    IMPORTANT:
        This class does NOT control the motor.

        It only decides:
            - current state
            - which target should control zoom
            - target size ratio
    """

    def __init__(
        self,

        # Person zoom target
        person_target_ratio: float = 0.50,
        person_tolerance: float = 0.04,
        person_ready_frames: int = 5,

        # Face zoom target
        face_target_ratio: float = 0.25,
        face_tolerance: float = 0.03,
        face_ready_frames: int = 3,

        # Lost handling
        face_lost_frames: int = 5,
        target_lost_timeout: float = 0.80,
        face_acquire_frames: int = 3,
        face_lost_timeout: float = 1.0,
        face_lost_hard_timeout: float = 3.0,
        ratio_history: int = 3,
        ratio_filter: str = "median",
        face_min_ratio: Optional[float] = None,
        face_max_ratio: Optional[float] = None,
    ):
        self.person_target_ratio = float(
            person_target_ratio
        )

        self.person_tolerance = float(
            person_tolerance
        )

        self.person_ready_frames = max(
            1,
            int(person_ready_frames)
        )

        self.face_target_ratio = float(
            face_target_ratio
        )

        self.face_tolerance = float(
            face_tolerance
        )

        self.face_ready_frames = max(
            1,
            int(face_ready_frames)
        )

        self.face_lost_frames = max(
            1,
            int(face_lost_frames)
        )

        self.target_lost_timeout = float(
            target_lost_timeout
        )

        if ratio_filter != "median":
            raise ValueError("Only median ratio filtering is supported")
        self.face_acquire_frames = max(1, int(face_acquire_frames))
        self.face_lost_timeout = float(face_lost_timeout)
        self.face_lost_hard_timeout = float(face_lost_hard_timeout)
        self.face_min_ratio = (self.face_target_ratio - self.face_tolerance
                               if face_min_ratio is None else face_min_ratio)
        self.face_max_ratio = (self.face_target_ratio + self.face_tolerance
                               if face_max_ratio is None else face_max_ratio)
        self.face_ratios = deque(maxlen=max(1, int(ratio_history)))
        self.face_acquire_count = 0
        self.face_latched = False
        self.state = (
            TargetState.SEARCH_PERSON
        )

        self.person_ready_count = 0
        self.face_ready_count = 0
        self.face_lost_count = 0

        self.last_person_time = None
        self.last_face_time = None

    # ========================================================
    # RESET
    # ========================================================

    def reset(self) -> None:
        self.face_ratios.clear()
        self.face_acquire_count = 0
        self.face_latched = False

        self.state = (
            TargetState.SEARCH_PERSON
        )

        self.person_ready_count = 0
        self.face_ready_count = 0
        self.face_lost_count = 0

        self.last_person_time = None
        self.last_face_time = None

    # ========================================================
    # UTILITIES
    # ========================================================

    @staticmethod
    def calculate_height_ratio(
        target: Optional[dict],
        frame_shape,
    ) -> Optional[float]:
        """
        bbox height / frame height
        """

        if target is None:
            return None

        if frame_shape is None:
            return None

        bbox = target.get("bbox")

        if bbox is None:
            return None

        frame_h = frame_shape[0]

        if frame_h <= 0:
            return None

        x1, y1, x2, y2 = bbox

        target_height = max(
            0,
            y2 - y1
        )

        return (
            float(target_height)
            /
            float(frame_h)
        )

    @staticmethod
    def ratio_is_ready(
        ratio: Optional[float],
        target_ratio: float,
        tolerance: float,
    ) -> bool:

        if ratio is None:
            return False

        return (
            abs(
                ratio
                -
                target_ratio
            )
            <=
            tolerance
        )

    # ========================================================
    # RESULT CREATION
    # ========================================================

    def _result(
        self,
        control_target,
        target_type,
        target_ratio,
        tolerance,
        person_ratio,
        face_ratio,
        target_lost=False,
    ) -> StateMachineResult:

        from telemetry import latency
        latency.profiler.gate('target_state', self.state.value,
            person_ready_count=self.person_ready_count, face_ready_count=self.face_ready_count,
            face_lost_count=self.face_lost_count, target_lost=target_lost)
        return StateMachineResult(
            state=self.state,
            control_target=control_target,
            target_type=target_type,
            target_ratio=target_ratio,
            tolerance=tolerance,
            person_ratio=person_ratio,
            face_ratio=face_ratio,
            person_ready_count=self.person_ready_count,
            face_ready_count=self.face_ready_count,
            face_lost_count=self.face_lost_count,
            target_lost=target_lost,
        )

    # ========================================================
    # UPDATE
    # ========================================================

    def update(self, frame_shape, person_target, face_target, now=None, face_observed=True):
        """Reuse ZOOM_PERSON / SEARCH_FACE / ZOOM_FACE / TRACK_FACE states.

        TRACK_FACE is the zoom lock; short face losses never select a body box.
        Ratios always refer to the original frame, before digital cropping.
        """
        now = time.monotonic() if now is None else now
        person_ratio = self.calculate_height_ratio(person_target, frame_shape)
        raw_face = self.calculate_height_ratio(face_target, frame_shape)
        if raw_face is None or not math.isfinite(raw_face) or raw_face <= 0:
            face_target = None
            raw_face = None
        if person_target is not None:
            self.last_person_time = now

        # Face ownership survives detector misses and slow inference. Only a full
        # target reset releases it; an oversized body must never reverse face zoom.
        face_gap = now-self.last_face_time if self.last_face_time is not None else 0.0
        if not self.face_latched and face_gap >= self.face_lost_timeout:
            self.face_acquire_count = 0
        if face_gap >= self.face_lost_hard_timeout:
            self.face_ratios.clear()
            self.face_ready_count = 0

        face_ratio = None
        if face_target is not None:
            self.last_face_time = now
            self.face_lost_count = 0
            self.face_acquire_count += 1
            self.face_ratios.append(raw_face)
            face_ratio = median(self.face_ratios)
            if self.face_acquire_count >= self.face_acquire_frames:
                self.face_latched = True
        elif face_observed:
            self.face_acquire_count = 0
            self.face_ready_count = 0
            self.face_lost_count += 1
            # Preserve median history across short misses.
            if not self.face_latched:
                self.face_ratios.clear()

        if self.face_latched:
            if face_target is None:
                self.state = (TargetState.FACE_RECOVERY
                              if person_target is None and face_gap >= self.face_lost_hard_timeout
                              else TargetState.FACE_LOST_HOLD)
                seen = [t for t in (self.last_person_time, self.last_face_time) if t is not None]
                lost = person_target is None and bool(seen) and now-max(seen) >= self.face_lost_hard_timeout
                return self._result(None, "FACE", self.face_target_ratio,
                                    self.face_tolerance, person_ratio, None, lost)
            ready = self.face_min_ratio <= face_ratio <= self.face_max_ratio
            self.face_ready_count = self.face_ready_count + 1 if ready else 0
            self.state = (TargetState.TRACK_FACE
                          if self.face_ready_count >= self.face_ready_frames
                          else TargetState.ZOOM_FACE)
            self.person_ready_count = 0
            return self._result(face_target, "FACE", self.face_target_ratio,
                                self.face_tolerance, person_ratio, face_ratio)

        if person_target is None and face_target is None:
            seen = [t for t in (self.last_person_time, self.last_face_time)
                    if t is not None]
            lost = bool(seen) and now - max(seen) >= self.target_lost_timeout
            self.state = TargetState.LOST_TARGET if lost else TargetState.SEARCH_PERSON
            self.person_ready_count = 0
            return self._result(None, None, None, None, None, None, lost)

        ready = self.ratio_is_ready(person_ratio, self.person_target_ratio,
                                    self.person_tolerance)
        self.person_ready_count = self.person_ready_count + 1 if ready else 0
        self.state = (TargetState.SEARCH_FACE
                      if face_target is not None or self.person_ready_count >= self.person_ready_frames
                      else TargetState.ZOOM_PERSON)
        return self._result(person_target, "PERSON", self.person_target_ratio,
                            self.person_tolerance, person_ratio, face_ratio)
