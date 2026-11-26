# Event-driven face pipeline

Person detection, spatial association and OSNet still establish the target session. The existing inference worker submits person zoom immediately after lock, before submitting face work. Motor RPC, PositionABS/fallback, zoom watchdog/recovery, zoom/focus ownership, person 46–54% and face 22–28% bands are unchanged.

The existing FaceWorker now owns a latest-only spatial mailbox and a single biometric worker. SCRFD searches the target head ROI, excludes faces inside another person's box, and retains the previous face IoU/ambiguity guard. Between detections, forward/backward Lucas–Kanade flow and RANSAC affine estimation update the face. Flow uses an image downscaled to at most 640 pixels; recognition always uses the original source frame and SCRFD landmarks. Tracking does not generate biometric observations.

A bounded best-shot buffer scores size, sharpness, landmark pose/roll, illumination, contrast, stability and alignment geometry. It stores original frame/session/track metadata and an independent snapshot crop. There is no unvalidated occlusion classifier. Alignment is performed once, from the selected original frame, immediately before R50. Snapshot scoring continues after identification, without periodic ArcFace calls.

The identity states are UNIDENTIFIED, COLLECTING, RECOGNIZING, CANDIDATE, IDENTIFIED, UNKNOWN and REVERIFY. Existing multi-observation confirmation remains authoritative. Confirmed identity stops biometric scheduling. Unknown results retry with a cooldown; candidate timestamps and small grayscale signatures suppress reuse and nearly identical shots. A duplicate image can become a new observation after the configured two-second cooldown; this is temporal independence, not proof of a changed pose. A timeout admits lower quality candidates with valid landmarks rather than waiting indefinitely.

Session/track/generation changes discard queued work and reject late results. Short face misses preserve identity/snapshot. Reacquisition after the timeout clears biometric evidence and the candidate buffer and requires re-verification. Ambiguous crossings invalidate the existing lock branch. The conservative temporal IoU guard may decline a large face displacement until the person/session is reacquired; it does not silently transfer identity.

The GUI draws a bounded overlay after Digital PTZ, using worker-produced crops only. It shows state, confirmed name, cosine similarity, optional session/track and temporary face loss. No inference or image-quality work occurs in Tk callbacks. `FACE_PANEL_ENABLED` hides it without changing preview geometry.

## Configuration

All existing model paths and the R50-specific 0.45 match threshold remain unchanged. Important new defaults in config.py:

| Setting | Default | Meaning |
|---|---:|---|
| FACE_PIPELINE_MODE | optimized | baseline enables the former interval/strict-quality branch for replay |
| FACE_DETECTION_HZ | 3 | Stable detection correction rate; misses/lens movement retry on fresh submissions |
| FACE_TRACK_MAX_AGE | 0.65 s | Maximum elapsed time since trustworthy detection |
| FACE_TRACK_MIN_CONFIDENCE | 0.7 | Flow survival and RANSAC inlier gates |
| FACE_TRACK_FB_ERROR | 1.5 px | Forward/backward error at tracking resolution |
| FACE_TRACK_MAX_SCALE_CHANGE | 0.06 | Larger per-frame scale changes force detection |
| FACE_TRACK_MAX_DIMENSION | 640 | Maximum tracking image dimension |
| FACE_CANDIDATE_LIMIT | 5 | Maximum stored candidate frames, plus the best snapshot |
| FACE_CANDIDATE_SPACING | 0.35 s | Minimum sampling separation |
| FACE_QUALITY_MIN_SCORE | 0.65 | Weighted score admission threshold |
| FACE_QUALITY_FALLBACK_TIMEOUT | 2 s | Relax score admission after unsuccessful collection |
| FACE_CANDIDATE_MAX_AGE | 3 s | Reject old candidates before model admission |
| FACE_UNKNOWN_RETRY_INTERVAL | 1.5 s | Unknown retry cooldown |
| FACE_REACQUIRE_REVERIFY_TIMEOUT | 2 s | Reverify after prolonged face absence |
| FACE_LENS_SETTLE_TIME | 0.2 s | Post-zoom stability gate |

Quality weights, flow pyramid depth and duplicate suppression are also configurable. These are initial operational settings, not calibrated accuracy claims. Zoom/focus instability is captured in each job; returning to stable lens state forces detector correction. Existing ORT sessions/providers and model weights are reused. No I/O binding changes were made without evidence of a benefit. Future recognizer changes require rebuilding database embeddings, a new model identifier, dimension/schema compatibility checks and threshold calibration; the current database remains R50/512-dimensional.

