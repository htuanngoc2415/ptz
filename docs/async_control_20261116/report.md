# Asynchronous AI tracking / PTZ — 16-11-2026

## Kết luận

Baseline đã tách capture, inference, auto-pan/tilt và camera I/O thành các worker. Không phát hiện RPC pan/tilt chạy trong vòng inference. Thay đổi này **không biến một pipeline tuần tự thành song song**, cũng chưa chứng minh cải thiện latency trên camera thật. Bổ sung mailbox observation bất biến, kiểm tra thứ tự/generation và đo publish–consume vào worker có sẵn. Mặc định vẫn `legacy`.

Không đổi YOLO26n, ByteTrack, OSNet-AIN, SCRFD, nhận dạng mặt, xử lý overlap, ngưỡng ReID, tốc độ motor, pulse, deadband hoặc chính sách zoom/focus. `ai/hybrid_reid.py`, `ai/overlap_policy.py`, `control/pan_policy.py` khớp byte-for-byte với backup đầu tác vụ; các thay đổi AI xuất hiện trong `git diff` là thay đổi có sẵn trước tác vụ.

## A. Audit concurrency

```text
RTSP decoder/capture → GUI frame callback → FrameManager (latest slot)
                                           ↓
                                    inference worker
                               YOLO → ByteTrack / ReID
                                  ↓             ↓
                         face worker      periodic OSNet worker
                                  ↓
                      target session/state + selected person
                                  ↓
                AutoPanTiltController.submit (no motor RPC)
                                  ↓
                auto-pan-tilt worker → ControlOrchestrator
                                  ↓
                 camera-command worker → HANET WebSocket RPC
```

- `gui/cctv_app.py`: frame callback publishes the latest decoded frame. Display refresh consumes latest display state rather than waiting for pan completion. GUI close delegates shutdown work.
- `runtime/frame_manager.py`: lock/condition, single latest frame; no inference FIFO backlog.
- `runtime/inference_worker.py`: detector, tracker, acquisition/recovery ReID and target decisions remain in AI worker. Periodic OSNet verification is asynchronous in the existing hybrid selector; acquisition/recovery ReID may still block AI on model work. Face branch has its own worker.
- `runtime/target_session.py`: current lock session/track authorization. UUID lock session acts as session generation; tracker generation is carried separately.
- `control/auto_pan_tilt.py`: existing condition, latest intent and independent worker. Network call is outside the condition. AI only waits briefly for shared-state locks and bounded telemetry enqueue, not RPC completion.
- Auto zoom and autofocus retain their workers and arbiter. Pan preview still returns synchronously to inference to preserve the existing zoom-centering interlock.
- `runtime/camera_worker.py`: existing serialized transport lane, bounded normal queue and separate priority STOP queue. STOP cannot interrupt bytes already in flight; it runs after the current bounded transport operation. No additional transport owner was created.
- `camera/hanet_controller.py`: existing READY / COMMAND_IN_FLIGHT / MOTOR_ACTIVE / STOP_UNCONFIRMED / FAULT_LATCHED state model retained. No duplicate motor state machine.
- `runtime/shutdown.py`: bounded close/join and unconfirmed-stop reporting remain.

Actual waiting points: model computation, scheduling/locks, pan worker wake-up (up to its existing 30 ms poll when a notification was missed), pulse/settle eligibility, arbiter availability, serialized camera queue, network response. Capture and inference do not wait on pan RPC. A shared CPU/GPU or Python scheduling contention can still affect throughput; separate threads do not guarantee real-time execution.

## B. Backup and branch

Verified full snapshot:

`/mnt/files/Project/ptz_before_async_control_20261116_162242/project`

Source snapshot root: `/mnt/files/Project/ptz` (includes inner repository, `.git`, models/assets, configs, logs and pre-existing uncommitted work). 8,864 manifest entries, no exclusions. SHA-256, modes and symlink targets checked before source edits. Manifest and initial Git status are alongside `project/`. New branch: `dev/async-observation-20261116`; original HEAD: `ede31989fe6a39b04d7cd4d31675ebd811a28397`.

Latest task attachment explicitly required this backup. Original 08:38 backup remains untouched at `/mnt/files/Project/AI_PTZ_BASELINE_0838_BACKUP_20261116_114845`.

Exact full restoration, after closing the app and checking any reported unconfirmed motor state:

```bash
cd /mnt/files/Project
mv ptz "ptz_retained_before_restore_$(date +%Y%m%d_%H%M%S)"
cp -a /mnt/files/Project/ptz_before_async_control_20261116_162242/project ptz
```

This retains the current development tree and restores the complete old tree including Git state. Do not overlay a backup onto a running app or delete development files with `git reset --hard`. See backup `RESTORE.md` for checksum verification.

## C. Implementation

