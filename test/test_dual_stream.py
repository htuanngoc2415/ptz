"""Independent display/inference streams and resolution mapping."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import Mock, patch
import numpy as np
from camera.stream import DualStreamPlayer
from runtime.display_mapping import display_result
from control.digital_ptz import DigitalPTZController


class DualStreamTests(unittest.TestCase):
    def test_routes_frames_and_disconnects_independently(self):
        ai, display, ai_lost, display_lost = Mock(), Mock(), Mock(), Mock()
        with patch('camera.stream.RTSPPlayer') as factory:
            player = DualStreamPlayer({'uri':'rtsp://test/sub','display_uri':'rtsp://test/main'},
                ai, display, disconnect_callback=ai_lost, display_disconnect_callback=display_lost)
        sub, main = factory.call_args_list
        self.assertEqual(sub.args[0]['uri'], 'rtsp://test/sub')
        self.assertEqual(main.args[0]['uri'], 'rtsp://test/main')
        main.args[1]('main image')
        display.assert_called_once_with('main image')
        ai.assert_not_called()
        main.args[3]()
        display_lost.assert_called_once()
        ai_lost.assert_not_called()
        sub.args[1]('sub image')
        ai.assert_called_once_with('sub image')

    def test_stops_display_even_if_ai_stop_raises(self):
        with patch('camera.stream.RTSPPlayer', side_effect=[Mock(), Mock()]):
            player = DualStreamPlayer({'uri':'rtsp://test/sub'}, Mock(), Mock())
        player.ai.stop.side_effect = RuntimeError()
        with self.assertRaises(RuntimeError):player.stop()
        player.display.stop.assert_called_once()

    def test_boxes_and_landmarks_scale_without_mutating_ai(self):
        detection = {'bbox':(10,20,30,40),'landmarks':[(12,24)]}
        original = dict(frame_shape=(100,200,3),frame_received=10,
                        persons=[detection],person_target=detection)
        mapped = display_result(original,(300,800,3),10.2,.8)
        self.assertEqual(mapped['person_target']['bbox'],(40,60,120,120))
        self.assertEqual(mapped['person_target']['landmarks'],[(48,72)])
        self.assertEqual(detection['bbox'],(10,20,30,40))
        self.assertEqual(display_result(original,(300,800,3),12,.8)['persons'],[])

    def test_digital_crop_center_maps_to_main_without_changing_ai_state(self):
        controller = DigitalPTZController(enabled=True)
        controller.update((100,200,3),{'bbox':(80,30,120,70)})
        controller.zoom=2
        controller.center_x=100
        controller.center_y=50
        _,transform=controller.render(np.zeros((300,800,3),dtype=np.uint8))
        self.assertEqual((transform['crop_x1'],transform['crop_y1']),(200,75))
        self.assertEqual((controller.center_x,controller.center_y),(100,50))

    def test_frozen_main_hides_fresh_sub_boxes_and_identity_panels(self):
        detection = {'bbox': (10,20,30,40)}
        original = dict(frame_shape=(100,200,3), frame_received=12.9,
                        persons=[detection], person_target=detection,
                        face_panel={'identity':'target'}, recognition={'identity':'target'})
        for main_received in (10., 13.1, float('nan')):
            mapped = display_result(original, (300,800,3), 13., .8,
                                    display_received=main_received)
            self.assertEqual(mapped['persons'], [])
            self.assertIsNone(mapped['person_target'])
            self.assertIsNone(mapped['face_panel'])
            self.assertIsNone(mapped['recognition'])
        recovered = display_result(original, (300,800,3), 13., .8,
                                   display_received=12.95)
        self.assertEqual(recovered['person_target']['bbox'], (40,60,120,120))
        self.assertEqual(original['persons'], [detection])
