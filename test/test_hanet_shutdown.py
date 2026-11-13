"""Offline concurrency, GUI deadline and shutdown ordering regressions."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import threading
import time
import tempfile
import unittest
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import Mock, patch

from camera.hanet_controller import HanetController
from runtime.camera_worker import CameraCommandWorker
from runtime.stop_retry import StopRetryBudget
from runtime.shutdown import close_controls
from control.auto_zoom import AutoZoomController
from control.orchestrator import ControlOrchestrator
from telemetry import latency


class ShutdownTests(unittest.TestCase):
    def camera(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        rpc=Mock();rpc.motion.return_value=True
        camera=HanetController(ip='offline.invalid',username='u',password='p',autoconnect=False,
            timeout=.1,rpc_factory=lambda *a,**k:rpc,safety_state_path=Path(temporary.name)/'state.json')
        self.addCleanup(camera.close)
        return camera,rpc

    def test_pending_start_cancelled_by_priority_stop(self):
        worker=CameraCommandWorker();entered=threading.Event();release=threading.Event()
        def block():entered.set();release.wait(1)
        running=threading.Thread(target=lambda:worker.call(block));running.start()
        self.assertTrue(entered.wait(1))
        started=[]
        def command():started.append(True)
        future=Future();worker.queue.put_nowait((future,copy_context(),None,command,(),{}))
        admitted=threading.Event();enqueue=worker.priority.put_nowait
        def admit(item):enqueue(item);admitted.set()
        def stop():return True
        with patch.object(worker.priority,'put_nowait',admit):
            stopper=threading.Thread(target=lambda:worker.call(stop));stopper.start()
            self.assertTrue(admitted.wait(1))
        release.set();running.join(1);stopper.join(1)
        self.assertTrue(worker.close(1));self.assertFalse(started)
        self.assertIn('superseded',str(future.exception()))

    def test_timed_out_queued_start_never_executes(self):
        worker=CameraCommandWorker();entered=threading.Event();release=threading.Event()
        def block():entered.set();release.wait(1)
        running=threading.Thread(target=lambda:worker.call(block));running.start();self.assertTrue(entered.wait(1))
        starts=[]
        def command():starts.append(True)
        with self.assertRaises(FutureTimeoutError):worker.call(command,_wait_timeout=.01)
        release.set();running.join(1);worker.close(1)
        self.assertFalse(starts)

    def test_close_timeout_retains_ordered_cleanup_and_no_late_start(self):
        camera,rpc=self.camera();entered=threading.Event();release=threading.Event()
        def motion(*args):
            if args[1]:entered.set();release.wait(1)
            return True
        rpc.motion.side_effect=motion
        # A fake blocking network call wakes on abort, as socket.shutdown does.
        rpc.abort.side_effect=release.set
        result=[]
        running=threading.Thread(target=lambda:result.append(camera.command('pan_left')));running.start()
        self.assertTrue(entered.wait(1))
        start=time.perf_counter()
        self.assertFalse(camera.close(timeout=.02))
        self.assertLess(time.perf_counter()-start,.3)
        running.join(1);camera._command_worker.thread.join(1)
        self.assertFalse(camera._command_worker.thread.is_alive())
        rpc.close.assert_called_once()
        with self.assertRaises(RuntimeError):camera.command('zoom_in')
        operations=[call.args[1] for call in rpc.motion.call_args_list]
        self.assertEqual(operations,[True,False])

    def test_shutdown_during_login_does_not_transmit_start(self):
        camera,rpc=self.camera()
        def connect(*args,**kwargs):camera.begin_shutdown();return rpc
        camera._rpc_factory=connect
        self.assertFalse(camera.command('pan_left')[0])
        rpc.motion.assert_not_called()

    def test_no_ack_return_is_unconfirmed(self):
        camera,rpc=self.camera();rpc.motion.return_value=None
        self.assertFalse(camera.command('pan_left')[0])
        self.assertTrue(camera._motor_fault)
        self.assertFalse(camera.command('zoom_in')[0])

    def test_zoom_stop_retry_is_bounded_without_releasing_ownership(self):
        camera=Mock();camera.has_zoom_telemetry=False;camera.stop.return_value=(False,'lost ACK')
        zoom=AutoZoomController(camera,wait_after_zoom=0)
        self.addCleanup(zoom.close)
        owner=ControlOrchestrator();owner.acquire('zoom');zoom.orchestrator=owner
        zoom.busy=zoom.zoom_motor_active=True;zoom._started=time.monotonic();zoom.zoom_direction=1
        now=[100.];zoom._stop_budget=StopRetryBudget(clock=lambda:now[0])
        for _ in range(100):zoom.stop();now[0]+=5
        self.assertEqual(camera.stop.call_count,5)
        self.assertTrue(zoom.busy);self.assertEqual(owner.owner,'zoom')
        camera.motor_state='READY';camera.stop.return_value=(True,'ACK')
        zoom.stop();self.assertFalse(zoom.busy);self.assertIsNone(owner.owner)

    def test_cleanup_order_and_errors_are_reported(self):
        order=[]
        class Backend:
            _motor_fault=False;shutdown_unconfirmed=False
            def begin_shutdown(self):order.append('deny_start')
            def cancel_focus(self):pass
            def close(self,timeout):order.append('camera');return True
        pan,zoom,focus,manual=Mock(),Mock(),Mock(),Mock()
        pan.close.side_effect=lambda **k:order.append('pan') or (_ for _ in ()).throw(RuntimeError('cleanup'))
        zoom.close.side_effect=lambda **k:order.append('zoom') or True
        focus.close.side_effect=lambda **k:order.append('focus') or True
        manual._thread.join.side_effect=lambda timeout:order.append('manual')
        manual._thread.is_alive.return_value=False
        result=close_controls(pan,zoom,focus,manual,Backend(),ControlOrchestrator(),timeout=1)
        self.assertEqual(order,['deny_start','pan','zoom','focus','manual','camera'])
        self.assertEqual(result['cleanup_failures'],['auto_pan_tilt'])
        self.assertTrue(result['unconfirmed_motor'])

    def test_gui_deadline_exits_even_if_stream_and_controls_never_finish(self):
        from gui.cctv_app import CCTVApp
        now=[0.]
        class Root:
            def __init__(self):self.pending=[];self.destroyed=False
            def title(self,title):pass
            def after(self,ms,callback):self.pending.append((ms,callback))
            def destroy(self):self.destroyed=True
        class DeferredThread:
            def __init__(self,*args,**kwargs):pass
            def start(self):pass
        root=Root()
        app=SimpleNamespace(root=root,running=True,image_settings_stop=threading.Event(),
            auto_pan_tilt=Mock(),manual_controller=Mock(),auto_zoom=Mock(),auto_focus=Mock(),
            ptz=Mock(),orchestrator=ControlOrchestrator(),rtsp=Mock(),_ai_started=False,set_status=Mock())
        with patch('gui.cctv_app.threading.Thread',DeferredThread),patch('gui.cctv_app.time.monotonic',lambda:now[0]), \
             patch.object(latency.profiler,'event',wraps=latency.profiler.event) as events:
            CCTVApp.on_close(app)
            self.assertFalse(root.destroyed)
            while root.pending and not root.destroyed:
                ms,callback=root.pending.pop(0);now[0]+=ms/1000;callback()
            self.assertTrue(root.destroyed);self.assertLessEqual(now[0],17.1)
            self.assertTrue(app.orchestrator.closing)
            self.assertTrue(any(c.args[0]=='SHUTDOWN_COMPLETED_WITH_UNCONFIRMED_MOTOR' for c in events.call_args_list))

    def test_manual_start_queued_during_failed_stop_cannot_bypass_fault(self):
        from control.manual import ManualController
        entered, release = threading.Event(), threading.Event()
        camera = Mock()
        def stop():
            entered.set(); release.wait(1)
            return False, 'unconfirmed'
        camera.stop.side_effect = stop
        status = Mock()
        handled = threading.Event()
        status.side_effect = lambda message: handled.set() if message.startswith('Manual movement blocked') else None
        manual = ManualController(camera, Mock(), Mock(), ControlOrchestrator(), status)
        try:
            manual.request()
            self.assertTrue(entered.wait(1))
            manual.request('pan_left')
            release.set()
            self.assertTrue(handled.wait(1))
            camera.command.assert_not_called()
        finally:
            release.set(); manual.close(); manual._thread.join(1)
        self.assertFalse(manual._thread.is_alive())

    def test_shutdown_watchdog_interrupts_inflight_rpc_before_resource_close(self):
        camera, rpc = self.camera()
        entered, release = threading.Event(), threading.Event()
        order = []
        def motion(code, start, speed):
            if start:
                entered.set(); release.wait(1)
            order.append('START' if start else 'STOP')
            return True
        rpc.motion.side_effect = motion
        rpc.abort.side_effect = lambda: (order.append('ABORT'), release.set())
        rpc.close.side_effect = lambda: order.append('CLOSE')
        running = threading.Thread(target=lambda: camera.command('pan_left'))
        running.start(); self.assertTrue(entered.wait(1))
        pan, zoom, focus, manual = Mock(), Mock(), Mock(), Mock()
        pan.close.return_value = zoom.close.return_value = focus.close.return_value = True
        manual._thread.is_alive.return_value = False
        result = close_controls(pan, zoom, focus, manual, camera, ControlOrchestrator(), timeout=.05)
        running.join(1); camera._command_worker.thread.join(1)
        self.assertFalse(camera._command_worker.thread.is_alive())
        self.assertIn('ABORT', order)
        self.assertLess(order.index('ABORT'), order.index('CLOSE'))
        self.assertFalse(camera._shutdown_timer.is_alive())
        self.assertLess(result['elapsed_seconds'], .5)

    def test_abort_failure_is_logged_without_masking_cleanup(self):
        camera, rpc = self.camera(); camera.read_status()
        rpc.abort.side_effect = RuntimeError('private-password')
        with patch.object(latency.profiler, 'event', wraps=latency.profiler.event) as events:
            camera._abort_transport()
            self.assertFalse(camera.close(timeout=1))
        self.assertTrue(camera.cleanup_failed)
        self.assertFalse(camera._command_worker.thread.is_alive())
        self.assertTrue(any(c.args[0] == 'HANET_CLEANUP_ERROR' for c in events.call_args_list))
        self.assertNotIn('private-password', str(events.call_args_list))


if __name__=='__main__':unittest.main()
