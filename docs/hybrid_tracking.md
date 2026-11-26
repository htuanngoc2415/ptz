# ByteTrack + OSNet, contextual framing

Implemented 2026-11-10 in the `ptz` application. Restart the application in Conda `py` to load the changes.

## Assessment

Use YOLO on the substream, ByteTrack (Kalman prediction and two-stage association) for motion continuity, and OSNet for target identity. ByteTrack IDs describe trajectories, not guaranteed identities. Do not reset IDs every frame or on every lens movement. A person crossing another person can cause an ID switch without a missing ID, so invoking ReID only after disappearance is insufficient.

Reference: [ByteTrack paper](https://arxiv.org/abs/2110.06864). The adapter uses installed Ultralytics 8.4.25, pinned in requirements, with LAP 0.5.14 installed in Conda `py`.

## Implemented behavior

- YOLO detection threshold 0.10 feeds weak detections into ByteTrack. Acquisition/identity control still requires confidence >= 0.35. Original detection indices are preserved through high/low partitions; weak boxes cannot initiate an identity lock.
- Stable, spatially continuous tracks skip OSNet until the 1-second verification interval. Missing IDs, changed IDs, large spatial changes, or overlapping candidates trigger verification sooner. OSNet scores all eligible candidates against the original target gallery.
- Rejected verification suppresses the target, invalidates motor/face session authorization, and requests an immediate automatic zoom stop. A high-scoring alternative cannot silently inherit the old target session.
- Overlap holds control until the scene is unambiguous. Recovery requires 3 successive spatially consistent confirmations and a score margin over other people. It rotates the session and increments track generation even when the recovered tracker number is unchanged.
- Temporary loss retains the target reference for 3 seconds. Persistent missing observations (also subject to the existing frame-count gate) drop the lock and reacquire against the same reference. Widening recovery begins after 3.5 seconds when automatic zoom is enabled. No predicted-only boxes authorize camera motion.
- Original target gallery remains unchanged; live crops cannot gradually contaminate the identity reference.
- `CONTEXT_FRAMING=True`: person height target is 40% of frame (36–44% band); faces cannot take optical zoom ownership. Near frame edges, additional zoom-in is blocked. Digital PTZ follows the body rather than switching to a face crop. Automatic zoom remains controlled by the existing UI toggle.
- Display remains mainstream; inference remains substream.

## Deliberate limits

No color histogram is needed in the default identity decision: similar clothing, shadows and camera white balance can confuse it. A histogram could later serve as a cheap suspicion trigger, but should not authorize target recovery.

This is dynamic person-centered framing, not a calibrated elevator-door ROI. A fixed door/window region needs scene coordinates or a camera preset. A 40% person target leaves more context but cannot guarantee that all 3–4 surrounding people stay visible. Digital zoom settings can further reduce displayed context.

Camera movement changes image geometry; ByteTrack's motion model does not compensate for camera motion here. Geometry changes trigger OSNet checks, but rapid motion, occlusion, matching clothes, or low-resolution crops can still prevent identification. During ambiguity the policy favors stopping over following an uncertain person. There remains up to the verification interval of exposure to a silent ID swap with no geometry/overlap trigger. Crowded scenes can trigger OSNet frequently, so GPU savings are scene-dependent.

No real-camera movement or labeled-video accuracy benchmark was performed for this change. The 1-second, 3-second and score thresholds are starting values, not a proven global optimum.

## Validation

Conda `py`: 11 hybrid/ByteTrack/context tests, 42 existing ReID/worker tests, and 59 zoom tests passed. Hybrid tests include a real worker with mocked detections and motor calls. Existing face-framing tests explicitly select legacy framing so they continue validating that optional behavior.

Tests cover low-confidence association, source-index preservation, no ID reuse after reset, same-ID rejection, crossing ID swaps, changed-ID recovery, temporary/full loss, reference preservation, no face zoom takeover, and edge zoom suppression. OSNet scores are scripted in tests; these validate control logic, not real-world ReID accuracy.

Key code: `runtime/byte_tracking.py`, `ai/hybrid_reid.py`, `runtime/inference_worker.py`, `control/zoom_policy.py`, `config.py`.
