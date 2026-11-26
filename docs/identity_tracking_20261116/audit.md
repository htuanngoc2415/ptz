# Identity tracking audit and design — 2026-11-16

## Verified architecture before edits

`camera/stream.py` → `runtime/frame_manager.py:FrameManager.publish` (monotonic decoded-arrival timestamp; sensor timestamp generally unknown) → `runtime/inference_worker.py:ai_worker` → `ai/detector.py:PersonDetector.detect` (YOLO person model) → `runtime/byte_tracking.py:ByteTrackPersonTracker.update` → `ai/hybrid_reid.py:HybridReIDTargetSelector.select_person` → `TargetSession.publish` → existing pan/zoom workers. `runtime/face_worker.py` runs SCRFD/recognition separately. `person_search/reid.py:OSNetReID` loads OSNet-AIN, normalizes 512-D features, and shares the inference scheduler.

1. OSNet is **not required every frame** in the configured hybrid path (`TARGET_CONTINUOUS_REID=False`). Initial acquisition and recovery synchronously score candidates; stable tracking runs background verification every second. The alternate continuous path does gate every frame.
2. Hybrid already distinguishes `target_uid`, `track_id`, `lock_session_id`, and `track_generation`.
3. Scores below KEEP=.50 accumulate negatives; three negatives enter HOLD. Acquisition uses .65, reacquisition .70, ambiguity margin .08. Evidence older than five seconds also forces HOLD. These are different thresholds, not a universal .70 gate.
4. HOLD: missing track, smaller-box overlap, raw prior/current IoU below .25, ambiguous verification, repeated negative scores, or verification timeout (`hybrid_reid.py:select_person/_take_verification`). Raw IoU cannot distinguish camera motion from person discontinuity.
5. `reid_target.py:finish_frame` resets after missing-frame threshold AND three seconds since loss. Hybrid ignores face observations when body is missing. Missing face alone does not lose a visible body.
6. `reset(preserve_reacquire=True)` retains the reference/gallery and hybrid target UID but clears person box, pending evidence, and verification history. `InferenceWorker.reset_tracking_worker` also resets ByteTrack on full loss.
7. HOLD publishes a null motor authorization; recovery rotates the lock session even for the same native ID. Stable periodic verification does not rotate it. `auto_zoom.py:_control_loop` counts stale-session checks even for control-only STOP messages, explaining why mismatch counts exceed motor dispatches. This is not evidence of transport failure.
8. Background verification carries epoch/session/track/time/frame; face results carry generation/session/track/frame and use validity checks. Acquisition is synchronous and rechecked for age before motor publication. The new path must retain these protections and move acquisition/recovery inference off detection.
9. `ai/reid/gallery.py:TargetEmbeddingGallery` holds five photometric variants of the original image and uses maximum cosine. There is no trusted live-view admission, provenance, or rollback.
10. `runtime/camera_motion.py` already estimates masked sparse-flow/RANSAC affine motion with validity checks. Runtime ByteTrack only logs it. `camera_motion_shadow.py` applies translation to XYAH in offline experiments; runtime compensation is disabled.

## Evidence and limits

Original application log `logs/ptz_20261116_083342.log:780` confirms SEARCHING at 08:38:05.480; lines 783 and 869–871 show .512/.516/.510/.502 scores, with zero confirmations. The report's 77.559-second unrecovered interval ends at GUI shutdown, not at a recovered lock. These scores alone cannot establish whether the same person remained visible.

`16_11(1).mov` exists, 1920×1080, 29.97 FPS, duration 235.603 s. It cannot automatically be time-aligned with the full 340-second run. The requested 113–190-second interval is present. Final wall-clock loss coverage needs explicit alignment. Do not infer ground-truth identities from native tracker IDs or face MATCH labels.

`face_worker.py:_detect` rejects faces outside the upper-body/full containment rule, faces shared by another body, previous-face IoU failures, and multiple eligible faces under the same FACE_ASSOCIATION_AMBIGUOUS label. This conflates rejection reasons; thresholds will remain unchanged while diagnostic subreasons are added.

Installed Conda `py`: Python 3.10.20, torch 2.7.1+cu118, OpenCV 4.13.0, Ultralytics 8.4.25; CUDA available. The shell's base Python lacks torch. Model weights and target images exist. No repository Git metadata exists; create a local source backup before edits.

## Implementation design

Opt-in `PTZ_IDENTITY_TRACKING=1`; default retains the existing hybrid path. Reuse installed Ultralytics BoT-SORT XYWH Kalman/lifecycle/association with its extra encoder disabled. Apply only quality-gated translation/near-axis-aligned scale from the existing estimator, once before matching; reject rotation and unreliable transforms. Export observed detections separately from bounded predictions and record association margin/crop quality. OSNet assists target identity association and recovery; native multi-object association remains geometric.

A dedicated selector extending existing public interfaces owns persistent identity, bounded temporal evidence, async single-flight verification/acquisition/recovery, conservative ambiguity gates, reference-anchored live gallery, and an immutable observation contract. Three distinct fresh positive result frames confirm acquisition/rebinding; .65/.70 thresholds remain unchanged. Motion-supported appearance dips retain continuity for a configurable bounded interval, with increased verification; hard contradiction/overlap/missing observations revoke motor authorization. Full loss retains trusted identity/history, never drives motors with predictions, and uses existing bounded zoom recovery.

Diagnostics distinguish observed/uncertain/occluded/lost/reacquired, tracker rebindings from annotated identity switches, source-frame provenance, GMC validity, and control permission. Offline video/cached-evidence replay imports no camera transport and produces paired decisions and annotation-aware metrics. CPU synthetic tests cover safety independently of model inference.
