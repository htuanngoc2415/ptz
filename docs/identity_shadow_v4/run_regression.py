"""Offline regression: deny camera/motor access during test execution."""
import unittest
from tools.identity_replay import offline_guard
names=['test_identity_shadow_batch','test_identity_shadow','test_dual_stream','test_hybrid_tracking','test_minor_overlap','test_crossing_retention','test_target_tracking_p0','test_reid_target','test_continuous_reid','test_control_regression','test_pan_follow_revision','test_zoom_latency_safety','test_hanet_motor_p0','test_target_identity','test_identity_replay','test_identity_recording','test_camera_motion','test_hybrid_latency','test_camera_entrypoints','test_runtime_architecture.ControlIntegrationTests.test_unavailable_camera_keeps_desktop_startup_alive']
with offline_guard():
 result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames(names))
raise SystemExit(not result.wasSuccessful())
