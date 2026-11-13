#!/usr/bin/env python3

from __future__ import annotations
from event_logging import event_print as print, redact

import sys
import threading
from runtime.reconnect import ReconnectBackoff

import numpy as np


# ============================================================
# GSTREAMER IMPORT
# ============================================================

try:
    import gi

    gi.require_version(
        "Gst",
        "1.0"
    )

    from gi.repository import Gst

except (ImportError, ValueError) as exc:

    print(
        "Không import được GStreamer GI.\n"
        "\n"
        "Cài bằng:\n"
        "sudo apt install "
        "python3-gi "
        "gir1.2-gstreamer-1.0\n"
        "\n"
        f"Chi tiết: {exc}",
        file=sys.stderr,
    )

    raise SystemExit(2)


# ============================================================
# RTSP PLAYER
# ============================================================

class RTSPPlayer:
    """
    GStreamer RTSP receiver.

    Pipeline:

        RTSP
          ↓
        playbin
          ↓
        queue
          ↓
        videoconvert
          ↓
        RGB
          ↓
        appsink
          ↓
        NumPy frame

    frame_callback receives:

        frame_rgb: np.ndarray
        shape = (H, W, 3)
        dtype = uint8
    """

    def __init__(
        self,
        config: dict,
        frame_callback,
        status_callback=None,
        disconnect_callback=None,
    ):
        self.disconnect_callback = disconnect_callback
        self._retry_lock = threading.RLock()
        self._retry_timer = None
        self._closed = False
        self._backoff = ReconnectBackoff(config.get("reconnect_initial", 1.), config.get("reconnect_max", 30.))
        self.config = config

        self.frame_callback = (
            frame_callback
        )

        self.status_callback = (
            status_callback
        )

        self.player = None
        self._bus = None
        self._bus_handler = None
        self.appsink = None

        self.running = False

    # ========================================================
    # STATUS
    # ========================================================

    def _status(
        self,
        message: str
    ) -> None:

        message = redact(message)
        if self.status_callback is not None:

            try:
                self.status_callback(
                    message
                )

            except Exception as exc:

                print(
                    "[RTSP] status callback error:",
                    exc,
                    file=sys.stderr
                )

    # ========================================================
    # START
    # ========================================================

    def start(self) -> None:
        with self._retry_lock:
            if self._closed:
                return
            try:
                self._start_pipeline()
            except Exception as exc:
                self._status(f"RTSP unavailable ({type(exc).__name__}); reconnect scheduled")
                self._schedule_reconnect()

    def _schedule_reconnect(self):
        with self._retry_lock:
            if self._closed or self._retry_timer is not None:
                return
            if self.disconnect_callback is not None:
                self.disconnect_callback()
            delay = self._backoff.next_delay()
            self._status(f"RTSP disconnected; retry in {delay:.1f}s")
            self._retry_timer = threading.Timer(delay, self._reconnect)
            self._retry_timer.daemon = True
            self._retry_timer.start()

    def _remove_bus_watch(self):
        if self._bus is not None:
            if self._bus_handler is not None:
                self._bus.disconnect(self._bus_handler)
            self._bus.remove_signal_watch()
            self._bus = None
            self._bus_handler = None

    def _reconnect(self):
        with self._retry_lock:
            self._retry_timer = None
            if self._closed:
                return
            self._remove_bus_watch()
            if self.player is not None:
                self.player.set_state(Gst.State.NULL)
                self.player = None
            self.running = False
            self.start()

    def _start_pipeline(self) -> None:

        if self.running:
            return

        Gst.init(None)

        # ----------------------------------------------------
        # PLAYBIN
        # ----------------------------------------------------

        self.player = (
            Gst.ElementFactory.make(
                "playbin",
                "camera-player"
            )
        )

        if self.player is None:

            raise RuntimeError(
                "Không tạo được GStreamer playbin"
            )

        # ----------------------------------------------------
        # VIDEO SINK
        # ----------------------------------------------------

        sink_description = (
            "queue "
            "leaky=downstream "
            "max-size-buffers=2 "
            "! "
            # Bound downstream memory/CPU work before copying RGB into Python.
            # Keep the full view and square pixels; the RTSP URI is unchanged.
            "videoscale ! video/x-raw,width=(int)[1,1280],height=(int)[1,720],pixel-aspect-ratio=1/1 ! "
            "videoconvert "
            "! "
            "video/x-raw,format=RGB "
            "! "
            "appsink "
            "name=gui_sink "
            "emit-signals=true "
            "sync=false "
            "max-buffers=1 "
            "drop=true"
        )

        video_sink = (
            Gst.parse_bin_from_description(
                sink_description,
                True
            )
        )

        self.appsink = (
            video_sink.get_by_name(
                "gui_sink"
            )
        )

        if self.appsink is None:

            raise RuntimeError(
                "Không tạo được GStreamer appsink"
            )

        self.appsink.connect(
            "new-sample",
            self.on_new_sample
        )

        # ----------------------------------------------------
        # RTSP URI
        # ----------------------------------------------------

        uri = self.config.get(
            "uri"
        )

        if not uri:

            raise ValueError(
                "RTSP config không có 'uri'"
            )

        self.player.set_property(
            "uri",
            uri
        )

        self.player.set_property(
            "video-sink",
            video_sink
        )

        # ----------------------------------------------------
        # SOURCE CONFIG
        # ----------------------------------------------------

        self.player.connect(
            "source-setup",
            self.on_source_setup
        )

        # ----------------------------------------------------
        # GST BUS
        # ----------------------------------------------------

        bus = self.player.get_bus()

        self._bus = bus
        bus.add_signal_watch()

        self._bus_handler = bus.connect(
            "message",
            self.on_bus_message
        )

        # ----------------------------------------------------
        # PLAY
        # ----------------------------------------------------

        result = self.player.set_state(
            Gst.State.PLAYING
        )

        if (
            result
            ==
            Gst.StateChangeReturn.FAILURE
        ):

            self.player.set_state(
                Gst.State.NULL
            )

            raise RuntimeError(
                "Không thể PLAY RTSP"
            )

        self.running = True

        self._status(
            "RTSP starting..."
        )

    # ========================================================
    # RTSP SOURCE CONFIG
    # ========================================================

    def on_source_setup(
        self,
        player,
        source
    ) -> None:

        # ----------------------------------------------------
        # LATENCY
        # ----------------------------------------------------

        latency = int(
            self.config.get(
                "latency_ms",
                200
            )
        )

        if source.find_property(
            "latency"
        ):

            source.set_property(
                "latency",
                latency
            )

        # ----------------------------------------------------
        # DROP OLD DATA
        # ----------------------------------------------------

        if source.find_property(
            "drop-on-latency"
        ):

            source.set_property(
                "drop-on-latency",
                True
            )

        # ----------------------------------------------------
        # TCP
        # ----------------------------------------------------
        #
        # GstRtsp.RTSPLowerTrans.TCP = 4
        #
        # Keep numeric value to preserve compatibility with
        # the original implementation.
        # ----------------------------------------------------

        if source.find_property(
            "protocols"
        ):

            source.set_property(
                "protocols",
                4
            )

        # ----------------------------------------------------
        # AUTH
        # ----------------------------------------------------

        username = self.config.get(
            "username",
            ""
        )

        password = self.config.get(
            "password",
            ""
        )

        if username:

            if source.find_property(
                "user-id"
            ):

                source.set_property(
                    "user-id",
                    username
                )

        if password:

            if source.find_property(
                "user-pw"
            ):

                source.set_property(
                    "user-pw",
                    password
                )

    # ========================================================
    # NEW FRAME
    # ========================================================

    def on_new_sample(
        self,
        sink
    ):

        sample = sink.emit(
            "pull-sample"
        )

        if sample is None:

            return Gst.FlowReturn.ERROR

        caps = sample.get_caps()

        if caps is None:

            return Gst.FlowReturn.ERROR

        structure = (
            caps.get_structure(0)
        )

        width = structure.get_value(
            "width"
        )

        height = structure.get_value(
            "height"
        )

        buffer = sample.get_buffer()

        if buffer is None:

            return Gst.FlowReturn.ERROR

        success, map_info = buffer.map(
            Gst.MapFlags.READ
        )

        if not success:

            return Gst.FlowReturn.ERROR

        try:

            array = np.frombuffer(
                map_info.data,
                dtype=np.uint8
            )

            expected_size = (
                width
                * height
                * 3
            )

            if array.size < expected_size:

                print(
                    "[RTSP] Invalid frame size:",
                    array.size,
                    "expected:",
                    expected_size,
                    file=sys.stderr
                )

                return Gst.FlowReturn.ERROR

            # Limit to expected image bytes in case the
            # mapped buffer contains extra data.
            array = array[
                :expected_size
            ]

            frame_rgb = (
                array.reshape(
                    height,
                    width,
                    3
                )
                .copy()
            )

            try:

                self._backoff.reset()
                self.frame_callback(
                    frame_rgb
                )

            except Exception as exc:

                print(
                    "[RTSP] frame callback error:",
                    exc,
                    file=sys.stderr
                )

        finally:

            buffer.unmap(
                map_info
            )

        return Gst.FlowReturn.OK

    # ========================================================
    # GSTREAMER BUS
    # ========================================================

    def on_bus_message(
        self,
        bus,
        message
    ) -> None:

        # ----------------------------------------------------
        # ERROR
        # ----------------------------------------------------

        if (
            message.type
            ==
            Gst.MessageType.ERROR
        ):

            self._schedule_reconnect()
            error, debug = (
                message.parse_error()
            )

            self._status(
                f"RTSP ERROR: {error.message}"
            )

            print(
                "[RTSP ERROR]",
                error.message,
                file=sys.stderr
            )

            if debug:

                print(
                    "[GSTREAMER DEBUG]",
                    debug,
                    file=sys.stderr
                )

        # ----------------------------------------------------
        # WARNING
        # ----------------------------------------------------

        elif (
            message.type
            ==
            Gst.MessageType.WARNING
        ):

            warning, debug = (
                message.parse_warning()
            )

            print(
                "[RTSP WARNING]",
                warning.message,
                file=sys.stderr
            )

            if debug:

                print(
                    "[GSTREAMER DEBUG]",
                    debug,
                    file=sys.stderr
                )

        # ----------------------------------------------------
        # EOS
        # ----------------------------------------------------

        elif (
            message.type
            ==
            Gst.MessageType.EOS
        ):

            self._schedule_reconnect()
            self._status(
                "RTSP EOS"
            )

            print(
                "[RTSP] End of stream",
                file=sys.stderr
            )

        # ----------------------------------------------------
        # STATE CHANGE
        # ----------------------------------------------------

        elif (
            message.type
            ==
            Gst.MessageType.STATE_CHANGED
            and
            message.src == self.player
        ):

            old_state, new_state, pending = (
                message.parse_state_changed()
            )

            if (
                new_state
                ==
                Gst.State.PLAYING
            ):

                self._status(
                    "RTSP connected"
                )

                print(
                    "[RTSP] PLAYING"
                )

    # ========================================================
    # STOP
    # ========================================================

    def stop(
        self
    ) -> None:

        with self._retry_lock:
            self._closed = True
            if self._retry_timer is not None:
                self._retry_timer.cancel()
                self._retry_timer = None
        self.running = False
        self._remove_bus_watch()

        if self.player is not None:

            self.player.set_state(
                Gst.State.NULL
            )

            self.player = None

        self.appsink = None

        self._status(
            "RTSP stopped"
        )


