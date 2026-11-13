"""Serialized, expiring motor commands with priority STOP and fault latch."""
import math
import queue
import threading
import time
from concurrent.futures import Future
from uuid import uuid4

class RPCAdapter:
    def __init__(self, camera, cfg):
        from camera.dahua_rpc import ZoomRPC
        self.rpc = ZoomRPC(host=camera['ptz_ip'], port=camera.get('ptz_port',80),
                           user=camera.get('ptz_username', camera.get('username','')),
                           password=camera.get('ptz_password',camera.get('password','')),
                           channel=camera.get('ptz_channel',0), timeout=cfg['command_timeout_s'])
        # No configManager writes: focus/AF tracking and zoom-speed config remain untouched.
        self.speed = cfg['speed']
    def start(self, direction):
        return self.rpc.start(1 if direction=='zoom_in' else -1, self.speed, 'zoom')
    def stop(self):
        return self.rpc.stop('zoom')
    def telemetry(self):
        s = (self.rpc.status_raw(background=True).get('params') or {}).get('status') or {}
        def number(v):
            return float(v) if isinstance(v,(int,float)) and math.isfinite(v) else None
        return dict(zoom_position=number(s.get('PTZZoomHD')),
                    zoom_position_source='ptz.getStatus.PTZZoomHD' if number(s.get('PTZZoomHD')) is not None else None,
                    zoom_value=number(s.get('ZoomValue')),
                    zoom_value_source='ptz.getStatus.ZoomValue (raw)' if number(s.get('ZoomValue')) is not None else None,
                    timestamp=time.monotonic())
    def close(self):
        self.rpc.close(stop_motors=False)

