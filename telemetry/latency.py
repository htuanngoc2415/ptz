"""Bounded asynchronous latency trace. All durations use perf_counter_ns.

Wall times are labels only. Missing sensor/decode/motor telemetry is never inferred.
"""
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
import csv
import json
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import queue
import threading
import time
import os
from uuid import uuid4

context = ContextVar('latency_context', default={})


class LatencyProfiler:
    def __init__(self, directory='logs', enabled=False, report_interval_seconds=30,
                 save_csv_summary=True, log_per_stage=True, log_camera_control=True,
                 detailed_gpu_profiling=False, max_bytes=8_000_000, backups=3,
                 queue_size=8192, sample_limit=4096, resource_interval_seconds=1.,
                 scenario=None, **unused):
        self.enabled = enabled
        self.directory = Path(directory)
        self.interval = max(1., float(report_interval_seconds))
        self.save_csv = save_csv_summary
        self.per_stage, self.camera = log_per_stage, log_camera_control
        self.detailed_gpu = detailed_gpu_profiling
        self.max_bytes, self.backups = max_bytes, backups
        self.queue = queue.Queue(maxsize=queue_size)
        self.samples = defaultdict(lambda: deque(maxlen=sample_limit))
        self.totals = {}
        self.counters = Counter()
        self.latest = {}
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.started = time.perf_counter_ns()
        self.stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')
        self.thread = None
        self.error = None
        self.handlers = []
        self.run_id = uuid4().hex
        self.scenario = scenario or os.environ.get('PTZ_PROFILE_SCENARIO', 'unknown')
        self.resource_interval = max(.25, float(resource_interval_seconds))
        self._overhead_sequence = 0
        self._resource_previous = None
        self._gates = {}
        self.first_failures = {}
        self._failure_dirty = threading.Event()
        if enabled:
            self.start()

    def start(self):
        with self.lock:
            if self.thread is None and not self.closed.is_set():
                self.thread = threading.Thread(target=self._write, name='latency-writer', daemon=True)
                self.thread.start()

    def count(self, name, amount=1):
        if self.enabled:
            with self.lock:
                self.counters[name] += amount

    def event(self, event, metric=None, duration_ms=None, monotonic_ns=None, **fields):
        overhead_start = time.perf_counter_ns()
        now = time.perf_counter_ns() if monotonic_ns is None else monotonic_ns
        from telemetry import zoom_trace
        zoom_trace.recorder.observe(event, now, dict(context.get(), metric=metric, duration_ms=duration_ms, **fields))
        if not self.enabled or self.closed.is_set():
            return now
        with self.lock:
            if metric is not None and duration_ms is not None and math.isfinite(duration_ms) and duration_ms >= 0:
                self.samples[metric].append(duration_ms)
                n, total, low, high = self.totals.get(metric, (0, 0., duration_ms, duration_ms))
                self.totals[metric] = (n+1, total+duration_ms, min(low, duration_ms), max(high, duration_ms))
                self.latest[metric] = duration_ms
        if not self.per_stage and event.endswith(('_START', '_END')):
            return now
        row = dict(context.get(), **fields)
        # Explicit aliases win over inherited context from the previous frame.
        row['session_id'] = fields.get('session_id', fields.get('lock_session_id',
            row.get('lock_session_id') or row.get('session_id')))
        row['track_id'] = fields.get('track_id', fields.get('target_track_id',
            row.get('target_track_id', row.get('track_id'))))
        for key in ('frame_id', 'track_generation'):
            row.setdefault(key, None)
        row.update(timestamp_monotonic=now/1e9, run_id=self.run_id, scenario=self.scenario)
        row.update(timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'),
                   monotonic_ns=now, event=event, thread=threading.current_thread().name)
        if metric is not None:
            row.update(metric=metric, duration_ms=duration_ms)
        failure_key = event
        if event == 'HANET_RESPONSE_SCHEMA':
            # Preserve one bounded sanitized schema per operation/classification,
            # including the first success, independently of queue and rotation.
            operation = row.get('operation')
            classification = row.get('ack_classification')
            if operation in ('START', 'STOP') and classification in (
                    'ACK_SUCCESS', 'ACK_REJECTED', 'ACK_UNKNOWN', 'PROTOCOL_ERROR'):
                failure_key = (event, operation, classification)
        if failure_key != event or event in ('HANET_TRANSPORT_ERROR', 'PAN_TILT_START_UNCONFIRMED',
                     'PAN_TILT_STOP_UNCONFIRMED', 'MOTOR_FAULT_LATCHED',
                     'HANET_RECONNECT_FAILED', 'HANET_CLEANUP_ERROR',
                     'SHUTDOWN_STOP_TIMEOUT', 'SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR'):
            with self.lock:
                if failure_key not in self.first_failures:
                    # One small summary per failure kind, independent of rotation
                    # and of the possibly full per-frame log queue.
                    self.first_failures[failure_key] = row.copy()
                    self._failure_dirty.set()
        try:
            self.queue.put_nowait(row)
        except queue.Full:
            self.count('dropped_log_events')
        # Sample instrumentation cost, including the existing zoom observer,
        # without recursively emitting another event on the producer thread.
        self._overhead_sequence += 1
        if self._overhead_sequence % 128 == 0:
            cost = (time.perf_counter_ns()-overhead_start)/1e6
            with self.lock:
                self.samples['profiling_emit_sample_ms'].append(cost)
                n, total, low, high = self.totals.get('profiling_emit_sample_ms', (0, 0., cost, cost))
                self.totals['profiling_emit_sample_ms'] = (n+1, total+cost, min(low,cost), max(high,cost))
        return now

    def elapsed(self, event, start, metric, **fields):
        now = time.perf_counter_ns()
        if start is not None and now >= start:
            self.event(event, metric=metric, duration_ms=(now-start)/1e6,
                       monotonic_ns=now, start_ns=start, **fields)
        return now

    def snapshot(self):
        with self.lock:
            elapsed = max(1e-9, (time.perf_counter_ns()-self.started)/1e9)
            return dict(self.latest, **self.counters,
                        face_detector_fps=self.counters['scrfd_calls']/elapsed,
                        face_tracker_fps=self.counters['face_tracker_frames']/elapsed,
                        camera_receive_fps=self.counters['received_frames']/elapsed,
                        detection_fps=self.counters['detection_frames']/elapsed,
                        gui_unique_fps=self.counters['gui_unique_frames']/elapsed if self.counters['gui_submissions'] else None,
                        recognition_attempts_per_second=self.counters['recognition_attempts']/elapsed)

    def summary(self):
        rows = []
        with self.lock:
            for stage, values in self.samples.items():
                ordered = sorted(values)
                if not ordered:
                    continue
                n, total, low, high = self.totals[stage]
                row = dict(stage=stage, count=n, mean=total/n, min=low, max=high,
                           percentile_window_count=len(ordered))
                for p in (50, 90, 95, 99):
                    index = (len(ordered)-1)*p/100
                    lo, hi = math.floor(index), math.ceil(index)
                    row[f'p{p}'] = ordered[lo]+(ordered[hi]-ordered[lo])*(index-lo)
                rows.append(row)
        return rows

    def _report(self):
        rows = self.summary()
        if self.handlers:
            report_ns = time.perf_counter_ns()
            report = dict(event='LATENCY_REPORT', timestamp=datetime.now().astimezone().isoformat(),
                          monotonic_ns=report_ns, thread=threading.current_thread().name,
                          run_id=self.run_id, scenario=self.scenario,
                          timestamp_monotonic=report_ns/1e9, frame_id=None,
                          session_id=None, track_id=None, track_generation=None,
                          statistics=rows, counters_and_rates=self.snapshot(),
                          percentile_scope='bounded_recent_window', aggregate_scope='since_profiler_start')
            self.handlers[0].emit(logging.LogRecord('latency', 20, '', 0, json.dumps(report), (), None))
            lines = ['================ LATENCY REPORT ================',
                     'Stage                          Mean      P50      P90      P99']
            for row in rows:
                lines.append(f"{row['stage']:30} {row['mean']:8.2f} {row['p50']:8.2f} {row['p90']:8.2f} {row['p99']:8.2f}")
            lines.append(json.dumps(self.snapshot()))
            self.handlers[2].emit(logging.LogRecord('latency', 20, '', 0, '\n'.join(lines), (), None))
        if self.save_csv and rows:
            path = self.directory/'latency'/f'latency_summary_{self.stamp}.csv'
            temp = path.with_suffix('.tmp')
            with temp.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            temp.replace(path)

    def _write(self):
        handlers = []
        try:
            for folder, name, suffix in [('latency', 'latency_trace', 'jsonl'),
                                          ('camera', 'camera_control', 'log'),
                                          ('latency', 'latency_readable', 'log')]:
                directory = self.directory/folder
                directory.mkdir(parents=True, exist_ok=True)
                # Bound retained sessions as well as each rotating file.
                old = sorted(directory.glob(f'{name}_*'), key=lambda p: p.stat().st_mtime)
                for path in old[:-20]:
                    path.unlink()
                if folder == 'latency':
                    summaries = sorted(directory.glob('latency_summary_*.csv'))
                    for path in summaries[:-20]:
                        path.unlink()
                handlers.append(RotatingFileHandler(directory/f'{name}_{self.stamp}.{suffix}',
                                maxBytes=self.max_bytes, backupCount=self.backups, encoding='utf-8'))
            fault_dir = self.directory/'camera'
            for path in sorted(fault_dir.glob('first_failures_*.json'))[:-19]:
                path.unlink()
            self.handlers = handlers
            deadline = time.monotonic()+self.interval
            resource_deadline = time.monotonic()+self.resource_interval
            while not self.closed.is_set() or not self.queue.empty() or self._failure_dirty.is_set():
                try:
                    row = self.queue.get(timeout=.1)
                except queue.Empty:
                    row = None
                if self._failure_dirty.is_set():
                    from telemetry.events import _sanitize
                    with self.lock:
                        failures = list(self.first_failures.values())
                        self._failure_dirty.clear()
                    path = fault_dir/f'first_failures_{self.stamp}.json'
                    temp = path.with_suffix('.tmp')
                    temp.write_text(json.dumps(_sanitize(failures), allow_nan=False), encoding='utf-8')
                    temp.replace(path)
                if row is not None:
                    from telemetry.events import _sanitize
                    text = json.dumps(_sanitize(row), allow_nan=False)
                    handlers[0].emit(logging.LogRecord('latency', 20, '', 0, text, (), None))
                    duration = row.get('duration_ms')
                    suffix = '' if duration is None else f' {duration:.3f} ms'
                    readable = f"[{row['timestamp']}] [FRAME {row.get('frame_id', 'N/A')}] [{row['event']}]{suffix}"
                    handlers[2].emit(logging.LogRecord('latency', 20, '', 0, readable, (), None))
                    if self.camera and row['event'].startswith(('ZOOM_', 'FOCUS_', 'CAMERA_', 'HANET_', 'PAN_TILT_', 'MOTOR_', 'SHUTDOWN_')):
                        readable = f"[{row['timestamp']}] [{row['event']}] {text}"
                        handlers[1].emit(logging.LogRecord('camera', 20, '', 0, readable, (), None))
                if time.monotonic() >= deadline:
                    self._report()
                    deadline = time.monotonic()+self.interval
                if time.monotonic() >= resource_deadline and not self.closed.is_set():
                    self._resource_sample()
                    resource_deadline = time.monotonic()+self.resource_interval
            self._report()
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
        finally:
            for handler in handlers:
                handler.close()

    def close(self, timeout=5.):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout)
            return not self.thread.is_alive()
        return True

    def gate(self, lane, reason, **fields):
        """Observed state dwell, NOT proof that a worker was blocked throughout.

        Closing requires another observation. An open gate at EOF is censored.
        Independent lanes can overlap and must never be added to E2E latency.
        """
        if not self.enabled:
            return
        current = dict(context.get(), **fields)
        key = (threading.get_ident(), lane)
        token = (reason, current.get('lock_session_id'), current.get('track_generation'))
        now = time.perf_counter_ns()
        with self.lock:
            previous = self._gates.get(key)
            if previous and previous[0] == token:
                return
            if previous:
                old, start, correlation = previous
                self.event('GATE_END', metric=lane+'_observed_dwell_ms',
                    duration_ms=(now-start)/1e6, monotonic_ns=now, start_ns=start,
                    **dict(correlation, lane=lane, reason=old[0], evidence='observed_state_dwell'))
            if reason is None:
                self._gates.pop(key, None)
            else:
                self._gates[key] = (token, now, current)
                self.event('GATE_START', monotonic_ns=now,
                    **dict(current, lane=lane, reason=reason, evidence='observed_state_dwell'))

    def _resource_sample(self):
        # Runs on the existing writer. No CUDA synchronization or external process.
        from telemetry.resources import sample
        values, self._resource_previous = sample(self._resource_previous)
        self.event('RESOURCE_SAMPLE', **values, counters=self.snapshot(),
                   queue_depth=self.queue.qsize(), evidence='host_sample')


