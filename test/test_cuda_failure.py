"""CUDA error containment with mock predictor/CUDA APIs; never allocates on GPU."""
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import numpy as np
from ai.detector import YOLODetector
from runtime.inference_worker import InferenceWorker

class CudaFailureTests(unittest.TestCase):
    def make(self):
        model=Mock();model.predictor=None
        with patch('ai.detector.YOLO',return_value=model):
            detector=YOLODetector('mock.pt')
        self.frame=np.zeros((20,20,3),dtype=np.uint8)
        return detector,model

    def test_oom_latches_once_and_does_not_clear_error_on_following_frames(self):
        z,model=self.make();model.predict.side_effect=RuntimeError('CUDA error: out of memory')
        with patch('torch.cuda.memory_allocated',return_value=40),patch('torch.cuda.memory_reserved',return_value=80),\
             patch('torch.cuda.mem_get_info',return_value=(100,200)),patch('telemetry.events.emit') as emit:
            for _ in range(20):self.assertEqual(z.detect(self.frame),[])
        model.predict.assert_called_once()
        self.assertEqual(z.last_error,z.fatal_error)
        self.assertIn('out of memory',z.last_error)
        self.assertEqual(sum(c.args[0]=='CUDA_ERROR' for c in emit.call_args_list),1)
        row=next(c.kwargs for c in emit.call_args_list if c.args[0]=='CUDA_MEMORY')
        self.assertEqual(row['driver_free_bytes'],100)
        self.assertEqual(row['frame_shape'],[20,20,3]);self.assertEqual(row['error_stage'],'predict')

    def test_diagnostic_failure_does_not_retry_inference_or_escape(self):
        z,model=self.make();model.predict.side_effect=RuntimeError('CUDA error: out of memory')
        with patch('torch.cuda.memory_allocated',side_effect=RuntimeError('broken context')):
            self.assertEqual(z.detect(self.frame),[]);self.assertEqual(z.detect(self.frame),[])
        model.predict.assert_called_once();self.assertIsNotNone(z.fatal_error)

    def test_generic_error_is_not_permanent_cuda_failure(self):
        z,model=self.make();model.predict.side_effect=[ValueError('invalid image'),[]]
        self.assertEqual(z.detect(self.frame),[]);self.assertEqual(z.last_error,'invalid image')
        self.assertEqual(z.detect(self.frame),[]);self.assertIsNone(z.last_error)
        self.assertIsNone(z.fatal_error);self.assertEqual(model.predict.call_count,2)

    def test_async_cuda_failure_in_box_transfer_discards_partial_results(self):
        z,model=self.make()
        good=Mock();good.cls=[Mock()];good.cls[0].item.return_value=0
        good.conf=[Mock()];good.conf[0].item.return_value=.9
        good.xyxy=[Mock()];good.xyxy[0].detach.return_value.cpu.return_value.numpy.return_value=[1,1,10,10]
        bad=Mock();bad.cls=[Mock()];bad.cls[0].item.side_effect=RuntimeError('CUDA illegal memory access')
        model.predict.return_value=[SimpleNamespace(boxes=[good,bad])]
        with patch('torch.cuda.memory_allocated',return_value=0),patch('torch.cuda.memory_reserved',return_value=0),\
             patch('torch.cuda.mem_get_info',side_effect=RuntimeError('bad CUDA')):
            self.assertEqual(z.detect(self.frame),[]);self.assertEqual(z.detect(self.frame),[])
        self.assertIn('illegal memory access',z.fatal_error);model.predict.assert_called_once()

    def test_worker_disables_control_and_invalidates_once_until_success(self):
        app=SimpleNamespace(person_detector=SimpleNamespace(last_error='CUDA OOM',fatal_error='CUDA OOM'),
            auto_zoom=Mock(),reid_target=Mock(),reset_tracking_worker=Mock(),set_status=Mock(),
            notify_auto_zoom_disabled=Mock())
        for _ in range(10):self.assertTrue(InferenceWorker.handle_detector_failure(app))
        app.auto_zoom.request_stop.assert_called_once_with(reason='DETECTOR_ERROR')
        app.auto_zoom.set_enabled.assert_called_once_with(False,wait=False)
        app.reid_target.invalidate.assert_called_once_with('CUDA OOM')
        app.reset_tracking_worker.assert_called_once()
        app.person_detector.last_error=None
        self.assertFalse(InferenceWorker.handle_detector_failure(app))
        app.auto_zoom.set_enabled.assert_called_once() # never automatically re-enable motor

if __name__=='__main__':unittest.main()
