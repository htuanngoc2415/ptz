#!/usr/bin/env python3
"""Compatibility launcher for the shared Dahua 37x application.

All camera controls now live in camera/ and control/; configuration is the
project-root camera.json. This entry point uses the current application UI.
"""
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
