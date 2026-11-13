"""HANET adapter using the application's serialized motor execution lane.

Lens coordinates and CNN focus calibrated for Dahua are deliberately unavailable.
"""
import threading
from functools import partial
from concurrent.futures import TimeoutError as FutureTimeoutError
import time
import hashlib
import json
import os
from pathlib import Path

from .hanet_rpc import HanetPTZClient
from .ptz_controller import PTZController, PTZ_STOP
from .zoom_capability import ZoomCapability
from runtime.camera_worker import camera_operation
from runtime.reconnect import ReconnectBackoff
from runtime.stop_retry import StopRetryBudget
from telemetry import latency


class HanetController(PTZController):
    has_zoom_telemetry = False
    supports_focus = False
    supports_autofocus = False
    supports_pan_tilt = True
    max_speed = 8
    CODES = {'zoom_in': 'kCmdZoomTele', 'zoom_out': 'kCmdZoomWide',
             'pan_left': 'kCmdLeft', 'pan_right': 'kCmdRight',
             'tilt_up': 'kCmdUp', 'tilt_down': 'kCmdDown'}

    def __init__(self, *args, autoconnect=True, rpc_factory=None, safety_state_path=None,
                 ack_profile='camera_py', **kwargs):
        # The deployed HANET camera is operator-confirmed to work with camera.py.
        # Preserve strict/custom clients for explicit injection and diagnostics.
        if rpc_factory is None:
            rpc_factory = partial(HanetPTZClient, ack_profile=ack_profile)
        super().__init__(*args, autoconnect=False, rpc_factory=rpc_factory, **kwargs)
        self.command_wait_timeout = self.timeout*3+2
        self._active_codes = set()
        self.motor_state = 'READY'
        self._stop_budget = StopRetryBudget()
        self._close_lock = threading.Lock()
        self.shutdown_unconfirmed = False
        self.cleanup_failed = False
        self._shutdown_deadline = None
        self._shutdown_timer = None
        identity = hashlib.sha256(f'{self.ip}:{self.port}'.encode()).hexdigest()[:20]
        self._safety_state_path = Path(safety_state_path) if safety_state_path else Path(__file__).resolve().parents[1]/'logs/camera/motor_state'/(identity+'.json')
        self._stop_guard_path = self._safety_state_path.with_suffix('.pending')
        load_reason = 'UNCONFIRMED_PREVIOUS_RUN'
        try:
            for path in (self._safety_state_path, self._stop_guard_path):
                if path.exists():
                    self._active_codes.update(self._load_motor_checkpoint(path))
                    if path == self._stop_guard_path:
                        load_reason = 'INCOMPLETE_STOP_CHECKPOINT'
        except Exception:
            # An unreadable checkpoint is uncertainty, not a clean restart.
            self._active_codes = set(self.CODES.values())
            load_reason = 'INVALID_MOTOR_CHECKPOINT'
        if self._active_codes:
            self._motor_fault = True
            self.motor_state = 'FAULT_LATCHED'
            self._fault_event('MOTOR_FAULT_LATCHED', reason=load_reason)
        self._absolute_capability = ZoomCapability(False, 'HANET_POSITION_UNVERIFIED')
        if autoconnect:
            self._poller = threading.Thread(target=self._poll, name='hanet-heartbeat', daemon=True)
            self._poller.start()

    @staticmethod
    def _clamp_speed(speed):
        return max(1, min(8, int(speed)))

    def _connect(self, allow_shutdown=False):
        # Called only from the command lane; HTTP and WebSocket never overlap.
        if self._closed.is_set() and not allow_shutdown:
            raise RuntimeError('Camera controller is closed')
        if self._shutdown_deadline is not None and time.monotonic() >= self._shutdown_deadline:
            raise TimeoutError('Camera shutdown deadline reached')
        if self._rpc is None:
            timeout = self.timeout if self._shutdown_deadline is None else min(self.timeout, self._shutdown_deadline-time.monotonic())
            self._rpc = self._rpc_factory(self.ip, self.port, self.username,
                                          self.password, timeout=max(.001, timeout))
        return self._rpc

    def _poll(self):
        backoff = ReconnectBackoff()
        while not self._closed.is_set():
            try:
                self.read_status()
                backoff.reset()
                delay = 1.
            except Exception as exc:
                self.last_error = f'HANET unavailable ({type(exc).__name__})'
                delay = backoff.next_delay()
            if self._closed.wait(delay):
                return

    @camera_operation
    def read_status(self):
        try:
            self._connect().heartbeat()
        except Exception:
            # Never automatically reconnect/replay motion after a lost START/STOP.
            if not self._active_codes and self._rpc is not None:
                self._rpc.close()
                self._rpc = None
            raise
        self.last_error = None
        return {'connected': True, 'zoom': None, 'focus': None,
                'timestamp': time.monotonic(), 'telemetry_available': False}

    def telemetry(self, max_age=1.):
        return None

    def probe_zoom_capability(self):
        return self._absolute_capability

    @camera_operation
    def command(self, ptz_cmd, speed=None):
        if ptz_cmd == PTZ_STOP:
            return self.stop()
        if self._closed.is_set():
            return False, 'Camera controller is shutting down'
        if ptz_cmd not in self.CODES:
            return False, 'Command unsupported by verified HANET adapter'
        if self._motor_fault:
            return False, 'Motor blocked: retry STOP'
        if self._active_codes:
            ok, message = self.stop()
            if not ok:
                return ok, message
        try:
            rpc = self._connect()
            if self._closed.is_set():
                return False, 'Camera controller is shutting down'
            # Frigate checks movement freshness after dequeue; do the same at I/O.
            fields = latency.context.get()
            if fields.get('lock_session_id') is not None:
                if fields.get('motor_owner') == 'pan_tilt':
                    valid = (ptz_cmd in ('pan_left', 'pan_right', 'tilt_up', 'tilt_down')
                             and fields.get('pan_tilt_command') == ptz_cmd
                             and getattr(self, 'pan_tilt_intent_valid', lambda _: False)(fields))
                else:
                    valid = (self.session_valid(fields['lock_session_id'])
                             and getattr(self, 'zoom_intent_valid', lambda _: True)(fields)
                             and getattr(self, 'zoom_policy_valid', lambda _: True)(fields))
                if not valid:
                    return False, 'Stale camera movement rejected'
            code = self.CODES[ptz_cmd]
            self.motor_state = 'COMMAND_IN_FLIGHT'
            self._active_codes.add(code)  # lost ACK may still mean physical motion
            self._save_motor_state()  # persist uncertainty BEFORE transmission
            speed_value = self._clamp_speed(self.speed if speed is None else speed)
            is_zoom = ptz_cmd in ('zoom_in', 'zoom_out')
            sent = time.perf_counter_ns()
            if is_zoom:
                latency.profiler.event('ZOOM_COMMAND_SENT', backend='hanet',
                    rpc_method='HANET.motion', rpc_code=code, command_kind='pulse',
                    zoom_direction=1 if ptz_cmd == 'zoom_in' else -1)
            elif fields.get('motor_owner') == 'pan_tilt':
                latency.profiler.event('PAN_TILT_WIRE_DISPATCH', backend='hanet',
                    command_direction=ptz_cmd, command_speed=speed_value,
                    observation_age_ms=(time.monotonic()-fields['observation_time'])*1000
                        if 'observation_time' in fields else None)
            try:
                if self._closed.is_set():
                    raise RuntimeError('START cancelled by shutdown')
                if rpc.motion(code, True, speed_value) is not True:
                    raise RuntimeError('HANET START has no explicit ACK')
                self.motor_state = 'MOTOR_ACTIVE'
            except Exception as exc:
                latency.profiler.event('ZOOM_RPC_ERROR' if is_zoom else 'PAN_TILT_WIRE_ERROR',
                    backend='hanet', exception=type(exc).__name__)
                raise
            latency.profiler.elapsed('ZOOM_RPC_RESPONSE' if is_zoom else 'PAN_TILT_WIRE_RESPONSE',
                sent, 'zoom_rpc_latency_ms' if is_zoom else 'pan_tilt_rpc_latency_ms',
                backend='hanet', acknowledged=True)
            return True, 'HANET acknowledged; physical motion unverified'
        except Exception as exc:
            self._motor_fault = bool(self._active_codes)
            if self._motor_fault:
                self.motor_state = 'STOP_UNCONFIRMED'
                self._fault_event('MOTOR_FAULT_LATCHED', exception_type=type(exc).__name__)
                self.stop()
            return self._failure('HANET command', exc)

    def _save_motor_state(self):
        self._write_motor_checkpoint(self._safety_state_path, self._active_codes)

    def _load_motor_checkpoint(self, path):
        with path.open() as stream:
            text = stream.read(8193)
        if len(text) > 8192:
            raise ValueError('Motor checkpoint exceeds limit')
        saved = json.loads(text)
        if (not isinstance(saved, dict) or type(saved.get('version')) is not int
                or saved['version'] != 1):
            raise ValueError('Unsupported motor checkpoint version')
        codes = saved.get('active_codes')
        if (not isinstance(codes, list) or not all(isinstance(code, str) for code in codes)
                or not set(codes) <= set(self.CODES.values())):
            raise ValueError('Invalid motor state checkpoint')
        return set(codes)

    @staticmethod
    def _write_motor_checkpoint(path, codes):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        with temp.open('w') as stream:
            json.dump({'version': 1, 'active_codes': sorted(codes)}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def _discard_stop_guard(self):
        # Only called AFTER the main checkpoint's file and directory fsync have
        # succeeded. A crash before this cleanup causes a conservative re-STOP.
        try:
            self._stop_guard_path.unlink(missing_ok=True)
            directory = os.open(self._stop_guard_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            # Do not grant READY while a guard can reintroduce the old fault
            # on restart. The caller retains the code and ownership for retry.
            self._fault_event('MOTOR_CHECKPOINT_CLEANUP_PENDING',
                              exception_type=type(exc).__name__)
            raise

    def _fault_event(self, event, **fields):
        generation = getattr(self._rpc, 'generation', None)
        latency.profiler.event(event, backend='hanet', fault_state=self.motor_state,
            motor_owner=latency.context.get().get('motor_owner'),
            connection_generation=generation if isinstance(generation, int) else None,
            retry_attempt=self._stop_budget.attempts, **fields)

    @camera_operation
    def stop(self, emergency=False):
        self.cancel_focus()
        if getattr(self, '_resources_closed', False):
            return not self.shutdown_unconfirmed, 'Camera resources closed; no new STOP sent'
        if self._active_codes and not emergency and not self._stop_budget.ready():
            return False, 'HANET STOP unconfirmed; retry budget/backoff active'
        failed = False
        previous_fault = self._motor_fault
        self.motor_state = 'STOP_PENDING'
        if previous_fault:
            self._fault_event('MOTOR_RECOVERY_STARTED', operation='STOP', emergency=emergency)
        for code in tuple(self._active_codes):
            ack_received = False
            try:
                # After failed START/connect, _rpc may be absent. Establishing
                # transport here is allowed ONLY to STOP the remembered direction.
                rpc = self._rpc or self._connect(allow_shutdown=True)
                if rpc.motion(code, False, 1) is not True:
                    raise RuntimeError('HANET STOP has no explicit ACK')
                ack_received = True
                # Keep a durable copy of uncertainty until the replacement main
                # checkpoint has also been directory-fsynced. If replace works
                # but fsync fails, restart must not mistake an empty file for READY.
                self._write_motor_checkpoint(self._stop_guard_path, self._active_codes)
                self._active_codes.remove(code)
                try:
                    self._save_motor_state()
                    self._discard_stop_guard()
                except Exception:
                    self._active_codes.add(code)
                    raise
            except Exception as exc:
                failed = True
                self._fault_event('MOTOR_CHECKPOINT_COMMIT_FAILED' if ack_received else 'PAN_TILT_STOP_UNCONFIRMED',
                    rpc_code=code, operation='STOP', acknowledged=ack_received,
                    rpc_stop_accepted=ack_received, physical_motor_stopped=None,
                    exception_type=type(exc).__name__,
                    exception_message='STOP checkpoint commit failed' if ack_received else 'STOP acknowledgement unavailable')
        self._motor_fault = failed or bool(self._active_codes)
        if self._motor_fault:
            self._stop_budget.failed()
            self.motor_state = 'FAULT_LATCHED' if self._stop_budget.exhausted else 'STOP_UNCONFIRMED'
            if not previous_fault or self._stop_budget.attempts == self._stop_budget.limit:
                self._fault_event('MOTOR_FAULT_LATCHED')
            if previous_fault:
                self._fault_event('MOTOR_RECOVERY_FAILED', operation='STOP',
                                  reason='STOP_OR_CHECKPOINT_UNCONFIRMED')
            return False, 'HANET STOP unconfirmed'
        self.motor_state = 'READY'
        self._stop_budget.reset()
        if previous_fault:
            self._fault_event('MOTOR_FAULT_RECOVERED', evidence='STOP_RPC_ACK', physical_motor_stopped=None)
        return True, 'HANET STOP acknowledged; physical motion unverified'

    def emergency_stop(self):
        # One operator-requested attempt even after automatic retry exhaustion.
        return self.stop(emergency=True)

    def begin_shutdown(self):
        self._closed.set()  # invalidates every queued/new START at the I/O boundary
        self.cancel_focus()

    def arm_shutdown_deadline(self, deadline):
        # One watchdog interrupts socket I/O even while a supervisor is joining.
        # Resource destruction still runs exclusively on the command lane.
        self._shutdown_deadline = deadline
        if self._shutdown_timer is None:
            def abort_io():
                self._abort_transport()
                self._fault_event('SHUTDOWN_STOP_TIMEOUT', shutdown_phase='transport_deadline')
            self._shutdown_timer = threading.Timer(max(0., deadline-time.monotonic()), abort_io)
            self._shutdown_timer.daemon = True
            self._shutdown_timer.start()

    def _abort_transport(self):
        rpc = self._rpc
        if rpc is not None and hasattr(rpc, 'abort'):
            try:
                rpc.abort()
            except Exception as exc:
                self.cleanup_failed = True
                self._fault_event('HANET_CLEANUP_ERROR', shutdown_phase='abort_io',
                    exception_type=type(exc).__name__, exception_message='Transport abort failed')

    def autofocus(self, **kwargs):
        return False, 'HANET autofocus protocol is unverified'

    def move_focus(self, *args, **kwargs):
        return False, 'HANET focus position is unavailable'

    def move_zoom(self, *args, **kwargs):
        return False, 'HANET absolute zoom is unavailable; use directional zoom'

    start_zoom_target = move_zoom
    move_to_zoom_position = move_zoom

    def close(self, timeout=None):
        timeout = self.timeout*3+2 if timeout is None else max(0., timeout)
        deadline = time.monotonic()+timeout
        if not self._close_lock.acquire(timeout=timeout):
            return False
        try:
            if getattr(self, '_resources_closed', False):
                return not self._command_worker.thread.is_alive() and not self.cleanup_failed
            self.begin_shutdown()
            self._fault_event('SHUTDOWN_REQUESTED', shutdown_phase='camera')
            if self._poller is not None:
                self._poller.join(max(0., deadline-time.monotonic()))

            def finish():
                try:
                    ok, _ = self.stop(emergency=True)
                    self.shutdown_unconfirmed = not ok
                except Exception as exc:
                    self.shutdown_unconfirmed = True
                    self._fault_event('HANET_CLEANUP_ERROR', exception_type=type(exc).__name__,
                                      exception_message='Final STOP failed', shutdown_phase='final_stop')
                finally:
                    if self.shutdown_unconfirmed:
                        self._fault_event('SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR',
                            shutdown_phase='rpc_close', physical_motor_stopped=None)
                    try:
                        if self._rpc is not None:
                            self.cleanup_failed = (self._rpc.close() is False) or self.cleanup_failed
                    except Exception as exc:
                        self.cleanup_failed = True
                        self._fault_event('HANET_CLEANUP_ERROR', exception_type=type(exc).__name__,
                                          exception_message='RPC cleanup failed', shutdown_phase='rpc_close')
                    finally:
                        self._rpc = None
                        self.motor_state = 'CLOSED'
                        self._resources_closed = True
                        if self._shutdown_timer is not None:
                            self._shutdown_timer.cancel()
                            self._shutdown_timer.join(timeout=.1)
                        self._command_worker.close(timeout=0., cancel_pending=True)
            try:
                self._command_worker.call(finish, _priority=True, _cancel_on_timeout=False,
                    _wait_timeout=max(0., deadline-time.monotonic()))
            except FutureTimeoutError:
                self.shutdown_unconfirmed = bool(self._active_codes)
                self._fault_event('SHUTDOWN_STOP_TIMEOUT', shutdown_phase='camera_lane')
                # Interrupt only I/O, leaving cleanup ordered on the camera lane.
                self._abort_transport()
                return False
            exited = self._command_worker.close(max(0., deadline-time.monotonic()))
            return exited and not self.cleanup_failed
        finally:
            self._close_lock.release()
