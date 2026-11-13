import unittest
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camera.mpv_player import MPVPlayer
from camera.stream import RTSPPlayer


class MPVPlayerTests(unittest.TestCase):
    def test_early_timestamp_waits_for_first_decoded_frame(self):
        image = np.zeros((18, 32, 3), dtype=np.uint8)
        callback = Mock()
        r = MPVPlayer({'uri': 'rtsp://camera/live'}, callback)
        player = Mock(time_pos=1.)
        player.screenshot_raw.side_effect = [SystemError('COMMAND_ERROR'), image]
        with patch.object(r._stop, 'is_set', side_effect=[False, False, True]), \
             patch.object(r._stop, 'wait'):
            r._capture(player)
        self.assertEqual(player.screenshot_raw.call_count, 2)
        callback.assert_called_once()
        self.assertIs(callback.call_args.args[0], image)

    def test_factory_selects_mpv_without_gstreamer(self):
        with patch('camera.mpv_player.MPVPlayer') as receiver:
            RTSPPlayer({'video_backend': 'mpv'}, Mock())
            receiver.assert_called_once()

    def test_open_uses_reference_options_and_escapes_credentials(self):
        r = MPVPlayer({'uri': 'rtsp://camera/live?subtype=0',
                       'username': 'a@b', 'password': 'x:y'}, Mock())
        r._mpv = SimpleNamespace(MPV=Mock())
        p = r._open()
        opts = r._mpv.MPV.call_args.kwargs
        self.assertEqual(opts['hwdec'], 'no')
        self.assertEqual(opts['cache'], 'no')
        self.assertEqual(opts['vo'], 'null')
        self.assertIn('1280', opts['vf'])
        p.play.assert_called_once_with('rtsp://a%40b:x%3Ay@camera/live?subtype=0')

    def test_repeated_pts_does_not_copy_another_frame(self):
        r = MPVPlayer({'uri': 'rtsp://camera/live'}, Mock())
        player = Mock()
        type(player).time_pos = property(lambda _: 1.)
        image = np.zeros((18, 32, 3), dtype=np.uint8)
        player.screenshot_raw.return_value = image
        with patch.object(r._stop, 'is_set', side_effect=[False, False, True]), \
             patch.object(r._stop, 'wait'):
            r._capture(player)
        player.screenshot_raw.assert_called_once()
        self.assertIs(r.frame_callback.call_args.args[0], image)

    def test_stalled_camera_disconnects_and_terminates(self):
        disconnected = Mock()
        r = MPVPlayer({'uri': 'rtsp://camera/live'}, Mock(), Mock(), disconnected)
        player = Mock()
        with patch.object(r, '_open', return_value=player), \
             patch.object(r, '_capture', side_effect=TimeoutError), \
             patch.object(r._stop, 'wait', side_effect=lambda _: r._stop.set()):
            r._run()
        player.terminate.assert_called_once()
        disconnected.assert_called_once()
        self.assertFalse(r.running)
        self.assertIsNone(r.player)

    def test_stop_interrupts_reconnect_wait(self):
        r = MPVPlayer({'uri': 'rtsp://camera/live'}, Mock())
        r._thread = Mock()
        r.stop()
        self.assertTrue(r._stop.is_set())
        r._thread.join.assert_called_once_with(timeout=5)

    def test_size_limit_failure_never_publishes_full_resolution(self):
        callback = Mock()
        r = MPVPlayer({'uri': 'rtsp://camera/live'}, callback)
        player = Mock(time_pos=1.)
        player.screenshot_raw.return_value = np.zeros((721, 1280, 3), dtype=np.uint8)
        with self.assertRaisesRegex(RuntimeError, 'size limits'):
            r._capture(player)
        callback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
