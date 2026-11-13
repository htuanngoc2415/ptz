"""Hardware-free tests for timing, bounded mailboxes and worker ownership."""
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from telemetry import latency
from runtime.camera_worker import CameraCommandWorker
from runtime.scheduling import InferenceScheduler
from runtime.frame_manager import FrameManager


class LatencyTests(unittest.TestCase):
    def test_correlated_trace_summary_and_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            p = latency.LatencyProfiler(directory, enabled=True)
            with latency.correlated(frame_id=14, acquisition_id='a', lock_session_id='l'):
                p.event('TEST', metric='stage_ms', duration_ms=2.)
                p.event('TEST', metric='stage_ms', duration_ms=4.)
            self.assertTrue(p.close())
            rows = [json.loads(line) for line in next((Path(directory)/'latency').glob('*.jsonl')).read_text().splitlines()]
            self.assertEqual(rows[0]['frame_id'], 14)
            self.assertEqual(rows[0]['lock_session_id'], 'l')
            self.assertRegex(rows[0]['timestamp'], r'[+-]\d\d:\d\d$')
            summary = p.summary()[0]
            self.assertEqual((summary['count'], summary['mean'], summary['p50']), (2, 3., 3.))
            self.assertAlmostEqual(summary['p99'], 3.98)
            self.assertTrue(list((Path(directory)/'latency').glob('*.csv')))

    def test_numpy_geometry_does_not_kill_async_log_writers(self):
        import numpy as np
        from telemetry.zoom_trace import ZoomTrace
        from telemetry.events import _sanitize
        fields=dict(zoom_intent_bbox=tuple(np.array([1,2,3,4],dtype=np.int64)),
                    score=np.float32(.5), invalid=np.float32('nan'))
        self.assertIsNone(_sanitize(fields)['invalid'])
        with tempfile.TemporaryDirectory() as directory:
            p=latency.LatencyProfiler(directory,enabled=True)
            z=ZoomTrace(directory,enabled=True)
            for event in ('ZOOM_REQUEST_CREATED','ZOOM_STOP_ACK'):
                p.event(event,**fields)
                z.observe(event,time.perf_counter_ns(),fields)
            self.assertTrue(p.close())
            self.assertTrue(z.close())
            self.assertIsNone(p.error)
            self.assertIsNone(z.error)
            for path in (next((Path(directory)/'latency').glob('*.jsonl')),z.trace_path):
                rows=[json.loads(line) for line in path.read_text().splitlines()]
                rows=[r for r in rows if r['event'] != 'LATENCY_REPORT']
                self.assertEqual([r['event'] for r in rows],['ZOOM_REQUEST_CREATED','ZOOM_STOP_ACK'])
                self.assertEqual(rows[0]['zoom_intent_bbox'],[1,2,3,4])

    def test_missing_clock_pair_does_not_invent_duration(self):
        p = latency.LatencyProfiler()
        p.enabled = True
        with patch('telemetry.latency.time.perf_counter_ns', return_value=2_000_000):
            p.elapsed('UNKNOWN', None, 'invalid')
            p.elapsed('CLOCK_REVERSED', 3_000_000, 'invalid')
            p.elapsed('VALID', 1_000_000, 'valid_ms')
        self.assertNotIn('invalid', p.snapshot())
        self.assertEqual(p.snapshot()['valid_ms'], 1.)
        p.close()

    def test_queue_and_statistics_memory_are_bounded(self):
        p = latency.LatencyProfiler(queue_size=2, sample_limit=3)
        p.enabled = True  # deliberately no writer; fill the bounded queue
        for i in range(10):
            p.event('X', metric='x', duration_ms=float(i))
        self.assertEqual(p.queue.qsize(), 2)
        self.assertEqual(p.snapshot()['dropped_log_events'], 8)
        self.assertEqual(len(p.samples['x']), 3)
        self.assertEqual(p.summary()[0]['count'], 10)
        p.close()

    def test_disabled_profiling_creates_no_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = latency.LatencyProfiler(d, enabled=False)
            p.event('X', metric='x', duration_ms=5.)
            p.close()
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertEqual(p.summary(), [])

    def test_frame_manager_preserves_monotonic_receipt_and_unknown_capture(self):
        manager = FrameManager()
        manager.publish('one', received_ns=100)
        manager.publish('two', received_ns=200)
        packet = manager.latest(consumer='detection')
        self.assertEqual((packet.frame_id, packet.image, packet.received_ns), (2, 'two', 200))
        self.assertIsNone(packet.capture_timestamp)
        self.assertIsNone(manager.latest(after=packet.frame_id))

    def test_rotation_bounds_trace_size(self):
        with tempfile.TemporaryDirectory() as d:
            p = latency.LatencyProfiler(d, enabled=True, max_bytes=1000, backups=2)
            for _ in range(100):
                p.event('SAMPLE', field='x'*100)
            p.close()
            self.assertLessEqual(len(list((Path(d)/'latency').glob('*.jsonl*'))), 3)


