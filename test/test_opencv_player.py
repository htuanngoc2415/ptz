"""RTSP lifecycle tests without opening a camera."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import threading
import unittest
from unittest.mock import Mock, patch
import numpy as np
from camera.opencv_player import OpenCVPlayer


class OpenCVPlayerTests(unittest.TestCase):
    def test_rgb_contract_and_release_on_stop(self):
        received = []
        ready = threading.Event()
        capture = Mock()
        capture.isOpened.return_value = True
        capture.read.return_value = (True, np.array([[[10, 20, 30]]], dtype=np.uint8))
        player = None
        def consume(frame):
            received.append(frame.tolist())
            player._stop.set()
            ready.set()
        player = OpenCVPlayer({'uri':'rtsp://camera/live'}, consume)
        with patch('camera.opencv_player.cv2.VideoCapture', return_value=capture):
            player.start()
            self.assertTrue(ready.wait(1))
            player.stop()
        self.assertEqual(received, [[[[30, 20, 10]]]])
        capture.release.assert_called_once()
        self.assertFalse(player.diagnostics()['worker_alive'])

    def test_failed_connection_clears_mailbox_and_backoff_is_interruptible(self):
        disconnected = threading.Event()
        capture = Mock()
        capture.isOpened.return_value = False
        player = OpenCVPlayer({'uri':'rtsp://camera/live','reconnect_initial':30.,'reconnect_max':30.},
                              Mock(), disconnect_callback=disconnected.set)
        with patch('camera.opencv_player.cv2.VideoCapture', return_value=capture):
            player.start()
            self.assertTrue(disconnected.wait(1))
            player.stop()
        self.assertEqual(player.diagnostics()['counts']['failures'], 1)
        self.assertFalse(player.diagnostics()['worker_alive'])

    def test_credentials_are_url_encoded(self):
        player = OpenCVPlayer({'uri':'rtsp://camera:554/live','username':'a@b',
                               'password':'a:b/c'}, Mock())
        self.assertEqual(player._uri(), 'rtsp://a%40b:a%3Ab%2Fc@camera:554/live')
