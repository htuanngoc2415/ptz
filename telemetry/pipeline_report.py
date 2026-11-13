"""Offline, dependency-free analysis of the existing latency trace format.

No motor/model imports. Never join clocks across runs or sum parallel durations.
"""
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
from telemetry.benchmark import distribution, public_value


def read_traces(paths):
    rows, errors, sources = [], [], []
    seen_paths = set()
    for supplied in paths:
        supplied = Path(supplied)
        if supplied.is_dir():
            candidates = sorted(supplied.rglob('latency_trace_*.jsonl*'))
        else:
            base = re.sub(r'\.jsonl\.\d+$', '.jsonl', str(supplied))
            candidates = sorted(Path(base).parent.glob(Path(base).name+'*'))
        for path in candidates:
            if path.resolve() in seen_paths or not path.is_file():
                continue
            seen_paths.add(path.resolve())
            sources.append(str(path))
            fallback = re.sub(r'\.jsonl(?:\.\d+)?$', '', str(path.resolve()))
            with path.open(errors='replace') as stream:
                for number, line in enumerate(stream, 1):
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError('not an event object')
                        ns = row.get('monotonic_ns')
                        if type(ns) is not int or ns < 0:
                            raise ValueError('missing monotonic_ns; wall clock is not substituted')
                    except (ValueError, TypeError) as exc:
                        errors.append(dict(file=str(path), line=number, reason=str(exc)))
                        continue
                    row['run_id'] = row.get('run_id') or fallback
                    row['session_id'] = row.get('session_id') or row.get('lock_session_id')
                    row['track_id'] = row.get('track_id', row.get('target_track_id'))
                    row.setdefault('track_generation', None)
                    row.setdefault('frame_id', None)
                    row.setdefault('timestamp_monotonic', ns/1e9)
                    rows.append(row)
    rows.sort(key=lambda r: (r['run_id'], r['monotonic_ns']))
    # Acquisition and same-frame joins are unique-only, never nearest-time joins.
    acqs, frames = defaultdict(set), defaultdict(set)
    for r in rows:
        if r['session_id']:
            if r.get('acquisition_id'):
                acqs[(r['run_id'], r['acquisition_id'])].add(r['session_id'])
            if r.get('frame_id') is not None:
                frames[(r['run_id'], r['frame_id'])].add(r['session_id'])
    for r in rows:
        if r['session_id']:
            r['correlation_evidence'] = 'explicit_session'
            continue
        # Never reattribute an unscoped recovery/manual motor command to a
        # future lock just because they share a frame or acquisition candidate.
        can_join = (r['event'].startswith(('YOLO_', 'REID_', 'SCRFD_', 'FRAME_', 'INFERENCE_'))
                    or r['event'] in ('PERSON_DETECTION_RESULT','TARGET_FIRST_OBSERVED','TARGET_CANDIDATE_FOUND'))
        if not can_join:
            r['correlation_evidence'] = 'unscoped'
            continue
        found = acqs.get((r['run_id'], r.get('acquisition_id')), set())
        evidence = 'unique_acquisition_join'
        if not found:
            found = frames.get((r['run_id'], r.get('frame_id')), set())
            evidence = 'unique_frame_join_shared_frame_work'
        if len(found) == 1:
            r['session_id'] = next(iter(found))
            r['correlation_evidence'] = evidence
        else:
            r['correlation_evidence'] = 'unscoped'
    return rows, dict(sources=sources, invalid_rows=errors)


MILESTONES = {
    'yolo': ('YOLO_INFERENCE_END',), 'osnet_lock': ('TARGET_LOCKED',),
    'first_zoom_dispatch': ('ZOOM_COMMAND_SENT',),
    'motor_arrival': ('ZOOM_TARGET_REACHED',),
    'image_settling': ('ZOOM_VISUALLY_STABLE',),
    'framing': ('FRAMING_CONFIRMED', 'TARGET_RATIO_REACHED'),
    'face': ('FACE_DETECTED',), 'usable_face': ('FACE_QUALITY_PASSED',),
    'focus_start': ('AF_STARTED',), 'focus_quality_validated': ('FOCUS_STABLE',),
    'identity': ('IDENTITY_CONFIRMED',),
}