class SchedulingTests(unittest.TestCase):
    def test_camera_commands_share_one_thread_and_nested_calls_do_not_deadlock(self):
        worker = CameraCommandWorker()
        seen = []
        def operation():
            seen.append(threading.current_thread().name)
            return worker.call(lambda: latency.context.get()['lock_session_id'])
        try:
            with latency.correlated(lock_session_id='session'):
                self.assertEqual(worker.call(operation), 'session')
            self.assertEqual(seen, ['camera-command'])
        finally:
            self.assertTrue(worker.close())
        with self.assertRaises(RuntimeError):
            worker.call(lambda: None)

    def test_queued_detection_has_priority_over_face(self):
        scheduler = InferenceScheduler()
        order = []
        def run(branch):
            with scheduler.use(branch):
                order.append(branch)
        with scheduler.use('reid'):
            face = threading.Thread(target=run, args=('face',))
            detection = threading.Thread(target=run, args=('detection',))
            face.start(); detection.start()
            deadline = time.monotonic()+1
            while scheduler.waiting_detection == 0 and time.monotonic() < deadline:
                time.sleep(.001)
            self.assertEqual(scheduler.waiting_detection, 1)
        face.join(1); detection.join(1)
        self.assertEqual(order, ['detection', 'face'])


class FaceBranchTests(unittest.TestCase):
    def make_worker(self):
        import config
        from runtime.face_worker import FaceWorker
        cfg = SimpleNamespace(**{key: getattr(config, key) for key in dir(config) if key.isupper()})
        cfg.FACE_PIPELINE_MODE = "baseline"  # legacy biometric lifecycle contract
        cfg.FACE_RECOGNITION_INTERVAL = 0
        self.recognizer = Mock()
        self.matcher = Mock()
        self.matcher.match.return_value = dict(identity='A', score=.9, matched=True)
        worker = FaceWorker(Mock(), self.recognizer, self.matcher, cfg)
        self.addCleanup(worker.close)
        return worker

    def test_inflight_result_discarded_after_invalidation(self):
        worker = self.make_worker()
        entered, release = threading.Event(), threading.Event()
        def process(job):
            entered.set()
            release.wait(2)
            return dict(job=job)
        with patch.object(worker, '_process', side_effect=process):
            worker.submit(1, time.monotonic(), None, None, None, [], 'a', 'l', 1)
            self.assertTrue(entered.wait(1))
            worker.invalidate()
            release.set()
            worker.close()
            self.assertIsNone(worker.take())

    def test_latest_only_queue_replaces_old_frames(self):
        worker = self.make_worker()
        entered, release = threading.Event(), threading.Event()
        seen = []
        def process(job):
            seen.append(job.frame_id)
            if job.frame_id == 1:
                entered.set(); release.wait(2)
            return dict(job=job)
        with patch.object(worker, '_process', side_effect=process):
            worker.submit(1, time.monotonic(), None, None, None, [], 'a', 'l', 1)
            self.assertTrue(entered.wait(1))
            for i in range(2, 20):
                worker.submit(i, time.monotonic(), None, None, None, [], 'a', 'l', 1)
            release.set()
            with worker.condition:
                self.assertTrue(worker.condition.wait_for(lambda: worker.result is not None and
                    worker.result['job'].frame_id == 19, timeout=2))
            self.assertEqual(seen, [1, 19])

    def test_face_quality_and_identity_work_without_camera_lease(self):
        import numpy as np
        worker = self.make_worker()
        face = {'bbox': (20, 20, 120, 120), 'landmarks': np.zeros((5, 2))}
        person = {'bbox': (10, 10, 180, 350)}
        image = np.zeros((400,400,3), np.uint8)
        with patch('ai.face.face_detector.detect_face_inside_person', return_value=[face]), \
             patch('ai.sharpness.calculate_face_sharpness', return_value=200), \
             patch('ai.face.face_alignment.align_face', return_value=np.zeros((112,112,3), np.uint8)):
            for frame in (1,2):
                worker.submit(frame, time.monotonic(), image, person, None, [person], 'a', 'l', 7)
                with worker.condition:
                    self.assertTrue(worker.condition.wait_for(lambda: bool(worker.identity_results), timeout=2))
                result = worker.take_identity()
                self.assertEqual(result['recognition']['frame_id'], frame)
            self.assertTrue(result['recognition']['confirmed'])
            self.assertEqual(result['recognition']['lock_session_id'], 'l')
            self.assertEqual(self.recognizer.extract_embedding.call_count, 2)

    def test_spatial_result_available_while_arcface_is_still_running(self):
        import numpy as np
        worker=self.make_worker()
        entered,release=threading.Event(),threading.Event()
        def embedding(image):
            entered.set()
            release.wait(2)
            return np.ones(512)
        self.recognizer.extract_embedding.side_effect=embedding
        face={'bbox':(20,20,120,120),'landmarks':np.zeros((5,2))}
        person={'bbox':(10,10,180,350)}
        try:
            with patch('ai.face.face_detector.detect_face_inside_person',return_value=[face]), \
                 patch('ai.sharpness.calculate_face_sharpness',return_value=200), \
                 patch('ai.face.face_alignment.align_face',return_value=np.zeros((112,112,3),np.uint8)):
                worker.submit(1,time.monotonic(),np.zeros((400,400,3),np.uint8),person,None,[person],'a','l',7)
                self.assertTrue(entered.wait(1))
                spatial=worker.take()
                self.assertEqual(spatial['face']['bbox'],face['bbox'])
                self.assertIsNone(spatial['recognition'])
                self.assertIsNone(worker.take_identity())
                release.set()
                with worker.condition:
                    self.assertTrue(worker.condition.wait_for(lambda: bool(worker.identity_results),timeout=2))
                self.assertIsNotNone(worker.take_identity())
                self.assertIsNone(worker.take())  # same bbox must not count twice as face confirmation
        finally:
            release.set()

    def test_neighbor_face_cannot_be_assigned_to_target(self):
        import numpy as np
        worker = self.make_worker()
        face = {'bbox': (20,20,120,120), 'landmarks': np.zeros((5,2))}
        target, other = {'bbox': (10,10,180,350)}, {'bbox': (15,15,150,360)}
        with patch('ai.face.face_detector.detect_face_inside_person', return_value=[face]):
            worker.submit(1, time.monotonic(), np.zeros((400,400,3), np.uint8), target, None,
                          [target,other], 'a', 'l', 1)
            with worker.condition:
                self.assertTrue(worker.condition.wait_for(lambda: worker.result is not None, timeout=2))
            result = worker.take()
            self.assertIsNone(result['face'])
            self.recognizer.extract_embedding.assert_not_called()

    def test_stale_job_never_runs_model(self):
        worker = self.make_worker()
        worker.submit(1, time.monotonic()-5, None, None, None, [], 'a', 'l', 1)
        worker.close()
        self.recognizer.extract_embedding.assert_not_called()


