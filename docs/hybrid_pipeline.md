# Hybrid camera pipeline and latency tracing

> Cập nhật: logic fallback về person framing đã được thay bằng sticky face phase/HOLD; xem [zoom oscillation fix](zoom_oscillation_fix.md).
Run the desktop with `./run_camera.sh`. This uses the existing Autofocus_Camera
Python environment. Camera connection values and existing backlight settings are
preserved in `camera.json`; `pipeline` and `profiling` configure the new behavior.
Auto Zoom / Auto Focus still default OFF. No weights or recognition thresholds
were changed.

## Execution and identity ownership

* GStreamer keeps its existing decoder/reconnection implementation. FrameManager
  stores one immutable frame packet (the image itself must not be mutated), with
  an increasing frame ID and monotonic receipt timestamp.
* `detection-tracking` owns ReID selection, track continuity, target state and
  publication to the GUI. The existing spatial association, ambiguity rejection,
  multi-observation confirmation, crossing hold, lost grace and recovery remain.
  `PersonTracker` assigns conservative spatial track IDs before OSNet; ambiguous
  associations get new detection IDs and still require the existing ReID evidence.
  Supporting OSNet verification after lock requires the configured frame interval
  **and** the existing time interval; `reid_on_every_frame` overrides that cadence,
  but does not bypass the full-body crop suitability check.
* `face-recognition` has one pending frame, one latest spatial result and a bounded
  queue of 32 identity observations. Spatial feedback is published before ArcFace. After lock, padded
  target ROI -> SCRFD -> conservative face association -> existing size,
  landmarks and sharpness gates -> alignment -> ArcFace -> database matching ->
  existing temporal confirmation. Lens motion is not a recognition veto.
  An overlapping neighbor makes association ambiguous and suppresses recognition.
  When the person detector loses a close-up body, only the previously associated
  face ROI may be searched.
* Models are loaded once. A shared, reentrant model scheduler serializes model
  submissions; queued YOLO detection has priority. Face recognition retains its
  existing cooldown. These are logical asynchronous branches, not a claim that
  concurrent CUDA calls improve throughput. A running model call is not preempted.
* The face worker owns its confirmation accumulator. Spatial results require the
  existing 800 ms motor freshness and current geometric compatibility. Identity
  observations require fresh admission, reliable source-frame association and the
  same live session/track/generation, but are not expired by the motor timeout.
  Identity never rewrites current bbox coordinates; duplicate frame IDs cannot
  increment confirmation. UNKNOWN is an accepted observation with no known match.
  Empty mailboxes do not count as failed detections. Session/track changes reject
  in-flight results. GUI snapshots retain the existing lock.
* On target lock, person zoom is submitted before face/quality/display work.
  A transient face detection does not suspend person zoom. Three associated face
  observations select face zoom; a face gap keeps the current bounded absolute
  move alive only while the original person remains trusted. After the configured
  grace, the state falls back to person control and requires face confirmation again.
  Person framing within 0.46–0.54 holds optical zoom and retries the head ROI without
  extra blind magnification. Existing final-face framing and safety stops remain.
  See [pipeline fix and mock evidence](hybrid_identity_zoom_fix.md).
* `camera-command` is the single executor for PTZ motor transactions. Zoom,
  focus and manual supervisors retain their existing ownership/cancellation
  policies and only this executor performs their motor operations. Cached
  telemetry polling and RPC keepalive use the existing separate status path;
  the RPC reconnect fail-safe may issue STOP. No new model or motor waits run in
  Tk or detection. STOP requests cancel focus before entering the executor queue.
* Absolute zoom now probes capability and uses one main PositionABS move, then
  waits for measured endpoint/idle and three distinct stable bbox observations.
  At most two corrections are allowed, each bounded to 15%. A validated camera-bound
  optical LUT is preferred; otherwise the ZoomValue geometric approximation is
  explicitly uncalibrated. Below the historically measured 6.4 floor, or without
  capability, the explicit pulse fallback retains safety stops and watchdogs.
  Encoder coordinates are never substituted for PositionABS command coordinates.
  See [absolute zoom audit](absolute_zoom_audit.md) for evidence, limits and tools.
* Manual controls, target browse/drop, live preview, digital PTZ and GUI logs
  remain. Recognition success alone never stops zoom. The existing independent
  face-framing condition can stop it.

`pipeline.mode=hybrid`, async flags and `prefer_latest_frame=true` describe the
supported architecture; setting them false raises a clear configuration error
rather than silently reintroducing a blocking legacy path.

## Measurements and files

`profiling.enabled` or the GUI checkbox controls tracing. GUI metrics refresh at
2 Hz on Tk's thread. No metric value is fabricated when unavailable.

* `logs/latency/latency_trace_<session>.jsonl`: correlated events and periodic
  `LATENCY_REPORT` records containing counters/rates and stage statistics.
* `logs/latency/latency_readable_<session>.log`: readable events and reports.
* `logs/latency/latency_summary_<session>.csv`: atomically replaced summary.
* `logs/camera/camera_control_<session>.log`: camera actions and RPC diagnostics.