def journey(rows, appearance_ns=None):
    origin = None
    for r in rows:
        if r['event'] == 'TARGET_FIRST_OBSERVED' and type(r.get('observed_ns')) is int:
            origin = r['observed_ns'] if origin is None else min(origin, r['observed_ns'])
    if appearance_ns is not None:
        origin = appearance_ns
    marks = {}
    locks = [r['monotonic_ns'] for r in rows if r['event']=='TARGET_LOCKED']
    locked = min(locks) if locks else None
    for name, events in MILESTONES.items():
        candidates = [r for r in rows if r['event'] in events]
        if name == 'first_zoom_dispatch':
            candidates = [r for r in candidates if r.get('rpc_method') == 'ptz.start'
                          and r.get('rpc_code') in ('PositionABS', 'ZoomTele', 'ZoomWide')
                          and r.get('correlation_evidence') == 'explicit_session'
                          and locked is not None and r['monotonic_ns'] >= locked]
        if name == 'yolo' and origin is not None:
            candidates = [r for r in candidates if r['monotonic_ns'] >= origin]
        event = min(candidates, key=lambda r:r['monotonic_ns']) if candidates else None
        stamp = event['monotonic_ns'] if event else None
        marks[name] = dict(monotonic_ns=stamp, since_observed_frame_ms=(stamp-origin)/1e6
            if stamp is not None and origin is not None and stamp >= origin else None,
            frame_id=event.get('frame_id') if event else None)
    return dict(origin_ns=origin, origin=('operator-annotated target appearance frame, application receipt clock'
                if appearance_ns is not None else 'received frame of first accepted ReID candidate; actual appearance N/A'),
                milestones=marks, end_to_end_ms=marks['identity']['since_observed_frame_ms'])


def category(metric):
    if metric.endswith('observed_dwell_ms') or 'af_waiting' in metric:
        return 'state machine (observed dwell)'
    if 'queue_wait' in metric or 'mailbox_wait' in metric:
        return 'scheduler / mailbox'
    if metric in ('zoom_motor_movement_ms', 'zoom_motor_duration_ms', 'focus_motor_loop_ms'):
        return 'camera motor (polling boundary)'
    if metric == 'zoom_visual_stabilization_ms':
        return 'zoom request to visual settling (inclusive)'
    if metric in ('focus_quality_wait_ms', 'focus_stabilization_ms'):
        return 'focus / visual settling'
    if metric in ('yolo_inference_ms','reid_inference_ms','scrfd_inference_ms','arcface_inference_ms','focus_cnn_ms'):
        return 'inference (host wall time)'
    return None


RECOMMENDATIONS = {
    'zoom request to visual settling (inclusive)': 'This includes dispatch, motor and image confirmation; pair command_id milestones before attributing the residual delay to image settling alone.',
    'inference (host wall time)': 'Inspect preprocessing, batch size, CPU copies and GPU execution evidence; compare under the same workload before changing model or cadence.',
    'scheduler / mailbox': 'Join queue spans with overlapping resource hold spans; inspect priority starvation and stale job rates before changing scheduling.',
    'camera motor (polling boundary)': 'Compare dispatch, ACK, position telemetry and visual settling separately; inspect polling cadence and travel distance.',
    'focus / visual settling': 'Separate CNN, fresh-frame wait, motor travel and quality confirmation; inspect exposure/ROI quality and unsuccessful attempts.',
    'state machine (observed dwell)': 'Inspect reason, transitions and concurrent work; distinguish intended guards/cooldowns from critical-path delay before changing thresholds.',
}


