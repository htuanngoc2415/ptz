"""Single bounded execution lane for motor transactions, shared by all controllers.

Supervisors may wait here; inference and Tk only submit to their mailboxes.
Nested operations run inline on the owner thread, avoiding executor deadlocks.
"""
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextvars import copy_context
from functools import wraps
import queue
import threading
from uuid import uuid4
from telemetry import latency


class CameraCommandWorker:
    def __init__(self):
        self.queue = queue.Queue(maxsize=16)
        self.priority = queue.Queue(maxsize=16)
        self._urgent = {}
        self.lock = threading.Lock()
        self.closed = False
        self._stop = threading.Event()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, name='camera-command', daemon=True)
        self.thread.start()

    def call(self, function, *args, _wait_timeout=None, _priority=False, _cancel_on_timeout=True, **kwargs):
        if threading.current_thread() is self.thread:
            return function(*args, **kwargs)
        future = Future()
        with self.lock:
            if self.closed:
                raise RuntimeError('Camera command worker closed')
            safety_stop = function.__name__ in ('stop', 'emergency_stop') or (
                function.__name__ == 'command' and len(args) > 1 and args[1] == 'stop')
            urgent = _priority or safety_stop
            if safety_stop:
                retained = []
                while True:
                    try:
                        pending = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    if pending[3].__name__ in ('command', 'move_zoom', 'move_focus', 'autofocus'):
                        if not pending[0].done():
                            pending[0].set_exception(RuntimeError('Movement superseded by STOP'))
                    else:
                        retained.append(pending)
                for pending in retained:
                    self.queue.put_nowait(pending)
            key = (function, id(args[0]) if args else None, bool(kwargs.get('emergency')))
            if urgent and key in self._urgent:
                future = self._urgent[key]
            else:
                if urgent:
                    self._urgent[key] = future
                try:
                    fields = latency.context.get()
                    with latency.correlated(command_id=fields.get('command_id') or 'cmd_'+uuid4().hex):
                        name = function.__name__
                        if name == 'command' and len(args) > 1:
                            name = str(args[1])
                        axis = 'ZOOM' if 'zoom' in name else 'FOCUS' if 'focus' in name else None
                        if axis is not None and 'command_id' not in fields:
                            latency.profiler.event(axis+'_REQUEST_CREATED', source='manual_or_diagnostic')
                        queued = latency.profiler.event('CAMERA_COMMAND_QUEUE_ENTER')
                        (self.priority if urgent else self.queue).put_nowait((future, copy_context(), queued, function, args, kwargs))
                        self._ready.set()
                except queue.Full:
                    self._urgent.pop(key, None)
                    latency.profiler.count('dropped_camera_jobs')
                    latency.profiler.event('CAMERA_JOB_DROPPED', reason='QUEUE_FULL')
                    raise RuntimeError('Camera command queue full') from None
        try:
            return future.result(timeout=_wait_timeout)
        except FutureTimeoutError:
            # A timed-out queued START must never execute later. A running RPC
            # remains uncertain and its controller is responsible for STOP.
            if _cancel_on_timeout and not urgent:
                future.cancel()
            raise

    def _run(self):
        while True:
            with self.lock:
                try:
                    item = self.priority.get_nowait()
                except queue.Empty:
                    try:
                        item = self.queue.get_nowait()
                    except queue.Empty:
                        item = None
                        self._ready.clear()
                if item is None and self._stop.is_set():
                    return
            if item is None:
                self._ready.wait(.1)
                continue
            future, context, queued, function, args, kwargs = item
            if not future.set_running_or_notify_cancel():
                with self.lock:
                    self._urgent = {key: value for key, value in self._urgent.items() if value is not future}
                continue
            def invoke():
                latency.profiler.elapsed('CAMERA_COMMAND_QUEUE_EXIT', queued, 'camera_command_queue_wait_ms')
                with latency.span('CAMERA_TRANSACTION', 'camera_transaction_ms', operation=function.__name__):
                    return function(*args, **kwargs)
            try:
                result = context.run(invoke)
            except BaseException as exc:
                with self.lock:
                    self._urgent = {key: value for key, value in self._urgent.items() if value is not future}
                future.set_exception(exc)
            else:
                with self.lock:
                    self._urgent = {key: value for key, value in self._urgent.items() if value is not future}
                future.set_result(result)

    def close(self, timeout=5., cancel_pending=False):
        timeout = 5. if timeout is None else max(0., timeout)
        with self.lock:
            if not self.closed:
                self.closed = True
                # A full queue must never block shutdown while holding the lock.
                # The worker drains accepted transactions before exiting.
                self._stop.set()
                self._ready.set()
            if cancel_pending:
                for pending in (self.priority, self.queue):
                    while True:
                        try:
                            item = pending.get_nowait()
                        except queue.Empty:
                            break
                        if not item[0].done():
                            item[0].set_exception(RuntimeError('Camera worker shutting down'))
                self._urgent.clear()
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout)
        return not self.thread.is_alive()


def camera_operation(function):
    @wraps(function)
    def run(self, *args, **kwargs):
        # Unit/diagnostic adapters can still instantiate with autoconnect=False.
        if function.__name__ == 'stop':
            self.cancel_focus()  # interrupt a long focus task before waiting in its queue
        worker = self._command_worker
        return worker.call(function, self, *args,
            _wait_timeout=getattr(self, 'command_wait_timeout', None), **kwargs)
    return run
