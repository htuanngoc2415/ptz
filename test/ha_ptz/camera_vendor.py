"""Shared 37x controller exports for camera experiments.

Use PTZController.command(), move_zoom(), move_focus(), autofocus() and close().
The old CGI CameraVendorAPI and numeric command payloads are retired.
"""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from camera.ptz_controller import (
    PTZController, PTZ_STOP, PTZ_ZOOM_IN, PTZ_ZOOM_OUT,
    PTZ_FOCUS_PLUS, PTZ_FOCUS_MINUS, PTZ_AUTO_FOCUS,
)
