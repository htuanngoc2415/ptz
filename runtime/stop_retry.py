"""Finite STOP attempts; exhaustion retains the caller's motor fault latch."""
import time


class StopRetryBudget:
    def __init__(self, attempts=5, initial=.5, maximum=4., clock=time.monotonic):
        self.limit = max(1, int(attempts))
        self.initial, self.maximum, self.clock = initial, maximum, clock
        self.reset()

    def reset(self):
        self.attempts = 0
        self.next_at = 0.

    @property
    def exhausted(self):
        return self.attempts >= self.limit

    def ready(self):
        return not self.exhausted and self.clock() >= self.next_at

    def failed(self):
        self.attempts += 1
        self.next_at = self.clock()+min(self.maximum, self.initial*2**min(self.attempts-1, 20))
