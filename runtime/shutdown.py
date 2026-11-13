"""Ordered control teardown, called off Tk. Expiry never grants a motor lease."""
import time
from telemetry import latency

UNCONFIRMED_WARNING = ('STOP chưa được xác nhận; camera có thể còn chuyển động. '
                       'Cần kiểm tra trực tiếp hoặc dùng cơ chế dừng độc lập của thiết bị.')


def close_controls(pan, zoom, focus, manual, ptz, orchestrator, timeout=12., status=lambda _: None):
    deadline = time.monotonic()+max(0., timeout)
    remaining = lambda: max(0., deadline-time.monotonic())
    orchestrator.begin_shutdown()
    # HANET rejects all new STARTs at dequeue, including manual requests.
    begin = getattr(type(ptz), 'begin_shutdown', None)
    if begin:
        begin(ptz)
    arm = getattr(type(ptz), 'arm_shutdown_deadline', None)
    if arm:
        arm(ptz, deadline)
    pan.set_enabled(False)
    manual.close()  # sticky emergency STOP, does not wait for ownership
    zoom.request_stop(reason='APPLICATION_CLOSE')
    focus.set_enabled(False)
    ptz.cancel_focus()
    failures = []
    for name, controller in (('auto_pan_tilt', pan), ('auto_zoom', zoom), ('auto_focus', focus)):
        try:
            if controller.close(timeout=remaining()) is False:
                failures.append(name)
                latency.profiler.event('SHUTDOWN_STOP_TIMEOUT', shutdown_phase=name)
        except Exception as exc:
            failures.append(name)
            latency.profiler.event('SHUTDOWN_CLEANUP_ERROR', shutdown_phase=name,
                exception_type=type(exc).__name__, exception_message='Controller cleanup failed')
    manual._thread.join(remaining())
    if manual._thread.is_alive():
        failures.append('manual')
    # Even if a supervisor timed out, closing the command lane rejects its later
    # calls and cancels queued transactions before RPC resources are destroyed.
    try:
        if begin:
            closed = ptz.close(timeout=remaining())
        else:
            closed = ptz.close()
        if closed is False:
            failures.append('camera')
    except Exception as exc:
        failures.append('camera')
        latency.profiler.event('SHUTDOWN_CLEANUP_ERROR', shutdown_phase='camera',
            exception_type=type(exc).__name__, exception_message='Camera cleanup failed')
    unconfirmed = (getattr(ptz, 'shutdown_unconfirmed', False) is True
                   or getattr(ptz, '_motor_fault', False) is True
                   or bool(failures))
    if unconfirmed:
        status(UNCONFIRMED_WARNING)
    latency.profiler.event('SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR' if unconfirmed else 'SHUTDOWN_COMPLETED',
        shutdown_phase='controls', motor_owner=orchestrator.owner,
        unconfirmed_motor=unconfirmed, cleanup_failures=failures, physical_motor_stopped=None)
    return {'unconfirmed_motor': unconfirmed, 'cleanup_failures': failures,
            'elapsed_seconds': time.monotonic()-(deadline-timeout)}