profiler = LatencyProfiler()


def configure(**kwargs):
    global profiler
    profiler.close()
    if os.environ.get('PTZ_PROFILE_DIRECTORY'):
        kwargs['directory'] = os.environ['PTZ_PROFILE_DIRECTORY']
        kwargs['enabled'] = True
        kwargs['log_per_stage'] = True
    profiler = LatencyProfiler(**kwargs)
    return profiler


@contextmanager
def correlated(**fields):
    token = context.set(dict(context.get(), **fields))
    try:
        yield
    finally:
        context.reset(token)


@contextmanager
def span(name, metric=None, **fields):
    if not profiler.enabled:
        # Keep existing zoom tracing semantics when the latency sink is disabled.
        from telemetry import zoom_trace
        if not zoom_trace.recorder.enabled:
            yield
            return
    fields = dict(fields, span_id=uuid4().hex)
    fields.setdefault('timing', 'host_wall')
    start = profiler.event(name+'_START', **fields)
    try:
        yield
    except Exception:
        profiler.event(name+'_ERROR', **fields)
        raise
    else:
        profiler.elapsed(name+'_END', start, metric or name.lower()+'_ms', **fields)


def measured(name, metric=None):
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            with span(name, metric):
                return function(*args, **kwargs)
        return run
    return decorate


def observed_result(lane, reason_index=None):
    """Record the controller's existing result without evaluating it again."""
    def decorate(function):
        @wraps(function)
        def run(*args, **kwargs):
            result = function(*args, **kwargs)
            reason = result[reason_index] if reason_index is not None else result
            profiler.gate(lane, None if reason is None else str(reason))
            return result
        return run
    return decorate


@contextmanager
def torch_gpu_span(metric, device):
    """Opt-in benchmark timing only; no production-path CUDA synchronization."""
    if not profiler.enabled or not profiler.detailed_gpu or not str(device).startswith('cuda'):
        yield
        return
    import torch
    with torch.cuda.device(device):
        start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        start.record()
        try:
            yield
        finally:
            end.record()
            end.synchronize()
            profiler.event('GPU_EXECUTION', metric=metric, duration_ms=start.elapsed_time(end),
                           timing='cuda_event', device=str(device))


def record_stale(reason, category=None):
    """Typed event counts, not unique frames. Control cancellation is not inference."""
    category = category or ('tracking_reset_cancellation' if reason == 'TRACKING_RESET' else 'inference')
    if category == 'inference':
        profiler.count('stale_inference_results')
    profiler.count('stale_category_'+category)
    profiler.count('stale_reason_'+reason)
