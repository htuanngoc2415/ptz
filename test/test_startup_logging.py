import io
import logging
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from types import SimpleNamespace

import event_logging


class StartupLoggingTests(unittest.TestCase):
    def test_startup_failure_reaches_terminal_and_log_without_credentials(self):
        output = io.StringIO()
        with patch('sys.stderr', output), \
                patch.object(event_logging.logger, 'handlers', [logging.NullHandler()]), \
                patch.object(event_logging.logger, 'log') as log:
            event_logging.event_print('IMPORT ERROR: rtsp://user:secret@camera/live', file=output)
        self.assertIn('IMPORT ERROR', output.getvalue())
        self.assertNotIn('secret', output.getvalue())
        log.assert_called_once()
        self.assertNotIn('secret', log.call_args.args[1])

    def test_routine_events_remain_file_only(self):
        with patch.object(event_logging.logger, 'handlers', [logging.NullHandler()]), \
                patch.object(event_logging.logger, 'log'), \
                patch('builtins.print') as console:
            event_logging.event_print('[ZOOM] idle')
        console.assert_not_called()


class GUIStartupTests(unittest.TestCase):
    def test_ai_starts_once_from_gui_callback(self):
        from gui.cctv_app import CCTVApp
        app = SimpleNamespace(running=True, _ai_started=False,
                              status_text=Mock(), ai_thread=Mock())
        CCTVApp.start_ai_worker(app)
        CCTVApp.start_ai_worker(app)
        app.ai_thread.start.assert_called_once()
        app.status_text.set.assert_called_once_with("Loading AI models...")

    def test_close_before_callback_does_not_start_ai(self):
        from gui.cctv_app import CCTVApp
        app = SimpleNamespace(running=False, _ai_started=False,
                              status_text=Mock(), ai_thread=Mock())
        CCTVApp.start_ai_worker(app)
        app.ai_thread.start.assert_not_called()
        self.assertFalse(app._ai_started)
