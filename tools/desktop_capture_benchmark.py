#!/usr/bin/env python3
"""Launch one real production desktop A/B arm with RPC writes denied."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--backend', choices=('mpv', 'gstreamer_cpu'), required=True)
    p.add_argument('--seconds', type=float, default=60)
    p.add_argument('--warmup', type=float, default=20)
    p.add_argument('--startup-timeout', type=float, default=180)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--display', default=os.environ.get('DISPLAY'))
    p.add_argument('--cuda-timing', action='store_true',
                   help='Separate diagnostic: existing CUDA-event timing synchronizes stages; exclude from normal A/B')
    args = p.parse_args()
    if args.seconds < 60 or args.warmup < 0:
        p.error('measurement must be >=60 seconds and warmup nonnegative')
    if not args.display:
        p.error('A real desktop DISPLAY is required; no headless substitution')
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    options = dict(backend=args.backend, seconds=args.seconds, warmup=args.warmup,
                   startup_timeout=args.startup_timeout, output=str(args.output), cuda_timing=args.cuda_timing)
    env = dict(os.environ, PTZ_VIDEO_BACKEND=args.backend, PTZ_BENCHMARK_READ_ONLY='1',
               PTZ_DESKTOP_BENCHMARK=json.dumps(options), DISPLAY=args.display, GST_DEBUG='0',
               PTZ_PROFILE_DIRECTORY=str(args.output/'trace'))
    (args.output/'options.json').write_text(json.dumps(options, indent=2)+'\n')
    with (args.output/'console.txt').open('w') as log:
        child = subprocess.Popen(['bash', str(ROOT/'run_camera.sh')], cwd=ROOT, env=env,
                                 stdout=log, stderr=subprocess.STDOUT)
        try:
            code = child.wait(timeout=args.startup_timeout+args.warmup+args.seconds+90)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
            (args.output/'timeout.json').write_text('{"status":"FAILED","reason":"DESKTOP_TIMEOUT"}\n')
            return 2
    path = args.output/'result.json'
    if not path.exists():
        path.write_text(json.dumps(dict(status='FAILED', failure='DESKTOP_STARTUP_FAILED', exit_code=code))+'\n')
    result = json.loads(path.read_text())
    print(json.dumps({k:result.get(k) for k in ('backend','status','failure','seconds','capture_fps','pts_segments','shutdown_complete')}))
    return 0 if code == 0 and result.get('status') == 'MEASURED' else 2


if __name__ == '__main__':
    sys.exit(main())