- New `runtime/target_observation.py`: frozen dataclass; copied bbox/shape and scalar metadata. Carries frame ID, host receive/publish timestamps, target decision timestamp, lock session, track/generation, bbox, optional detection confidence, identity/tracking state, control authorization. No fabricated tracking/identity score. `OBSERVED`, `PREDICTED_ONLY`, `LOST_OR_UNCERTAIN` plus identity state/control flag distinguish observation and authorization; an observed box alone is insufficient authorization.
- `control/auto_pan_tilt.py`: opt-in latest observation slot under existing condition. Monotonic timestamp watermark and last accepted frame/generation survive cancellation. Older/equal receive timestamps, future timestamps and decreasing frame/generation in the same session are rejected before mutating pending state. Replacing an unconsumed observation increments a superseded counter.
- Planning occurs in the existing pan worker. Wire-side validation may also consume the latest observation on the existing camera worker immediately before START. Both paths hold the same condition and do no model inference.
- Pending START must match the current observation timestamp and generation. After START returns successfully, fresh same-direction observations from the same session/generation can maintain the existing bounded pulse. Changed session, generation, direction, expired observation or loss cancels authorization and ends the pulse with STOP.
- Worker exception wrapper disables control and attempts STOP if it owns/has an active motor command. Existing fault persistence and retry policy remain in the backend. Shutdown is bounded; physical stop cannot be guaranteed with an unreachable camera.
- `runtime/inference_worker.py`: adds decision timestamp and tracker generation to publication metadata only.
- `config.py`: `PTZ_EXECUTION_MODE=legacy|asynchronous`, validated at startup. No live mode-switch API; use a clean shutdown and restart.
- New tests and `tools/compare_async_control.py`; two existing test modules now await decision events rather than assuming immediate logging. Async stale samples that also predate the watermark are tested as out-of-order rejection.

## D. Safety and clock domains

Existing arbiter owns all movement. Zoom/manual preemption, target/session guards, backend wire validation, emergency STOP, priority STOP queue, persisted transport faults and bounded stop retry are retained. New START is blocked while STOP is unresolved. A delayed ACK cannot reauthorize a cancelled session; worker checks current state after return. ACK is protocol evidence only, not physical motion/stop telemetry.

All new age fields use host `time.monotonic()` seconds. JSONL envelope uses existing `perf_counter_ns()` and thread name; derive intervals within the same clock representation. Camera-source timestamps remain unknown/unsynchronized and are never used as host receive time. Frame receive is the **decoded application frame** boundary, not sensor exposure or RTSP packet arrival.

Existing structured profiling records frame/inference/detection/tracking, queue and RPC events. New `PAN_OBSERVATION_PUBLISHED` / `PAN_OBSERVATION_CONSUMED` include frame/session/generation and publish–consume time. `PAN_TILT_DISPATCH` adds consume–dispatch time; decision/RPC/STOP events retain command correlation. Rejection counters identify future/out-of-order/frame/generation; superseded counter counts replaced unconsumed observations. Existing decision counters record stale/manual/motor busy gates. Existing queue/RPC instrumentation remains authoritative for enqueue/wire/ACK times.

For a dispatched command, correlate by command ID and frame/session; join published/consumed events by frame/session. Stages: frame→decision + decision→publish + publish→consume + consume→dispatch + dispatch→RPC return. ACK duration is nested in the last stage and must not be added again. Replaced observations have no command latency and must not be treated as missing successful commands.

## E. Validation

No physical camera movement was performed. Transport tests use mock RPC/FakeSocket. Replay never constructs a HANET network client.

- Dedicated async suite: 34 passing tests (includes inherited safety cases). Covers immutable copy, replacement, timestamp/frame/generation rejection, watermark after cancel, predicted-only rejection, pending START obsolescence, delayed START/STOP, START timeout, session invalidation/reacquisition, worker planning crash, bounded close, manual and zoom ownership, stop failure and target loss.
- Actual inference loop with fake detector/ReID processes 12 frames while a mock START is still blocked. This verifies runtime progress, not production neural-network throughput.
- Existing HANET fault suites cover lost/late ACK, STOP timeout, WebSocket disconnect/reconnect, persistent uncertainty, emergency stop, normal queue saturation with STOP priority, bounded retries and shutdown cleanup. They run under both mode settings.
- Full suite, final source: **656 tests in each mode; 654 pass, 2 environment errors**. Legacy: 15.336 s; asynchronous: 15.119 s. Both errors are `test_gstreamer_cpu` import and `test_rtsp_unavailable_schedules_backoff_without_raising`, caused by missing Python `gi` for GStreamer. Logs: `full_tests.log`, `full_async_tests.log`; dedicated suite: `async_tests.log`. `git diff --check` passes. Desktop close responsiveness with a real GStreamer GUI is not measured.

