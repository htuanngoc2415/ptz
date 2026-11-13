"""Hardware-free regression tests for bounded runtime waits."""
import threading
import unittest
from concurrent.futures import Future
from contextvars import copy_context

from runtime.frame_manager import FrameManager
from runtime.camera_worker import CameraCommandWorker


class FrameWaitingTests(unittest.TestCase):
    def test_publish_wakes_consumer(self):
        frames = FrameManager()
        ready = threading.Event()
        result = []

        def consume():
            ready.set()
            result.append(frames.latest(after=0, timeout=1))

        thread = threading.Thread(target=consume)
        thread.start()
        self.assertTrue(ready.wait(1))
        packet = frames.publish(object())
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0].frame_id, packet.frame_id)

    def test_timeout_does_not_reuse_previous_frame(self):
        frames = FrameManager()
        packet = frames.publish(object())
        self.assertIsNone(frames.latest(after=packet.frame_id, timeout=0.01))

    def test_slow_consumer_receives_only_latest_frame(self):
        frames = FrameManager()
        for _ in range(100):
            packet = frames.publish(object())
        self.assertEqual(frames.latest(timeout=0.1).frame_id, packet.frame_id)
        frames.clear()
        self.assertIsNone(frames.latest(timeout=0.01))


class CommandShutdownTests(unittest.TestCase):
    def test_full_queue_cannot_block_close(self):
        worker = CameraCommandWorker()
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def transaction():
            entered.set()
            release.wait(2)

        caller = threading.Thread(target=lambda: worker.call(transaction))
        caller.start()
        futures = []
        try:
            self.assertTrue(entered.wait(1))
            for _ in range(worker.queue.maxsize):
                future = Future()
                futures.append(future)
                worker.queue.put_nowait((future, copy_context(), None, lambda: 42, (), {}))

            def close():
                worker.close(timeout=0.01)
                finished.set()

            closer = threading.Thread(target=close)
            closer.start()
            self.assertTrue(finished.wait(0.5), 'close blocked on the full queue')
            with self.assertRaises(RuntimeError):
                worker.call(lambda: None)
        finally:
            release.set()
            caller.join(2)
            worker.close(timeout=2)
        self.assertFalse(worker.thread.is_alive())
        self.assertTrue(all(f.done() and f.result() == 42 for f in futures))

    def test_idle_worker_stops(self):
        worker = CameraCommandWorker()
        self.assertTrue(worker.close(timeout=1))
        self.assertTrue(worker.close(timeout=1))
