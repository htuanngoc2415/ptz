"""Observation-only harness around the real CCTVApp workers and Tk event loop."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import threading
import time

from telemetry.benchmark import distribution


class DesktopBenchmark:
    def __init__(self, options):
        self.options = options
        self.output = Path(options['output'])
        self.output.mkdir(parents=True, exist_ok=True)
        self.started = time.perf_counter()
        self.begin_ns = self.end_ns = None
        self.ready_at = None
        self.capture = []
        self.capture_lock = threading.Lock()
        self.resources = []
        self.previous_resource = None
        self.last_resource = 0
        self.failure = None
        self.finished = False
        self.start_counts = {}
        self.end_counts = {}
        self.start_diag = self.end_diag = None
        self.app = None
        self.auto_zoom_requested = False

    def observe_capture(self, pts, generation, shape, received_ns):
        with self.capture_lock:
            if self.begin_ns is not None and self.end_ns is None:
                self.capture.append(dict(ns=received_ns, pts=pts, generation=generation, shape=list(shape)))

    def attach(self, app):
        self.app = app
        app.root.title('RTSP A/B benchmark — camera writes blocked')
        app.root.after(250, self.poll)

    def poll(self):
        from telemetry import latency
        from telemetry.resources import sample
        app = self.app
        if not app.running:
            if not self.finished:
                self.failure = 'DESKTOP_CLOSED_BEFORE_MEASUREMENT_COMPLETE'
            return
        now = time.perf_counter()
        if app.ai_initialization_error:
            self.failure = 'AI_INITIALIZATION_FAILED'
        elif app._ai_started and not app.ai_thread.is_alive():
            self.failure = 'AI_WORKER_STOPPED'
        if self.failure:
            self.finish()
            return
        with app.ai_lock:
            target_ready = app.ai_result.get('reid', {}).get('valid', False)
        ready = (hasattr(app, 'face_worker') and app.reid_target.valid and target_ready
                 and latency.profiler.counters['detection_frames'] > 0
                 and app.frames.latest() is not None)
        if ready and not self.auto_zoom_requested:
            # Same UI action on both arms. Policies run normally; RPC rejects writes.
            app.auto_zoom_var.set(True)
            app.toggle_auto_zoom()
            self.auto_zoom_requested = True
        if ready and self.auto_zoom_requested and self.ready_at is None:
            self.ready_at = now
        if self.begin_ns is None:
            if now-self.started > self.options.get('startup_timeout', 180):
                self.failure = 'STARTUP_OR_TARGET_TIMEOUT'
                self.finish()
                return
            if self.ready_at is not None and now-self.ready_at >= self.options['warmup']:
                self.begin_ns = time.perf_counter_ns()
                self.start_counts = dict(latency.profiler.counters)
                self.start_diag = app.rtsp.diagnostics() if hasattr(app.rtsp, 'diagnostics') else None
                latency.profiler.event('DESKTOP_BENCHMARK_BEGIN', monotonic_ns=self.begin_ns)
        if self.begin_ns is not None:
            if now-self.last_resource >= 1:
                row, self.previous_resource = sample(self.previous_resource)
                row['ns'] = time.perf_counter_ns()
                self.resources.append(row)
                self.last_resource = now
            if now-self.begin_ns/1e9 >= self.options['seconds']:
                self.finish()
                return
        app.root.after(100, self.poll)

    def finish(self):
        from telemetry import latency
        if self.finished:
            return
        self.finished = True
        self.end_ns = time.perf_counter_ns()
        self.end_counts = dict(latency.profiler.counters)
        self.end_diag = self.app.rtsp.diagnostics() if hasattr(self.app.rtsp, 'diagnostics') else None
        latency.profiler.event('DESKTOP_BENCHMARK_END', monotonic_ns=self.end_ns)
        self.shutdown_started = time.perf_counter()
        # Keep partial evidence even if native shutdown blocks.
        self.write_result(shutdown_complete=False)
        self.app.on_close()

    def write_result(self, shutdown_complete):
        from telemetry import latency
        duration = (self.end_ns-self.begin_ns)/1e9 if self.begin_ns and self.end_ns else 0
        metrics = defaultdict(list)
        events = Counter()
        for path in (self.output/'trace').rglob('latency_trace*.jsonl*'):
            for line in path.read_text().splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ns = row.get('monotonic_ns', 0)
                if not self.begin_ns or not self.end_ns or not self.begin_ns <= ns <= self.end_ns:
                    continue
                events[row.get('event')] += 1
                metric, value = row.get('metric'), row.get('duration_ms')
                if metric and isinstance(value, (float, int)):
                    metrics[metric].append(value)
        counters = {k: self.end_counts.get(k, 0)-self.start_counts.get(k, 0)
                    for k in self.end_counts}
        with self.capture_lock:
            capture = [r for r in self.capture if self.begin_ns and self.end_ns
                       and self.begin_ns <= r['ns'] <= self.end_ns]
        segments = defaultdict(list)
        for row in capture:
            if row['pts'] is not None:
                segments[row['generation']].append(row)
        drift = []
        for rows in segments.values():
            if len(rows) > 1:
                a, b = rows[0], rows[-1]
                drift.append(dict(samples=len(rows), wall_seconds=(b['ns']-a['ns'])/1e9,
                                  pts_seconds=b['pts']-a['pts'],
                                  drift_seconds=(b['ns']-a['ns'])/1e9-(b['pts']-a['pts'])))
        resource_stats = {k:distribution([r[k] for r in self.resources if r.get(k) is not None])
            for k in ('process_cpu_percent', 'system_cpu_percent', 'rss_mb', 'gpu_percent')}
        alive = [t.name for t in threading.enumerate() if t.name in
                 ('detection-tracking', 'gst-cpu-capture', 'mpv-capture', 'face-spatial', 'face-identity',
                  'camera-command', 'dahua-status', 'rpc-keepalive')]
        if shutdown_complete and (alive or duration < self.options['seconds']):
            self.failure = self.failure or 'INCOMPLETE_MEASUREMENT_OR_SHUTDOWN'
        result = dict(backend=self.options['backend'], cuda_timing=self.options.get('cuda_timing', False), status='FAILED' if self.failure else
                      ('MEASURED' if shutdown_complete else 'SHUTDOWN_PENDING'),
                      failure=self.failure, seconds=duration, warmup_seconds=self.options['warmup'],
                      begin_ns=self.begin_ns, end_ns=self.end_ns, capture_frames=len(capture),
                      capture_fps=len(capture)/duration if duration else None, pts_segments=drift,
                      frame_shapes=sorted({tuple(r['shape']) for r in capture}),
                      counters=counters, rates={k:v/duration for k,v in counters.items()} if duration else {},
                      metrics={k:distribution(v) for k,v in metrics.items()}, events=dict(events),
                      resources=resource_stats, resource_samples=self.resources,
                      decoder_start=self.start_diag, decoder_end=self.end_diag,
                      shutdown_complete=shutdown_complete, workers_alive=alive,
                      shutdown_seconds=time.perf_counter()-getattr(self, 'shutdown_started', time.perf_counter()),
                      profiler_error=latency.profiler.error,
                      camera_writes='RPC allowlist enforced; image settings skipped',
                      glass_to_glass_ms=None,
                      workload='live RTSP; real CCTVApp/InferenceWorker/FaceWorker/scheduler; Auto Zoom enabled, physical writes rejected',
                      limitation='Live scene is not a deterministic replay. Blocked actuation may affect target/face gates.')
        import config as cfg
        result['target_sha256'] = hashlib.sha256(Path(cfg.TARGET_IMAGE_PATH).read_bytes()).hexdigest()
        result['inputs_sha256'] = {name:hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for name, path in dict(camera_config=cfg.CAMERA_CONFIG_PATH,
                config=Path(cfg.__file__), yolo=cfg.PERSON_MODEL_PATH, osnet=cfg.REID_MODEL_PATH,
                scrfd=cfg.FACE_DETECT_MODEL, arcface=cfg.FACE_RECOGNITION_MODEL,
                gallery=cfg.FACE_DATABASE_PATH).items()}
        result['model_settings'] = dict(device=str(cfg.DEVICE), arcface_device=cfg.ARCFACE_DEVICE,
            person_imgsz=cfg.PERSON_IMGSZ, ai_max_fps=cfg.AI_MAX_FPS,
            scrfd_input=list(cfg.FACE_DET_INPUT_SIZE), latency_ms=cfg.RTSP_LATENCY_MS)
        (self.output/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        (self.output/'capture.json').write_text(json.dumps(capture)+'\n')
