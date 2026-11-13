"""Dedicated bounded asynchronous zoom evidence; NULL means unobserved.

Counts come from actual RPC dispatches, not plans or controller requests.
Session durations are timestamp differences, never sums of parallel branches.
"""
import csv
from datetime import datetime
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import threading

FIELDS = '''zoom_state previous_zoom_state face_visible face_lost_duration_ms person_track_valid person_ratio face_ratio command_type decision_reason command_id face_observation_id frame_received_timestamp recognition_started_timestamp recognition_finished_timestamp frame_id lock_session_id target_track_id current_zoom_position zoom_position_source current_face_ratio current_person_ratio target_ratio estimated_zoom_position prediction_method calibration_valid prediction_confidence_or_status actual_command_method requested_zoom_position rpc_command_id rpc_latency_ms motor_state actual_zoom_position zoom_error af_state focus_score command_rejection_reason correction_count'''.split()


class ZoomTrace:
    def __init__(self, directory='logs', enabled=False, evidence_source='runtime', queue_size=8192):
        self.enabled = enabled
        self.source = evidence_source
        self.directory = Path(directory)/'zoom'
        self.stamp = datetime.now().astimezone().strftime('%Y%m%d_%H%M%S_%f')
        self.trace_path = self.directory/f'zoom_trace_{self.stamp}.jsonl'
        self.summary_path = self.directory/f'zoom_summary_{self.stamp}.csv'
        self.queue = queue.Queue(maxsize=queue_size)
        self.closed = threading.Event()
        self.dropped = 0
        self.error = None
        self.sessions = {}
        self._correction_commands = set()
        self.thread = None
        if enabled:
            self.thread = threading.Thread(target=self._write, name='zoom-trace-writer', daemon=True)
            self.thread.start()

    def observe(self, event, now, fields):
        if not self.enabled or self.closed.is_set():
            return
        if not (event.startswith(('ZOOM_', 'FOCUS_', 'AF_', 'ABSOLUTE_ZOOM_', 'FACE_', 'ARCFACE_', 'IDENTITY_', 'PERSON_ZOOM_')) or
                event in ('TARGET_LOCKED', 'FIRST_VALID_ZOOM_PLAN', 'FIRST_ZOOM_COMMAND_SENT', 'FIRST_USABLE_FACE', 'IDENTITY_CONFIRMED', 'FALLBACK_TO_PERSON_TRACKING', 'TARGET_CANDIDATE_FOUND', 'REID_CONFIRMED')):
            return
        row = dict.fromkeys(FIELDS)
        row.update(fields)
        row.update(event=event, monotonic_ns=now, evidence_source=self.source,
                   timestamp=datetime.now().astimezone().isoformat(timespec='milliseconds'))
        row['rpc_command_id'] = fields.get('command_id')
        if event == 'ZOOM_RPC_RESPONSE':
            row['rpc_latency_ms'] = fields.get('duration_ms')
        try:
            self.queue.put_nowait(row)
        except queue.Full:
            self.dropped += 1

    def _aggregate(self, row):
        key = row.get('lock_session_id') or 'UNSCOPED'
        s = self.sessions.setdefault(key, dict(lock_session_id=key, evidence_source=self.source,
            number_of_zoom_commands=0, number_of_absolute_commands=0, number_of_pulse_commands=0,
            zoom_request_replanning_count=0, absolute_zoom_cancellation_count=0, cancellation_reasons={},
            observation_age_at_planning_ms=None, observation_age_at_rpc_dispatch_ms=None,
            frame_telemetry_timestamp_difference_ms=None, zoom_target_reached_count=0,
            motor_telemetry_after_ack=None,
            recognition_accepted_count=0, recognition_rejected_count=0, recognition_rejection_reasons={},
            number_of_corrections=0, rpc_errors=0, _rpc_sum=0., _rpc_count=0, _times={},
            motor_movement_duration_ms=None, af_waiting_duration_ms=None,
            zoom_prediction_error=None, zoom_position_error=None, zoom_value_error=None))
        event, now = row['event'], row['monotonic_ns']
        times = s['_times']
        if row.get('locked_ns') is not None:
            times.setdefault('TARGET_LOCKED', row['locked_ns'])
        if event in ('TARGET_LOCKED','FIRST_VALID_ZOOM_PLAN','ZOOM_VISUALLY_STABLE','FIRST_USABLE_FACE','IDENTITY_CONFIRMED',
                     'ZOOM_REQUEST_CREATED','FACE_DETECTED','FACE_QUALITY_PASSED','IDENTITY_OBSERVATION_ACCEPTED'):
            times.setdefault(event, now)
        if event == 'ZOOM_REQUEST_REPLANNED':
            s['zoom_request_replanning_count'] += 1
        if event == 'ABSOLUTE_ZOOM_CANCELLED':
            s['absolute_zoom_cancellation_count'] += 1
            reason = row.get('cancellation_reason', 'UNKNOWN')
            s['cancellation_reasons'][reason] = s['cancellation_reasons'].get(reason, 0)+1
        if event == 'ZOOM_OBSERVATION_AT_PLANNING':
            s['observation_age_at_planning_ms'] = row.get('observation_age_ms')
            s['frame_telemetry_timestamp_difference_ms'] = row.get('frame_telemetry_timestamp_difference_ms')
        if event == 'ZOOM_DISPATCH_VALIDATION':
            s['observation_age_at_rpc_dispatch_ms'] = row.get('observation_age_ms')
        if event == 'ZOOM_TARGET_REACHED':
            s['zoom_target_reached_count'] += 1
        if event == 'ZOOM_STATUS' and row.get('after_ack'):
            s['motor_telemetry_after_ack'] = row.get('actual_zoom_value')
        if event == 'IDENTITY_OBSERVATION_ACCEPTED':
            s['recognition_accepted_count'] += 1
        if event == 'IDENTITY_OBSERVATION_REJECTED':
            s['recognition_rejected_count'] += 1
            reason = row.get('reason', 'UNAVAILABLE')
            reasons = s['recognition_rejection_reasons']
            reasons[reason] = reasons.get(reason, 0)+1
        if event == 'ZOOM_COMMAND_SENT' and row.get('rpc_method') in ('ptz.start', 'HANET.motion'):
            s['number_of_zoom_commands'] += 1
            if row.get('command_id') in self._correction_commands:
                s['number_of_corrections'] += 1
                self._correction_commands.discard(row['command_id'])
            code = row.get('rpc_code')
            s['number_of_absolute_commands'] += code == 'PositionABS'
            s['number_of_pulse_commands'] += code in ('ZoomTele','ZoomWide','kCmdZoomTele','kCmdZoomWide')
            times.setdefault('FIRST_COMMAND', now)
        if event == 'ZOOM_ACTION_ACCEPTED' and row.get('correction'):
            self._correction_commands.add(row.get('command_id'))
        if event == 'ZOOM_RPC_RESPONSE':
            if row.get('duration_ms') is not None:
                s['_rpc_sum'] += row['duration_ms']
                s['_rpc_count'] += 1
            s['rpc_errors'] += row.get('acknowledged') is False
        if event == 'ZOOM_RPC_ERROR':
            s['rpc_errors'] += 1
        for ev, field in [('ZOOM_MOTOR_DURATION','motor_movement_duration_ms'),
                          ('ZOOM_AF_WAIT_ENDED','af_waiting_duration_ms')]:
            if event == ev and row.get('duration_ms') is not None:
                s[field] = (s[field] or 0)+row['duration_ms']
        for source, dest in [('zoom_prediction_error','zoom_prediction_error'),
                             ('zoom_error','zoom_position_error'), ('zoom_value_error','zoom_value_error')]:
            if row.get(source) is not None:
                s[dest] = row[source]

    def _summary(self):
        rows = []
        for session in self.sessions.values():
            row = {k:v for k,v in session.items() if not k.startswith('_')}
            times = session['_times']
            for label, start, end in [
                ('target_lock_to_first_valid_zoom_plan_ms','TARGET_LOCKED','FIRST_VALID_ZOOM_PLAN'),
                ('target_lock_to_zoom_request_ms','TARGET_LOCKED','ZOOM_REQUEST_CREATED'),
                ('target_lock_to_first_face_detected_ms','TARGET_LOCKED','FACE_DETECTED'),
                ('target_lock_to_first_usable_face_ms','TARGET_LOCKED','FACE_QUALITY_PASSED'),
                ('target_lock_to_first_identity_observation_ms','TARGET_LOCKED','IDENTITY_OBSERVATION_ACCEPTED'),
                ('target_lock_to_first_zoom_command_ms','TARGET_LOCKED','FIRST_COMMAND'),
                ('first_zoom_command_to_zoom_stable_ms','FIRST_COMMAND','ZOOM_VISUALLY_STABLE'),
                ('first_zoom_command_to_usable_face_ms','FIRST_COMMAND','FACE_QUALITY_PASSED'),
                ('target_lock_to_identity_confirmed_ms','TARGET_LOCKED','IDENTITY_CONFIRMED')]:
                row[label] = ((times[end]-times[start])/1e6
                    if start in times and end in times and times[end] >= times[start] else None)
            row['cancellation_reasons'] = json.dumps(row['cancellation_reasons'], sort_keys=True)
            row['recognition_rejection_reasons'] = json.dumps(row['recognition_rejection_reasons'], sort_keys=True)
            row['mean_rpc_latency_ms'] = session['_rpc_sum']/session['_rpc_count'] if session['_rpc_count'] else None
            row['dropped_trace_events'] = self.dropped
            rows.append({k:('NULL' if v is None else v) for k,v in row.items()})
        if rows:
            temp = self.summary_path.with_suffix('.tmp')
            with temp.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            temp.replace(self.summary_path)

    def _write(self):
        handler = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(self.trace_path, maxBytes=8_000_000, backupCount=3, encoding='utf-8')
            count = 0
            while not self.closed.is_set() or not self.queue.empty():
                try:
                    row = self.queue.get(timeout=.2)
                except queue.Empty:
                    self._summary()
                    continue
                from telemetry.events import _sanitize
                handler.emit(logging.LogRecord('zoom',20,'',0,json.dumps(_sanitize(row), allow_nan=False),(),None))
                self._aggregate(row)
                count += 1
                if count % 100 == 0:
                    self._summary()
            self._summary()
        except Exception as exc:
            self.error = f'{type(exc).__name__}: {exc}'
        finally:
            if handler:
                handler.close()

    def close(self, timeout=5.):
        self.closed.set()
        if self.thread:
            self.thread.join(timeout)
        return self.thread is None or not self.thread.is_alive()


recorder = ZoomTrace()


def configure(**kwargs):
    global recorder
    recorder.close()
    recorder = ZoomTrace(**kwargs)
    return recorder
