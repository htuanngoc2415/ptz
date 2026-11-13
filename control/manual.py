"""Latest-command manual motor worker; never performs HTTP in Tk callbacks."""
import threading
import time
from telemetry import latency


class ManualController:
    def __init__(self, ptz, zoom, focus, orchestrator, status, settle=.2):
        self.ptz, self.zoom, self.focus = ptz, zoom, focus
        self.orchestrator, self.status, self.settle = orchestrator, status, settle
        self._condition = threading.Condition()
        self._operation_cancel = threading.Event()
        self._pending = None
        self._pending_stop = None
        self._version = 0
        self._closed = False
        self._fault = False
        self._thread = threading.Thread(target=self._run, name='manual-motor', daemon=True)
        self._thread.start()

    def request(self, command=None, speed=None, duration=None, completed=None):
        with self._condition:
            if self._closed:
                return
            if command is not None and (self._fault or self._pending_stop is not None
                                        or self.orchestrator.closing):
                self.status("Motor blocked after STOP failure; request STOP again")
                return
            self.orchestrator.manual = True  # deny new automatic leases immediately
            self._operation_cancel.set()
            if hasattr(self.ptz, "cancel_focus"):
                self.ptz.cancel_focus()
            self._operation_cancel = threading.Event()
            self._version += 1
            requested = latency.profiler.event('MANUAL_REQUEST', manual_request_id=self._version,
                                               manual_command=command)
            request = (self._version, command, speed, duration, completed, self._operation_cancel, requested)
            if command is None:
                self._pending_stop = request  # sticky; ordinary START cannot overwrite STOP
                self._pending = None
            else:
                self._pending = request
            self._condition.notify_all()

    def close(self):
        self.request()
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending_stop is not None or self._pending is not None or self._closed)
                if self._pending_stop is not None:
                    item, self._pending_stop = self._pending_stop, None
                elif self._pending is not None:
                    item, self._pending = self._pending, None
                else:
                    return
                version, command, speed, duration, completed, cancel, requested = item
            latency.profiler.elapsed('MANUAL_WORKER_BEGIN', requested, 'manual_queue_wait_ms',
                                     manual_request_id=version, manual_command=command)
            self.zoom.set_enabled(False)
            self.focus.set_enabled(False)
            if command is None:
                # Emergency STOP must not wait for the owner it is trying to stop.
                try:
                    stop = getattr(type(self.ptz), 'emergency_stop', None)
                    ok, message = stop(self.ptz) if stop else self.ptz.stop()
                except Exception as exc:
                    ok, message = False, type(exc).__name__
                self._fault = not ok
                if ok:
                    self.orchestrator.release('manual', self.settle)
                    if version == self._version and not self._closed:
                        self.orchestrator.manual = False
                else:
                    self.status(f'Emergency STOP unconfirmed: {message}; movement blocked')
                continue
            if self._fault or self._closed or self.orchestrator.closing:
                # A START may have been queued while emergency STOP was in flight.
                self.status('Manual movement blocked by STOP fault or shutdown')
                continue
            # An outstanding camera HTTP transaction cannot safely be cancelled.
            deadline = time.monotonic()+20.
            acquired = self._fault and self.orchestrator.owner == "manual"
            while not acquired:
                with self._condition:
                    if version != self._version or self._closed:
                        break
                acquired = self.orchestrator.acquire('manual')
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    self.status('Manual command deferred: camera still busy')
                    break
                with self._condition:
                    self._condition.wait(.05)
            if not acquired:
                if version == self._version:
                    self.orchestrator.manual = False
                continue
            succeeded = False
            try:
                if command is not None and not cancel.is_set():
                    latency.profiler.elapsed('MANUAL_DISPATCH', requested, 'manual_request_to_dispatch_ms',
                                             manual_request_id=version, manual_command=command)
                    dispatched = time.perf_counter_ns()
                    if isinstance(command, tuple):
                        kind, value = command
                        if kind == 'zoom_target':
                            ok, message = self.ptz.move_zoom(value, cancel=cancel)
                        elif kind == 'focus_target':
                            ok, message = self.ptz.move_focus(value, cancel=cancel)
                        else:
                            raise ValueError('Unknown position command')
                    elif command == 'cnn_focus':
                        ok, message = self.ptz.autofocus(cancel=cancel)
                    else:
                        ok, message = self.ptz.command(command, speed)
                    latency.profiler.elapsed('MANUAL_COMMAND_RETURN', dispatched, 'manual_command_call_ms',
                                             manual_request_id=version, acknowledged=bool(ok))
                    if not ok:
                        self.status(f'Manual command failed: {message}')
                    elif isinstance(command, tuple) or command == 'cnn_focus':
                        succeeded = not cancel.is_set()
                        self.status(message)
                    else:
                        with self._condition:
                            self._condition.wait_for(lambda: self._closed or version != self._version,
                                                     timeout=duration)
                            succeeded = not self._closed and version == self._version
            except Exception as exc:
                self.status(f'Manual command error: {type(exc).__name__}')
            finally:
                try:
                    stop_started = latency.profiler.event('MANUAL_STOP_BEGIN', manual_request_id=version,
                                                          trigger_request_id=self._version)
                    ok, message = self.ptz.stop()
                    latency.profiler.elapsed('MANUAL_STOP_RETURN', stop_started, 'manual_stop_call_ms',
                                             manual_request_id=version, acknowledged=bool(ok))
                    if not ok:
                        # Fail closed: do not release ownership after an unconfirmed STOP.
                        self.status(f'Manual STOP failed: {message}')
                        self.orchestrator.manual = True
                        self._fault = True
                        continue
                except Exception as exc:
                    self.status(f'Manual STOP error: {type(exc).__name__}')
                    self._fault = True
                    continue
                self._fault = False
                self.orchestrator.release('manual', self.settle)
                with self._condition:
                    if version == self._version:
                        self.orchestrator.manual = False
            if succeeded and completed is not None:
                completed()
