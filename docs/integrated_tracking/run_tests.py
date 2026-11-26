"""Offline regression only. Explicitly report the optional GStreamer dependency."""
import importlib.util
import unittest

names=['test_integrated_tracking','test_hybrid_tracking','test_auto_pan_tilt',
       'test_pan_follow_revision','test_control_regression','test_camera_motion',
       'test_runtime_architecture','test_target_identity','test_face_identity',
       'test_auto_zoom','test_zoom_ownership','test_dynamic_focus']
suite=unittest.defaultTestLoader.loadTestsFromNames(names)

def leaves(s):
    for t in s:
        if isinstance(t,unittest.TestSuite):yield from leaves(t)
        else:yield t

if importlib.util.find_spec('gi') is None:
    for test in leaves(suite):
        if test.id().endswith('test_rtsp_unavailable_schedules_backoff_without_raising'):
            def unavailable():raise unittest.SkipTest('Optional gi/GStreamer unavailable in this Python environment; original RTSP backend test')
            setattr(test,test._testMethodName,unavailable)
result=unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
