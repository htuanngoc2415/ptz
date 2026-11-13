"""Application adapter for the Dahua 37x RPC2 zoom/focus controller.

Command constants are internal UI identifiers, never CGI command numbers.
Network operations run in workers; construction and cached telemetry are nonblocking.
"""
from __future__ import annotations
import copy
import math
from pathlib import Path
import threading
import time

from .dahua_rpc import ZoomRPC, RPCError
from .zoom_capability import ZoomCapability
from .zoom_prediction import PositionABSMapping
from telemetry import latency
from runtime.camera_worker import CameraCommandWorker, camera_operation

PTZ_STOP = "stop"
PTZ_ZOOM_IN = "zoom_in"
PTZ_ZOOM_OUT = "zoom_out"
PTZ_FOCUS_PLUS = "focus_near"
PTZ_FOCUS_MINUS = "focus_far"
PTZ_AUTO_FOCUS = "cnn_focus"


class PTZController:
    has_zoom_telemetry = True
    supports_parallel_focus = False

    def __init__(self, ip, username, password, speed=100, channel=0, timeout=5.,
                 port=80, frame_provider=None, focus_model=None, rpc_factory=ZoomRPC,
                 autoconnect=True):
        self.ip, self.username, self.password = ip, username, password
        self.channel, self.timeout, self.port = channel, timeout, port
        self.speed = self._clamp_speed(speed)
        self.frame_provider = frame_provider
        self.focus_model = Path(focus_model or Path(__file__).resolve().parents[1] / 'models/focus/mang_delta.pt')
        self.session_valid = lambda session: True
        self.expected_camera_id = None
        self._verified_camera_id = None
        self._absolute_capability = ZoomCapability(None, 'NOT_PROBED')
        self._capability_checked = False
        self._position_mapping = PositionABSMapping()
        self._rpc_factory = rpc_factory
        self._command_worker = CameraCommandWorker()
        self._rpc = None
        self._connect_lock = threading.RLock()
        self.command_lock = threading.RLock()
        self._telemetry_lock = threading.Lock()
        self._snapshot = None
        self._closed = threading.Event()
        self._focus_cancel = threading.Event()
        self._focus_lock = threading.Lock()
        self._network = None
        self._zoom_config = None
        self._zoom_speed = None
        self._motor_fault = False
        self.last_error = None
        self._poller = None
        self.parallel_focus_enabled = lambda: False
        self._parallel_cancel = threading.Event()
        self._parallel_thread = None
        if autoconnect:
            self._poller = threading.Thread(target=self._poll, name='dahua-status', daemon=True)
            self._poller.start()

    @property
    def supports_absolute_zoom(self):
        return self._absolute_capability.supported is True

    def probe_zoom_capability(self):
        try:
            capability = ZoomCapability.from_response(self._connect().protocol_caps())
        except RPCError as exc:
            capability = ZoomCapability(None, 'CAPABILITY_RPC_ERROR', exc.response)
        except Exception as exc:
            capability = ZoomCapability(None, 'CAPABILITY_UNAVAILABLE', type(exc).__name__)
        self._absolute_capability = capability
        self._capability_checked = True
        latency.profiler.event('ZOOM_CAPABILITY', supported=capability.supported,
                               capability_source=capability.source, error=capability.error,
                               command_mapping_source=self._position_mapping.source)
        return capability

    @staticmethod
    def _clamp_speed(speed):
        return max(1, min(100, int(speed)))

    def set_speed(self, speed):
        # UI calls this: apply the value on the next zoom command, off the Tk thread.
        self.speed = self._clamp_speed(speed)

    def _connect(self):
        with self._connect_lock:
            if self._closed.is_set():
                raise RuntimeError('Camera controller is closed')
            if self._rpc is None:
                self._rpc = self._rpc_factory(host=self.ip, port=self.port, user=self.username,
                                              password=self.password, channel=self.channel,
                                              timeout=self.timeout)
                self._rpc.session_valid = lambda session: self.session_valid(session)
                self._rpc.zoom_intent_valid = lambda fields: getattr(self, 'zoom_intent_valid', lambda fields: True)(fields)
                self._rpc.zoom_policy_valid = lambda fields: getattr(self, 'zoom_policy_valid', lambda fields: True)(fields)
            return self._rpc

    def _poll(self):
        while not self._closed.is_set():
            try:
                self.read_status()
                delay = .15
            except Exception as exc:
                # No optimistic telemetry and no automatic replay of motor commands.
                with self._telemetry_lock:
                    self._snapshot = None
                self.last_error = f'Camera status unavailable ({type(exc).__name__})'
                delay = 1.
            self._closed.wait(delay)

    def read_status(self):
        rpc = self._connect()
        if self.expected_camera_id and self._verified_camera_id != self.expected_camera_id:
            info = rpc._call('magicBox.getSystemInfo', {})
            actual_id = (info.get('params') or {}).get('serialNumber')
            if actual_id != self.expected_camera_id:
                latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason='CALIBRATION_CAMERA_MISMATCH')
                raise RuntimeError('Calibration does not match connected camera')
            self._verified_camera_id = actual_id
        r = rpc.status_raw(background=True)
        s = (r.get('params') or {}).get('status') or {}
        # Do not reinterpret an unrelated raw Zoom field as magnification.
        z = s.get('ZoomValue')
        hd = s.get('PTZFocusHD')
        if z is None or not math.isfinite(float(z)) or not 10 <= float(z) <= 370:
            raise RuntimeError('Camera did not return a valid ZoomValue')
        if hd is not None and (not math.isfinite(float(hd)) or not 0 <= float(hd) <= 32768):
            raise RuntimeError('Invalid PTZFocusHD')
        def number(value):
            return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None
        position = s.get('Postion', s.get('Position'))
        encoder_normalized = number(position[2]) if isinstance(position, list) and len(position) >= 3 else None
        snapshot = {'zoom': float(z)/10., 'focus': None if hd is None else float(hd),
                    'camera_id': self._verified_camera_id,
                    'zoom_value_raw': float(z), 'zoom_position_raw': number(s.get('PTZZoomHD')),
                    'zoom_encoder_normalized': encoder_normalized,
                    'zoom_position_source': 'ptz.getStatus.PTZZoomHD',
                    'zoom_value_source': 'ptz.getStatus.ZoomValue/10',
                    'zoom_motion_state': s.get('ZoomStatus'), 'focus_motion_state': s.get('FocusStatus'),
                    'timestamp': time.monotonic()}
        if not self._capability_checked:
            self.probe_zoom_capability()
        with self._telemetry_lock:
            self._snapshot = snapshot
        self.last_error = None
        return dict(snapshot)

    def telemetry(self, max_age=1.):
        with self._telemetry_lock:
            s = self._snapshot
            if s is None or time.monotonic()-s['timestamp'] > max_age:
                return None
            return dict(s)

    def _set_zoom_speed(self, rpc, speed):
        speed = self._clamp_speed(self.speed if speed is None else speed)
        if self._zoom_speed == speed:
            return
        table = rpc._call('configManager.getConfig', {'name': 'VideoInZoom'})['params']['table']
        if self._zoom_config is None:
            self._zoom_config = copy.deepcopy(table)
        node = rpc._profile_dau(table)
        if node is None or 'Speed' not in node:
            raise RuntimeError('VideoInZoom.Speed unavailable')
        node['Speed'] = speed
        rpc._call('configManager.setTemporaryConfig',
                  {'name': 'VideoInZoom', 'table': table, 'options': []})
        self._zoom_speed = speed

    @staticmethod
    def _failure(operation, exc):
        # requests errors may contain URLs: do not echo credentials or full replies.
        return False, f'{operation} failed ({type(exc).__name__})'

    @camera_operation
    def command(self, ptz_cmd, speed=None):
        if ptz_cmd == PTZ_STOP:
            return self.stop()
        if self._motor_fault:
            return False, 'Motor blocked: retry STOP before another movement'
        if ptz_cmd == PTZ_AUTO_FOCUS:
            return self.autofocus()
        axes = {PTZ_ZOOM_IN: ('zoom', 1), PTZ_ZOOM_OUT: ('zoom', -1),
                PTZ_FOCUS_PLUS: ('focus', 1), PTZ_FOCUS_MINUS: ('focus', -1)}
        if ptz_cmd not in axes:
            return False, 'Unknown RPC2 camera command'
        try:
            with self.command_lock:
                rpc = self._connect()
                axis, direction = axes[ptz_cmd]
                if axis == 'zoom':
                    self._set_zoom_speed(rpc, speed)
                else:
                    ok, message = rpc.focus_manual(True)
                    if not ok:
                        raise RuntimeError(message)
                rpc.start(direction, 8, axis)
            return True, 'RPC2 acknowledged'
        except Exception as exc:
            self.stop()  # A lost ACK does not mean the motor stayed still.
            return self._failure('Motor command', exc)

    def cancel_focus(self):
        self._focus_cancel.set()
        self._parallel_cancel.set()

    def _start_parallel_focus(self):
        """A subordinate focus worker shares the zoom ownership lease."""
        if not self.parallel_focus_enabled():
            return
        if self._parallel_thread is not None and self._parallel_thread.is_alive():
            return
        cancel = threading.Event()
        self._parallel_cancel = cancel

        def follow():
            while not cancel.is_set() and not self._closed.is_set():
                if not self.parallel_focus_enabled() or self._motor_fault:
                    break
                # Recompute from a fresh frame after each short movement.
                # Never queue predictions behind a long focus journey.
                ok, message = self.autofocus(cancel=cancel, movement_timeout=.35)
                if not ok and not message.startswith('Focus HD='):
                    self.last_error = message
                    break
                if cancel.wait(.05):
                    break

        self._parallel_thread = threading.Thread(target=follow, name='zoom-cnn-focus', daemon=True)
        self._parallel_thread.start()

    @camera_operation
    def stop(self):
        self.cancel_focus()
        try:
            worker = self._parallel_thread
            if worker is not None and worker is not threading.current_thread():
                # Brake zoom immediately, without waiting for CNN inference.
                if self._rpc is not None:
                    self._rpc.stop('zoom')
                worker.join(timeout=self.timeout+1.)
                if worker.is_alive():
                    # Keep ownership blocked until no late focus START is possible.
                    self._motor_fault = True
                    if self._rpc is not None:
                        self._rpc.stop_all()
                    return False, 'CNN focus worker has not stopped yet'
            with self.command_lock, self._connect_lock:
                if self._rpc is not None:
                    self._rpc.stop_all()
            self._motor_fault = False
            latency.profiler.event('ZOOM_STOP_ACK', motor_stopped=None,
                                   evidence='rpc_ack_only', stop_code_convention='ZoomTele_after_PositionABS')
            return True, 'STOP acknowledged; physical motor state unverified'
        except Exception as exc:
            self._motor_fault = True
            return self._failure('STOP', exc)

    def zoom_in(self, speed=None): return self.command(PTZ_ZOOM_IN, speed)
    def zoom_out(self, speed=None): return self.command(PTZ_ZOOM_OUT, speed)
    def focus_plus(self, speed=None): return self.command(PTZ_FOCUS_PLUS, speed)
    def focus_minus(self, speed=None): return self.command(PTZ_FOCUS_MINUS, speed)

    @camera_operation
    def move_to_zoom_position(self, target_position, speed=None):
        """One zoom-only PositionABS command, in the reference command coordinate.

        Does not touch pan/tilt or substitute pulses on rejection. Completion is
        monitored separately against telemetry, never inferred from an ACK.
        """
        if self._motor_fault:
            return False, 'Motor blocked: retry STOP'
        try:
            self._position_mapping.command_to_zoom(target_position)  # strict verified range
        except ValueError as exc:
            return False, str(exc)
        if not self._capability_checked:
            self.probe_zoom_capability()
        if not self.supports_absolute_zoom:
            latency.profiler.event('ABSOLUTE_ZOOM_UNSUPPORTED', supported=self._absolute_capability.supported,
                                   reason=self._absolute_capability.source)
            return False, 'ABSOLUTE_ZOOM_UNSUPPORTED_OR_UNVERIFIED'
        try:
            with self.command_lock:
                rpc = self._connect()
                self._set_zoom_speed(rpc, speed)
                rpc.goto(target_position)
            return True, 'PositionABS acknowledged; movement completion pending'
        except Exception as exc:
            # Never retry by pulse: a lost response can still mean motion began.
            self.stop()
            latency.profiler.event('ZOOM_COMMAND_REJECTED', command_rejection_reason=type(exc).__name__)
            return self._failure('PositionABS', exc)

    @camera_operation
    def start_zoom_target(self, target, speed=None, parallel_focus=False):
        """Compatibility API in ZoomValue/10 units, with explicit fallback logging."""
        if self._motor_fault:
            return False, 'Motor blocked: retry STOP'
        try:
            target = float(target)
            if not math.isfinite(target) or not 1 <= target <= 37:
                raise ValueError('Zoom must be 1..37')
            current = self.read_status()['zoom']
            if abs(current-target) <= .15:
                return True, 'Already at target'
            if self.supports_absolute_zoom and target >= 6.4:
                return self.move_to_zoom_position(self._position_mapping.zoom_to_command(target), speed)
            reason = 'BELOW_VERIFIED_POSITIONABS_RANGE' if self.supports_absolute_zoom else 'ABSOLUTE_ZOOM_UNSUPPORTED_OR_UNVERIFIED'
            latency.profiler.event('ZOOM_FALLBACK', command_rejection_reason=reason,
                                   prediction_method='FEEDBACK_SEEK', target_zoom_value=target)
            # This legacy manual seek is explicitly not absolute positioning.
            return self.command(PTZ_ZOOM_IN if target > current else PTZ_ZOOM_OUT, speed)
        except Exception as exc:
            self.stop()
            return self._failure('Zoom target', exc)

    @camera_operation
    def move_zoom(self, target, cancel=None, timeout=15.):
        target = float(target)
        if not math.isfinite(target) or not 1 <= target <= 37:
            raise ValueError('Zoom must be 1..37')
        if self._closed.is_set() or (cancel is not None and cancel.is_set()):
            return False, 'Zoom cancelled'
        try:
            start = self.read_status()['zoom']
            ok, message = self.start_zoom_target(target)
            if not ok:
                return ok, message
            deadline = time.monotonic()+timeout
            while time.monotonic() < deadline:
                if self._closed.is_set() or (cancel is not None and cancel.is_set()):
                    return False, 'Zoom cancelled'
                z = self.read_status()['zoom']
                if abs(z-target) <= .3 or (target > start and z >= target) or (target < start and z <= target):
                    return True, f'Zoom reached {z:.2f}x'
                time.sleep(.05)
            return False, 'Zoom target timeout'
        finally:
            ok, message = self.stop()
            if not ok:
                raise RuntimeError(message)

    @camera_operation
    def move_focus(self, target, cancel=None, timeout=15.):
        if self._motor_fault:
            return False, 'Motor blocked: retry STOP'
        if cancel is not None and cancel.is_set():
            return False, 'Focus cancelled'
        target = float(target)
        if not math.isfinite(target) or not 0 <= target <= 32768:
            raise ValueError('Focus must be 0..32768')
        rpc = self._connect()
        ok, message = rpc.focus_manual(True)
        if not ok:
            return False, 'Cannot enable manual focus'
        try:
            with latency.span('FOCUS_MOTOR', 'focus_motor_loop_ms', evidence='position_poll_control_loop'):
                result = rpc.chay_toi(target, dung_sai=1024, dung_flag=cancel, timeout=timeout)
            latency.profiler.event('FOCUS_MOTOR_RESULT', arrived=bool(result['den_noi']),
                actual_position=result['hd'], target_position=target, evidence='position_poll_tolerance_1024')
        except Exception:
            self._motor_fault = True
            raise
        if cancel is not None and cancel.is_set():
            return False, 'Focus cancelled'
        return bool(result['den_noi']), f"Focus HD={result['hd']}, target={target:g}"

    def _load_network(self):
        if self._network is None:
            import torch
            from .focus_network import MangDelta
            checkpoint = torch.load(self.focus_model, map_location='cpu', weights_only=True)
            if checkpoint.get('nhiem_vu') != 'delta':
                raise RuntimeError('Expected reference delta-focus checkpoint')
            model = MangDelta(2, tuple(checkpoint['rong']), checkpoint['laplacian'], checkpoint['an'])
            model.load_state_dict(checkpoint['mo_hinh'])
            model.eval()
            self._network = model, checkpoint
        return self._network

    @camera_operation
    def autofocus(self, cancel=None, movement_timeout=15.):
        """Reference CNN delta focus, not an invented Dahua one-push AF opcode."""
        if not self._focus_lock.acquire(blocking=False):
            return False, 'CNN focus already running'
        self._focus_cancel = cancel if cancel is not None else threading.Event()
        try:
            if self._motor_fault:
                return False, 'Motor blocked: retry STOP' 
            import cv2
            import numpy as np
            import torch
            from .focus_network import roi_tam
            model, ckpt = self._load_network()
            if self._focus_cancel.is_set() or self._closed.is_set():
                return False, 'CNN focus cancelled'
            rpc = self._connect()
            ok, message = rpc.focus_manual(True)
            if not ok:
                return False, 'Cannot enable manual focus'
            # Wait for a genuinely new D1 frame after focus mode settles.
            with latency.span('FOCUS_FRESH_FRAME_WAIT', 'focus_fresh_frame_wait_ms'):
                earliest = time.monotonic()+.40
                deadline = earliest+5.
                packet = None
                while time.monotonic() < deadline:
                    if self._focus_cancel.is_set() or self._closed.is_set():
                        return False, 'CNN focus cancelled'
                    packet = self.frame_provider() if self.frame_provider else None
                    now = time.monotonic()
                    if packet is not None and earliest <= packet.receive_timestamp <= now and now-packet.receive_timestamp <= .5:
                        break
                    self._focus_cancel.wait(.02)
                else:
                    return False, 'No fresh D1 frame for CNN focus'
            status = self.read_status()
            if status['focus'] is None:
                return False, 'PTZFocusHD unavailable'
            rgb = packet.image  # FrameManager holds unmodified RGB, before digital PTZ.
            h, w = rgb.shape[:2]
            roi = roi_tam(w, h, float(ckpt.get('roi_canh', .4)))
            x0, y0, x1, y1 = roi
            crop = rgb[int(y0*h):int(y1*h), int(x0*w):int(x1*w)]
            side = int(ckpt.get('canh', 128))
            a = cv2.resize(crop, (side, side), interpolation=cv2.INTER_AREA).astype(np.float32)/255.
            a = (a-np.array([.485,.456,.406], np.float32))/np.array([.229,.224,.225], np.float32)
            x = torch.from_numpy(a.transpose(2,0,1).copy()).unsqueeze(0)
            meta = torch.tensor([[status['zoom']/37., status['focus']/32768.]], dtype=torch.float32)
            with torch.no_grad(), latency.span('FOCUS_CNN', 'focus_cnn_ms'):
                delta = model(x, meta).item()*32768.
            if not math.isfinite(delta):
                return False, 'CNN returned invalid focus target'
            if self._focus_cancel.is_set() or self._closed.is_set():
                return False, 'CNN focus cancelled'
            if time.monotonic()-packet.receive_timestamp > .5:
                return False, 'CNN frame became stale during inference'
            return self.move_focus(min(32768., max(0., status['focus']+delta)),
                                   self._focus_cancel, timeout=movement_timeout)
        except Exception as exc:
            return self._failure('CNN focus', exc)
        finally:
            self._focus_lock.release()

    @camera_operation
    def pulse(self, command, duration, speed=None):
        # Used only by diagnostics; production target zoom uses lens position.
        with self.command_lock:
            try:
                ok, message = self.command(command, speed)
                if ok:
                    self._closed.wait(max(0., float(duration)))
            finally:
                stopped, stop_message = self.stop()
            return (ok, message) if stopped else (False, stop_message)

    def zoom_in_pulse(self, duration, speed=None): return self.pulse(PTZ_ZOOM_IN, duration, speed)
    def zoom_out_pulse(self, duration, speed=None): return self.pulse(PTZ_ZOOM_OUT, duration, speed)
    def focus_plus_pulse(self, duration, speed=None): return self.pulse(PTZ_FOCUS_PLUS, duration, speed)
    def focus_minus_pulse(self, duration, speed=None): return self.pulse(PTZ_FOCUS_MINUS, duration, speed)

    def close(self):
        self.cancel_focus()
        self._command_worker.call(self._close_camera)
        self._command_worker.close(self.timeout+2.)

    def _close_camera(self):
        self._closed.set()
        self.cancel_focus()
        worker = self._parallel_thread
        if worker is not None:
            worker.join(timeout=self.timeout+1.)
            if worker.is_alive():
                raise RuntimeError('Cannot close while CNN focus worker is still running')
        if self._poller is not None:
            self._poller.join(timeout=self.timeout+1.)
        with self.command_lock, self._connect_lock:
            if self._rpc is not None:
                try:
                    # A read-only session has never started a motor or changed
                    # settings. Do not issue writes while releasing its resources.
                    if getattr(self._rpc, 'read_only', False) is not True:
                        self._rpc.stop_all()
                        if self._zoom_config is not None:
                            self._rpc._call('configManager.setTemporaryConfig',
                                            {'name':'VideoInZoom','table':self._zoom_config,'options':[]})
                finally:
                    self._rpc.close()
