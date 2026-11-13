#!/usr/bin/env python3

from __future__ import annotations
from event_logging import event_print as print
from telemetry.events import emit
from telemetry import latency
from uuid import uuid4

import math
import threading
import time
from typing import Callable, Optional


class AutoFocusController:
    """
    Target-aware focus supervisor. CNN_DELTA uses the 37x reference network
    and closed-loop lens movement; fresh target ROI frames validate the result.
    BUILTIN_AF is retained for hardware-free legacy controller fixtures.

    Current strategy:

        Sharpness >= threshold
                ↓
             skip this check (no persistent lock)

        Sharpness < threshold
                ↓
        Trigger camera vendor AF
                ↓
        fresh ROI frames above threshold → finish early
                ↓
        maximum wait expires → TIMEOUT (never quality success)

    NOTE:
        This is NOT yet a custom hill-climbing focus motor
        algorithm.

        It supervises the camera's built-in autofocus using
        target ROI sharpness.
    """

    def __init__(
        self,
        ptz,

        status_callback: Optional[
            Callable[[str], None]
        ] = None,

        sharpness_threshold: float = 350.0,

        cooldown: float = 2.5,

        settle_time: float = 1.5,
        min_settle_time: float = 0.2,
        confirm_frames: int = 3,
        max_frame_age: float = 0.3,
        max_attempts: int = 3,
        method: str = "BUILTIN_AF",
        orchestrator=None,
    ):
        if method not in ("BUILTIN_AF", "CNN_DELTA", "UNSUPPORTED"):
            raise ValueError("Supported focus methods: CNN_DELTA and legacy test adapter BUILTIN_AF")
        self._cancel_focus = threading.Event()
        self.method = method
        self.orchestrator = orchestrator
        self.max_attempts = max_attempts
        self._worker = None
        self.attempts = 0
        self.quality = None
        self.ptz = ptz

        self.status_callback = (
            status_callback
        )

        self.sharpness_threshold = float(
            sharpness_threshold
        )

        self.cooldown = float(
            cooldown
        )

        self.settle_time = float(
            settle_time
        )

        self.min_settle_time = float(min_settle_time)
        self.confirm_frames = int(confirm_frames)
        self.max_frame_age = float(max_frame_age)
        self._ready = threading.Event()
        self._ack_time = None
        self._last_frame_time = float('-inf')
        self._good_frames = 0
        self._roi_kind = None
        self._generation = 0
        self._attempt_generation = None

        self.enabled = False

        self.locked = False

        self.busy = False

        self.last_trigger_time = 0.0

        self.last_action = "IDLE"

        self.last_sharpness = 0.0

        self._lock = threading.Lock()

    # ========================================================
    # STATUS
    # ========================================================

    def _status(
        self,
        message: str
    ) -> None:

        print(f"[AF] {message}")

        if self.status_callback is not None:

            try:

                self.status_callback(
                    message
                )

            except Exception:
                pass

    # ========================================================
    # ENABLE
    # ========================================================

    def set_enabled(
        self,
        enabled: bool
    ) -> None:

        self.attempts = 0
        self.enabled = bool(enabled) and self.method != "UNSUPPORTED"
        self._cancel_focus.set()
        self._ready.set()
        if (self.busy or not self.enabled) and hasattr(self.ptz, "cancel_focus"):
            self.ptz.cancel_focus()
        self._generation += 1
        self._good_frames = 0

        if not self.enabled:

            self.locked = False

            self.last_action = (
                "AUTO FOCUS DISABLED"
            )

        else:

            self.last_action = (
                "AUTO FOCUS ENABLED"
            )

    # ========================================================
    # UNLOCK
    # ========================================================

    def unlock(self) -> None:
        """
        Call this after optical zoom changes.

        A zoom operation changes focal conditions, therefore
        the previous sharpness lock is no longer trustworthy.
        """

        self._cancel_focus.set()
        if self.busy and hasattr(self.ptz, "cancel_focus"):
            self.ptz.cancel_focus()
        self._generation += 1
        self._good_frames = 0
        self.locked = False

        self.last_action = (
            "FOCUS RECHECK"
        )

    # ========================================================
    # UPDATE
    # ========================================================

    @latency.observed_result('focus_state')
    def update(
        self,
        sharpness: Optional[float],
        allow_focus: bool = True,
        frame_time: Optional[float] = None,
        roi_kind: str = "FACE",
        quality=None,
    ) -> str:

        if not self.enabled:

            self.last_action = (
                "DISABLED"
            )

            return self.last_action

        if not allow_focus:
            self._good_frames = 0
            self.last_action = (
                "WAITING FOR ZOOM"
            )

            return self.last_action

        if sharpness is None:

            self.last_action = (
                "NO SHARPNESS"
            )

            return self.last_action

        self.quality = quality
        self.last_sharpness = float(sharpness)
        if getattr(self, '_report_after', False) and not self.busy:
            print(f'[AF] sharpness_after={self.last_sharpness:.1f}')
            self._report_after = False

        # Only distinct, fresh frames received after the AF acknowledgement
        # may complete this attempt. Changing ROI invalidates consecutive scores.
        if self.busy:
            now = time.monotonic()
            if (self._attempt_generation == self._generation
                    and frame_time is not None and self._ack_time is not None
                    and self._ack_time + self.min_settle_time <= frame_time <= now
                    and now - frame_time <= self.max_frame_age
                    and frame_time > self._last_frame_time):
                self._last_frame_time = frame_time
                if roi_kind != self._roi_kind:
                    self._good_frames = 0
                    self._roi_kind = roi_kind
                good = bool(quality['good']) if quality is not None else math.isfinite(self.last_sharpness) and self.last_sharpness >= self.sharpness_threshold
                latency.profiler.event('FOCUS_QUALITY_VALIDATION', good=good,
                    sharpness=self.last_sharpness, roi_kind=roi_kind,
                    frame_received_timestamp=frame_time, evidence='fresh_post_focus_frame')
                self._good_frames = self._good_frames + 1 if good else 0
                if self._good_frames >= self.confirm_frames:
                    self._ready.set()
            self.last_action = "AUTO FOCUS RUNNING"
            return self.last_action

        # ====================================================
        # QUALITY CHECK (no persistent lock)
        # ====================================================

        if (
            (bool(quality['good']) if quality is not None else self.last_sharpness >= self.sharpness_threshold)
        ):

            self.locked = False

            self.last_action = (
                "SHARPNESS OK"
            )

            return self.last_action

        # ====================================================
        # SHARPNESS DROPPED
        # ====================================================

        now = time.monotonic()

        if (
            now
            -
            self.last_trigger_time
            <
            self.cooldown
        ):

            self.last_action = (
                "FOCUS COOLDOWN"
            )

            return self.last_action

        # ====================================================
        # START AF
        # ====================================================

        if self.attempts >= self.max_attempts:
            self.last_action = "ATTEMPT_LIMIT"
            return self.last_action
        if frame_time is not None and not 0 <= time.monotonic()-frame_time <= self.max_frame_age:
            self.last_action = 'WAIT_FRESH_FRAME'
            return self.last_action
        self._start_autofocus()

        return self.last_action

    # ========================================================
    # START AUTOFOCUS
    # ========================================================

    def _start_autofocus(
        self
    ) -> None:

        with self._lock:

            if self.busy:
                return

            if self.orchestrator is not None and not self.orchestrator.acquire("focus"):
                self.last_action = "WAIT_OPTICS"
                return
            self.attempts += 1
            self.busy = True

        self._cancel_focus = threading.Event()
        self._attempt_generation = self._generation
        self._ready.clear()
        self._ack_time = None
        self._last_frame_time = float('-inf')
        self._good_frames = 0
        self._roi_kind = None
        self.last_trigger_time = time.monotonic()

        self.last_action = "AUTO FOCUS TRIGGER"
        emit('FOCUS_START', attempt=self.attempts, sharpness=self.last_sharpness)
        print(f'[AF] AF_TRIGGER sharpness_before={self.last_sharpness:.1f}')

        correlation = dict(latency.context.get(), command_id='cmd_'+uuid4().hex)
        self._focus_request_ns = latency.profiler.event('FOCUS_REQUEST_CREATED', **correlation,
                                                       focus_score_before=self.last_sharpness)
        thread = threading.Thread(target=self._autofocus_worker, args=(correlation,),
                                  name='focus-supervisor', daemon=True)
        self._worker = thread

        thread.start()

    # ========================================================
    # WORKER
    # ========================================================

    def _autofocus_worker(self, correlation=None) -> None:
        latency.context.set(correlation or {})

        generation = self._attempt_generation
        started = time.monotonic()
        latency.profiler.event("AF_STARTED", evidence="application_focus_worker_started", af_state="BUSY")
        outcome = "ERROR"
        try:
            if self.method == 'CNN_DELTA':
                success, response = self.ptz.autofocus(cancel=self._cancel_focus)
            else:
                success, response = self.ptz.autofocus()

            if not success:

                self._status(
                    "AUTO FOCUS FAILED: "
                    f"{response}"
                )

                return

            self._status(
                "AUTO FOCUS..."
            )

            self._ack_time = time.monotonic()
            settle_ns = latency.profiler.event('FOCUS_SETTLE_STARTED',
                evidence='controller_returned_after_motor_loop')
            print(f'[AF] ACK command_ms={(self._ack_time-started)*1000:.1f}')
            # Independent worker timeout also expires when video stops arriving.
            ready = self._ready.wait(self.settle_time)
            outcome = "ABORTED" if generation != self._generation else "QUALITY_OK" if ready else "TIMEOUT"
            latency.profiler.elapsed('FOCUS_SETTLE_ENDED', settle_ns, 'focus_quality_wait_ms', outcome=outcome)
            if outcome == 'QUALITY_OK':
                latency.profiler.elapsed('FOCUS_STABLE', getattr(self, '_focus_request_ns', None),
                    'focus_stabilization_ms', evidence='estimated_visual_quality',
                    motor_stopped='unavailable', focus_score_after=self.last_sharpness)
            self._report_after = True

        except Exception as exc:

            self._status(
                f"AUTO FOCUS ERROR: {exc}"
            )

        finally:

            self.last_trigger_time = (
                time.monotonic()
            )

            self._ack_time = None
            self.last_action = outcome
            latency.profiler.event('AF_COMPLETED', outcome=outcome, evidence='application_focus_worker_finished',
                                   focus_score=self.last_sharpness, af_state=outcome)
            emit('FOCUS_RESULT', outcome=outcome, attempt=self.attempts, quality=self.quality)
            print(f'[AF] {outcome} elapsed={time.monotonic()-started:.3f}s '
                  f'sharpness={self.last_sharpness:.1f} cooldown={self.cooldown:.2f}s')
            self.busy = False
            if self.orchestrator is not None:
                self.orchestrator.release("focus", self.min_settle_time)

    # ========================================================
    # STOP / RESET
    # ========================================================

    def reset(self) -> None:

        self.attempts = 0
        self.unlock()
        # Keep ownership until any in-flight HTTP request/worker finishes.
        self.last_trigger_time = 0.0

        self.last_action = (
            "RESET"
        )

    def close(self, timeout=5.):
        self.set_enabled(False)
        self._ready.set()
        if self._worker is not None:
            self._worker.join(timeout)
            return not self._worker.is_alive()
        return True
