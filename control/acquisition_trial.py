"""Per-attempt timing from the first person frame received by the application.

This is an application latency measurement, not a sensor-to-result measurement.
The AI loop enforces the deadline at its next scheduling opportunity; existing
motor watchdogs remain responsible for stalled inference/video.
"""
from event_logging import event_print as print


class AcquisitionTrial:
    def __init__(self, deadline):
        self.deadline = float(deadline)
        self.started = None
        self.outcome = None
        self.marks = set()
        self._stop_reported = False

    def start(self, now):
        if self.started is None:
            self.started = now
            print(f'[TRIAL] START deadline={self.deadline:.1f}s origin=first_person_received_frame')

    def mark(self, name, now):
        if self.started is not None and self.outcome is None and name not in self.marks:
            self.marks.add(name)
            print(f'[TRIAL] {name} elapsed={now-self.started:.3f}s')

    def finish(self, outcome, now):
        if self.started is None or self.outcome is not None:
            return
        elapsed = now - self.started
        self.outcome = 'TIMEOUT' if elapsed >= self.deadline else outcome
        print(f'[TRIAL] {self.outcome} elapsed={elapsed:.3f}s deadline={self.deadline:.1f}s')

    def expire(self, now):
        if self.started is not None and self.outcome is None and now-self.started >= self.deadline:
            self.finish('TIMEOUT', now)
        if self.outcome == 'TIMEOUT' and not self._stop_reported:
            self._stop_reported = True
            return True
        return False