class SessionAndControlTests(unittest.TestCase):
    def test_reset_invalidates_old_motor_token_and_new_session_is_distinct(self):
        from runtime.target_session import TargetSession
        state = TargetSession()
        self.assertFalse(state.matches(None))
        state.publish('first')
        self.assertTrue(state.matches('first'))
        state.publish(None)
        self.assertFalse(state.matches('first'))
        state.publish('second')
        self.assertFalse(state.matches('first'))
        self.assertTrue(state.matches('second'))

    def test_worker_rpc_errors_reach_caller_and_lane_remains_usable(self):
        worker = CameraCommandWorker()
        try:
            with self.assertRaises(TimeoutError):
                worker.call(lambda: (_ for _ in ()).throw(TimeoutError()))
            self.assertEqual(worker.call(lambda: 'next'), 'next')
        finally:
            self.assertTrue(worker.close())

    def test_stale_session_never_reaches_rpc_post(self):
        from camera.dahua_rpc import ZoomRPC
        rpc = ZoomRPC.__new__(ZoomRPC)
        rpc._lock = threading.Lock()
        rpc._http_lock = threading.RLock()
        rpc._poll_lock = threading.Lock()
        rpc._id = rpc._seq = 0
        rpc.session = None
        rpc.http = Mock()
        rpc.session_valid = lambda session: False
        with latency.correlated(lock_session_id='old'):
            with self.assertRaisesRegex(RuntimeError, 'Stale target session'):
                rpc._call('ptz.start', {'code':'PositionABS'})
        rpc.http.post.assert_not_called()

    def test_visual_stability_needs_distinct_frames_and_is_labeled_estimated(self):
        from control.auto_zoom import AutoZoomController
        zoom = AutoZoomController(Mock(), wait_after_zoom=0)
        zoom._stability_reported = False
        zoom._action_request_ns = time.perf_counter_ns()
        zoom._action_correlation = dict(lock_session_id='l', command_id='c')
        with patch.object(latency.profiler, 'elapsed') as elapsed:
            for _ in range(5):
                zoom.observe_stability(.2, 1, time.monotonic())
            elapsed.assert_not_called()
            zoom.observe_stability(.201, 2, time.monotonic())
            elapsed.assert_not_called()
            zoom.observe_stability(.2, 3, time.monotonic())
            self.assertEqual(elapsed.call_args.args[0], 'ZOOM_VISUALLY_STABLE')
            self.assertEqual(elapsed.call_args.kwargs['evidence'], 'estimated_bbox_ratio')


