"""Hardware-free regression checks for face acquisition and autofocus."""
import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


state = load_module("target_state_regression", "control/target_state_machine.py")
focus = load_module("focus_regression", "control/auto_focus.py")


class ControlRegressionTests(unittest.TestCase):
    def test_face_bypasses_body_size_gate_at_near_and_far_sizes(self):
        for height in (800, 200):
            machine = state.TargetStateMachine()
            for index in range(3):
                result = machine.update((1000, 1000),
                                        {"bbox": (0, 0, 300, height)},
                                        {"bbox": (0, 0, 60, 60)}, now=index * .1)
            self.assertEqual(result.state, state.TargetState.ZOOM_FACE)
            self.assertEqual(result.target_type, "FACE")

    def test_face_survives_missing_body_detection(self):
        machine = state.TargetStateMachine()
        face = {"bbox": (0, 0, 250, 250)}
        for index in range(5):
            result = machine.update((1000, 1000), None, face, now=index * .1)
        self.assertEqual(result.state, state.TargetState.TRACK_FACE)
        self.assertFalse(result.target_lost)

    def test_body_acquisition_still_works_without_face(self):
        machine = state.TargetStateMachine()
        for now in range(5):
            result = machine.update((1000, 1000),
                                    {"bbox": (0, 0, 300, 500)}, None, now=now)
        self.assertEqual(result.state, state.TargetState.SEARCH_FACE)
        self.assertEqual(result.target_type, "PERSON")

    def test_focus_does_not_lock_while_lens_is_settling(self):
        controller = focus.AutoFocusController(object())
        controller.set_enabled(True)
        controller.busy = True
        self.assertEqual(controller.update(1000), "AUTO FOCUS RUNNING")
        self.assertFalse(controller.locked)


if __name__ == "__main__":
    unittest.main()
