import os
import threading
import time
import unittest
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from camera.rtsp_player import CPUStreamPlayer, Gst
from camera.stream import RTSPPlayer
from camera.dahua_rpc import ZoomRPC


class CPUStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gst.init(None)

    def test_rgb_padding_lifetime_and_channels(self):
        caps = Gst.Caps.from_string('video/x-raw,format=RGB,width=3,height=2,framerate=25/1')
        buf = Gst.Buffer.new_allocate(None, 24, None)
        buf.fill(0, bytes(range(24)))
        frame = CPUStreamPlayer.copy_rgb(Gst.Sample.new(buf, caps, None, None))
        buf.fill(0, bytes(24))
        np.testing.assert_array_equal(frame[0, 0], [0, 1, 2])
        np.testing.assert_array_equal(frame[1, 0], [12, 13, 14])
        self.assertTrue(frame.flags.owndata)
        self.assertTrue(frame.flags.c_contiguous)

    def test_truncated_buffer_rejected(self):
        caps = Gst.Caps.from_string('video/x-raw,format=RGB,width=3,height=2,framerate=25/1')
        buf = Gst.Buffer.new_allocate(None, 20, None)
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            CPUStreamPlayer.copy_rgb(Gst.Sample.new(buf, caps, None, None))

    def test_video_meta_offset_and_stride_override_caps(self):
        import gi
        gi.require_version('GstVideo', '1.0')
        from gi.repository import GstVideo
        caps = Gst.Caps.from_string('video/x-raw,format=RGB,width=3,height=2,framerate=25/1')
        buf = Gst.Buffer.new_allocate(None, 36, None)
        buf.fill(0, bytes(range(36)))
        GstVideo.buffer_add_video_meta_full(buf, GstVideo.VideoFrameFlags.NONE,
            GstVideo.VideoFormat.RGB, 3, 2, 1, [4,0,0,0], [16,0,0,0])
        frame = CPUStreamPlayer.copy_rgb(Gst.Sample.new(buf, caps, None, None))
        np.testing.assert_array_equal(frame[0,0], [4,5,6])
        np.testing.assert_array_equal(frame[1,0], [20,21,22])

    def test_explicit_decoder_and_bounded_queues(self):
        p = Gst.parse_launch(CPUStreamPlayer.pipeline_description({}))
        try:
            self.assertEqual(p.get_by_name('decoder').get_factory().get_name(), 'avdec_h264')
            q = p.get_by_name('raw_queue')
            self.assertEqual(q.get_property('max-size-buffers'), 2)
            self.assertEqual(q.get_property('max-size-bytes'), 0)
            self.assertEqual(q.get_property('max-size-time'), 0)
            self.assertEqual(int(q.get_property('leaky')), 2)
            sink = p.get_by_name('gui_sink')
            self.assertFalse(sink.get_property('emit-signals'))
            self.assertTrue(sink.get_property('drop'))
            self.assertEqual(sink.get_property('max-buffers'), 1)
        finally:
            p.set_state(Gst.State.NULL)

    def test_opt_in_factory(self):
        self.assertIsInstance(RTSPPlayer({'video_backend':'gstreamer_cpu'}, Mock()), CPUStreamPlayer)

    def test_failure_retry_and_shutdown_interrupt(self):
        disconnected = Mock()
        receiver = CPUStreamPlayer({'reconnect_initial':.01}, Mock(), Mock(), disconnected)
        with patch.object(receiver, '_open_cpu', side_effect=RuntimeError('private URI')):
            receiver.start()
            deadline = time.monotonic()+2
            while not disconnected.called and time.monotonic()<deadline:
                time.sleep(.01)
            receiver.stop()
        self.assertTrue(disconnected.called)
        self.assertFalse(receiver._thread.is_alive())
        self.assertIsNone(receiver.player)
        receiver.start()
        self.assertFalse(receiver._thread.is_alive())

    def test_slow_consumer_does_not_backpressure_source(self):
        p = Gst.parse_launch('videotestsrc is-live=true ! video/x-raw,format=RGB,width=32,height=18,framerate=25/1 '
            '! appsink name=sink sync=false max-buffers=1 drop=true enable-last-sample=false')
        sink = p.get_by_name('sink')
        p.set_state(Gst.State.PLAYING)
        try:
            first = sink.emit('try-pull-sample', Gst.SECOND).get_buffer().pts
            time.sleep(.4)
            newest = sink.emit('try-pull-sample', Gst.SECOND).get_buffer().pts
            self.assertGreater(newest-first, .25*Gst.SECOND)
        finally:
            p.set_state(Gst.State.NULL)


class ReadOnlyTests(unittest.TestCase):
    def test_controller_read_only_close_releases_worker_without_writes(self):
        from camera.ptz_controller import PTZController
        controller = PTZController('localhost', 'u', 'p', autoconnect=False)
        rpc = Mock(read_only=True)
        controller._rpc = rpc
        controller._zoom_config = {'original': 'never_changed'}
        controller.close()
        rpc.stop_all.assert_not_called()
        rpc._call.assert_not_called()
        rpc.close.assert_called_once()
        self.assertFalse(controller._command_worker.thread.is_alive())

    def test_all_write_methods_rejected_before_network(self):
        rpc = ZoomRPC.__new__(ZoomRPC)
        rpc.read_only = True
        for method in ('ptz.start', 'ptz.stop', 'ptz.moveAbsolutely',
                       'configManager.setTemporaryConfig', 'unknown.newWrite'):
            with self.subTest(method=method), self.assertRaisesRegex(RuntimeError, 'Read-only'):
                rpc._call(method, {})

    def test_environment_forces_rpc_read_only(self):
        session = Mock()
        with patch.dict(os.environ, {'PTZ_BENCHMARK_READ_ONLY':'1'}), \
             patch.object(ZoomRPC, '_login'), patch.object(ZoomRPC, '_instance'):
            rpc = ZoomRPC('localhost', 80, 'u', 'p', session_factory=lambda:session)
            self.assertTrue(rpc.read_only)
            rpc.close()
            session.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