class Motor:
    def __init__(self, adapter_factory, cfg, session):
        self.cfg, self.session = cfg, session
        self.factory = adapter_factory
        self.adapter = None
        self.queue = queue.Queue(maxsize=8)
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.fault = True
        self.current = 'STOP'
        self.command_id = None
        self.telemetry_snapshot = None
        self.generation = 0
        self.lock = threading.RLock()
        self.ready = threading.Event()
        self.deadline = None
        self.stop_retry_at = None
        self.thread = threading.Thread(target=self._run, name='zoom-control', daemon=True)
        self.thread.start()
        self.poller = threading.Thread(target=self._poll, name='zoom-telemetry', daemon=True)
        self.poller.start()
        self.watchdog = threading.Thread(target=self._watch, name='motor-watchdog', daemon=True)
        self.watchdog.start()

    def telemetry(self):
        s = self.telemetry_snapshot
        return dict(s) if s and time.monotonic()-s['timestamp']<1.5 else {}

    def pulse(self, direction, ms, trial_id=None, guard=None):
        f = Future()
        if direction not in ('zoom_in','zoom_out') or not isinstance(ms, (int, float)) or isinstance(ms, bool) or not math.isfinite(ms) or not 1 <= ms <= self.cfg['max_pulse_ms']:
            f.set_exception(ValueError('Invalid direction or pulse duration'))
            return f
        with self.lock:
            if self.fault or self.closed.is_set() or self.stop_event.is_set():
                f.set_exception(RuntimeError('Motor unavailable; require acknowledged STOP'))
                return f
            try:
                self.queue.put_nowait((self.generation, time.monotonic()+self.cfg['max_frame_age_s'],
                                       direction, ms, trial_id, guard, f))
            except queue.Full:
                f.set_exception(RuntimeError('Command queue full'))
        self.wake.set()
        return f

    def stop(self, reason='USER_STOP'):
        with self.lock:
            self.generation += 1
            self.stop_event.set()
            self.session.event('STOP_REQUEST', reason)
        self.wake.set()

    def _ack_stop(self, trial=None):
        row = dict(command_id=uuid4().hex, trial_id=trial, command_type='STOP',
                   send_timestamp=time.monotonic(), send_wall_timestamp=time.time(), timing_source='software RPC ACK; motor timestamps unknown')
        try:
            self.adapter.stop()
            row.update(response_timestamp=time.monotonic(),response_wall_timestamp=time.time(),status='ACKNOWLEDGED')
            self.fault = False
            self.current = 'STOP'
            self.stop_retry_at = None
        except Exception as exc:
            self.fault = True
            self.current = 'STOP_UNCONFIRMED'
            self.stop_retry_at = time.monotonic()+.5
            row.update(response_timestamp=time.monotonic(),response_wall_timestamp=time.time(),status='FAILED',error_message=type(exc).__name__)
        self.session.log('commands',row)
        return row

    def _run(self):
        try:
            self.adapter = self.factory()
            # Initial STOP is required to establish idle motor state; only after explicit Connect.
            self._ack_stop()
        except Exception as exc:
            self.session.event('CAMERA_CONNECT_ERROR', type(exc).__name__)
        finally:
            self.ready.set()
        while not self.closed.is_set():
            if self.stop_retry_at and time.monotonic()>=self.stop_retry_at and self.adapter:
                self._ack_stop()
            if self.stop_event.is_set():
                with self.lock:
                    self.stop_event.clear()
                if self.adapter:
                    self._ack_stop()
            try:
                item = self.queue.get(timeout=.05)
            except queue.Empty:
                continue
            gen, expires, direction, ms, trial, guard, future = item
            row = dict(command_id=uuid4().hex, trial_id=trial, command_type=direction,
                       requested_duration_ms=ms, timing_source='software send-to-STOP; motor timestamps unknown',
                       zoom_position_before=self.telemetry().get('zoom_position'))
            started = False
            try:
                if gen != self.generation or time.monotonic()>expires or self.fault:
                    raise RuntimeError('CANCELLED_EXPIRED_OR_FAULT')
                if guard and not guard():
                    raise RuntimeError('MEASUREMENT_GUARD_REJECTED')
                row['send_timestamp'] = time.monotonic()
                row['send_wall_timestamp'] = time.time()
                self.command_id = row['command_id']
                self.current = direction
                self.deadline = row['send_timestamp']+ms/1000
                started = True  # Even a lost ACK may have started the motor.
                if hasattr(self.adapter, 'rpc'):
                    self.adapter.rpc.zoom_intent_valid = lambda fields: (gen==self.generation and not self.stop_event.is_set() and (guard is None or guard()))
                self.adapter.start(direction)
                row['response_timestamp'] = time.monotonic()
                row['response_wall_timestamp'] = time.time()
                if row['response_timestamp']-row['send_timestamp']>self.cfg['command_timeout_s']:
                    raise TimeoutError('START_TIMEOUT')
                remaining = max(0, self.deadline-time.monotonic())
                interrupted = self.stop_event.wait(remaining)
                row['status'] = 'INTERRUPTED' if interrupted or gen != self.generation else 'ACKNOWLEDGED'
            except Exception as exc:
                if started and 'response_timestamp' not in row:
                    row['response_timestamp'] = time.monotonic()
                row['response_wall_timestamp'] = time.time()
                row.update(status='FAILED',error_message=str(exc) if isinstance(exc,(RuntimeError,TimeoutError)) else type(exc).__name__)
                self.fault = True
            finally:
                if started:
                    row['stop_timestamp'] = time.monotonic()
                    row['stop_wall_timestamp'] = time.time()
                    stop = self._ack_stop(trial)
                    row['stop_response_timestamp'] = stop.get('response_timestamp')
                    row['stop_response_wall_timestamp'] = stop.get('response_wall_timestamp')
                    row['actual_duration_ms'] = (row['stop_timestamp']-row['send_timestamp'])*1000
                    if stop['status'] != 'ACKNOWLEDGED':
                        row.update(status='FAILED',error_message='STOP_UNCONFIRMED')
                    # Start failure remains latched even if fail-safe STOP succeeds.
                    if row['status']=='FAILED':
                        self.fault = True
                self.deadline = None
                row['zoom_position_after'] = self.telemetry().get('zoom_position')
                self.session.log('commands',row)
                self.command_id = None
                if not future.done():
                    future.set_result(row)
        if self.adapter:
            for _ in range(3):
                if self._ack_stop()['status']=='ACKNOWLEDGED':
                    break
            self.adapter.close()

    def _poll(self):
        self.ready.wait()
        while not self.closed.is_set():
            if self.adapter:
                try:
                    self.telemetry_snapshot = self.adapter.telemetry()
                except Exception as exc:
                    self.telemetry_snapshot = None
                    self.session.event('TELEMETRY_ERROR',type(exc).__name__)
            self.closed.wait(.2)

    def _watch(self):
        while not self.closed.wait(.05):
            # Separate watchdog does not send competing RPCs; wakes the serial owner.
            d = self.deadline
            if d and time.monotonic()>d+self.cfg['command_timeout_s']:
                self.stop('MOTOR_DEADLINE_EXCEEDED')
                self.fault = True

    def close(self):
        self.stop('SHUTDOWN')
        self.closed.set()
        self.thread.join(2*self.cfg['command_timeout_s']+3)
        self.poller.join(self.cfg['command_timeout_s']+1)
        self.watchdog.join(1)
        while True:
            try:
                *_, f = self.queue.get_nowait()
                if not f.done():
                    f.set_exception(RuntimeError('SHUTDOWN'))
            except queue.Empty:
                break
        if self.thread.is_alive():
            self.session.event('SHUTDOWN_ERROR','Motor worker still awaiting network response')