The accelerator scheduler gives waiting person detection precedence over face detection, and face detection over recognition. CPU-only ORT sessions may execute independently with one owner per model. An already-running accelerator kernel cannot be preempted: recognition can still occupy the shared accelerator until that inference completes. Tracking and motor dispatch do not wait for biometric completion; detector starvation/expiry remains guarded by existing observation freshness and watchdogs. Actual GPU contention must be measured on the deployment hardware.

## Verification and replay

Verification completed: `python tools/run_unit_tests.py` passed **286 tests in 6.114 seconds**; output is saved in `logs/face_pipeline_unit_tests.log`. `python -m compileall -q runtime gui config.py tools/benchmark_face_pipeline.py` also passed. Legacy tests explicitly use baseline mode where they rely on synchronous completion or invalid synthetic landmarks. The new optimized tests use real crops/flow with mocked neural inference: confirmation stops ArcFace, slow recognition leaves geometry available, late-session results are rejected, crossing people are excluded, short misses preserve the snapshot, reacquisition requires verification, Unknown retries are bounded, quality fallback and best-shot ordering work, tracker expiry/occlusion/resolution changes fail closed, and the overlay preserves frame dimensions. Existing motor/zoom/recovery tests remain in the suite.

Example of a reproducible face-branch replay:

```bash
python tools/benchmark_face_pipeline.py test/recordings/camera_20261001_103938.mp4 \
  --roi 0 0 2448 2048 --start 20 --seconds 5 --fps 5 \
  --output logs/face_pipeline_ab_closeup_final.json
```

This uses an explicitly supplied fixed target box in both runs. It isolates the face branch; it is not a YOLO/OSNet/Auto Zoom end-to-end benchmark. The video contains a second person in the background; without per-frame person annotations this replay cannot validate crossing association or false-match rates. Accuracy and full-system metrics remain null in the artifact. Source-frame seeking, CPU decoding and sequential run order also limit timing comparisons.

The recorded CPU replay in `logs/face_pipeline_ab_closeup_final.json` submitted 25 observations per mode. Baseline made 19 SCRFD / 0 ArcFace calls; optimized made 16 SCRFD / 2 ArcFace calls, both returning Unknown. Baseline rejected the blurry faces under its strict sharpness gate; optimized exercised fallback. SCRFD p50/p90 was 234.33/309.88 ms baseline and 305.62/386.13 ms optimized; optimized flow was 15.14/17.86 ms and ArcFace 568.68/738.59 ms. These measurements do **not** demonstrate a performance improvement or reduced ArcFace use on a correctly identified target. They expose a CPU cost/quality tradeoff requiring deployment tuning. Unit tests establish scheduling/caching behavior, not biometric accuracy.

Existing latency reports provide bounded-window p50/p90 for SCRFD, ArcFace, alignment, focus/face quality, candidate assessment, tracking and scheduler/mailbox waits. Added counters/rates cover actual detector/recognizer calls, tracking attempts/losses, reverify and association conflicts; existing rejection/drop metrics and zoom traces retain session mismatch, first face, identity confirmation and first zoom timing. Tracking FPS counts attempted flow updates, not successful associations. Missing hardware utilization, ground truth or sensor timing is never inferred.

A real camera is still required for lens-motion/focus transitions, physical motor response, OSNet crossing/occlusion association, recovery and watchdog behavior under network delays, full Tk preview responsiveness, GPU memory/transfer contention and sustained CPU/GPU utilization. Annotated same-video trials are needed for false matches, identity switches and time-to-confirmation comparisons. The provided clip did not produce a confirmed identity during this replay.

## Changed files

- runtime/face_worker.py: session lifecycle, two bounded branches, cache/states, detector cadence, snapshot publication.
- runtime/face_tracking.py: lightweight flow tracker and failure/freshness gates.
- runtime/face_candidates.py: scoring, bounded best-shot selection and duplicate suppression.
- runtime/inference_worker.py: lens state, panel publication and old-identity clearing.
- runtime/scheduling.py: accelerator priorities and independent CPU sessions.
- gui/face_panel.py and gui/cctv_app.py: snapshot overlay after Digital PTZ.
- config.py and telemetry/latency.py: tunable defaults and face rates/counters.
- tools/benchmark_face_pipeline.py: same-video baseline/optimized replay and JSON evidence.
- test/test_event_face_pipeline.py: optimized-path tests; three existing test modules explicitly select baseline for legacy contracts.