The writer has a bounded queue, nonblocking producers, 8 MB rotation with three
backups, and bounded retention of previous log files. Queue overflow increments
`dropped_log_events`. Statistics retain count/mean/min/max across the profiler
lifetime; p50/p90/p95/p99 describe the most recent 4096 samples per metric. CSV
records `percentile_window_count`. Disabling profiling stops new events; shutdown
still flushes records already queued.

Every duration uses `perf_counter_ns`; ISO 8601 wall timestamps include the host's
local timezone offset. IDs include `frame_id`, `acquisition_id`, `lock_session_id`
and `command_id`. Frame IDs on identity results refer to the image actually used,
not the newer detection image that accepted the result. Acquisition begins at the
first **accepted ReID candidate** in a continuous confirmation sequence, with that
frame's receipt time as the start boundary. Recovery begins when the old target
is declared lost; correlation is retained until same-reference reacquisition.

Supported timings: frame age since app receipt; frame-mailbox and model-scheduler
queue waits; YOLO preprocessing/inference/postprocessing/total; ReID crops,
preprocessing/inference/matching/total; face ROI preparation; SCRFD; face quality;
alignment; ArcFace; database matching; face recognition total; camera command queue
and actual RPC calls; target lock; first zoom command; first usable face; identity
confirmation; target recovery. Parallel branch durations are never summed into
end-to-end latency. Receive FPS and detection FPS are separate lifetime rates.

Camera target arrival is reported only from measured zoom position, never from an
RPC acknowledgement. Motor onset from a changed reported position and the duration
between position observations are explicitly **estimated**. Zoom visual stability
requires three distinct post-settle observations with <=2% size variation; it is
also an estimate. Focus stability means the existing multi-frame visual quality
check passed; it does not assert focus motor telemetry. Missing completion pairs
produce no duration. Actual positions are only read from camera telemetry.

## Honest timing limits

The existing appsink callback exposes decoded RGB, not camera capture time or
network-arrival time. `FRAME_RECEIVED` therefore declares
`receive_boundary=decoded_app_frame`; sensor capture and exact decoder start/end
are explicitly unavailable. GStreamer PTS is not treated as synchronized camera
wall time. No capture-to-receive or capture-to-inference latency is invented.

Normal model spans measure CPU wall time around actual APIs. ONNX Runtime `run`
returns host outputs; its time is not labeled GPU kernel time. PyTorch CUDA-event
measurements can be enabled with `detailed_gpu_profiling`; only that benchmark
mode introduces explicit event synchronization. ORT kernel-only GPU timing is not
available from these spans and must be measured with an external provider-aware
profiler. CPU smoke tests are not CUDA performance benchmarks.

## Validation and inspection

```bash
/home/edabk/miniconda3/envs/Autofocus_Camera/bin/python tools/run_unit_tests.py
/home/edabk/miniconda3/envs/Autofocus_Camera/bin/python -m compileall -q app ai camera control gui runtime telemetry tools
python tools/latency_report.py
python tools/latency_report.py logs/latency/latency_trace_<session>.jsonl --session lock_<id>
```

Hardware-free tests cover confirmation, crossing, loss/recovery, blurry/small
faces, recognition during motor activity, single-slot replacement, invalidated
results, queue bounds, scheduler priority, monotonic metrics, rotation, manual
controls, STOP failure, RPC mocks and reconnect behavior. Model smoke tests can
use synthetic images on CPU; they do not establish identity accuracy.

Real RTSP reconnects, preview/Tk interaction, motor timing/safety, absolute zoom
calibration, autofocus, CUDA contention and time-to-identity improvements still
require supervised camera testing. Shutdown requests cancellation/STOP first and
keeps Tk responsive while waiting for AI cleanup, rather than treating a timeout
as successful cleanup. Failed STOP/config restoration remains explicitly logged.

## File inventory

New modules: `runtime/face_worker.py`, `runtime/camera_worker.py`,
`runtime/scheduling.py`, `runtime/target_session.py`, `runtime/person_tracking.py`, `telemetry/latency.py`,
`tools/latency_report.py`, `test/test_hybrid_latency.py`, and this document.

Updated integration: `runtime/inference_worker.py`, `runtime/frame_manager.py`,
`app/state.py`, `gui/cctv_app.py`, `config.py`, `camera.json`.

Updated model instrumentation: `ai/detector.py`, `ai/reid_target.py`,
`person_search/reid.py`, `ai/face/face_detector.py`, `ai/face/face_recognizer.py`.

Updated motor/feedback handling: `camera/ptz_controller.py`, `camera/dahua_rpc.py`,
`control/auto_zoom.py`, `control/auto_focus.py`, `control/target_state_machine.py`,
`control/face_framing.py`.

Updated regression harness/docs: `test/test_reid_target.py`,
`tools/run_unit_tests.py`, `README.md`, `docs/refactor.md`.
