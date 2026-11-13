from dataclasses import dataclass, replace
import threading
import time
from telemetry import latency


@dataclass(frozen=True)
class Frame:
    frame_id: int
    image: object
    receive_timestamp: float
    capture_timestamp: float | None = None  # Unknown without a synchronized sensor clock.
    received_ns: int | None = None
    inference_timestamp: float | None = None

    def age_ms(self, now=None):
        return max(0., ((time.monotonic() if now is None else now)-self.receive_timestamp)*1000)


class FrameManager:
    """Single-slot mailbox. Published images must not be mutated by consumers."""
    def __init__(self, profile=True):
        self._profile = profile
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._frame = None
        self._sequence = 0

    def publish(self, image, received=None, captured=None, received_ns=None):
        with self._lock:
            self._sequence += 1
            stamp = time.perf_counter_ns() if received_ns is None else received_ns
            self._frame = Frame(self._sequence, image, time.monotonic() if received is None else received, captured, stamp)
            self._condition.notify_all()
            if self._profile:
                latency.profiler.count('received_frames')
                latency.profiler.event('FRAME_RECEIVED', frame_id=self._sequence, monotonic_ns=stamp,
                                       receive_boundary='decoded_app_frame')
                latency.profiler.event('FRAME_CAPTURE_TIMESTAMP', frame_id=self._sequence,
                                       capture_timestamp=captured, status='unavailable' if captured is None else 'unsynchronized')
                latency.profiler.event('DECODE_START', frame_id=self._sequence, status='unavailable',
                                       reason='GStreamer decoder timing not exposed by appsink')
                latency.profiler.event('DECODE_END', frame_id=self._sequence, status='unavailable')
                latency.profiler.event('FRAME_MANAGER_PUSH', frame_id=self._sequence)
                latency.profiler.event('INFERENCE_QUEUE_ENTER', frame_id=self._sequence, branch='detection_mailbox')
            return self._frame

    def latest(self, after=-1, now=None, consumer=None, timeout=0):
        """Wait at most timeout seconds for a newer frame; never queue old images."""
        with self._condition:
            if timeout:
                self._condition.wait_for(
                    lambda: self._frame is not None and self._frame.frame_id > after,
                    timeout=timeout,
                )
            if self._frame is None or self._frame.frame_id <= after:
                return None
            if consumer == 'detection':
                latency.profiler.event('FRAME_MANAGER_POP', frame_id=self._frame.frame_id)
                latency.profiler.elapsed('INFERENCE_QUEUE_EXIT', self._frame.received_ns,
                                         'detection_mailbox_wait_ms', frame_id=self._frame.frame_id)
            return replace(self._frame, inference_timestamp=time.monotonic() if now is None else now)

    def clear(self):
        with self._lock:
            self._frame = None
