"""All executable camera tools must use the shared 37x control path."""
from pathlib import Path
import json
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools import camera_diagnostics

ROOT = Path(__file__).resolve().parents[1]


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        # Diagnostics tests use an explicit protocol fixture, never the live config.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        folder = Path(temporary.name)
        (folder / 'camera.json').write_text(json.dumps({
            'ptz_backend':'dahua', 'ptz_ip':'test',
            'ptz_username':'test', 'ptz_password':'test'}))
        patcher = patch.object(camera_diagnostics, 'ROOT', folder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_legacy_gui_launchers_open_root_application(self):
        for name in ('test/camera.py', 'test/detect.py', 'test/detect_v2.py',
                     'test/ha_ptz/main.py'):
            with self.subTest(name=name), patch('runpy.run_path') as launch:
                exec(compile((ROOT / name).read_text(), name, 'exec'),
                     {'__name__': '__main__', '__file__': str(ROOT / name)})
                launch.assert_called_once_with(str(ROOT / 'main.py'), run_name='__main__')

    def test_diagnostic_movement_requires_explicit_execute(self):
        with patch.object(camera_diagnostics, 'create_controller') as camera:
            with self.assertRaises(SystemExit):
                camera_diagnostics.main(['--zoom', '19'])
            camera.assert_not_called()

    def test_read_only_diagnostics_never_stop_motors(self):
        with patch.object(camera_diagnostics, 'create_controller') as factory:
            camera = factory.return_value
            camera.read_status.return_value = {'zoom': 12., 'focus': 12000.}
            camera_diagnostics.main([])
            camera.close.assert_not_called()
            camera._rpc.close.assert_called_once_with(stop_motors=False)
            camera.command.assert_not_called()

    def test_benchmark_records_feedback_and_closes_on_failure(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(camera_diagnostics, 'create_controller') as factory:
            camera = factory.return_value
            camera.move_zoom.return_value = (False, 'timeout')
            camera.read_status.return_value = {'zoom': 18.7, 'focus': 12000.}
            output = Path(directory) / 'lens.csv'
            with self.assertRaises(RuntimeError):
                camera_diagnostics.main(['--execute', '--levels', '19,37', '--output', str(output)])
            camera.move_zoom.assert_called_once_with(19.)
            camera.close.assert_called_once()
            self.assertIn('18.7', output.read_text())


if __name__ == '__main__':
    unittest.main()
