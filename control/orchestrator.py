"""Atomic camera ownership, including settling; no I/O or GUI dependencies."""
import threading
import time
from telemetry import latency


class ControlOrchestrator:
    def __init__(self):
        self._lock = threading.RLock()
        self.owner = None
        self.until = 0.
        self.manual = False
        self.closing = False
    def acquire(self, owner, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.closing:
                return False
            if self.manual and owner != 'manual':
                latency.profiler.event('CONTROL_RESOURCE_REJECTED', requester=owner, reason='MANUAL_OWNER')
                return False
            if self.owner is not None or now < self.until:
                latency.profiler.event('CONTROL_RESOURCE_REJECTED', requester=owner,
                    reason='OWNER_BUSY' if self.owner is not None else 'LENS_SETTLE',
                    owner=self.owner, settle_remaining_ms=max(0., self.until-now)*1000)
                return False
            self.owner = owner
            return True

    def release(self, owner, settle=0., now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            if self.owner == owner:
                self.owner = None
                self.until = now + settle

    def blocked(self, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            return self.closing or self.manual or self.owner is not None or now < self.until


    def begin_shutdown(self):
        with self._lock:
            self.closing = True
            self.manual = True
