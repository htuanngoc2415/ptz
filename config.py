#!/usr/bin/env python3

"""
Global configuration for AI PTZ Camera.

Architecture:

    camera.json
        ↓
    config.py
        ↓
    ┌──────────────┐
    │ application  │
    └──────────────┘

camera.json:
    Camera-specific configuration.

config.py:
    AI / control / GUI configuration.
"""

from __future__ import annotations
from event_logging import event_print as print

import json
import os
from pathlib import Path


# ============================================================
# PROJECT PATH
# ============================================================

PROJECT_ROOT = Path(
    __file__
).resolve().parent


# ============================================================
# CAMERA CONFIG FILE
# ============================================================

CAMERA_CONFIG_PATH = (
    PROJECT_ROOT
    /
    "camera.json"
)


def load_camera_config() -> dict:
    """
    Load camera.json.

    Raises a clear error when configuration is missing or
    malformed.
    """

    if not CAMERA_CONFIG_PATH.exists():

        raise FileNotFoundError(
            "\n"
            "Không tìm thấy camera.json:\n"
            f"{CAMERA_CONFIG_PATH}\n"
        )

    try:

        with open(
            CAMERA_CONFIG_PATH,
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(
                file
            )

    except json.JSONDecodeError as exc:

        raise RuntimeError(
            "\n"
            "camera.json không đúng định dạng JSON.\n"
            f"{exc}\n"
        ) from exc

    if not isinstance(
        data,
        dict
    ):

        raise RuntimeError(
            "camera.json phải chứa JSON object."
        )

    return data


CAMERA_CONFIG = (
    load_camera_config()
)

# Validated by ImageSettingsClient before sending any camera command.
# Process-local opt-in / rollback; never rewrites camera.json.
if os.environ.get('PTZ_VIDEO_BACKEND'):
    _video_backend = os.environ['PTZ_VIDEO_BACKEND']
    if _video_backend not in ('mpv', 'gstreamer', 'gstreamer_cpu', 'opencv'):
        raise ValueError('Invalid PTZ_VIDEO_BACKEND')
    CAMERA_CONFIG['video_backend'] = _video_backend
BENCHMARK_READ_ONLY = os.environ.get('PTZ_BENCHMARK_READ_ONLY') == '1'

BACKLIGHT_CONFIG = CAMERA_CONFIG.get("backlight", {"enabled": False})


# ============================================================
# HELPER
# ============================================================

def camera_value(
    key: str,
    default=None,
    required: bool = False,
):

    if key in CAMERA_CONFIG:

        return CAMERA_CONFIG[
            key
        ]

    if required:

        raise KeyError(
            f"camera.json thiếu field bắt buộc: {key}"
        )

    return default


# ============================================================
# RTSP
# ============================================================

RTSP_URI = camera_value(
    "uri",
    required=True,
)

RTSP_USERNAME = camera_value(
    "username",
    "",
)

RTSP_PASSWORD = camera_value(
    "password",
    "",
)

RTSP_LATENCY_MS = int(
    camera_value(
        "latency_ms",
        200,
    )
)


# ============================================================
# PTZ CAMERA
# ============================================================

PTZ_IP = camera_value(
    "ptz_ip",
    required=True,
)

PTZ_USERNAME = camera_value(
    "ptz_username",
    RTSP_USERNAME,
)

PTZ_PASSWORD = camera_value(
    "ptz_password",
    RTSP_PASSWORD,
)

PTZ_BACKEND = camera_value('ptz_backend', 'dahua')
if PTZ_BACKEND not in ('hanet', 'dahua'):
    raise ValueError('Invalid ptz_backend')

PTZ_PORT = int(camera_value('ptz_port', 80))

PTZ_CHANNEL = int(
    camera_value(
        "ptz_channel",
        0,
    )
)

PTZ_TIMEOUT = float(
    camera_value(
        "ptz_timeout",
        2.0,
    )
)

PTZ_SPEED = int(
    camera_value(
        "ptz_speed",
        100,
    )
)


# ============================================================
# OPTICAL ZOOM
# ============================================================

MIN_OPTICAL_ZOOM = float(
    camera_value(
        "min_zoom",
        1.0,
    )
)

MAX_OPTICAL_ZOOM = float(
    camera_value(
        "max_zoom",
        37.0,
    )
)

FULL_ZOOM_TIME = float(
    camera_value(
        "full_zoom_time",
        4.8,
    )
)

# When resetting:
#
# ZOOM OUT for:
#
#     FULL_ZOOM_TIME
#          +
#     RESET_ZOOM_EXTRA_TIME
#
# to make sure the lens reaches its mechanical wide limit.

RESET_ZOOM_EXTRA_TIME = 1.0


# ============================================================
# MODEL PATHS
# ============================================================

# ------------------------------------------------------------
# PERSON MODEL
# ------------------------------------------------------------

def resolve_model_path(path: str | Path) -> Path:
    """Resolve model paths from the project root, including environment overrides."""
    resolved = Path(os.path.expandvars(str(path))).expanduser()
    if not resolved.is_absolute():
        resolved = PROJECT_ROOT / resolved
    return resolved.resolve()


PERSON_MODEL_PATH = str(resolve_model_path("models/person/person.pt"))

# ------------------------------------------------------------
# FACE MODELS (ONNX-only InsightFace pipeline)
# ------------------------------------------------------------

FACE_DETECT_MODEL = str(resolve_model_path("models/face/det_2.5g.onnx"))

# Backward-compatible name used by older code. This is no longer face.pt.
FACE_MODEL_PATH = FACE_DETECT_MODEL


# ============================================================
# DEVICE
# ============================================================

# Ultralytics:
#
#     0       -> cuda:0
#     "cpu"   -> CPU

# Optional diagnostic override; GPU remains the default.
_device_override = os.getenv("PTZ_DEVICE", "0").strip()
DEVICE = int(_device_override) if _device_override.isdecimal() else _device_override
ARCFACE_DEVICE = os.getenv("PTZ_ARCFACE_DEVICE", "cuda").strip().lower()
if ARCFACE_DEVICE not in ("cpu", "cuda"):
    raise ValueError("PTZ_ARCFACE_DEVICE must be cpu or cuda")

# Use the validated FP16 model on CUDA; explicit CPU mode uses the FP32 original.
_arcface_cpu = str(DEVICE).strip().lower() == "cpu" or ARCFACE_DEVICE == "cpu"
FACE_RECOGNITION_MODEL = str(resolve_model_path(os.getenv(
    "PTZ_ARCFACE_MODEL",
    str(PROJECT_ROOT / "models" / "face" /
        ("w600k_r50.onnx" if _arcface_cpu else "w600k_r50.fp16.onnx")),
)))


# ============================================================
# PERSON DETECTOR
# ============================================================

PERSON_CONF_THRESHOLD = 0.35

PERSON_IOU_THRESHOLD = 0.60

PERSON_CLASS_ID = 0

PERSON_IMGSZ = 640


# ============================================================
# FACE DETECTOR (SCRFD)
# ============================================================

FACE_DET_INPUT_SIZE = (640, 640)
FACE_DET_CONF = 0.50
FACE_DET_NMS = 0.40


# ============================================================
# AI LOOP
# ============================================================

AI_MAX_FPS = 15.0


# ============================================================
# PERSON REID TARGET VERIFICATION
# ============================================================

TARGET_IMAGE_PATH = str(PROJECT_ROOT / "person_search/targets/target.jpg")
# False: detect and crop the largest person. True: explicitly trust a prepared person crop.
# If detection fails in False mode, autonomous zoom stays blocked.
TARGET_IMAGE_IS_CROP = False
REID_MODEL_PATH = str(resolve_model_path("person_search/models/osnet_ain_x1_0_msmt17.pth"))
REID_DEVICE = f"cuda:{DEVICE}" if isinstance(DEVICE, int) else str(DEVICE)
REID_BATCH_SIZE = 4
# Baselines only: calibrate similarity/margin and spatial continuity on the real camera.
REID_SIM_THRESHOLD = 0.65
REID_MARGIN_THRESHOLD = 0.08
REID_CONFIRM_FRAMES = 3
TARGET_LOST_FRAMES = 8
REID_CONFIRM_IOU = 0.40
REID_TRACK_IOU = 0.25
REID_FACE_IOU = 0.25
REID_ASSOC_MARGIN = 0.10
REID_FULL_BODY_ASPECT = 1.5
REID_SEARCH_INTERVAL = 0.0  # seconds; 0 evaluates every fresh inference frame
REID_VERIFY_INTERVAL = 1.0  # OSNet verification even when ByteTrack ID is unchanged
REID_RELOAD_INTERVAL = 1.0
REID_LOG_INTERVAL = 1.0
# Images accepted by the runtime ReID target picker.
TARGET_ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
# Reuse size/stability/settling gates; add absolute pixels and existing sharpness baseline.
FACE_RECOGNITION_MIN_SIZE = 80


# ============================================================
# FACE SEARCH ROI
# ============================================================

# Search face only inside upper part of Person bbox.
#
# 0.55:
#
# Person
# ┌──────────┐
# │   HEAD   │
# │          │
# │----------│  ~55%
# │          │
# │   BODY   │
# │          │
# └──────────┘

FACE_SEARCH_UPPER_BODY_RATIO = 0.60

# Set the ratio to 1.0 to search the whole person box for unusual camera views.
FACE_PERSON_ROI_TOP_RATIO = FACE_SEARCH_UPPER_BODY_RATIO


# ============================================================
# FACE RECOGNITION
# ============================================================

FACE_DATABASE_PATH = str(PROJECT_ROOT / "data" / "face_database" / "embeddings.npz")

# Baseline only -- MUST BE CALIBRATED ON REAL DEPLOYMENT DATA.
FACE_RECOGNITION_THRESHOLD = 0.45

# Recognition cadence for a valid associated face >= minimum size.
FACE_RECOGNITION_INTERVAL = 0.3
# Consecutive accepted face matches for the same locked person.
FACE_ID_CONFIRM_COUNT = 2
FACE_ID_CONFIRM_TIMEOUT = 3.0  # preserve context through short face misses

FACE_RECOGNITION_LOG_INTERVAL = 2.0  # rate-limit WAIT logs only

# Draw decoded five-point landmarks to make SCRFD validation visible.
FACE_DRAW_LANDMARKS = True


# ============================================================
# HIERARCHICAL TARGET CONTROL
# ============================================================

# ------------------------------------------------------------
# PERSON
# ------------------------------------------------------------

# Desired:
#
# person bbox height
# ------------------ = 0.40
# frame height

CONTEXT_FRAMING = True  # Keep body and walking space; face detection cannot take over zoom.
TARGET_PERSON_HEIGHT_RATIO = 0.40

PERSON_RATIO_TOLERANCE = 0.04

# Person must remain approximately the desired size for
# several inference frames before switching to FACE.

PERSON_READY_FRAMES = 5


# ------------------------------------------------------------
# FACE
# ------------------------------------------------------------

# Desired:
#
# face bbox height
# ---------------- = 0.25
# frame height
#
# Do NOT make this too large initially because optical zoom
# greatly narrows the FOV.

TARGET_FACE_HEIGHT_RATIO = 0.25

FACE_RATIO_TOLERANCE = 0.03

FACE_READY_FRAMES = 3


# ============================================================
# TARGET LOSS
# ============================================================

# Face may disappear temporarily because:
#
#     head pose
#     blur
#     occlusion
#     detector miss
#
# Do not immediately return to person.


# Person must be missing this long before it is considered
# truly lost.

TARGET_LOST_TIMEOUT = 0.80


# ============================================================
# AUTO ZOOM
# ============================================================

AUTO_ZOOM_DEFAULT = False

FACE_FINAL_CONFIRM_FRAMES = 3
FACE_FINAL_LOST_TIMEOUT_SEC = 2.0

LOG_DIRECTORY = PROJECT_ROOT / "logs"

AUTO_ZOOM_PERSON_TARGET_RATIO = TARGET_PERSON_HEIGHT_RATIO
AUTO_ZOOM_PERSON_MIN_RATIO = AUTO_ZOOM_PERSON_TARGET_RATIO - PERSON_RATIO_TOLERANCE
AUTO_ZOOM_PERSON_MAX_RATIO = AUTO_ZOOM_PERSON_TARGET_RATIO + PERSON_RATIO_TOLERANCE
AUTO_ZOOM_FACE_TARGET_RATIO = TARGET_FACE_HEIGHT_RATIO
AUTO_ZOOM_FACE_MIN_RATIO = 0.22
AUTO_ZOOM_FACE_MAX_RATIO = 0.28
AUTO_ZOOM_FACE_ACQUIRE_FRAMES = 3
AUTO_ZOOM_FACE_LOST_TIMEOUT = 1.0
FACE_LOST_HARD_TIMEOUT = 3.0
AUTO_ZOOM_RATIO_FILTER = "median"
AUTO_ZOOM_RATIO_HISTORY = 3
AUTO_ZOOM_CONTINUOUS_ERROR = 0.25
AUTO_ZOOM_LONG_ERROR = 0.10
AUTO_ZOOM_SHORT_ERROR = 0.04
AUTO_ZOOM_LONG_PULSE = 0.25
AUTO_ZOOM_SHORT_PULSE = 0.12
AUTO_ZOOM_FINE_PULSE = 0.06
# Independent watchdog: bound each continuous run and stop on missing feedback.
AUTO_ZOOM_CONTINUOUS_TIMEOUT = 5.0
AUTO_ZOOM_FEEDBACK_TIMEOUT = 0.8  # worker publication watchdog, seconds
MAX_CONTROL_OBSERVATION_AGE = 0.8  # capture age at acceptance; never re-stamp
AUTO_ZOOM_DIRECTION_HOLD = 0.40
AUTO_ZOOM_DIRECTION_COOLDOWN = 0.40
AUTO_ZOOM_REVERSE_ERROR = 0.10  # relative error beyond the opposite hold-zone edge
ENABLE_PERF_LOG = False
# Keep the session log quiet while no person is detected.
LOG_IDLE_NO_PERSON = False
PERF_LOG_INTERVAL_SEC = 1.0
# Initial latency estimate; tune against the actual stream and motor response.
AUTO_ZOOM_STOP_LEAD_TIME = 0.15
# Stop inward motion near any frame edge; release only further inside to
# avoid repeated starts from detector jitter. Fractions of width/height.
AUTO_ZOOM_FRAME_EDGE_MARGIN = 0.02
AUTO_ZOOM_FRAME_EDGE_RELEASE_MARGIN = 0.03
AUTO_ZOOM_SETTLE_TIME = 0.20
AUTO_ZOOM_DEBUG = True
AUTO_ZOOM_DEBUG_INTERVAL = 0.15


# ============================================================
# LOST TARGET ZOOM OUT
# ============================================================

# When Person is completely lost:
#
#     zoom out
#         ↓
#     wait
#         ↓
#     zoom out
#         ↓
#     ...
#         ↓
#       ~1x

LOST_ZOOM_OUT_DELAY = 3.5  # continuous fresh frames with no matched person/associated face
LOST_ZOOM_OUT_MAX_DURATION = FULL_ZOOM_TIME + RESET_ZOOM_EXTRA_TIME
LOST_ZOOM_OUT_STEP = 0.30

LOST_ZOOM_OUT_INTERVAL = 0.35


# Allow a small tolerance around the measured wide limit; consider
# anything close to minimum as minimum.

MIN_ZOOM_EPSILON = 0.15


# ============================================================
# AUTO FOCUS
# ============================================================

AUTO_FOCUS_DEFAULT = False


# Existing experimental threshold from your current system.
#
# IMPORTANT:
# This value should be calibrated later for Person and Face
# separately.

AUTO_FOCUS_SHARPNESS_THRESHOLD = 350.0


# Minimum time between autofocus attempts.

AUTO_FOCUS_COOLDOWN = 0.75


# Time allowed for camera autofocus mechanism to settle.

AUTO_FOCUS_SETTLE_TIME = 1.5  # maximum wait AFTER AF acknowledgement
AUTO_FOCUS_MIN_SETTLE_TIME = 0.20
AUTO_FOCUS_CONFIRM_FRAMES = 3
AUTO_FOCUS_MAX_FRAME_AGE = 0.30
FACE_RECOGNITION_MIN_SHARPNESS = 120.0  # trial value; calibrate on face crops
ACQUISITION_DEADLINE_SEC = 10.0
# A trial timeout is diagnostic only; it must not override the user Auto Zoom switch.
ACQUISITION_TIMEOUT_DISABLE_AUTO_ZOOM = False
# Override without changing camera.json, e.g. PTZ_RTSP_LATENCY_MS=100 python main.py
RTSP_LATENCY_MS = max(0, int(os.getenv("PTZ_RTSP_LATENCY_MS", RTSP_LATENCY_MS)))


# ============================================================
# DIGITAL PTZ
# ============================================================

# Keep OFF initially while validating optical zoom behavior.

DIGITAL_PTZ_DEFAULT = False


DIGITAL_PTZ_MIN_ZOOM = 1.0

DIGITAL_PTZ_MAX_ZOOM = 2.5


# This is independent of optical Person / Face target ratio.
#
# Digital PTZ attempts to make the current control target
# occupy approximately this fraction of the displayed frame.

DIGITAL_PTZ_TARGET_HEIGHT_RATIO = 0.70


# Center tracking smoothing.

DIGITAL_PTZ_CENTER_SMOOTHING = 0.60
# Do not re-center for minor detector jitter around the current ROI centre.
DIGITAL_PTZ_DEADZONE_X = 0.10
DIGITAL_PTZ_DEADZONE_Y = 0.10
# Prefer a recent face location during short detector misses before following body.
DIGITAL_PTZ_FACE_HOLD_SEC = 0.80


# Digital zoom smoothing.

DIGITAL_PTZ_ZOOM_SMOOTHING = 0.30


# Hold crop briefly when target temporarily disappears.

DIGITAL_PTZ_LOST_HOLD = 0.60


# Slowly return to full frame after target is lost.

DIGITAL_PTZ_RETURN_SMOOTHING = 0.08


# ============================================================
# GUI
# ============================================================

WINDOW_GEOMETRY = "1400x820"

# Tkinter video refresh interval.
#
# ~30 FPS:
#
#     1000 / 33 ≈ 30

GUI_UPDATE_MS = 33


# ============================================================
# DEBUG
# ============================================================

DEBUG = True


# ============================================================
# CONFIG SUMMARY
# ============================================================

def print_config_summary():
    """
    Print non-sensitive startup configuration.

    Passwords are intentionally not printed.
    """

    print()
    print("=" * 60)
    print("AI PTZ CAMERA CONFIG")
    print("=" * 60)

    print(
        f"Project root      : {PROJECT_ROOT}"
    )

    print(
        f"RTSP latency      : {RTSP_LATENCY_MS} ms"
    )

    print(
        f"PTZ IP            : {PTZ_IP}"
    )

    print(
        f"PTZ speed         : {PTZ_SPEED}"
    )

    print(
        f"Optical zoom      : "
        f"{MIN_OPTICAL_ZOOM:.1f}x "
        f"-> "
        f"{MAX_OPTICAL_ZOOM:.1f}x"
    )

    print(
        f"Full zoom time    : "
        f"{FULL_ZOOM_TIME:.2f} s"
    )

    print()

    print(
        f"Person model      : "
        f"{PERSON_MODEL_PATH}"
    )

    print(
        f"Face detect model : "
        f"{FACE_DETECT_MODEL}"
    )

    print(
        f"Face recog model  : "
        f"{FACE_RECOGNITION_MODEL}"
    )

    print(
        f"Device            : "
        f"{DEVICE}"
    )

    print()

    print(
        f"Person target     : "
        f"{TARGET_PERSON_HEIGHT_RATIO:.2f}"
    )

    print(
        f"Face target       : "
        f"{TARGET_FACE_HEIGHT_RATIO:.2f}"
    )

    print(
        f"Sharpness lock    : "
        f"{AUTO_FOCUS_SHARPNESS_THRESHOLD:.1f}"
    )

    print()

    print(
        f"Auto Zoom default : "
        f"{AUTO_ZOOM_DEFAULT}"
    )

    print(
        f"Auto Focus default: "
        f"{AUTO_FOCUS_DEFAULT}"
    )

    print(
        f"Digital PTZ       : "
        f"{DIGITAL_PTZ_DEFAULT}"
    )

    print("=" * 60)
    print()

# Acquisition / retention. Similarity is evidence, never a post-lock kill switch.
REID_ACQUIRE_THRESHOLD = REID_SIM_THRESHOLD
REID_KEEP_THRESHOLD = 0.50
REID_REACQUIRE_THRESHOLD = 0.70
REID_LOST_GRACE_SEC = 3.0  # Temporary loss: hold motors and preserve reference.
REID_GALLERY_ENABLED = True
REID_NORMALIZATION_ENABLED = True
REID_NORMALIZE_LUMA = 90.0
REID_NORMALIZE_CONTRAST = 35.0
REID_NORMALIZE_GAMMA = 0.75
REID_NORMALIZE_CLAHE = 1.5
REID_FUSION = 'max'  # 'max' or 'weighted'
REID_RAW_WEIGHT = 0.6
REID_DIAGNOSTICS = True

IMAGE_DARK_THRESHOLD = 90.0
IMAGE_BACKGROUND_DELTA = 45.0
IMAGE_SHADOW_RATIO = 0.35
IMAGE_SHADOW_LEVEL = 60
FOCUS_METHOD = camera_value('focus_method', 'CNN_DELTA')
FOCUS_MODEL_PATH = resolve_model_path('models/focus/mang_delta.pt')
FOCUS_MAX_ATTEMPTS = 3
FOCUS_QUALITY_MODE = 'combined'  # 'legacy' retains the raw Laplacian gate
FOCUS_LAPLACIAN = 45.0
FOCUS_TENENGRAD = 150.0
FOCUS_EDGE_DENSITY = 0.025
FOCUS_MIN_ROI_SIZE = 32
RTSP_RECONNECT_INITIAL_SEC = 1.0
RTSP_RECONNECT_MAX_SEC = 30.0

REID_CENTER_MAX_SHIFT = 0.35  # normalized by previous box height/width; unique match only

# Crossing people: freeze association and optics until appearance is reconfirmed.
REID_CROSSING_OVERLAP = 0.15
REID_CROSSING_THRESHOLD = REID_ACQUIRE_THRESHOLD
REID_CROSSING_CONFIRM_FRAMES = 3

# Hybrid workers are always asynchronous; disable profiling independently.
PIPELINE = dict(mode='hybrid', face_recognition_async=True, zoom_control_async=True,
                reid_on_every_frame=False, reid_verification_interval_frames=10,
                prefer_latest_frame=True)
PIPELINE.update(CAMERA_CONFIG.get('pipeline', {}))
if (PIPELINE['mode'] != 'hybrid' or not PIPELINE['face_recognition_async']
        or not PIPELINE['zoom_control_async'] or not PIPELINE['prefer_latest_frame']):
    raise ValueError('Only the asynchronous latest-frame hybrid pipeline is supported')
if (not isinstance(PIPELINE['reid_verification_interval_frames'], int)
        or PIPELINE['reid_verification_interval_frames'] < 1):
    raise ValueError('reid_verification_interval_frames must be a positive integer')
PROFILING = dict(enabled=True, log_format='jsonl', log_per_stage=True,
                 log_camera_control=True, report_interval_seconds=30,
                 save_csv_summary=True, detailed_gpu_profiling=False)
PROFILING.update(CAMERA_CONFIG.get('profiling', {}))
if PROFILING['log_format'] != 'jsonl':
    raise ValueError('Profiling log_format must be jsonl')

# Absolute zoom: the vendor ZoomValue scale remains explicitly uncalibrated
# until a measured, camera-bound LUT is supplied. No example LUT is loaded.
ZOOM_CONTROL = dict(mode='auto', calibration_path=None, camera_id=None, absolute_timeout=8.,
                    max_corrections=2, correction_fraction=.15, absolute_settle_timeout=3.)
ZOOM_CONTROL.update(CAMERA_CONFIG.get('zoom_control', {}))
ZOOM_LOGGING = dict(enabled=True)
ZOOM_LOGGING.update(CAMERA_CONFIG.get('zoom_logging', {}))

# Event-driven face branch. R50 embeddings/threshold remain model-specific.
FACE_PIPELINE_MODE = 'optimized'  # 'baseline' for same-video A/B replay
FACE_DETECTION_HZ = 3.0
FACE_TRACK_MAX_AGE = 0.65
FACE_TRACK_MIN_CONFIDENCE = 0.7
FACE_TRACK_FB_ERROR = 1.5
FACE_CANDIDATE_LIMIT = 5
FACE_CANDIDATE_SPACING = 0.35
FACE_QUALITY_MIN_SCORE = 0.65
FACE_QUALITY_FALLBACK_TIMEOUT = 2.0
FACE_QUALITY_WEIGHTS = dict(size=2., sharpness=2., pose=2., illumination=1.,
                            contrast=1., stability=2., alignment=1.)
FACE_UNKNOWN_RETRY_INTERVAL = 1.5
FACE_PANEL_ENABLED = True
FACE_PANEL_DEBUG = False
FACE_REACQUIRE_REVERIFY_TIMEOUT = 2.0
FACE_CANDIDATE_MAX_AGE = 3.0
FACE_LENS_SETTLE_TIME = 0.2
FACE_DUPLICATE_RETRY_TIMEOUT = 2.0
FACE_DUPLICATE_PIXEL_DELTA = 3.0
FACE_TRACK_PYRAMID_LEVELS = 1
FACE_TRACK_MAX_SCALE_CHANGE = 0.06
FACE_TRACK_MAX_DIMENSION = 640

# Keep False for ByteTrack + hybrid OSNet selection. Face optical flow stays disabled.
TARGET_CONTINUOUS_REID = False
FACE_TRACKING_ENABLED = False

# ByteTrack sees weak detections; identity acquisition still requires PERSON_CONF_THRESHOLD.
BYTE_TRACK_LOW_THRESHOLD = 0.10

# Observation-only sidecar. GMC and control experiments remain offline.
CAMERA_MOTION_DIAGNOSTICS_ENABLED = os.environ.get('PTZ_CAMERA_MOTION_DIAGNOSTICS', '0') == '1'
CAMERA_MOTION_GMC_ENABLED = False  # Offline replay only; never applied in runtime.
CAMERA_MOTION_AWARE_CONTROL_ENABLED = False  # No runtime behavior wired to this flag.
CAMERA_MOTION_METHOD = 'sparse_optical_flow'
CAMERA_MOTION_DOWNSCALE = 2
CAMERA_MOTION_OSD_BOXES = ()  # Original frame coordinates; configure for a capture session.
CAMERA_MOTION_RECORD_ENABLED = os.environ.get('PTZ_CAMERA_MOTION_RECORD', '0') == '1'
CAMERA_MOTION_RECORD_DIRECTORY = str(PROJECT_ROOT / 'logs' / 'motion_capture')
CAMERA_MOTION_RECORD_MAX_BYTES = 256_000_000
CAMERA_MOTION_RECORD_MAX_FRAMES = 600

# Physical body centering, independent of optical/digital zoom toggles.
AUTO_PAN_TILT_ENABLED = True
AUTO_PAN_TILT_DEADZONE = 0.08  # Fraction of full image width/height from center.
AUTO_PAN_TILT_RELEASE = 0.04  # Hysteresis: stop closer to center than start threshold.
AUTO_PAN_TILT_MAX_SPEED = 3  # HANET scale 1..8; conservative initial speed.
AUTO_PAN_TILT_PULSE = 0.12
AUTO_PAN_TILT_SETTLE = 0.15
AUTO_PAN_SIGN = 1  # Set -1 for a mirrored/mount-reversed pan direction.
AUTO_TILT_SIGN = 1

# Hybrid verification: cosine scores are evidence, not probabilities.
REID_NEGATIVE_CONFIRMATIONS = 3  # tolerate one bad view; hold after 3 negative samples
REID_VERIFY_RESULT_MAX_AGE = 2.0  # monotonic capture-to-consumption seconds
REID_IDENTITY_MAX_AGE = 5.0  # bound continuation when verification is unavailable

# Opt-in A/B: retain conservative baseline until physical response is calibrated.
AUTO_PAN_TILT_ADAPTIVE = False
AUTO_PAN_TILT_MAX_PULSE = 0.24
AUTO_PAN_TILT_FAR_ERROR = 0.30

# No fabricated lens position: bounded OUT only, fresh no-target evidence required.
PULSE_RECOVERY_WITHOUT_TELEMETRY = True
PULSE_RECOVERY_BUDGET = 1.2  # cumulative commanded seconds per loss episode
TRACK_DIAGNOSTICS_INTERVAL = 0.5
TRACK_SNAPSHOT_ENABLED = os.environ.get('PTZ_TRACK_SNAPSHOTS', '0') == '1'
TRACK_SNAPSHOT_MAX_FILES = 20
TRACK_SNAPSHOT_DIRECTORY = str(PROJECT_ROOT / 'logs' / 'overlap_snapshots')

# P0 overlap: keep the existing 0.15 trigger; allow only minor contact with
# fresh identity + stable observed trajectory. These are conservative test bounds,
# not camera/identity calibration. Set False for the pre-P0 geometry gate.
REID_TEMPORAL_OVERLAP_ENABLED = True
REID_WEAK_TARGET_COVER = 0.10
REID_WEAK_IOU = 0.10
REID_WEAK_OVERLAP_GRACE = 0.35  # seconds to verify implicated identities
REID_WEAK_EVIDENCE_MAX_AGE = 1.5
REID_WEAK_AREA_MIN = 0.5
REID_WEAK_AREA_MAX = 2.0
REID_ASSOCIATION_RISK_MARGIN = 0.10
REID_CANDIDATE_AWARE = True
REID_HOLD_SCAN_INTERVAL = 0.5  # ambiguous geometry: bounded global retry, no spatial exclusion
TRACK_SNAPSHOT_COOLDOWN = 1.0
TRACK_SNAPSHOT_MAX_BYTES = 64_000_000  # raw+annotated JPEG+JSON bundles; OFF by default

# Only small intersections in BOTH crops bypass the overlap ambiguity gate.
# Appearance thresholds, motion continuity, freshness and 3-frame confirmation
# still apply. Set PTZ_MINOR_OVERLAP=0 for the historical geometry policy.
REID_MINOR_OVERLAP_ENABLED = os.environ.get('PTZ_MINOR_OVERLAP', '1') == '1'
REID_MINOR_TARGET_COVER = 0.05
REID_MINOR_COMPETITOR_COVER = 0.25
REID_MINOR_IOU = 0.05
REID_OVERLAP_DIAGNOSTICS = os.environ.get('PTZ_OVERLAP_DIAGNOSTICS', '0') == '1'
REID_CONFIRM_MAX_GAP = 1.0  # same-candidate confirmation cannot span long gaps

# Phase 1 offline replay only. No runtime/motor consumer reads these switches.
IDENTITY_MEMORY_ENABLED = False
IDENTITY_MEMORY_GALLERY_ENABLED = False  # requires explicit crop quality review

# Explicit GUI opt-in only; raw detector-frame dataset, no changes to control.
IDENTITY_RECORD_DIRECTORY = str(PROJECT_ROOT / 'logs' / 'identity_capture')
IDENTITY_RECORD_MAX_BYTES = 1_000_000_000
IDENTITY_RECORD_MAX_FRAMES = 3000

# GUI opt-in shadow only; never authorizes motor control.
IDENTITY_SHADOW_DIRECTORY = str(PROJECT_ROOT / 'logs' / 'identity_shadow')
IDENTITY_SHADOW_INTERVAL = 0.2  # at most 5 sampled jobs/s, one job in flight
IDENTITY_SHADOW_MAX_RECORDS = 10000

# Default v4 scorer for GUI-started shadow; live validation remains pending.
# Set PTZ_SHADOW_BATCHED=0 only for v3 comparison or rollback.
IDENTITY_SHADOW_BATCHED = os.environ.get('PTZ_SHADOW_BATCHED', '1') == '1'

# Phase 2 offline PoC only. These flags never grant camera/motor authority.
IDENTITY_CANDIDATE_FILTERING_ENABLED = False
IDENTITY_DISTRACTOR_MEMORY_ENABLED = False
IDENTITY_EVENT_REID_ENABLED = False
IDENTITY_MULTI_CUE_ENABLED = False
