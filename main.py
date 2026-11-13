#!/usr/bin/env python3

"""
AI PTZ Camera

Main entry point.

Run:

    python3 main.py
"""

from __future__ import annotations
from event_logging import event_print as print

import os
# Apply the launcher memory setting when starting directly with python3 main.py.
os.environ.setdefault("CUDA_MODULE_LOADING", "LAZY")

import builtins
import sys
import tkinter as tk
from pathlib import Path


# ============================================================
# PROJECT ROOT
# ============================================================

PROJECT_ROOT = Path(
    __file__
).resolve().parent


# Make sure project packages are importable even when main.py
# is launched from another working directory.

if str(PROJECT_ROOT) not in sys.path:

    sys.path.insert(
        0,
        str(PROJECT_ROOT)
    )


# ============================================================
# CONFIG
# ============================================================

try:

    import config as cfg

except Exception as exc:

    print(
        "\n"
        "============================================\n"
        "CONFIG ERROR\n"
        "============================================\n"
        f"{exc}\n",
        file=sys.stderr,
    )

    raise SystemExit(1)


# ============================================================
# VALIDATION
# ============================================================

def validate_file(
    path: str,
    name: str,
) -> None:

    if not os.path.isfile(
        path
    ):

        raise FileNotFoundError(
            f"{name} không tồn tại:\n"
            f"{path}"
        )


def validate_config() -> None:
    if not 0 <= cfg.REID_KEEP_THRESHOLD < cfg.REID_ACQUIRE_THRESHOLD <= 1:
        raise ValueError('ReID requires 0 <= keep < acquire <= 1')
    if not 0 <= cfg.REID_REACQUIRE_THRESHOLD <= 1:
        raise ValueError('Invalid ReID reacquire threshold')
    if cfg.REID_FUSION not in ('max', 'weighted') or not 0 <= cfg.REID_RAW_WEIGHT <= 1:
        raise ValueError('Invalid ReID fusion')
    if cfg.PTZ_BACKEND == 'dahua' and (cfg.FOCUS_METHOD != 'CNN_DELTA' or cfg.FOCUS_MAX_ATTEMPTS < 1):
        raise ValueError('37x camera requires CNN_DELTA; attempts must be positive')
    if cfg.FOCUS_QUALITY_MODE not in ('legacy', 'combined'):
        raise ValueError('Invalid focus quality mode')
    if min(cfg.REID_LOST_GRACE_SEC, cfg.MAX_CONTROL_OBSERVATION_AGE,
           cfg.AUTO_FOCUS_MAX_FRAME_AGE, cfg.REID_BATCH_SIZE) <= 0:
        raise ValueError('Invalid freshness/grace/batch configuration')

    if cfg.PTZ_BACKEND == "dahua":
        validate_file(str(cfg.FOCUS_MODEL_PATH), "Delta focus model")

    # --------------------------------------------------------
    # MODELS
    # --------------------------------------------------------

    validate_file(
        cfg.PERSON_MODEL_PATH,
        "Person model",
    )

    validate_file(
        cfg.FACE_DETECT_MODEL,
        "Face detection model",
    )

    validate_file(
        cfg.FACE_RECOGNITION_MODEL,
        "Face recognition model",
    )

    # --------------------------------------------------------
    # RTSP
    # --------------------------------------------------------

    if not cfg.RTSP_URI:

        raise ValueError(
            "RTSP_URI đang rỗng."
        )

    # --------------------------------------------------------
    # PTZ
    # --------------------------------------------------------

    if not cfg.PTZ_IP:

        raise ValueError(
            "PTZ_IP đang rỗng."
        )

    if not (
        1
        <=
        cfg.PTZ_SPEED
        <=
        100
    ):

        raise ValueError(
            "PTZ_SPEED phải nằm trong [1, 100]."
        )

    # --------------------------------------------------------
    # ZOOM
    # --------------------------------------------------------

    if (
        cfg.MAX_OPTICAL_ZOOM
        <=
        cfg.MIN_OPTICAL_ZOOM
    ):

        raise ValueError(
            "MAX_OPTICAL_ZOOM phải lớn hơn "
            "MIN_OPTICAL_ZOOM."
        )

    if (
        cfg.FULL_ZOOM_TIME
        <=	
        0
    ):

        raise ValueError(
            "FULL_ZOOM_TIME phải > 0."
        )

    # --------------------------------------------------------
    # TARGET RATIOS
    # --------------------------------------------------------

    if not (
        0.0
        <
        cfg.TARGET_PERSON_HEIGHT_RATIO
        <
        1.0
    ):

        raise ValueError(
            "TARGET_PERSON_HEIGHT_RATIO "
            "phải nằm trong (0, 1)."
        )

    if not (
        0.0
        <
        cfg.TARGET_FACE_HEIGHT_RATIO
        <
        1.0
    ):

        raise ValueError(
            "TARGET_FACE_HEIGHT_RATIO "
            "phải nằm trong (0, 1)."
        )

    # --------------------------------------------------------
    # FPS
    # --------------------------------------------------------

    if cfg.AI_MAX_FPS <= 0:

        raise ValueError(
            "AI_MAX_FPS phải > 0."
        )


