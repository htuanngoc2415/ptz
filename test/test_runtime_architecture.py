"""Deterministic tests of new policies; no models, camera, CGI or RTSP are opened."""
import json
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
import torch
from ai.reid.gallery import TargetEmbeddingGallery, illumination_normalized, target_variants
from ai.quality.image_quality import ImageQualityAnalyzer
from ai.quality.focus_quality import FocusQualityAnalyzer
from ai.reid_target import ReIDTargetSelector
from runtime.frame_manager import FrameManager
from runtime.reconnect import ReconnectBackoff
from control.orchestrator import ControlOrchestrator
from control.auto_focus import AutoFocusController
from camera.capabilities import CameraImageCapabilities
from telemetry.benchmark import record_trial
import config


class RuntimeTests(unittest.TestCase):
    def test_mailbox_only_returns_latest_and_never_invents_capture_timestamp(self):
        m = FrameManager()
        for i in range(20): m.publish(i, received=float(i))
        f = m.latest(now=20.)
        self.assertEqual(f.image, 19)
        self.assertEqual(f.frame_id, 20)
        self.assertEqual(f.age_ms(20.), 1000)
        self.assertIsNone(f.capture_timestamp)
        self.assertIsNone(m.latest(after=20))
        m.clear()
        self.assertIsNone(m.latest())

    def test_atomic_mutual_exclusion_and_settling(self):
        c = ControlOrchestrator()
        self.assertTrue(c.acquire('image', now=0))
        self.assertFalse(c.acquire('zoom', now=0))
        self.assertFalse(c.acquire('focus', now=0))
        c.release('image', settle=2, now=1)
        self.assertFalse(c.acquire('focus', now=2))
        self.assertTrue(c.acquire('focus', now=3))
        c.release('zoom', now=4)  # wrong owner cannot release
        self.assertFalse(c.acquire('zoom', now=4))
        c.release('focus', now=5)
        self.assertTrue(c.acquire('zoom', now=5))

    def test_manual_intent_blocks_new_automatic_leases(self):
        c = ControlOrchestrator()
        c.manual = True
        self.assertFalse(c.acquire('zoom', now=0))
        self.assertTrue(c.acquire('manual', now=0))

    def test_quality_distinguishes_backlight_from_uniform_darkness(self):
        frame = np.full((100,100,3), 220, np.uint8)
        frame[20:80,20:60] = 25
        q = ImageQualityAnalyzer().analyze(frame, {'bbox': (20,20,60,80)})
        self.assertTrue(q['backlit'])
        self.assertEqual(q['shadow_ratio'], 1.)
        self.assertFalse(ImageQualityAnalyzer().analyze(frame*0+25, {'bbox': (20,20,60,80)})['backlit'])

    def test_flat_roi_not_declared_focused(self):
        q = FocusQualityAnalyzer().analyze(np.full((100,100,3),100,np.uint8), {'bbox': (0,0,100,100)})
        self.assertFalse(q['good'])
        self.assertEqual(q['edge_density'], 0)

    def test_gallery_matches_nonprimary_template(self):
        embeddings = torch.eye(512)[:2]
        gallery = TargetEmbeddingGallery(embeddings)
        self.assertEqual(gallery.score(embeddings[1:2]).item(), 1.)
        with self.assertRaises(ValueError): TargetEmbeddingGallery(torch.zeros(1,512))

    def test_normalization_and_augmentation_preserve_raw_crop(self):
        raw = np.full((64,32,3),30,np.uint8)
        before = raw.copy()
        norm = illumination_normalized(raw)
        self.assertGreater(norm.mean(), raw.mean())
        self.assertEqual(norm.shape, raw.shape)
        self.assertEqual(len(target_variants(raw)), 5)
        np.testing.assert_array_equal(raw,before)

    def test_capabilities_do_not_assume_wdr(self):
        c = CameraImageCapabilities.from_results({'blc_level':'verified','wdr_sensor':'unsupported','wdr_level_sensor':'verified'})
        self.assertTrue(c.supports_blc)
        self.assertFalse(c.supports_wdr)
        self.assertFalse(c.supports_gain)

    def test_backoff_bounded_and_reset(self):
        b = ReconnectBackoff(1,4)
        self.assertEqual([b.next_delay() for _ in range(5)], [1,2,4,4,4])
        b.reset()
        self.assertEqual(b.next_delay(),1)

    def test_focus_attempt_limit_prevents_hunting(self):
        c = AutoFocusController(Mock(), max_attempts=2, cooldown=0)
        c.set_enabled(True)
        c.attempts = 2
        with patch.object(c, '_start_autofocus') as start:
            self.assertEqual(c.update(0), 'ATTEMPT_LIMIT')
            start.assert_not_called()
        c.reset()
        self.assertEqual(c.attempts,0)

    def test_benchmark_unknown_outcomes_stay_null(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'trial.jsonl'
            record_trial(path,'backlight',{'acquisition_time_ms':123})
            row = json.loads(path.read_text())
            self.assertIsNone(row['wrong_target'])
            self.assertIsNone(row['identity_success'])
            with self.assertRaises(ValueError): record_trial(path,'backlight',{'wrong_target':1})


class RetentionTests(unittest.TestCase):
    def make(self):
        cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        cfg.TARGET_CONTINUOUS_REID = False
        cfg.REID_NORMALIZATION_ENABLED = False
        cfg.REID_DIAGNOSTICS = False
        cfg.TARGET_LOST_FRAMES = 2
        cfg.REID_LOST_GRACE_SEC = 1.
        self.score = .95
        def extract(crops):
            e = torch.zeros(len(crops),512)
            e[:,0] = self.score
            e[:,1] = (1-self.score**2)**.5
            return e
        model = Mock()
        model.extract_embeddings.side_effect = extract
        c = ReIDTargetSelector(cfg, model)
        c.embedding = torch.eye(512)[0]
        c.error = None
        return c

    def test_acquire_retention_grace_and_reacquire(self):
        c = self.make()
        frame = np.full((300,200,3),120,np.uint8)
        p = {'bbox':(10,10,80,200)}
        for t in (0., .1): self.assertIsNone(c.select_person(frame,[p],t))
        self.assertIsNotNone(c.select_person(frame,[p],.2))
        self.score = .1
        self.assertIsNotNone(c.select_person(frame,[p],1.3))
        self.assertTrue(c.locked)
        self.assertLess(c.cfg.REID_KEEP_THRESHOLD,c.cfg.REID_ACQUIRE_THRESHOLD)
        for t in (1.4,1.5):
            c.select_person(frame,[],t)
            self.assertFalse(c.finish_frame(None,None))
        c.select_person(frame,[],2.5)
        self.assertTrue(c.finish_frame(None,None))
        self.assertTrue(c.reacquiring)
        self.score = .95
        for t in (2.6,2.7,2.8): c.select_person(frame,[p],t)
        self.assertTrue(c.locked)
        self.assertFalse(c.reacquiring)
        self.assertEqual(c.track_id,2)

    def test_normalized_path_can_recover_dark_candidate(self):
        c = self.make()
        c.cfg.REID_NORMALIZATION_ENABLED = True
        raw = torch.zeros(1,512); raw[0,0]=.4; raw[0,1]=(1-.4**2)**.5
        bright = torch.zeros(1,512); bright[0,0]=.9; bright[0,1]=(1-.9**2)**.5
        c.reid.extract_embeddings.side_effect = [raw, bright]
        p = {'bbox':(10,10,80,200)}
        c.select_person(np.full((300,200,3),30,np.uint8),[p],0.)
        self.assertAlmostEqual(p['raw_similarity'],.4,places=5)
        self.assertAlmostEqual(p['normalized_similarity'],.9,places=5)
        self.assertAlmostEqual(p['reid_score'],.9,places=5)

    def test_duplicate_frame_cannot_confirm(self):
        c = self.make()
        for _ in range(4): c.select_person(np.zeros((300,200,3),np.uint8),[{'bbox':(10,10,80,200)}],0.)
        self.assertFalse(c.locked)
        self.assertEqual(c.confirm,1)

class ControlIntegrationTests(unittest.TestCase):
    def test_focus_does_not_start_from_stale_measurement(self):
        c = AutoFocusController(Mock(), cooldown=0)
        c.set_enabled(True)
        with patch('control.auto_focus.time.monotonic', return_value=10), patch.object(c, '_start_autofocus') as start:
            self.assertEqual(c.update(0, frame_time=9), 'WAIT_FRESH_FRAME')
            start.assert_not_called()

    def test_focus_cannot_acquire_while_zoom_owns_motor(self):
        o = ControlOrchestrator()
        o.acquire('zoom')
        c = AutoFocusController(Mock(), cooldown=0, orchestrator=o)
        c.set_enabled(True)
        c.update(0)
        c.ptz.autofocus.assert_not_called()
        self.assertFalse(c.busy)
        self.assertEqual(c.attempts,0)

    def test_manual_worker_stops_and_releases_on_close(self):
        import threading
        from control.manual import ManualController
        began, stopped = threading.Event(), threading.Event()
        camera = Mock()
        camera.command.side_effect = lambda *args: (began.set() or True, '')
        camera.stop.side_effect = lambda: (stopped.set() or True, '')
        o = ControlOrchestrator()
        m = ManualController(camera, Mock(), Mock(), o, Mock(), settle=0)
        try:
            m.request(9,50)
            self.assertTrue(began.wait(1))
            self.assertEqual(o.owner,'manual')
            self.assertFalse(o.acquire('focus'))
        finally:
            m.close()
            m._thread.join(2)
        self.assertTrue(stopped.is_set())
        self.assertFalse(m._thread.is_alive())
        self.assertIsNone(o.owner)

    def test_unavailable_camera_keeps_desktop_startup_alive(self):
        from gui.cctv_app import CCTVApp
        root = Mock()
        with patch('gui.cctv_app.tk.StringVar'), patch('gui.cctv_app.tk.BooleanVar'), \
             patch('gui.cctv_app.tk.IntVar'), patch.object(CCTVApp,'build_ui'), \
             patch('gui.cctv_app.threading.Thread'), patch('gui.cctv_app.DualStreamPlayer') as stream, \
             patch('app.application.PersonDetector') as model:
            stream.return_value.start.side_effect = RuntimeError('camera unavailable')
            app = CCTVApp(root)
            self.assertTrue(app.running)
            self.assertFalse(app._ai_started)
            root.after.assert_any_call(100, app.start_ai_worker)
            root.protocol.assert_called_with('WM_DELETE_WINDOW', app.on_close)
            model.assert_not_called()  # model initialization belongs to AI worker

    def test_rtsp_unavailable_schedules_backoff_without_raising(self):
        from camera.rtsp_player import RTSPPlayer
        disconnected = Mock()
        player = RTSPPlayer({}, Mock(), Mock(), disconnected)
        with patch.object(player, '_start_pipeline', side_effect=RuntimeError('offline')), \
             patch('camera.rtsp_player.threading.Timer') as timer:
            player.start()
            self.assertEqual(timer.call_args.args[0],1.)
            disconnected.assert_called_once()
            player.stop()
            timer.return_value.cancel.assert_called_once()

class DiagnosticsTests(unittest.TestCase):
    def test_redaction_masks_url_credentials_and_camera_query_password(self):
        from event_logging import redact
        message = redact('rtsp://demo:secret@localhost/stream http://localhost/action?pwd=abcdef&action=get')
        self.assertNotIn('secret',message)
        self.assertNotIn('abcdef',message)
        self.assertIn('action=get',message)

    def test_numeric_control_error_is_not_logged_as_error(self):
        import event_logging
        with patch.object(event_logging.logger, 'handlers', [object()]), patch.object(event_logging.logger,'log') as log:
            event_logging.event_print('[ZOOM] error=-0.35 command=OUT')
            import logging
            self.assertEqual(log.call_args.args[0], logging.INFO)

    def test_event_redaction_preserves_valid_json(self):
        from telemetry.events import emit
        with patch('telemetry.events.logging.getLogger') as logger:
            emit('TEST_ERROR', error='http://localhost/cgi?pwd=secret')
            payload = logger.return_value.info.call_args.args[1]
            data = json.loads(payload)
            self.assertNotIn('secret',data['error'])
            self.assertIn('timestamp',data)

    def test_nonfinite_diagnostic_cannot_break_controller_logging(self):
        from telemetry.events import emit
        with patch('telemetry.events.logging.getLogger') as logger:
            emit('FOCUS_START', sharpness=float('nan'))
            data = json.loads(logger.return_value.info.call_args.args[1])
            self.assertIsNone(data['sharpness'])
