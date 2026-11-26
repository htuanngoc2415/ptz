# Temporary mode: fresh target identification on every processed frame

Enabled in config.py:

```python
TARGET_CONTINUOUS_REID = True
FACE_TRACKING_ENABLED = False
```

The inference loop bypasses PersonTracker.update. Every fresh YOLO detection set is scored against the reference by OSNet, including after target lock and on partial-body crops. Target selection does not use the previous bbox, predicted coordinates or optical flow. A candidate must pass the existing acquire similarity threshold (0.65 by default) and top-two margin (0.08), with three consecutive accepted observations for initial acquisition. All locked-frame selections also require the acquisition threshold; the weaker legacy keep threshold does not authorize movement here.

The selected person is assigned a stable logical ID for the lock session. Other detections receive per-frame negative labels solely for excluding neighbours. These labels are not tracked. This avoids inheriting an obsolete spatial track ID after geometry changes. Session/ID tokens still protect asynchronous recognition and motor jobs.

A low-score, absent or ambiguous candidate suspends association immediately. Existing session validity and motor freshness checks block movement; a return requires three accepted observations. Old face jobs are invalidated by the existing held-target path. Long absence still runs the existing target-loss/recovery lifecycle. No safety guard or RPC protocol is bypassed.

Face geometry comes only from SCRFD detections on submitted frames; optical flow and its feature initialization are disabled. Historical face IoU association is bypassed in this mode, but current target-head geometry, exclusion of other people, asynchronous-result source freshness and source/current person compatibility remain. ArcFace stays event driven: continuous target identification means repeated OSNet, not repeated ArcFace after identity confirmation.

This increases OSNet and SCRFD workload, so throughput and target confidence on zoomed/partial crops need a real-camera run. Initial thresholds are unchanged. No live camera commands were sent during implementation.

Restart main.py to activate. To restore the previous tracking-based branch, set TARGET_CONTINUOUS_REID=False and FACE_TRACKING_ENABLED=True, then restart. Startup logs identify the active target-selection mode.

Offline tests cover fresh scoring after large displacements, changing detection IDs, ambiguous people, confidence loss/reconfirmation, full target loss, bypass of PersonTracker and optical-flow initialization, stable session ID and zoom observation submission. Existing tracking tests explicitly keep legacy mode so both branches remain checked.

Validation: `python tools/run_unit_tests.py` passed 293 tests in 5.989 seconds; see `logs/continuous_reid_tests.log`. Compile checks also passed. Camera/GPU throughput for this mode remains unmeasured.
