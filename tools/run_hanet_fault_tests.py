#!/usr/bin/env python3
"""Offline HANET safety harness. Network and live video are denied by default."""
import argparse
from contextlib import ExitStack
import importlib.util
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'test')]


def cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from cases(item)
        else:
            yield item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regression', action='store_true')
    parser.add_argument('--json', type=Path)
    args = parser.parse_args()
    modules = ['test_hanet_motor_p0', 'test_hanet_persisted_recovery', 'test_hanet_ack_compatibility',
               'test_hanet_fault_injection', 'test_hanet_shutdown']
    if args.regression:
        modules += ['test_hanet_rpc', 'test_hanet_controller', 'test_auto_pan_tilt',
            'test_pan_follow_revision', 'test_auto_zoom', 'test_zoom_ownership',
            'test_zoom_latency_safety', 'test_runtime_waiting', 'test_control_regression',
            'test_runtime_architecture', 'test_target_tracking_p0', 'test_root_cause_revision',
            'test_opencv_player', 'test_startup_logging',
            'test_pipeline_profiling', 'test_hybrid_latency']
    suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
    # Report the unavailable optional backend explicitly; OpenCV (the deployed
    # HANET receiver) still runs all its lifecycle tests in this environment.
    if args.regression and importlib.util.find_spec('gi') is None:
        for case in cases(suite):
            if case.id().endswith('test_rtsp_unavailable_schedules_backoff_without_raising'):
                name = case._testMethodName
                setattr(case, name, unittest.skip('GI unavailable for this Python ABI')(getattr(case, name)))
    test_ids = [case.id() for case in cases(suite)]
    began = time.perf_counter()
    with ExitStack() as stack:
        stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('Live socket forbidden in offline tests')))
        stack.enter_context(patch('requests.sessions.Session.request', side_effect=AssertionError('Live HTTP forbidden in offline tests')))
        if importlib.util.find_spec('cv2') is not None:
            stack.enter_context(patch('cv2.VideoCapture', side_effect=AssertionError('Live capture forbidden in offline tests')))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    failed = {test.id() for test, _ in result.failures+result.errors}
    skipped = {test.id(): reason for test, reason in result.skipped}
    summary = {'tests_run': result.testsRun, 'passed': [name for name in test_ids if name not in failed and name not in skipped],
               'failed': sorted(failed), 'skipped': skipped,
               'elapsed_seconds': time.perf_counter()-began, 'network_guard': True,
               'python': sys.executable}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(summary, indent=2)+'\n')
    return not result.wasSuccessful()


if __name__ == '__main__':
    raise SystemExit(main())
