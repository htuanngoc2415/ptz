import unittest

from tools.stream_diagnostics import summarize


class StreamDiagnosticTests(unittest.TestCase):
    def test_media_drift_is_relative_not_sensor_latency(self):
        result = summarize([
            {'monotonic': 100., 'time_pos': 42.},
            {'monotonic': 120., 'time_pos': 54.},
        ], [75., 85.], 10)
        self.assertAlmostEqual(result['relative_lag_growth_seconds'], 8.)
        self.assertAlmostEqual(result['media_progress_ratio'], .6)
        self.assertIsNone(result['sensor_to_display_ms'])

    def test_pts_reset_invalidates_drift(self):
        result = summarize([
            {'monotonic': 100., 'time_pos': 42.},
            {'monotonic': 110., 'time_pos': 1.},
            {'monotonic': 120., 'time_pos': 11.},
        ], [], 0)
        self.assertTrue(result['pts_reset'])
        self.assertIsNone(result['relative_lag_growth_seconds'])
        self.assertIsNone(result['media_progress_ratio'])

    def test_missing_and_nonfinite_pts_are_not_latency_evidence(self):
        result = summarize([
            {'monotonic': 100., 'time_pos': None},
            {'monotonic': 110., 'time_pos': float('nan')},
            {'monotonic': 120., 'time_pos': 5.},
        ], [], 0)
        self.assertNotIn('relative_lag_growth_seconds', result)
        self.assertIsNone(result['screenshot_ms'])


if __name__ == '__main__':
    unittest.main()
