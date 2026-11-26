# Zoom latency, immutable intent and PositionABS motion safety fix

Date: 2026-10-09. Implementation follows the historical snapshot in
`ZOOM_POSITION_TECHNICAL_AUDIT.md`. No camera connection, motor command, hardware
benchmark, calibration change or timeout increase was performed for this change.

## Root causes and changes

1. An observation could be 779 ms old during planning and expire before dispatch.
   Worker validation previously used mutable `_last_observation`, which could be
   refreshed independently of the geometry that produced the motor destination.
   Each START now owns a frozen `ZoomIntent`: command/frame ID, original decoded
   frame timestamp, immutable bbox tuple, ratio, session/track, lens timestamp,
   generation, expiry, target domain/destination and planning timestamp. Worker
   preflight and the actual RPC wire boundary validate this intent, including time
   spent in the camera command queue and speed configuration. No old bbox is
   re-stamped. Normal source freshness remains 800 ms.
2. Worker rejection conflated session, track, person policy and spatial freshness.
   Logged boolean flags now distinguish `STALE_SESSION`, `STALE_TRACK`,
   `STALE_PERSON_ZOOM_POLICY`, `STALE_OBSERVATION` and
   `STALE_COMMAND_GENERATION`. A stale unsent request can replan from a genuinely
   newer observation with the same session, track and domain. It recomputes the
   ratio and destination; it does not renew the old intent. A known wire-guard
   rejection does not latch `START_FAILED`/ERROR. Unknown RPC failures retain the
   existing conservative STOP/fault path. Correction budget advances on ACK,
   rather than consuming the budget for rejected unsent requests.
3. Person lock already had an initial early submission. It now also supplies fresh
   body feedback before face-result consumption/quality/ArcFace and submits fresh
   person framing until face authority latches. A newly available stable face can
   supersede that early request in the same iteration. After face latch or final
   face framing lock, body geometry never selects person ratio 50% again.
4. A detector miss was treated as a request to stop even a valid absolute move.
   Motion safety now has independent fresh, same-session/track person evidence.
   The body must have a finite, nonempty bbox intersecting the image and a center
   inside horizontal 5% margins. This is a conservative FOV check, not a calibrated
   guarantee about the final face position. A short face miss can continue only
   the existing FACE PositionABS command. It never launches a replacement command
   or pulse. The grace is bounded by the existing soft face-loss timeout (1 s),
   independently of the absolute motor timeout (8 s). Persistent face loss,
   uncertain/mismatched/out-of-FOV/stale tracking, session loss, face-edge feedback,
   head recovery and other safety reasons still STOP. No evidence means no
   continuation. Fresh person feedback does not renew the intent's START expiry.
5. The observed 227 ms scheduler wait included SCRFD work on the shared lane,
   despite SCRFD/ArcFace reporting CPUExecutionProvider. Explicitly CPU-only ORT
   sessions now use a separate CPU lock in the existing face worker. They do not
   own the accelerator lane. GPU or unknown/mixed providers stay on the shared
   accelerator lane with detection priority. No worker, model instance or GPU
   context is added. Active GPU inference is not preempted. Face jobs validate
   after their lane wait; queued recognition has a bounded queue-age check while
   preserving the separation of historical identity and motor spatial freshness.
   Detection reloads the latest frame after acquiring its lane, updates frame
   correlation, and skips expired frames before model inference.
6. Safety STOP has a priority mailbox entry and immediately cancels queued START
   validation. A later normal framing observation cannot overwrite that STOP.

## STOP semantics

The existing wire convention remains:

```
START: ptz.start(code="PositionABS", arg3=integer GUI coordinate)
STOP:  ptz.stop(code="ZoomTele")
```

Mocks verify which codes are sent, not that a physical motor stops. Adapter STOP
success is now described as an ACK with unverified physical motor state.
`ZOOM_STOP_ACK` records `motor_stopped=null`, `evidence=rpc_ack_only`.
`ZOOM_STATUS` separately records after-ACK telemetry, and the existing target
arrival checks still require post-ACK status and destination tolerance. Calibration
LUT, the 6.4–37 verified-command guard and coordinate quantization are unchanged.

## Metrics and evidence

