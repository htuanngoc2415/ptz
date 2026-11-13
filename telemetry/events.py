"""Structured events without pixel/embedding payloads or credential-bearing URLs."""
import json
import math
from numbers import Integral, Real
import logging
import time
from datetime import datetime, timezone
from event_logging import redact


def _sanitize(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _sanitize(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(v) for v in value]
    return value


def emit(event, **metrics):
    from telemetry import latency
    if latency.profiler.enabled:
        latency.profiler.event('STRUCTURED_'+event, **metrics)
    # Callers must not pass credentials, image pixels or embeddings.
    payload = _sanitize(dict(event=event, timestamp=datetime.now(timezone.utc).isoformat(),
                             monotonic_ts=time.monotonic(), **metrics))
    logging.getLogger('ptz.metrics').info('%s', json.dumps(payload, ensure_ascii=False,
                                                        allow_nan=False, default=str))
