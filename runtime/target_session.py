"""Atomic validity token shared with motor workers; tracking remains single-owner."""
import threading


class TargetSession:
    def __init__(self):
        self._lock = threading.Lock()
        self._session = None
        self._track = None

    def publish(self, session, track_id=None):
        with self._lock:
            self._session = session
            self._track = track_id

    def matches(self, session, track_id=None):
        with self._lock:
            return (session is not None and session == self._session
                    and (track_id is None or track_id == self._track))
