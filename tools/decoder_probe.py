#!/usr/bin/env python3
"""Read-only decoder probe; no GUI, AI, camera RPC or motor imports.

Explicit decoder pipelines are benchmark candidates, never production config.
PTS-correlated host spans include internal waiting, not hardware kernel time.
"""
import argparse
from collections import defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from telemetry.benchmark import distribution
from telemetry.resources import sample as resource_sample


class PerceptionLoad:
    """Production model wrappers, isolated latest-frame workload, no controls.

    This is NOT InferenceWorker's target/face scheduler or a production E2E run.
    Face detection is full-frame; recognition executes only on detected faces.
    """
    def __init__(self, frames, fixed_image=None):
        import config as cfg
        from ai.detector import PersonDetector
        from ai.face import FaceDetector, FaceRecognizer
        from person_search.reid import OSNetReID
        from runtime.person_tracking import PersonTracker
        self.frames = frames
        self.yolo = PersonDetector(cfg.PERSON_MODEL_PATH, conf_threshold=cfg.PERSON_CONF_THRESHOLD,
            iou_threshold=cfg.PERSON_IOU_THRESHOLD, class_id=cfg.PERSON_CLASS_ID,
            device=cfg.DEVICE, imgsz=cfg.PERSON_IMGSZ)
        self.reid = OSNetReID(cfg.REID_MODEL_PATH, device=cfg.REID_DEVICE, batch_size=cfg.REID_BATCH_SIZE)
        self.scrfd = FaceDetector(cfg.FACE_DETECT_MODEL,
            providers=['CPUExecutionProvider'] if str(cfg.DEVICE)=='cpu' else None,
            input_size=cfg.FACE_DET_INPUT_SIZE, conf_threshold=cfg.FACE_DET_CONF, nms_threshold=cfg.FACE_DET_NMS)
        self.arcface = FaceRecognizer(cfg.FACE_RECOGNITION_MODEL,
            providers=['CPUExecutionProvider'] if str(cfg.DEVICE)=='cpu' or cfg.ARCFACE_DEVICE=='cpu'
            else ['CUDAExecutionProvider'])
        self.tracker = PersonTracker(cfg.REID_TRACK_IOU, cfg.REID_ASSOC_MARGIN,
                                     cfg.TARGET_LOST_FRAMES, cfg.REID_LOST_GRACE_SEC)
        self.fixed_image = None
        if fixed_image:
            import cv2
            import numpy as np
            from ai.face.face_alignment import align_face
            original = cv2.imread(str(fixed_image))
            if original is None:
                raise ValueError('FIXED_LOAD_IMAGE_UNREADABLE')
            scale = min(1280/original.shape[1], 720/original.shape[0])
            resized = cv2.resize(original, (round(original.shape[1]*scale), round(original.shape[0]*scale)))
            self.fixed_image = np.zeros((720,1280,3), dtype=np.uint8)
            self.fixed_image[:resized.shape[0], :resized.shape[1]] = resized
            people = self.yolo.detect(self.fixed_image)
            faces = self.scrfd.detect(self.fixed_image)
            if not people or not any(align_face(self.fixed_image, f['landmarks']) is not None for f in faces):
                raise ValueError('FIXED_LOAD_REQUIRES_DETECTED_PERSON_AND_ALIGNABLE_FACE')
        self.providers = dict(scrfd=self.scrfd.session.get_providers(), arcface=self.arcface.session.get_providers())
        self.stop = threading.Event()
        self.metrics = defaultdict(list)
        self.counts = defaultdict(int)
        self.errors = []
        self.last_stage = None
        self.active = lambda now: False
        self.thread = threading.Thread(target=self.run, name='probe-perception', daemon=True)

    def run(self):
        import cv2
        from ai.face.face_alignment import align_face
        from telemetry import latency
        last = 0
        def timed(name, fn):
            self.last_stage = name
            start = time.monotonic()
            value = fn()
            if self.active(start):
                self.metrics[name+'_total_ms'].append((time.monotonic()-start)*1000)
                self.counts[name] += 1
            return value
        try:
            while not self.stop.is_set():
                packet = self.frames.latest(after=last, consumer='detection')
                if packet is None:
                    self.stop.wait(.005); continue
                if self.active(time.monotonic()):
                    self.counts['mailbox_skipped'] += max(0, packet.frame_id-last-1)
                    self.metrics['frame_age_since_app_ms'].append(packet.age_ms())
                last = packet.frame_id
                with latency.correlated(frame_id=last):
                    image = (self.fixed_image if self.fixed_image is not None
                             else cv2.cvtColor(packet.image, cv2.COLOR_RGB2BGR))
                    persons = timed('yolo', lambda:self.yolo.detect(image))
                    if self.yolo.last_error:
                        raise RuntimeError('YOLO_INFERENCE_FAILED')
                    persons = timed('tracking', lambda:self.tracker.update(persons,packet.receive_timestamp))
                    crops = [image[int(p['bbox'][1]):int(p['bbox'][3]),int(p['bbox'][0]):int(p['bbox'][2])]
                             for p in persons]
                    if crops:
                        timed('osnet', lambda:self.reid.extract_embeddings(crops))
                    faces = timed('scrfd', lambda:self.scrfd.detect(image))
                    for face in faces:
                        aligned = align_face(image, face['landmarks'])
                        if aligned is not None:
                            timed('arcface', lambda:self.arcface.extract_embedding(aligned))
        except Exception as exc:
            self.errors.append(dict(type=type(exc).__name__, stage=self.last_stage))
            self.stop.set()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--config', type=Path, default=ROOT / 'camera.json')
    ap.add_argument('--decoder', choices=('cpu', 'nvdec'), required=True)
    ap.add_argument('--codec', choices=('h264', 'h265'), default='h264')
    ap.add_argument('--latency', type=int, choices=(50, 100, 200), default=100)
    ap.add_argument('--seconds', type=float, default=60)
    ap.add_argument('--warmup', type=float, default=5)
    ap.add_argument('--ai', action='store_true', help='Isolated perception load; NOT production scheduler/GUI')
    ap.add_argument('--ai-fixed-image', type=Path,
                    help='Matched model stress input instead of live image; requires --ai and a detected person/face')
    ap.add_argument('--disable-dpb', action='store_true', help='NVDEC candidate: use only after verifying no B frames')
    ap.add_argument('--jitter-probe', action='store_true',
                    help='Extra per-RTP-packet probes; quantify observer impact in a separate run')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if not 1 <= args.seconds <= 300 or args.warmup < 0:
        ap.error('seconds must be 1..300; warmup must be nonnegative')
    if args.disable_dpb and args.decoder != 'nvdec':
        ap.error('--disable-dpb requires --decoder nvdec')
    if args.ai_fixed_image and not args.ai:
        ap.error('--ai-fixed-image requires --ai')
    # Prevent GStreamer debug logs from printing authenticated RTSP locations.
    os.environ['GST_DEBUG'] = '0'
    import gi
    gi.require_version('Gst', '1.0')
    gi.require_version('GstVideo', '1.0')
    from gi.repository import Gst, GstVideo
    import numpy as np
    from runtime.frame_manager import FrameManager
    from telemetry import latency
    latency.profiler.enabled = False
    Gst.init(None)
    cfg = json.loads(args.config.read_text())
    cpu = args.decoder == 'cpu'
    desc = (f'rtspsrc name=source protocols=tcp latency={args.latency} drop-on-latency=true '
            f'! application/x-rtp,media=video,encoding-name={args.codec.upper()} '
            f'! rtp{args.codec}depay name=depay ! {args.codec}parse name=parser '
            f'! video/x-{args.codec},alignment=au '
            f'! {"avdec_" + args.codec if cpu else "nvv4l2decoder"} name=decoder '
            + ('disable-dpb=true ' if args.disable_dpb else '') +
            '! queue name=rawq leaky=downstream max-size-buffers=2 max-size-bytes=0 max-size-time=0 ! ')
    if cpu:
        desc += 'videoscale name=resize ! video/x-raw,width=1280,height=720,pixel-aspect-ratio=1/1 ! '
    else:
        desc += ('nvvidconv name=resize ! video/x-raw,format=BGRx,width=1280,height=720,'
                 'pixel-aspect-ratio=1/1 ! ')
    desc += ('videoconvert name=color ! video/x-raw,format=RGB ! appsink name=sink '
             'emit-signals=true sync=false max-buffers=1 drop=true enable-last-sample=false')
    pipeline = Gst.parse_launch(desc)
    source = pipeline.get_by_name('source')
    source.set_property('location', cfg['uri'])
    for key, prop in (('username', 'user-id'), ('password', 'user-pw')):
        if cfg.get(key):
            source.set_property(prop, cfg[key])
    metrics = defaultdict(list)
    stamps = defaultdict(dict)
    counts = defaultdict(int)
    caps = {}
    source_rows = []
    resources = []
    errors = []
    mailbox = FrameManager()
    ai = None
    if args.ai:
        try:
            latency.configure(directory=str(args.output.parent / (args.output.stem+'_trace')), enabled=True)
            ai = PerceptionLoad(mailbox, args.ai_fixed_image)
        except Exception as exc:
            latency.profiler.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            result = dict(status='AI_INIT_FAILED', error_type=type(exc).__name__,
                          decoder=args.decoder, latency_ms=args.latency, ai='isolated_perception',
                          measured_seconds=0, camera_to_display_ms=None)
            args.output.write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(result))
            return 2
    first_frame = None
    measurement_start = float('inf')
    measurement_end = float('inf')
    pts_samples = []
    raw_queue = pipeline.get_by_name('rawq')
    queue_levels = []
    segment = Gst.Segment.new()
    segment.init(Gst.Format.TIME)
    def active(now):
        return measurement_start <= now < measurement_end
    if ai:
        ai.active = active
        ai.thread.start()

    def jitter_added(_pipeline, _bin, element):
        factory = element.get_factory()
        if not factory or factory.get_name() != 'rtpjitterbuffer':
            return
        pending = {}
        def packet_probe(outgoing):
            def observe(pad, info):
                buffer = info.get_buffer()
                caps_now = pad.get_current_caps()
                if buffer is None or caps_now is None or caps_now.get_structure(0).get_string('media') != 'video':
                    return Gst.PadProbeReturn.OK
                header = buffer.extract_dup(0, min(12, buffer.get_size()))
                if len(header) < 12:
                    return Gst.PadProbeReturn.OK
                key = (bytes(header[8:12]), bytes(header[2:4]))  # SSRC + RTP sequence, not frame PTS.
                now = time.monotonic()
                if outgoing:
                    entered = pending.pop(key, None)
                    if entered is not None and active(entered) and active(now):
                        metrics['jitterbuffer_residence_ms'].append((now-entered)*1000)
                else:
                    pending[key] = now
                    if len(pending) > 4096:
                        pending.pop(next(iter(pending)))
                return Gst.PadProbeReturn.OK
            return observe
        element.get_static_pad('sink').add_probe(Gst.PadProbeType.BUFFER, packet_probe(False))
        element.get_static_pad('src').add_probe(Gst.PadProbeType.BUFFER, packet_probe(True))
    if args.jitter_probe:
        pipeline.connect('deep-element-added', jitter_added)

    def probe(label, previous=None, metric=None):
        def callback(pad, info):
            b = info.get_buffer()
            if b is None:
                return Gst.PadProbeReturn.OK
            now = time.monotonic()
            pts = b.pts
            if label not in caps:
                c = pad.get_current_caps()
                if c:
                    # Only media caps, never source URI/SDP or errors.
                    caps[label] = c.to_string()
            table = stamps[label]
            if pts != Gst.CLOCK_TIME_NONE:
                table[pts] = now
                if len(table) > 512:
                    table.pop(next(iter(table)))
            if active(now):
                counts[label] += 1
                if label == 'parser_out':
                    counts['encoded_bytes'] += b.get_size()
                if metric and pts in stamps[previous]:
                    began = stamps[previous][pts]
                    if active(began):
                        metrics[metric].append((now-began)*1000)
                if label == 'decoder_out' and pts != Gst.CLOCK_TIME_NONE:
                    pts_samples.append((now, pts / Gst.SECOND))
            return Gst.PadProbeReturn.OK
        return callback

    for element, pad, label, prev, metric in (
        ('depay', 'sink', 'rtp_out', None, None),
        ('depay', 'src', 'depay_out', 'rtp_out', 'last_rtp_to_depay_ms'),
        ('parser', 'src', 'parser_out', 'depay_out', 'depay_to_parser_ms'),
        ('decoder', 'sink', 'decoder_in', None, None),
        ('decoder', 'src', 'decoder_out', 'decoder_in', 'decoder_residence_ms'),
        ('rawq', 'src', 'queue_out', 'decoder_out', 'raw_queue_wait_ms'),
        ('resize', 'src', 'resize_out', 'queue_out', 'resize_or_nvmm_download_ms'),
        ('color', 'src', 'color_out', 'resize_out', 'color_convert_ms'),
    ):
        pipeline.get_by_name(element).get_static_pad(pad).add_probe(
            Gst.PadProbeType.BUFFER, probe(label, prev, metric))

    def sink_event(pad, info):
        nonlocal segment
        event = info.get_event()
        if event.type == Gst.EventType.SEGMENT:
            segment = event.parse_segment().copy()
        return Gst.PadProbeReturn.OK
    pipeline.get_by_name('sink').get_static_pad('sink').add_probe(
        Gst.PadProbeType.EVENT_DOWNSTREAM, sink_event)

    def receive(sink):
        nonlocal first_frame
        started = time.monotonic()
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        vi = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        tick = time.monotonic()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        mapped_at = time.monotonic()
        if not ok:
            errors.append('MAP_FAILED')
            return Gst.FlowReturn.ERROR
        try:
            # Respect row stride; production assumes packed RGB.
            rgb = np.ndarray((vi.height, vi.width, 3), dtype=np.uint8,
                             buffer=mapped.data, offset=vi.offset[0],
                             strides=(vi.stride[0], 3, 1)).copy()
        finally:
            buffer.unmap(mapped)
        copied_at = time.monotonic()
        mailbox.publish(rgb)
        published_at = time.monotonic()
        if first_frame is None:
            first_frame = started
        if active(started):
            counts['appsink'] += 1
            metrics['map_ms'].append((mapped_at-tick)*1000)
            metrics['copy_and_unmap_ms'].append((copied_at-mapped_at)*1000)
            metrics['frame_manager_publish_ms'].append((published_at-copied_at)*1000)
            metrics['appsink_to_frame_manager_ms'].append((published_at-started)*1000)
            if buffer.pts in stamps['parser_out'] and active(stamps['parser_out'][buffer.pts]):
                metrics['parser_to_app_ms'].append((published_at-stamps['parser_out'][buffer.pts])*1000)
            running_pts = segment.to_running_time(Gst.Format.TIME, buffer.pts)
            clock = pipeline.get_clock()
            if clock and running_pts != Gst.CLOCK_TIME_NONE:
                metrics['pipeline_clock_minus_pts_ms'].append(
                    (clock.get_time()-pipeline.get_base_time()-running_pts)/Gst.MSECOND)
        return Gst.FlowReturn.OK
    pipeline.get_by_name('sink').connect('new-sample', receive)
    bus = pipeline.get_bus()
    began = time.monotonic()
    pipeline.set_state(Gst.State.PLAYING)
    previous_resource = None
    next_sample = 0
    measured_seconds = 0
    try:
        while True:
            now = time.monotonic()
            if ai and ai.errors:
                errors.append('AI_RUNTIME_FAILED')
                break
            if first_frame is not None and measurement_start == float('inf'):
                measurement_start = first_frame + args.warmup
                measurement_end = measurement_start + args.seconds
            if now >= measurement_end:
                measured_seconds = args.seconds
                break
            if first_frame is None and now-began > 20:
                errors.append('NO_FRAME_WITHIN_20_SECONDS')
                break
            msg = bus.timed_pop_filtered(50 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
            if msg:
                if msg.type == Gst.MessageType.ERROR:
                    err, _ = msg.parse_error()
                    errors.append({'element':msg.src.get_name(), 'domain':str(err.domain), 'code':err.code})
                else:
                    errors.append('EOS')
                break
            if active(now) and now >= next_sample:
                values, previous_resource = resource_sample(previous_resource)
                for device, node in (('gpu','17000000.gpu'), ('nvdec','15480000.nvdec'), ('vic','15340000.vic')):
                    try:
                        values[device+'_mhz'] = int((Path('/sys/class/devfreq')/node/'cur_freq').read_text())/1e6
                    except (OSError, ValueError):
                        values[device+'_mhz'] = None
                values['elapsed'] = now-measurement_start
                resources.append(values)
                queue_levels.append(raw_queue.get_property('current-level-buffers'))
                next_sample = now+1
    finally:
        if measurement_start != float('inf') and not measured_seconds:
            measured_seconds = max(0, min(args.seconds,time.monotonic()-measurement_start))
        # Obtain jitterbuffer stats via recursive traversal, no source properties.
        it = pipeline.iterate_recurse()
        while True:
            status, element = it.next()
            if status == Gst.IteratorResult.RESYNC:
                it.resync(); continue
            if status != Gst.IteratorResult.OK:
                break
            factory = element.get_factory()
            if factory and factory.get_name() == 'rtpjitterbuffer':
                st = element.get_property('stats')
                source_rows.append(st.to_string() if st else None)
        pipeline.set_state(Gst.State.NULL)
        if ai:
            ai.stop.set()
            ai.thread.join(timeout=15)
            if ai.thread.is_alive():
                errors.append('AI_SHUTDOWN_TIMEOUT')
            latency.profiler.close()
    pts_reset = any(b[1] < a[1] for a,b in zip(pts_samples, pts_samples[1:]))
    progress = None
    if len(pts_samples)>1 and not pts_reset:
        wall = pts_samples[-1][0]-pts_samples[0][0]
        media = pts_samples[-1][1]-pts_samples[0][1]
        progress = dict(wall_seconds=wall, media_seconds=media,
                        ratio=media/wall, lag_growth_seconds=wall-media)
    result = dict(created_at=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).isoformat(),
        status='PASS' if not errors and measured_seconds >= args.seconds else 'FAILED',
        decoder=args.decoder, codec=args.codec, latency_ms=args.latency,
        disable_dpb=args.disable_dpb, ai='isolated_perception' if ai else 'OFF',
        ai_input='fixed_image_stress' if args.ai_fixed_image else 'live_frames',
        jitter_probe_enabled=args.jitter_probe,
        ai_results=None if ai is None else dict(counts=dict(ai.counts), errors=ai.errors,
            providers=ai.providers, metrics={k:distribution(v) for k,v in ai.metrics.items()}),
        pipeline=desc, requested_seconds=args.seconds, measured_seconds=measured_seconds,
        warmup_seconds=args.warmup, first_frame_seconds=None if first_frame is None else first_frame-began,
        errors=errors, caps=caps, counts=dict(counts),
        fps={k:counts[k]/measured_seconds if measured_seconds else None for k in ('parser_out','decoder_out','appsink')},
        encoded_mbps=counts['encoded_bytes']*8/1e6/measured_seconds if measured_seconds else None,
        metrics={k:distribution(v) for k,v in metrics.items()},
        resources=resources, resource_summary={k:distribution([r[k] for r in resources if isinstance(r.get(k),(int,float))])
            for k in ('process_cpu_percent','system_cpu_percent','rss_mb','gpu_percent','gpu_mhz','nvdec_mhz','vic_mhz')},
        queue_level_buffers=distribution(queue_levels), jitterbuffer_stats=source_rows,
        pts_progress=progress, pts_reset=pts_reset, camera_to_display_ms=None,
        limitations=['No GUI/motor. Optional AI is isolated perception, not production scheduler/target selection/zoom/face gating.',
            'Pipeline clock minus PTS is receiver-relative lateness, NOT camera-to-display latency.',
            'Jitter stats cover startup + warmup + measurement; other counts cover measurement only.',
            'Decoder PTS residence includes reorder/internal wait, not just decode execution.',
            'Last RTP to depay uses final matching packet timestamp, not first packet or network residence.',
            'Probe callback overhead included; raw queue count difference is boundary-sensitive, not an exact drop count.'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('status','decoder','latency_ms','measured_seconds','fps','pts_progress','errors')}))
    return 0 if result['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
