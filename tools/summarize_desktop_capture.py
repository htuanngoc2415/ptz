#!/usr/bin/env python3
"""Rebuild measured-window statistics from desktop A/B evidence (no inferred data)."""
import argparse
from collections import Counter, defaultdict
import html
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from telemetry.benchmark import distribution


def rebuild(run):
    result = json.loads((run/'result.json').read_text())
    begin, end = result.get('begin_ns'), result.get('end_ns')
    if not begin or not end:
        return result
    metrics, events = defaultdict(list), Counter()
    errors = []
    for path in (run/'trace').rglob('latency_trace*.jsonl*'):
        for line in path.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not begin <= row.get('monotonic_ns', 0) <= end:
                continue
            events[row.get('event')] += 1
            value = row.get('duration_ms')
            if row.get('metric') and isinstance(value, (int,float)):
                metrics[row['metric']].append(value)
            if 'ERROR' in row.get('event','') or 'FAILED' in row.get('event',''):
                errors.append(dict(event=row.get('event'), ns=row.get('monotonic_ns')))
    result['metrics'] = {k:distribution(v) for k,v in metrics.items()}
    result['events'] = dict(events)
    result['error_events'] = errors
    result['run'] = run.name
    (run/'measured.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('directory', type=Path)
    args=ap.parse_args()
    runs=[rebuild(p.parent) for p in sorted(args.directory.glob('*/result.json'))]
    (args.directory/'summary.json').write_text(json.dumps(runs, indent=2)+'\n')
    for r in runs:
        m=r.get('metrics',{})
        print(r.get('run'), r.get('status'), 'fps',r.get('capture_fps'),
              'drift',[round(x['drift_seconds'],3) for x in r.get('pts_segments',[])],
              'AI', {k:m[k] for k in ('yolo_inference_ms','reid_inference_ms','scrfd_inference_ms','arcface_inference_ms') if k in m})


if __name__=='__main__':
    main()
