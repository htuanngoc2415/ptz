#!/usr/bin/env python3
"""Read-only diagnostic aggregation; accepts a latency JSONL and its rotations.

Usage: python tools/pan_tilt_report.py logs/latency/latency_trace_<run>.jsonl
Decision ratios use full-run counters when available; sampled events otherwise.
"""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(paths):
    rows = []
    malformed = 0
    for path in paths:
        for line in Path(path).read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except (ValueError, TypeError):
                malformed += 1
    runs = {r.get('run_id') for r in rows if r.get('run_id')}
    if len(runs) > 1:
        raise ValueError('Supply one run only')
    rows.sort(key=lambda r:r.get('monotonic_ns', 0))
    reports = [r for r in rows if r.get('event') == 'LATENCY_REPORT']
    counters = reports[-1].get('counters_and_rates', {}) if reports else {}
    prefix = 'pan_tilt_decisions_'
    reasons = {k[len(prefix):]:v for k,v in counters.items() if k.startswith(prefix)}
    scope = 'full_run_evaluations' if reasons else 'sampled_retained_events'
    if not reasons:
        reasons = dict(Counter(r.get('decision_reason') for r in rows
            if r.get('event') == 'PAN_TILT_DECISION'))
    total = sum(reasons.values())
    dispatched = [r for r in rows if r.get('event') == 'PAN_TILT_DISPATCH']
    ended = [r for r in rows if r.get('event') == 'PAN_TILT_COMMAND_ENDED']
    def stats(values):
        values = sorted(v for v in values if isinstance(v,(int,float)))
        if not values:return None
        return dict(count=len(values),mean=sum(values)/len(values),max=max(values))
    span = ((rows[-1]['monotonic_ns']-rows[0]['monotonic_ns'])/1e9) if len(rows)>1 else 0
    return dict(run_id=next(iter(runs),None), malformed_lines=malformed,
        retained_span_seconds=span, retained_dispatch_count=len(dispatched),
        retained_command_rate_hz=len(dispatched)/span if span else None,
        total_command_count=counters.get('pan_tilt_command_count'),
        decision_scope=scope, decision_counts=reasons,
        decision_ratio={k:v/total for k,v in reasons.items()} if total else {},
        detection_to_supervisor_dispatch_ms=stats(r.get('detection_to_dispatch_ms') for r in dispatched),
        observation_to_supervisor_dispatch_ms=stats(r.get('observation_age_ms') for r in dispatched),
        observation_to_wire_dispatch_ms=stats(r.get('observation_age_ms') for r in rows
            if r.get('event')=='PAN_TILT_WIRE_DISPATCH'),
        host_active_duration_ms=stats(r.get('command_active_duration_ms') for r in ended),
        inter_pulse_wait_ms=stats(r.get('inter_pulse_wait_ms') for r in dispatched),
        image_displacement_samples=sum(r.get('image_displacement_px') is not None for r in ended),
        note='Retained trace may omit rotated-away data; ACK/durations are not physical motor telemetry.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('trace',type=Path)
    args=parser.parse_args()
    paths=list(args.trace.parent.glob(args.trace.name+'*'))
    print(json.dumps(summarize(paths),indent=2))
