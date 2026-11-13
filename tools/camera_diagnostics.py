#!/usr/bin/env python3
"""Dahua 37x RPC2 diagnostics using the application's zoom/focus controller."""
from pathlib import Path
import argparse
import csv
import json
import math
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from camera import create_controller
from camera.dahua_rpc import ZoomRPC
from camera.ptz_controller import PTZController, PTZ_FOCUS_PLUS, PTZ_FOCUS_MINUS


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'camera.json')
    parser.add_argument('--execute', action='store_true', help='Enable requested motor movements')
    parser.add_argument('--zoom', type=float, help='Target magnification, 1–37x')
    parser.add_argument('--levels', help='Comma-separated zoom targets for a benchmark')
    parser.add_argument('--focus', type=float, help='Target PTZFocusHD, 0–32768')
    parser.add_argument('--autofocus', action='store_true', help='CNN focus from fresh D1 frames')
    parser.add_argument('--focus-pulse', choices=('plus', 'minus'))
    parser.add_argument('--duration', type=float, default=.6, help='Focus diagnostic pulse in seconds')
    parser.add_argument('--speed', type=int, help='VideoInZoom.Speed, 1–100')
    parser.add_argument('--output', type=Path, help='Write measured positions and times to CSV')
    args = parser.parse_args(argv)
    levels = []
    try:
        if args.levels:
            levels = [float(x) for x in args.levels.split(',')]
        if args.zoom is not None:
            levels.append(args.zoom)
    except ValueError:
        parser.error('Invalid zoom levels')
    if any(not math.isfinite(z) or not 1 <= z <= 37 for z in levels):
        parser.error('Zoom must be within 1–37x')
    if args.focus is not None and (not math.isfinite(args.focus) or not 0 <= args.focus <= 32768):
        parser.error('Focus must be within 0–32768')
    if not math.isfinite(args.duration) or not 0 < args.duration <= 15:
        parser.error('Duration must be within 0–15 seconds')
    movement = levels or args.focus is not None or args.autofocus or args.focus_pulse
    if movement and not args.execute:
        parser.error('Motor movement requires --execute')
    cfg = json.loads(args.config.read_text(encoding='utf-8'))
    frames = stream = None
    if args.autofocus and cfg.get('ptz_backend') == 'hanet':
        parser.error('HANET autofocus is not supported')
    if args.autofocus:
        # Use the same authenticated GStreamer RGB mailbox as the desktop.
        from runtime.frame_manager import FrameManager
        from camera.rtsp_player import RTSPPlayer
        frames = FrameManager()
        stream = RTSPPlayer(cfg, frames.publish, lambda *_: None)
    rpc_factory = ZoomRPC if movement else lambda **kw: ZoomRPC(**kw, read_only=True)
    backend = cfg.get('ptz_backend', 'dahua')
    if backend == 'hanet' and args.autofocus:
        parser.error('HANET autofocus is not supported')
    camera = create_controller(backend, ip=cfg['ptz_ip'], username=cfg['ptz_username'], password=cfg['ptz_password'],
                           speed=args.speed or cfg.get('ptz_speed', 100),
                           channel=cfg.get('ptz_channel', 0), port=cfg.get('ptz_port', 80),
                           timeout=cfg.get('ptz_timeout', 5), autoconnect=False,
                           frame_provider=frames.latest if frames else None,
                           **({'rpc_factory': rpc_factory} if backend == 'dahua' else {}))
    rows = []

    def measure(operation, target, call):
        started = time.monotonic()
        ok, message = call()
        status = camera.read_status()
        row = dict(operation=operation, target=target, ok=ok,
                   seconds=round(time.monotonic()-started, 3),
                   zoom_x=status['zoom'], focus_hd=status['focus'])
        rows.append(row)
        print(json.dumps(row))
        if not ok:
            raise RuntimeError(message)

    try:
        if stream:
            stream.start()
        if not movement:
            print(json.dumps(camera.read_status()))
        for level in levels:
            measure('zoom', level, lambda: camera.move_zoom(level))
            if args.autofocus:
                measure('cnn_focus', None, camera.autofocus)
        if args.focus is not None:
            measure('focus', args.focus, lambda: camera.move_focus(args.focus))
        if args.focus_pulse:
            command = PTZ_FOCUS_PLUS if args.focus_pulse == 'plus' else PTZ_FOCUS_MINUS
            measure('focus_pulse', args.duration, lambda: camera.pulse(command, args.duration))
        if args.autofocus and not levels:
            measure('cnn_focus', None, camera.autofocus)
    finally:
        try:
            # Read-only status queries must not stop another operator's motor.
            if movement or backend == 'hanet':
                camera.close()
            elif camera._rpc is not None:
                camera._rpc.close(stop_motors=False)
            camera._command_worker.close()
        finally:
            if stream:
                stream.stop()
            if args.output and rows:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open('w', newline='', encoding='utf-8') as out:
                    writer = csv.DictWriter(out, fieldnames=rows[0])
                    writer.writeheader()
                    writer.writerows(rows)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