| Requirement | Event/metric |
|---|---|
| Lock → first valid plan | FIRST_VALID_ZOOM_PLAN / lock_to_first_valid_zoom_plan_ms |
| Lock → first actual zoom RPC | FIRST_ZOOM_COMMAND_SENT / time_to_zoom_command_ms (existing) |
| Observation age at planning | ZOOM_OBSERVATION_AT_PLANNING / observation_age_at_planning_ms |
| Observation age at wire dispatch | ZOOM_DISPATCH_VALIDATION / observation_age_at_rpc_dispatch_ms |
| Planning/worker/wire latency | ZOOM_WORKER_DISPATCH; ZOOM_PLANNING_TO_RPC |
| Scheduler queue wait | INFERENCE_QUEUE_ENTER/EXIT, branch and resource |
| Skipped queued work / latest frame | INFERENCE_JOB_SKIPPED; DETECTION_FRAME_REFRESHED |
| Replans | ZOOM_REQUEST_REPLANNED; zoom_request_replanning_count |
| Absolute commands actually sent | ZOOM_COMMAND_SENT(PositionABS); absolute_zoom_command_count |
| Absolute cancellation and reason | ABSOLUTE_ZOOM_CANCELLED; absolute_zoom_cancellation_count |
| Lens telemetry after ACK | ZOOM_STATUS(after_ack=true) |
| Arrival | ZOOM_TARGET_REACHED / ZOOM_COMPLETED (existing) |
| Frame vs lens time | frame_telemetry_timestamp_difference_ms in planning log/zoom summary |

Zoom summaries also retain replan/cancellation counts and reasons, first valid
plan latency, observation ages, signed frame/lens timestamp difference, latest
post-ACK zoom value and target-reached count. Trace events preserve individual
samples; latency CSV provides distributions for the stage metrics. Frame timestamp
means decoded application receive, not synchronized sensor capture. No sensor
latency is inferred. Control durations use monotonic differences; profiler stage
spans continue to use perf_counter_ns within the same clock domain.

## Files

- control/auto_zoom.py: immutable-intent lifecycle, specific validations, bounded
  replanning, independent tracking safety, short-miss continuation, priority STOP.
- runtime/zoom_intent.py: frozen intent definition.
- runtime/inference_worker.py: early person feedback/planning, latest frame refresh,
  same-iteration face takeover and conditional continuation.
- runtime/scheduling.py: CPU-only resource isolation, post-wait validation.
- runtime/face_worker.py: provider-aware lanes and stale-queued-work checks.
- camera/dahua_rpc.py: final wire guard and actual PositionABS send count.
- camera/ptz_controller.py: guard forwarding and explicit STOP ACK semantics.
- telemetry/zoom_trace.py: new event/summary accounting.
- test/test_zoom_latency_safety.py: 18 new acceptance/regression tests.
- test/test_reid_target.py: adapter mock explicitly denies continuation without
  validated evidence; final-framing assertion distinguishes the iteration which
  first establishes the latch from subsequent already-latched iterations.

## Offline verification

211 tests passed in 12 suites, using fake lens objects, MockFirmware, fake model
outputs, bounded queues and worker synchronization. Included A–G, late expiry at
wire dispatch, stop priority, CPU-only lane isolation, loss of tracking during
motion, bounded face-miss grace, immutable evidence/replanning and trace summary.
Existing absolute completion, correction budget, timeout, crossing/identity,
framing/oscillation, autofocus and RPC contract regressions also passed.

Reproduce from the repository root with the installed Python environment:

```bash
/home/edabk/miniconda3/envs/Autofocus_Camera/bin/python - <<'PY'
import sys, unittest
sys.path.insert(0, 'test')
# GUI adapters imported by older mock tests need GI; no RTSP player is started.
sys.path.append('/usr/lib/python3/dist-packages')
names = ['test_zoom_latency_safety', 'test_absolute_zoom_audit', 'test_auto_zoom',
         'test_hybrid_latency', 'test_pipeline_identity_zoom', 'test_zoom_oscillation',
         'test_dahua_camera', 'test_runtime_architecture', 'test_reid_target',
         'test_control_regression', 'test_crossing_retention', 'test_final_framing']
result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromNames(names))
raise SystemExit(not result.wasSuccessful())
PY
```

No measured device latency improvement is claimed. Still requiring separately
authorized supervised device verification: PositionABS STOP convention, physical
stop/arrival/AF settling, CPU/GPU concurrency under device resource pressure,
track continuity/FOV margins during zoom and the practical short-miss grace.
An already in-flight HTTP transaction cannot be preempted; cancellation rejects
queued STARTs immediately, while physical STOP still depends on serialized camera
I/O and firmware behavior.
Optical calibration and PositionABS low-end behavior remain outside this fix.
