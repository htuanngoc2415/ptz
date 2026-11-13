#!/usr/bin/env python3
"""Read-only firmware evidence. Never START/STOP a motor or modify focus settings."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camera.dahua_rpc import ZoomRPC, RPCError


def probe(config):
    if config.get('ptz_backend', 'dahua') != 'dahua':
        raise ValueError('Dahua-only diagnostic; use tools/check_camera.py for HANET')
    rpc = None
    evidence = dict(timestamp=datetime.now().astimezone().isoformat(), read_only=True, responses={})
    try:
        rpc = ZoomRPC(config['ptz_ip'], config.get('ptz_port', 80), config['ptz_username'],
                      config['ptz_password'], channel=config.get('ptz_channel', 0),
                      timeout=config.get('ptz_timeout', 2), read_only=True)
        calls = [('magicBox.getSystemInfo', {}), ('magicBox.getSoftwareVersion', {}),
                 ('ptz.getStatus', {'channel':rpc.channel}),
                 ('ptz.getCurrentProtocolCaps', {'channel':rpc.channel}),
                 ('ptz.isMoving', {'channel':rpc.channel}),
                 ('ptz.listMethod', {}),
                 ('configManager.getConfig', {'name':'VideoInFocus'})]
        for method, params in calls:
            try:
                reply = rpc._call(method, params, obj=rpc.object if method.startswith('ptz.') and method!='ptz.listMethod' else None, allow_false=method=='ptz.isMoving')
                # Session IDs and authentication material are deliberately omitted.
                evidence['responses'][method] = {key:reply.get(key) for key in ('result','error','params')}
            except RPCError as exc:
                evidence['responses'][method] = exc.response
            except Exception as exc:
                evidence['responses'][method] = dict(available=False, error_type=type(exc).__name__)
    except Exception as exc:
        evidence['connection_error'] = type(exc).__name__
    finally:
        if rpc is not None:
            rpc.close(stop_motors=False)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT/'camera.json')
    parser.add_argument('--output', type=Path, default=ROOT/'docs/zoom_audit/read_only_probe.json')
    args = parser.parse_args()
    evidence = probe(json.loads(args.config.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps(evidence, indent=2, ensure_ascii=False))
    return 1 if evidence.get('connection_error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
