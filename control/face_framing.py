"""AI-thread-owned framing latch; independent of recognition identity."""
from event_logging import event_print as print
from telemetry import latency

class FaceFramingLock:
    def __init__(self, ratio, frames, timeout):
        self.ratio, self.frames, self.timeout = ratio, frames, timeout
        self.locked = False
        self.count = 0
        self.last_face = None

    def reset(self, reason='TARGET_RESET'):
        if self.locked:
            latency.profiler.event('FRAMING_RELEASED', reason=reason)
            print(f'[STATE] FACE_FINAL_LOCK released reason={reason}')
        self.locked = False
        self.count = 0
        self.last_face = None

    def update(self, ratio, now, enabled, target_locked, face_observed=True):
        if not enabled or not target_locked:
            self.reset('DISABLED_OR_TARGET_LOST')
            return False
        if ratio is not None:
            self.last_face = now
        elif self.locked and self.last_face is not None and now-self.last_face >= self.timeout:
            self.reset('FACE_HARD_TIMEOUT')
            return False
        if not self.locked and face_observed:
            self.count = self.count + 1 if ratio is not None and ratio >= self.ratio else 0
            if self.count >= self.frames:
                self.locked = True
                latency.profiler.event('FRAMING_CONFIRMED', ratio=ratio, confirmation_count=self.count)
                print(f'[STATE] FACE_FINAL_LOCK entered ratio={ratio:.3f} confirm={self.count}/{self.frames}')
        return self.locked