class PersonTrackingTests(unittest.TestCase):
    def test_multiple_people_keep_ids_across_order_changes_and_short_misses(self):
        from runtime.person_tracking import PersonTracker
        tracker = PersonTracker(.25, .1, 3, 1.)
        first = tracker.update([dict(bbox=(0,0,100,200)), dict(bbox=(200,0,300,200))], 0.)
        a, b = [person['track_id'] for person in first]
        tracker.update([], .1)
        later = tracker.update([dict(bbox=(201,0,301,200)), dict(bbox=(1,0,101,200))], .2)
        self.assertEqual([person['track_id'] for person in later], [b, a])
        tracker.reset()
        new = tracker.update([dict(bbox=(0,0,100,200))], .3)
        self.assertGreater(new[0]['track_id'], max(a,b))

    def test_ambiguous_pair_does_not_share_one_id(self):
        from runtime.person_tracking import PersonTracker
        tracker = PersonTracker(.25, .1, 3, 1.)
        original = tracker.update([dict(bbox=(0,0,100,200))], 0.)[0]['track_id']
        persons = tracker.update([dict(bbox=(0,0,90,200)), dict(bbox=(10,0,100,200))], .1)
        ids = [person['track_id'] for person in persons]
        self.assertNotIn(original, ids)
        self.assertNotEqual(ids[0],ids[1])