def summarize(rows):
    metrics, reasons = defaultdict(list), Counter()
    calls = Counter(r['event'] for r in rows)
    intervals, gates = [], []
    for r in rows:
        duration = r.get('duration_ms')
        if (r.get('metric') and type(duration) in (int,float) and math.isfinite(duration) and duration >= 0):
            metrics[r['metric']].append(duration)
            start = r.get('start_ns', r['monotonic_ns']-int(duration*1e6))
            intervals.append(dict(start_ns=start, end_ns=r['monotonic_ns'],
                worker=r.get('thread','unknown'), event=r['event'], metric=r['metric'],
                duration_ms=duration, reason=r.get('reason'), frame_id=r.get('frame_id'),
                session_id=r.get('session_id'), run_id=r['run_id'],
                evidence=r.get('evidence',r.get('timing')), span_id=r.get('span_id')))
        reason = (r.get('command_rejection_reason') or r.get('reject_reason') or
                  r.get('decision_suppression_reason') or r.get('rejected_reason'))
        if not reason and any(v in r['event'] for v in ('REJECT','SKIP','DROP')):
            reason = r.get('reason')
        if reason:
            reasons[str(reason)] += 1
        if r['event'] in ('GATE_START','GATE_END'):
            gates.append(r)
    stats = {k:distribution(v) for k,v in metrics.items()}
    rankings = []
    for name, values in stats.items():
        group = category(name)
        if group:
            rankings.append(dict(category=group, metric=name, **values,
                recommendation=RECOMMENDATIONS[group]))
    rankings.sort(key=lambda r:r['p90'], reverse=True)
    counters = {}
    snapshots = [r.get('counters_and_rates', r.get('counters',{})) for r in rows
                 if r['event'] in ('LATENCY_REPORT','RESOURCE_SAMPLE')]
    if snapshots:
        counters = snapshots[-1]
    failures = [dict(event=r['event'], monotonic_ns=r['monotonic_ns'], error=r.get('error'))
                for r in rows if r['event'] in ('STRUCTURED_MODEL_INIT_ERROR', 'MODEL_INIT_ERROR',
                                               'DETECTOR_CONTROL_BLOCKED', 'STRUCTURED_INFERENCE_BLOCKED')]
    health = ('MODEL_INIT_FAILED' if any('MODEL_INIT_ERROR' in r['event'] for r in failures)
              else 'INFERENCE_BLOCKED' if failures
              else 'INFERENCE_OBSERVED' if calls['YOLO_INFERENCE_START']
              else 'NO_INFERENCE_OBSERVED')
    return dict(latency_ms=stats, model_calls={m:calls.get(ev) for m,ev in
        [('YOLO','YOLO_INFERENCE_START'),('OSNet','REID_INFERENCE_START'),
         ('SCRFD','SCRFD_INFERENCE_START'),('ArcFace','ARCFACE_INFERENCE_START')]},
        events=dict(calls), reject_reasons=dict(reasons), ranked_observed_delays=rankings,
        intervals=intervals, gates=gates, counters_at_run_end=counters,
        pipeline_health=dict(status=health, failures=failures,
            note='Video/GUI events alone do not establish a working AI pipeline; this is retained-trace evidence, not scenario success.'),
        sensor_to_display_ms=None, physical_display_fps=None, id_switch_ground_truth=None)