## F. Controlled offline comparison

Same first 100 recorded observations from `docs/ptz_latency_20261116/observations.jsonl`, same controller parameters, synthetic capture 30 Hz and observation publication 20 Hz, mock START latency 20 or 200 ms, mock STOP latency 5 ms. Real controller threads and arbiter. Recorded boxes are replayed at fixed cadence; original video timestamps/ages are not reproduced. This isolates scheduler handoff, not detector or motor performance.

| START delay | Mode | Capture FPS | Observation Hz | Observations during START wait | START / STOP | Frame→dispatch mean | Frame→RPC mean |
|---|---|---:|---:|---:|---:|---:|---:|
| 20 ms | legacy | 30.20 | 20.00 | 7 | 17 / 17 | 17.68 ms | 37.90 ms |
| 20 ms | asynchronous | 30.20 | 20.00 | 7 | 17 / 17 | 17.96 ms | 38.22 ms |
| 200 ms | legacy | 30.20 | 20.00 | 44 | 11 / 11 | 34.50 ms | 234.89 ms |
| 200 ms | asynchronous | 30.00 | 20.00 | 44 | 11 / 11 | 34.42 ms | 234.85 ms |

Both modes close successfully. Async submit mean 0.126 / 0.141 ms; publish→consume mean 0.244 / 3.949 ms at 20 / 200 ms RPC delay. At 200 ms, 67 of 100 async observations were consumed; replacement prevents backlog. `comparison.json` contains sample counts, mean/p50/p95/p99, counters, START rate and process CPU seconds. Only 11–17 commands per run: p95/p99 are descriptive order statistics, not stable tail estimates. Process CPU includes warmup/background effects; the first run is especially affected. Do not interpret CPU differences as an optimization result.

The 390-command **real-camera** reference at 13:49–13:53:

| Interval | Mean | P95 |
|---|---:|---:|
| Frame receive→dispatch | 100.93 ms | 161.85 ms |
| Dispatch→RPC returned | 16.47 ms | 25.55 ms |
| START sent→ACK (nested) | 4.95 ms | 11.11 ms |
| STOP sent→ACK | 5.21 ms | 11.21 ms |
| Frame receive→START RPC returned | 117.40 ms | 179.64 ms |

Offline rows cannot be compared as a speedup against these measurements: they omit real inference, decode, network and GUI load. No new real-camera A/B run was performed. Tracking continuity, identity switches, production detection FPS, GPU usage, GUI responsiveness and physical rotation speed are **not measured** by the observation replay. Model/threshold changes are absent, but that alone does not prove identical live tracking quality. A matched video/model replay and supervised live validation remain necessary for production performance claims.

## G. Run and rollback

From the outer workspace:

```bash
cd /mnt/files/Project/ptz
conda activate py
PTZ_EXECUTION_MODE=legacy python ptz/main.py
# Close the app before switching; then:
PTZ_EXECUTION_MODE=asynchronous python ptz/main.py
```

Default without the environment variable is legacy. Do not mutate `execution_mode` on a running controller. If shutdown reports an unconfirmed STOP, retain fault state and verify the camera using the existing independent stop/recovery procedure before restarting motion.

Offline tests and replay:

```bash
cd /mnt/files/Project/ptz/ptz
PYTHONPATH=.:test /home/admin1/miniconda3/envs/py/bin/python -m unittest test_async_pan_observation -v
PTZ_EXECUTION_MODE=legacy /home/admin1/miniconda3/envs/py/bin/python tools/run_unit_tests.py
PTZ_EXECUTION_MODE=asynchronous /home/admin1/miniconda3/envs/py/bin/python tools/run_unit_tests.py
/home/admin1/miniconda3/envs/py/bin/python tools/compare_async_control.py
```

Profiling: `PROFILING` defaults enabled; if overridden, set `profiling.enabled: true` and `log_format: "jsonl"` in `ptz/camera.json`, preserving the other fields. Optional output/scenario:

```bash
cd /mnt/files/Project/ptz
PTZ_PROFILE_DIRECTORY=/tmp/ptz_async_profile PTZ_PROFILE_SCENARIO=async_validation PTZ_EXECUTION_MODE=asynchronous /home/admin1/miniconda3/envs/py/bin/python ptz/main.py
```

Launching the real application uses the configured camera and normal automatic-control settings; these launch commands were not executed during this task.

## H. Remaining limitations

The consumer can still wait for the active RPC; the latest slot replaces intermediate observations during that wait. Emergency STOP is prioritized but cannot preempt a blocking socket operation already in progress. Bounded worker joins report failure instead of guaranteeing motor stop when the transport is lost. ACK does not establish physical onset latency or angular velocity. No continuous-velocity control was introduced. Keep legacy default until live validation is complete.