class CPUStreamPlayer(RTSPPlayer):
    """Opt-in H.264 CPU receiver; no autoplug and no GLib/Tk loop dependency.

    A dedicated pull worker owns samples and calls the existing lightweight
    FrameManager publisher. Appsink's one-slot queue isolates it from streaming
    threads. Images own their memory before the GstBuffer is unmapped.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from collections import Counter, OrderedDict
        self._stop_event = threading.Event()
        self._thread = None
        self._stats_lock = threading.Lock()
        self._counts = Counter()
        self._stages = OrderedDict()
        self._generation = 0
        self._caps = {}
        self._probes = []

    @staticmethod
    def pipeline_description(config):
        width = int(config.get('video_max_width', 1280))
        height = int(config.get('video_max_height', 720))
        latency_ms = int(config.get('latency_ms', 200))
        if width < 2 or height < 2 or not 0 <= latency_ms <= 10000:
            raise ValueError('Invalid CPU stream dimensions/latency')
        return (
            f'rtspsrc name=source protocols=tcp latency={latency_ms} '
            'drop-on-latency=true tcp-timeout=5000000 '
            '! application/x-rtp,media=video,encoding-name=H264 '
            '! rtph264depay ! h264parse name=parser '
            '! video/x-h264,alignment=au ! avdec_h264 name=decoder '
            '! queue name=raw_queue leaky=downstream max-size-buffers=2 '
            'max-size-bytes=0 max-size-time=0 '
            f'! videoscale ! video/x-raw,width={width},height={height},pixel-aspect-ratio=1/1 '
            '! videoconvert ! video/x-raw,format=RGB '
            '! appsink name=gui_sink emit-signals=false sync=false '
            'max-buffers=1 drop=true enable-last-sample=false wait-on-eos=false'
        )

    @staticmethod
    def copy_rgb(sample):
        gi.require_version('GstVideo', '1.0')
        from gi.repository import GstVideo
        caps, buffer = sample.get_caps(), sample.get_buffer()
        info = GstVideo.VideoInfo.new_from_caps(caps)
        if info is None or info.finfo.name != 'RGB':
            raise ValueError('Expected packed RGB video')
        meta = GstVideo.buffer_get_video_meta(buffer)
        stride = int(meta.stride[0] if meta else info.stride[0])
        offset = int(meta.offset[0] if meta else info.offset[0])
        width, height = int(info.width), int(info.height)
        if stride < width * 3 or offset < 0:
            raise ValueError('Invalid RGB stride')
        success, mapped = buffer.map(Gst.MapFlags.READ)
        if not success:
            raise RuntimeError('Cannot map video buffer')
        try:
            if offset + (height-1)*stride + width*3 > mapped.size:
                raise ValueError('Truncated RGB buffer')
            return np.ndarray((height, width, 3), dtype=np.uint8,
                buffer=mapped.data, offset=offset, strides=(stride, 3, 1)).copy()
        finally:
            buffer.unmap(mapped)

    def diagnostics(self):
        with self._stats_lock:
            return dict(counts=dict(self._counts), caps=dict(self._caps),
                        generation=self._generation, decoder='avdec_h264',
                        worker_alive=bool(self._thread and self._thread.is_alive()))

    def _probe(self, pad, info, boundary):
        import time
        buffer = info.get_buffer()
        if buffer is None:
            return Gst.PadProbeReturn.OK
        now = time.perf_counter_ns()
        with self._stats_lock:
            self._counts[boundary] += 1
            if boundary == 'decoder_out' and 'source' not in self._caps:
                caps = pad.get_current_caps()
                if caps:
                    self._caps['source'] = caps.to_string()
            if buffer.pts != Gst.CLOCK_TIME_NONE:
                stages = self._stages.setdefault(int(buffer.pts), {})
                stages[boundary] = now
                while len(self._stages) > 256:
                    self._stages.popitem(last=False)
        return Gst.PadProbeReturn.OK

    def _open_cpu(self):
        Gst.init(None)
        if not self.config.get('uri'):
            raise ValueError('Missing RTSP URI')
        pipeline = Gst.parse_launch(self.pipeline_description(self.config))
        # Store immediately so partially built/failed pipelines are also torn down.
        self.player = pipeline
        source = pipeline.get_by_name('source')
        source.set_property('location', self.config['uri'])
        self.on_source_setup(pipeline, source)
        decoder = pipeline.get_by_name('decoder')
        if decoder.get_factory().get_name() != 'avdec_h264':
            raise RuntimeError('CPU decoder verification failed')
        self.appsink = pipeline.get_by_name('gui_sink')
        for name, side, boundary in (('parser', 'src', 'parser_out'),
                                    ('decoder', 'src', 'decoder_out'),
                                    ('raw_queue', 'src', 'queue_out')):
            pad = pipeline.get_by_name(name).get_static_pad(side)
            probe_id = pad.add_probe(Gst.PadProbeType.BUFFER, self._probe, boundary)
            self._probes.append((pad, probe_id))
        with self._stats_lock:
            self._generation += 1
            self._counts['connections'] += 1
            self._stages.clear()
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('CPU pipeline failed to start')
        self.running = True
        return pipeline

    def _pull(self, pipeline):
        import time
        from telemetry import latency
        bus = pipeline.get_bus()
        last_frame = time.monotonic()
        first = True
        while not self._stop_event.is_set():
            message = bus.timed_pop_filtered(0, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if message:
                # Never stringify RTSP bus diagnostics: they can contain auth URIs.
                raise RuntimeError('RTSP bus error or EOS')
            sample = self.appsink.emit('try-pull-sample', 100 * Gst.MSECOND)
            if sample is None:
                if time.monotonic()-last_frame > float(self.config.get('video_stall_timeout', 12)):
                    raise TimeoutError('No decoded frame')
                continue
            received_ns = time.perf_counter_ns()
            buffer = sample.get_buffer()
            pts_ns = None if buffer.pts == Gst.CLOCK_TIME_NONE else int(buffer.pts)
            frame = self.copy_rgb(sample)
            copy_end = time.perf_counter_ns()
            with self._stats_lock:
                stages = self._stages.pop(pts_ns, {})
                self._counts['appsink_pulled'] += 1
                self._caps['output'] = sample.get_caps().to_string()
                generation = self._generation
            pts = None if pts_ns is None else pts_ns/Gst.SECOND
            observer = self.config.get('_capture_observer')
            if observer:
                observer(pts, generation, frame.shape, received_ns)
            for start, end, metric in (('parser_out', 'decoder_out', 'capture_decoder_residence_ms'),
                                      ('decoder_out', 'queue_out', 'capture_raw_queue_ms')):
                if start in stages and end in stages and stages[end] >= stages[start]:
                    latency.profiler.event('CAPTURE_STAGE', metric=metric,
                        duration_ms=(stages[end]-stages[start])/1e6)
            latency.profiler.event('CAPTURE_COPY', metric='capture_copy_ms',
                duration_ms=(copy_end-received_ns)/1e6, source_pts_seconds=pts,
                stream_generation=generation)
            if 'parser_out' in stages:
                latency.profiler.event('CAPTURE_READY', metric='capture_parser_to_rgb_ms',
                    duration_ms=(copy_end-stages['parser_out'])/1e6)
            # Release Gst sample before handing an independently owned RGB array on.
            sample = buffer = None
            if self._stop_event.is_set():
                break
            self.frame_callback(frame)
            last_frame = time.monotonic()
            if first:
                self._status('RTSP connected (GStreamer avdec_h264 CPU)')
                self._backoff.reset()
                first = False

    def start(self):
        with self._retry_lock:
            if self._closed or (self._thread and self._thread.is_alive()):
                return
            self._thread = threading.Thread(target=self._run_cpu, name='gst-cpu-capture', daemon=True)
            self._thread.start()

    def _run_cpu(self):
        while not self._stop_event.is_set():
            try:
                pipeline = self._open_cpu()
                self._pull(pipeline)
            except Exception as exc:
                if not self._stop_event.is_set():
                    with self._stats_lock:
                        self._counts['failures'] += 1
                    self._status(f'RTSP CPU unavailable ({type(exc).__name__})')
                    if self.disconnect_callback:
                        self.disconnect_callback()
            finally:
                if self.player is not None:
                    self.player.set_state(Gst.State.NULL)
                for pad, probe_id in self._probes:
                    pad.remove_probe(probe_id)
                self._probes.clear()
                self.player = self.appsink = None
                self.running = False
            if not self._stop_event.is_set():
                self._stop_event.wait(self._backoff.next_delay())

    def stop(self):
        with self._retry_lock:
            self._closed = True
            self._stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=15)
            if self._thread.is_alive():
                raise RuntimeError('GStreamer capture worker did not stop')
        self.running = False