def build_report(paths, output, scenario='unknown', annotations=None):
    rows, provenance = read_traces(paths)
    annotations = json.loads(Path(annotations).read_text()) if annotations else []
    annotated = {}
    for note in annotations:
        key = (note['run_id'], note['session_id'])
        if key in annotated:
            raise ValueError('Duplicate session appearance annotation')
        received = [r for r in rows if r['run_id']==key[0] and r['event']=='FRAME_RECEIVED'
                    and r.get('frame_id')==note['appearance_frame_id']]
        if len(received) != 1:
            raise ValueError('Appearance annotation requires exactly one matching FRAME_RECEIVED event')
        annotated[key] = received[0]['monotonic_ns']
    provenance['appearance_annotations'] = annotations
    groups = defaultdict(list)
    for row in rows:
        groups[(row['run_id'], row.get('session_id') or 'UNSCOPED')].append(row)
    if set(annotated)-set(groups):
        raise ValueError('Appearance annotation references an unknown run/session')
    if scenario == 'unknown':
        labels = {r.get('scenario') for r in rows} - {None, 'unknown'}
        if len(labels) == 1:
            scenario = next(iter(labels))
    sessions = []
    runs = sorted({r['run_id'] for r in rows})
    for run, sid in sorted(groups):
        selected = groups[(run,sid)]
        start = min(r['monotonic_ns'] for r in rows if r['run_id']==run)
        end = max(r['monotonic_ns'] for r in rows if r['run_id']==run)
        data = summarize(selected)
        # Resource/counter samples are process-wide context, not target CPU attribution.
        resources = [r for r in rows if r['run_id']==run and r['event']=='RESOURCE_SAMPLE']
        data.update(run_id=run, session_id=sid, start_ns=start, end_ns=end,
                    journey=journey(selected, annotated.get((run,sid))), resources=resources,
                    generation_coverage=sum(r.get('track_generation') is not None for r in selected)/len(selected),
                    transitions=[r for r in selected if 'TRANSITION' in r['event'] or r['event'] in ('ZOOM_STATE','REID_STATE')],
                    selected_events=[r for r in selected if r['event'] not in ('LATENCY_REPORT', 'RESOURCE_SAMPLE')])
        sessions.append(data)
    series = {}
    for run in runs:
        selected = [r for r in rows if r['run_id']==run]
        base = selected[0]['monotonic_ns']
        buckets = defaultdict(Counter)
        seen_gui = set()
        for r in selected:
            bucket = int((r['monotonic_ns']-base)/1e9)
            if r['event'] == 'FRAME_RECEIVED':
                buckets[bucket]['receive_fps'] += 1
            if r['event'] == 'PERSON_DETECTION_RESULT':
                buckets[bucket]['detection_fps'] += 1
            if r['event'] == 'GUI_SUBMITTED' and r.get('frame_id') not in seen_gui:
                buckets[bucket]['gui_fps'] += 1
                seen_gui.add(r.get('frame_id'))
        series[run] = [dict(monotonic_ns=base+i*1_000_000_000, **b) for i,b in sorted(buckets.items())]
    run_summaries = {run:summarize([r for r in rows if r['run_id']==run]) for run in runs}
    for data in run_summaries.values():
        data.pop('intervals'); data.pop('gates')
    for run in runs:
        run_summaries[run]['end_to_end_ms'] = distribution([
            s['journey']['end_to_end_ms'] for s in sessions if s['run_id']==run])
        reports = [r for r in rows if r['run_id']==run and r['event']=='LATENCY_REPORT']
        run_summaries[run]['emit_overhead_bounded_window'] = next((s for r in reversed(reports)
            for s in r.get('statistics',[]) if s.get('stage')=='profiling_emit_sample_ms'), None)
    report = public_value(dict(schema_version=1, scenario=scenario, evidence='recorded_trace' if rows else 'no_trace',
        provenance=provenance, event_count=len(rows), sessions=sessions, runs=run_summaries, series=series,
        limitations=[
            'Actual target appearance, sensor-to-display, physical display scanout and ground-truth ID switches are N/A without external evidence.',
            'E2E uses timestamp differences. Parallel model/zoom/focus spans overlap; never sum their p90 values.',
            'Ranked durations are observed delay candidates, not proven causal bottlenecks. Host CUDA timings may include deferred synchronization in later stages.',
            'Legacy logs may be rotated/truncated and lack generation, focus, state or utilization evidence. Missing events mean N/A, not zero.',
            'State dwell is bounded by observations; an unclosed GATE_START is right-censored. Rejections alone do not establish block duration.',
            'Frame drops count application mailbox gaps and replacements. RTSP/network/decoder drops remain N/A.',
            'RESOURCE_SAMPLE CPU/GPU and counters describe the whole process/run, including other sessions and profiling writers.',
            'Scenario is an operator label, not a scenario success assertion. No physical motor evidence can be obtained from video replay.',
        ]))
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding='utf8')
    with (output/'latency.csv').open('w', newline='') as stream:
        fields = ['run_id','session_id','metric','count','mean','min','max','p50','p90','p99']
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for session in sessions:
            for metric, stats in session['latency_ms'].items():
                writer.writerow(dict(run_id=session['run_id'],session_id=session['session_id'],metric=metric,**stats))
    template = Path(__file__).with_name('pipeline_report.html').read_text()
    encoded = json.dumps(report, ensure_ascii=False, allow_nan=False).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    (output/'report.html').write_text(template.replace('__REPORT_DATA__',encoded), encoding='utf8')
    return report
