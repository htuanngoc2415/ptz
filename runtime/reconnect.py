"""Bounded exponential retry scheduling, independent of GStreamer."""
class ReconnectBackoff:
    def __init__(self, initial=1., maximum=30.):
        if initial <= 0 or maximum < initial:
            raise ValueError('Invalid reconnect backoff')
        self.initial, self.maximum, self.delay = initial, maximum, initial

    def next_delay(self):
        result = self.delay
        self.delay = min(self.maximum, self.delay*2)
        return result

    def reset(self):
        self.delay = self.initial
