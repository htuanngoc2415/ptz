import unittest
from unittest.mock import Mock
from control.face_framing import FaceFramingLock
from control.auto_focus import AutoFocusController
from control.digital_ptz import DigitalPTZController

class FinalFramingTests(unittest.TestCase):
    def test_ratio_confirmation_fluctuation_and_short_miss(self):
        lock = FaceFramingLock(.18, 3, 2)
        for index, ratio in enumerate([.17,.181,.184,.186]):
            self.assertEqual(lock.update(ratio,index*.1,True,True), index == 3)
        for index, ratio in enumerate([.19,.17,.20,None,None,None]):
            self.assertTrue(lock.update(ratio,.4+index*.1,True,True))
        self.assertFalse(lock.update(None,2.7,True,True))

    def test_confirmation_resets_and_disable_or_target_loss_releases(self):
        lock = FaceFramingLock(.18,3,2)
        for ratio in [.19,.19,None,.19,.1,.19]:
            self.assertFalse(lock.update(ratio,0,True,True))
        for enabled, target in [(False,True),(True,False)]:
            for _ in range(3): lock.update(.19,1,True,True)
            self.assertTrue(lock.locked)
            self.assertFalse(lock.update(.19,2,enabled,target))

    def test_digital_hold_preserves_center_and_scale(self):
        digital = DigitalPTZController(enabled=True)
        digital.update((1080,1920),{'bbox':(100,100,300,400)},now=0)
        before = (digital.center_x,digital.center_y,digital.zoom)
        for target in [{'bbox':(900,400,1300,900)},None]:
            digital.update((1080,1920),target,now=10,hold=True)
            self.assertEqual(before,(digital.center_x,digital.center_y,digital.zoom))

    def test_focus_high_quality_does_not_lock_then_low_triggers(self):
        af = AutoFocusController(Mock())
        af.set_enabled(True)
        af._start_autofocus = Mock()
        self.assertEqual(af.update(420), 'SHARPNESS OK')
        self.assertFalse(af.locked)
        af._start_autofocus.assert_not_called()
        af.update(180)
        af._start_autofocus.assert_called_once()

if __name__ == '__main__': unittest.main()
