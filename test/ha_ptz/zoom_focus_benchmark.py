#!/usr/bin/env python3
"""Compatibility entry point for Dahua 37x RPC2 diagnostics.

Run --help for position-based zoom/focus options. Without movement options,
only read camera status. CGI and WebSocket command payloads are retired.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.camera_diagnostics import main

if __name__ == '__main__':
    raise SystemExit(main())