# ============================================================
# MAIN
# ============================================================

def main() -> int:
    if '--camera-only' in sys.argv:
        sys.argv.remove('--camera-only')
        from camera_viewer import main as camera_main
        return camera_main()

    if os.environ.get('PTZ_DESKTOP_BENCHMARK'):
        import json
        if not cfg.BENCHMARK_READ_ONLY:
            raise RuntimeError('Desktop benchmark requires read-only camera mode')
        cfg.LOG_DIRECTORY = Path(json.loads(os.environ['PTZ_DESKTOP_BENCHMARK'])['output'])
    from event_logging import setup_logging
    log_path = setup_logging(cfg.LOG_DIRECTORY)
    builtins.print(f"Đang khởi động AI PTZ — log: {log_path}", flush=True)

    print()
    print(
        "Starting AI PTZ Camera..."
    )

    # --------------------------------------------------------
    # VALIDATE
    # --------------------------------------------------------

    try:

        validate_config()

    except Exception as exc:

        print(
            "\n"
            "============================================\n"
            "STARTUP VALIDATION FAILED\n"
            "============================================\n"
            f"{exc}\n",
            file=sys.stderr,
        )

        return 1

    # --------------------------------------------------------
    # CONFIG SUMMARY
    # --------------------------------------------------------

    if cfg.DEBUG:

        cfg.print_config_summary()

    # --------------------------------------------------------
    # IMPORT GUI AFTER CONFIG VALIDATION
    # --------------------------------------------------------

    try:

        # Initialize CUDA before 4K decoding/GUI buffers consume shared Jetson RAM.
        # A requested GPU must not silently turn into slow CPU inference.
        from runtime.device import resolve_device
        cfg.DEVICE = resolve_device(cfg.DEVICE, strict=True)

        from gui import CCTVApp
        # libmpv loads native libraries. Finish this before Tk exists and before
        # the AI worker loads PyTorch models, rather than importing it from
        # RTSPPlayer.start() while the GUI is waiting to enter mainloop.
        if cfg.CAMERA_CONFIG.get("video_backend") == "mpv":
            print("[STARTUP] Preparing mpv video backend")
            import mpv
            print("[STARTUP] mpv video backend ready")

    except Exception as exc:

        print(
            "\n"
            "============================================\n"
            "STARTUP ERROR\n"
            "============================================\n"
            f"{exc}\n"
            f"Python đang dùng: {sys.executable}\n"
            "Thử chạy bằng môi trường camera: bash run_camera.sh\n",
            file=sys.stderr,
        )

        return 1

    # --------------------------------------------------------
    # TKINTER
    # --------------------------------------------------------

    try:

        # tkinterdnd2 supplies native file drop; retain plain Tk fallback when
        # the optional package is unavailable (Browse Target still works).
        try:
            from tkinterdnd2 import TkinterDnD
            root = TkinterDnD.Tk()
        except Exception:
            root = tk.Tk()

    except Exception as exc:

        print(
            "\n"
            "Không khởi tạo được Tkinter.\n"
            f"{exc}\n",
            file=sys.stderr,
        )

        return 1

    # --------------------------------------------------------
    # APPLICATION
    # --------------------------------------------------------

    try:

        benchmark = None
        if os.environ.get('PTZ_DESKTOP_BENCHMARK'):
            import json
            from runtime.desktop_benchmark import DesktopBenchmark
            if not cfg.BENCHMARK_READ_ONLY:
                raise RuntimeError('Desktop benchmark requires read-only camera mode')
            benchmark = DesktopBenchmark(json.loads(os.environ['PTZ_DESKTOP_BENCHMARK']))
            cfg.CAMERA_CONFIG['_capture_observer'] = benchmark.observe_capture
            cfg.PROFILING = dict(cfg.PROFILING, enabled=True, max_bytes=64_000_000, backups=3)
            if benchmark.options.get('cuda_timing'):
                cfg.PROFILING['detailed_gpu_profiling'] = True
        app = CCTVApp(
            root
        )
        if benchmark:
            benchmark.attach(app)

    except Exception as exc:

        print(
            "\n"
            "============================================\n"
            "APPLICATION START FAILED\n"
            "============================================\n"
            f"{exc}\n",
            file=sys.stderr,
        )

        try:
            root.destroy()
        except Exception:
            pass

        return 1

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    try:

        print("[STARTUP] Entering GUI main loop")
        root.mainloop()
        if benchmark:
            benchmark.write_result(shutdown_complete=True)

    except KeyboardInterrupt:

        print(
            "\nKeyboardInterrupt"
        )

        try:

            app.on_close()

        except Exception:
            pass

    except Exception as exc:

        print(
            "\n"
            "============================================\n"
            "APPLICATION ERROR\n"
            "============================================\n"
            f"{exc}\n",
            file=sys.stderr,
        )

        try:

            app.on_close()

        except Exception:
            pass

        return 1

    print(
        "AI PTZ Camera stopped."
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    raise SystemExit(
        main()
    )
