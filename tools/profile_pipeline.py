#!/usr/bin/env python3
"""Profile the normal desktop, or analyze recorded evidence. Never activates motors."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def overhead(output, count):
    from telemetry.latency import LatencyProfiler
    from telemetry.benchmark import distribution
    results = {}
    for enabled in (False, True):
        with tempfile.TemporaryDirectory(prefix='ptz-overhead-') as directory:
            p = LatencyProfiler(directory, enabled=enabled, queue_size=8192)
            costs=[]; start=time.perf_counter(); cpu=time.process_time()
            for i in range(count):
                tick=time.perf_counter_ns()
                p.event('OVERHEAD_PROBE', frame_id=i, metric='probe_ms', duration_ms=0.)
                costs.append((time.perf_counter_ns()-tick)/1e6)
            producer_elapsed=time.perf_counter()-start
            drained=p.close(timeout=30.)
            results['enabled' if enabled else 'disabled']=dict(
                emit_ms=distribution(costs), producer_seconds=producer_elapsed,
                seconds_including_drain=time.perf_counter()-start,
                cpu_seconds_including_drain=time.process_time()-cpu,
                dropped_log_events=p.counters['dropped_log_events'], drained=drained, error=p.error)
    results.update(scope='instrumentation microbenchmark on this host; NOT camera/model benchmark',
        pipeline_ab_overhead_percent=None,
        limitations='Burst enqueue cost includes queue saturation when present. Real workload overhead needs matched profiling ON/OFF runs; existing event/zoom logging cost is not isolated.')
    output.mkdir(parents=True, exist_ok=True)
    (output/'overhead.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    for name in ('run','report','video','overhead'):
        p=subs.add_parser(name)
        p.add_argument('--output',type=Path,default=ROOT/'logs/pipeline_profile')
        if name != 'overhead':
            p.add_argument('--scenario',choices=('P1','P2','P3','unknown'),default='unknown')
        if name=='report':
            p.add_argument('traces',type=Path,nargs='+')
            p.add_argument('--annotations',type=Path,help='Explicit appearance frame annotations; see docs/pipeline_profiling.md')
        if name=='overhead':
            p.add_argument('--events',type=int,default=10000)
        if name=='video':
            p.add_argument('--video',type=Path,required=True)
            p.add_argument('--target',type=Path,required=True)
            p.add_argument('--frames',type=int,default=300)
            p.add_argument('--python',default=os.environ.get('PTZ_PYTHON',sys.executable))
    args=parser.parse_args(argv)
    if args.command=='overhead':
        if args.events < 1: parser.error('--events must be positive')
        overhead(args.output,args.events);return 0
    from telemetry.pipeline_report import build_report
    if args.command=='report':
        report=build_report(args.traces,args.output,args.scenario,args.annotations)
        print(f"{args.output/'report.html'} ({report['event_count']} measured trace events)")
        return 0 if report['event_count'] else 2
    stamp=datetime.now().astimezone().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8]
    output=(args.output/stamp).resolve();output.mkdir(parents=True)
    env=dict(os.environ, PTZ_PROFILE_SCENARIO=args.scenario)
    if args.command=='run':
        env['PTZ_PROFILE_DIRECTORY']=str(output/'trace')
        command=['bash',str(ROOT/'run_camera.sh')]
        evidence='normal_production_desktop_operator_activated_controls'
    else:
        if not args.video.is_file() or not args.target.is_file():
            parser.error('--video and --target must be existing local files')
        # Reuse the existing offline replay adapter and its network-denial guard.
        command=[args.python,str(ROOT/'tools/modular_benchmark.py'),'run','--stage','2',
            '--case','replay','--modules','2,3,4,7,9','--video',str(args.video.resolve()),
            '--target',str(args.target.resolve()),'--frames',str(args.frames),
            '--output',str(output/'replay')]
        evidence='offline_module_replay_not_production_concurrent_E2E'
        env.pop('PTZ_PROFILE_DIRECTORY',None)
    print('Report directory:',output,flush=True)
    print('Auto Zoom / Focus stay under the existing operator controls.' if args.command=='run'
          else 'Video replay: motor/settling/production scheduler E2E = N/A.',flush=True)
    process=subprocess.Popen(command,cwd=ROOT,env=env)
    try:
        code=process.wait()
    except KeyboardInterrupt:
        # Terminal SIGINT also reaches the child. Allow its normal camera cleanup.
        code=process.wait()
    report=build_report([output],output,args.scenario)
    ai_failed = any(run.get('pipeline_health', {}).get('status') in
                    ('MODEL_INIT_FAILED', 'INFERENCE_BLOCKED') for run in report['runs'].values())
    status = ('FAILED_NO_TRACE' if code else 'NO_TRACE') if not report['event_count'] else (
        'AI_PIPELINE_FAILED' if ai_failed else 'PARTIAL_TRACE_PROCESS_FAILED' if code else 'TRACE_RECORDED')
    (output/'manifest.json').write_text(json.dumps(dict(evidence=evidence,scenario=args.scenario,
        status=status, child_exit_code=code,events=report['event_count'],created_at=datetime.now().astimezone().isoformat()),indent=2)+'\n')
    if not report['event_count']:
        print(f'Profiling {status}: child exit code={code}; 0 trace events. '
              'Không có số đo pipeline/scenario; report chỉ ghi N/A.', flush=True)
    elif ai_failed:
        print('Profiling AI_PIPELINE_FAILED: log ghi nhận lỗi khởi tạo/chạy AI. '
              'Có trace video/GUI không có nghĩa pipeline AI/scenario hoàn tất.', flush=True)
    elif code:
        print(f'Profiling {status}: child exit code={code}; '
              'report chứa dữ liệu một phần, không xác nhận scenario hoàn tất.', flush=True)
    print('Report:',output/'report.html')
    return code or (3 if ai_failed else 0 if report['event_count'] else 2)


if __name__=='__main__':
    raise SystemExit(main())
