#!/usr/bin/env python3
"""Check configured video and control protocol without loading AI models."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camera import create_controller
from camera.stream import RTSPPlayer
from runtime.frame_manager import FrameManager


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--output', type=Path, default=ROOT/'logs/camera_check.json')
    parser.add_argument('--zoom-pulse', action='store_true', help='Test a 0.15 second zoom-in followed by STOP')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error('seconds must be in 1..120')
    config = json.loads((ROOT/'camera.json').read_text())
    controller = create_controller(config.get('ptz_backend', 'dahua'),
        ip=config['ptz_ip'], username=config['ptz_username'], password=config['ptz_password'],
        port=config.get('ptz_port', 80), timeout=config.get('ptz_timeout', 5),
        speed=config.get('ptz_speed', 4), autoconnect=False)
    frames = FrameManager()
    receiver = RTSPPlayer(config, frames.publish, disconnect_callback=frames.clear)
    report = {'backend':config.get('ptz_backend'), 'video_backend':config.get('video_backend'),
              'received':0, 'motor_test_requested':args.zoom_pulse}
    try:
        report['control'] = controller.read_status()
        receiver.start()
        deadline = time.monotonic() + args.seconds
        last = -1
        while time.monotonic() < deadline:
            packet = frames.latest(after=last, timeout=.1)
            if packet is None:
                continue
            last = packet.frame_id
            report['received'] += 1
            report['shape'] = list(packet.image.shape)
        if args.zoom_pulse:
            report['zoom_pulse'] = controller.zoom_in_pulse(.15, speed=1)
    except Exception as exc:
        report['error'] = type(exc).__name__
    finally:
        for name, close in [('receiver', receiver.stop), ('controller', controller.close)]:
            try:
                close()
                report[name+'_closed'] = True
            except Exception as exc:
                report[name+'_close_error'] = type(exc).__name__
        report['stream'] = receiver.diagnostics() if hasattr(receiver, 'diagnostics') else None
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2))
    return 0 if (report['received'] and not report.get('error') and
                 report.get('receiver_closed') and report.get('controller_closed') and
                 (not args.zoom_pulse or report.get('zoom_pulse', [False])[0])) else 1


if __name__ == '__main__':
    raise SystemExit(main())
