"""Read-only shadow v3/v4 audit, with retained ON/OFF trace coverage."""
import argparse
import glob
import json
from pathlib import Path
import numpy as np
from tools.identity_replay import digest, write_json


def stats(values):
    a = np.asarray(values, dtype=float)
    return dict(n=len(a),p50=float(np.median(a)),p95=float(np.percentile(a,95)),
                max=float(max(a))) if len(a) else dict(n=0,p50=None,p95=None,max=None)


def analyze(path, trace_paths=()):
    rows = [json.loads(l) for l in Path(path).read_text().splitlines()]
    start = next(r for r in rows if r['event']=='SHADOW_START')
    stop = next((r for r in reversed(rows) if r['event']=='SHADOW_STOP'), None)
    policy = next((r for r in rows if r['event']=='SHADOW_POLICY'), {})
    samples = [r for r in rows if r['event']=='SHADOW_COMPARISON']
    gaps = np.diff([r['timestamp'] for r in samples])
    errors = []
    if any(g <= 0 for g in gaps):errors.append('NONMONOTONIC_SAMPLES')
    if any(not 0 <= r['age_ms'] <= start['max_age_s']*1000+1 for r in samples):errors.append('INVALID_AGE')
    if any(r.get('physical_commands') != 0 or r['decision'].get('ptz_authorized') is not False for r in samples):errors.append('UNEXPECTED_AUTHORITY')
    if stop and stop['saved'] != len(samples):errors.append('FOOTER_COUNT_MISMATCH')
    # VERIFIED means operational evidence exists, never an accuracy/performance pass.
    status = ('VERIFIED' if start.get('batched') is True and stop and samples and not errors
              else 'AWAITING_LIVE_EVIDENCE')
    first = start.get('monotonic_ts', samples[0]['timestamp'] if samples else 0.)
    last = (stop or {}).get('monotonic_ts', samples[-1]['timestamp'] if samples else first)
    traces = []
    for p in trace_paths:
        with Path(p).open() as f:
            for line in f:
                r = json.loads(line)
                t = r.get('timestamp_monotonic')
                if t is not None and first-20 <= t <= last+20:
                    traces.append(r)
    windows = {}
    for name,lo,hi in (('OFF_before',first-20,first),('ON',first,last),('OFF_after',last,last+20)):
        events = [r for r in traces if lo <= r['timestamp_monotonic'] < hi]
        bounds = [r['timestamp_monotonic'] for r in events]
        duration = max(bounds)-min(bounds) if bounds else 0
        detections = [r for r in events if r['event']=='PERSON_DETECTION_RESULT']
        gui = [r['duration_ms'] for r in events if r['event']=='GUI_SUBMITTED']
        waits = [r['duration_ms'] for r in events if r['event']=='INFERENCE_QUEUE_EXIT'
                 and r.get('branch') != 'identity_shadow' and r.get('resource')!='cpu']
        windows[name] = dict(requested_duration_s=hi-lo,retained_span_s=duration,
            retained_first=min(bounds) if bounds else None,retained_last=max(bounds) if bounds else None,
            detection_results=len(detections),detection_hz=len(detections)/duration if duration else None,
            foreground_wait_ms=stats(waits),receive_to_gui_submit_ms=stats(gui))
    sample_span = samples[-1]['timestamp']-samples[0]['timestamp'] if len(samples)>1 else 0
    return dict(status=status,status_scope='v4 operational log only; no GT or live performance acceptance',
        path=str(path),sha256=digest(path),runtime='v4' if start.get('batched') else 'v3_or_earlier',
        source_sha256=policy.get('source_sha256'),start=start,stop=stop,errors=errors,
        comparisons=len(samples),comparison_per_session_second=len(samples)/(last-first) if last>first else None,
        comparison_per_sample_span_second=len(samples)/sample_span if sample_span else None,
        gap_s=stats(gaps),gaps_above_1s=int(sum(gaps>1)),age_ms=stats([r['age_ms'] for r in samples]),
        windows=windows,trace_sha256={str(p):digest(p) for p in trace_paths},
        limitations=['ON/OFF scene and workload may differ; not causal A/B',
                    'Retained span does not prove complete trace coverage',
                    'GUI-submit is not glass-to-glass; no identity accuracy claim'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--shadow',type=Path)
    p.add_argument('--trace',action='append',default=[],help='Quoted glob, including rotated traces')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.shadow:
        result=analyze(a.shadow,sorted({x for pattern in a.trace for x in glob.glob(pattern)}))
    else:
        files=sorted(Path('logs/identity_shadow').glob('*.jsonl'))
        reports=[analyze(f) for f in files]
        result=dict(status='VERIFIED' if any(r['status']=='VERIFIED' for r in reports) else 'AWAITING_LIVE_EVIDENCE',
                    sessions=reports)
    write_json(a.output,result)
    print(json.dumps(dict(output=str(a.output),status=result['status'])))


if __name__=='__main__':main()
