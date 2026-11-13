"""Append measured/annotated trials. Unknown outcomes stay null, never inferred."""
import json
from datetime import datetime, timezone
from pathlib import Path

SCENARIOS = ('normal_3m', 'normal_6m', 'long_range', 'backlight', 'low_light',
             'walking', 'back_view', 'short_occlusion', 'multiple_people', 'lost_and_reacquire')
METRICS = ('target_acquisition_success', 'wrong_target', 'acquisition_time_ms',
           'track_survival_ms', 'reacquire_time_ms', 'face_acquisition_success',
           'focus_success', 'identity_success', 'end_to_end_latency_ms', 'median_frame_age_ms')


def record_trial(output, scenario, metrics, notes='', session=None):
    if scenario not in SCENARIOS or set(metrics)-set(METRICS):
        raise ValueError('Unknown scenario or metric')
    for key, value in metrics.items():
        if value is None:
            continue
        if key.endswith('_success') or key == 'wrong_target':
            if type(value) is not bool:
                raise ValueError(f'{key} must be boolean or null')
        else:
            import math
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} must be finite nonnegative milliseconds or null')
    row = dict(timestamp=datetime.now(timezone.utc).isoformat(), scenario=scenario,
               session=session, notes=notes, **{key: metrics.get(key) for key in METRICS})
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
    return row


# Modular runner helpers deliberately use only the standard library. Reporting
# must remain usable on machines without CUDA, OpenCV, GI or model packages.
def distribution(values):
    import math
    values = sorted(float(v) for v in values
                    if type(v) in (int, float) and math.isfinite(v) and v >= 0)
    if not values:
        return None
    result = dict(count=len(values), mean=sum(values)/len(values), min=values[0], max=values[-1])
    for p in (50, 90, 99):
        index = (len(values)-1)*p/100
        lo, hi = math.floor(index), math.ceil(index)
        result[f'p{p}'] = values[lo]+(values[hi]-values[lo])*(index-lo)
    return result


def public_value(value):
    """Redact nested connection settings, including secrets in error strings."""
    from event_logging import redact
    if isinstance(value, dict):
        return {str(k): ('[redacted]' if any(s in str(k).lower()
                for s in ('password', 'username', 'secret', 'token', 'uri', 'url',
                          'api_key', 'credential', 'authorization'))
                else public_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_value(v) for v in value]
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, 'tolist'):
        return public_value(value.tolist())
    if isinstance(value, float):
        import math
        return value if math.isfinite(value) else None
    return value


def box_iou(a, b):
    inter = max(0, min(a[2], b[2])-max(a[0], b[0]))*max(0, min(a[3], b[3])-max(a[1], b[1]))
    union = (a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union > 0 else 0.


class GroundTruth:
    """Explicitly annotated frames only; missing frames are NOT empty scenes.

    Schema: {"frames": [{"frame_id": 1, "objects": [{"bbox": [x1,y1,x2,y2],
    "identity": "person-a"}]}]}. Boxes must describe the evaluated class only.
    Greedy descending-IoU one-to-one matching at the recorded evaluation threshold.
    """
    def __init__(self, data, threshold=.5):
        import math
        if not 0 < threshold <= 1:
            raise ValueError('IoU threshold must be in (0, 1]')
        self.threshold, self.frames = threshold, {}
        for frame in data['frames']:
            fid = frame['frame_id']
            if type(fid) is not int or fid < 1 or fid in self.frames:
                raise ValueError('Ground truth frame IDs must be unique positive integers')
            identities = set()
            for obj in frame['objects']:
                box = obj['bbox']
                if (len(box) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in box)
                        or box[2] <= box[0] or box[3] <= box[1] or min(box) < 0):
                    raise ValueError('Invalid ground truth bbox')
                ident = obj.get('identity')
                if ident is not None:
                    if not isinstance(ident, str) or not ident or ident in identities:
                        raise ValueError('Identity must be unique nonempty text within each frame')
                    identities.add(ident)
            self.frames[fid] = frame['objects']
        self.tp = self.fp = self.fn = self.evaluated = 0
        self.switches = self.fragments = self.identity_observations = 0
        self.tracks, self.missing = {}, set()
        self.seen = set()

    def observe(self, frame_id, predictions):
        if frame_id not in self.frames:
            return
        if frame_id in self.seen:
            raise ValueError('Cannot evaluate the same frame twice')
        self.seen.add(frame_id)
        truth = self.frames[frame_id]
        pairs = sorted(((box_iou(p['bbox'], g['bbox']), i, j)
                        for i, p in enumerate(predictions) for j, g in enumerate(truth)), reverse=True)
        used_p, used_g = set(), set()
        for score, i, j in pairs:
            if score < self.threshold or i in used_p or j in used_g:
                continue
            used_p.add(i); used_g.add(j)
            ident, track = truth[j].get('identity'), predictions[i].get('track_id')
            if ident is not None and track is not None:
                self.identity_observations += 1
                if ident in self.tracks:
                    self.switches += self.tracks[ident] != track
                    self.fragments += ident in self.missing
                self.tracks[ident] = track
                self.missing.discard(ident)
        for j, obj in enumerate(truth):
            if j not in used_g and obj.get('identity') in self.tracks:
                self.missing.add(obj['identity'])
        self.tp += len(used_p)
        self.fp += len(predictions)-len(used_p)
        self.fn += len(truth)-len(used_g)
        self.evaluated += 1

    def summary(self):
        return dict(annotated_frames=len(self.frames), evaluated_frames=self.evaluated,
                    matching='greedy_descending_iou', iou_threshold=self.threshold,
                    tp=self.tp, fp=self.fp, fn=self.fn,
                    precision=self.tp/(self.tp+self.fp) if self.tp+self.fp else None,
                    recall=self.tp/(self.tp+self.fn) if self.tp+self.fn else None,
                    id_switches=self.switches if self.identity_observations else None,
                    track_fragmentation=self.fragments if self.identity_observations else None)


def summarize_trace(rows):
    """No cross-session pairing, no inferred sensor clock, no inferred accuracy."""
    from collections import Counter, defaultdict
    metrics, events, rejects, reasons = defaultdict(list), Counter(), Counter(), Counter()
    starts, pairs = {}, defaultdict(list)
    pair_definitions = {
        'TARGET_LOCKED': ('TARGET_SEARCH_STARTED', 'target_acquisition_ms'),
        'ZOOM_COMMAND_SENT': ('TARGET_LOCKED', 'lock_to_first_zoom_ms'),
        'TARGET_RATIO_REACHED': ('TARGET_LOCKED', 'time_to_target_ratio_ms'),
        'TARGET_RECOVERED': ('TARGET_LOST', 'tracking_recovery_ms'),
        'ZOOM_SETTLED': ('ZOOM_STARTED', 'zoom_settling_ms'),
        'FOCUS_SETTLED': ('FOCUS_STARTED', 'focus_settling_ms'),
    }
    consumed = set()
    for r in rows:
        event = r.get('event', 'UNKNOWN')
        events[event] += 1
        if r.get('metric') and r.get('duration_ms') is not None:
            metrics[r['metric']].append(r['duration_ms'])
        reason = (r.get('reject_reason') or r.get('reason') or r.get('rejected_reason')
                  or r.get('command_rejection_reason'))
        if reason:
            reasons[str(reason)] += 1
            if (r.get('reject_reason') or r.get('rejected_reason') or r.get('command_rejection_reason')
                    or any(word in event.upper() for word in ('REJECT', 'SKIPP', 'INVALID'))):
                rejects[str(reason)] += 1
        sid = r.get('session_id') or r.get('lock_session_id')
        track = r.get('track_id', r.get('target_track_id'))
        generation = r.get('track_generation')
        ns = r.get('monotonic_ns')
        # Track generation must be available to derive paired lifecycle timings.
        if sid is None or track is None or generation is None or type(ns) is not int:
            continue
        key = (sid, track, generation)
        if event in pair_definitions:
            begin, metric = pair_definitions[event]
            first = starts.get((key, begin))
            token = (key, metric, first)
            # STOP RPCs are not first zoom movements.
            is_zoom = event != 'ZOOM_COMMAND_SENT' or (
                r.get('rpc_method') == 'ptz.start' and
                r.get('rpc_code') in ('PositionABS', 'ZoomTele', 'ZoomWide'))
            if first is not None and ns >= first and token not in consumed and is_zoom:
                pairs[metric].append((ns-first)/1e6)
                consumed.add(token)
        starts[(key, event)] = ns
    summaries = {k: distribution(v) for k, v in metrics.items()}
    summaries.update({k: distribution(v) for k, v in pairs.items()})
    ranking = sorted((dict(metric=k, **v) for k, v in summaries.items() if v),
                     key=lambda x: x['p90'], reverse=True)
    model_calls={name: events.get(event) for name,event in (
        ('yolo','YOLO_INFERENCE_START'),('osnet','REID_INFERENCE_START'),
        ('scrfd','SCRFD_INFERENCE_START'),('arcface','ARCFACE_INFERENCE_START'))}
    return dict(latency_ms=summaries, events=dict(events), reject_reasons=dict(rejects),
                event_reasons=dict(reasons),
                model_calls_observed=model_calls,
                latency_candidates=ranking[:10],
                bottleneck_conclusion='NOT_RUN: ranked durations may overlap; require representative workload and contention evidence',
                accuracy=None, false_reacquisition=None, sensor_frame_latency_ms=None)


def write_report(directory, report):
    """Self-contained HTML with SVG latency bars and artifact links; no CDN."""
    import csv
    import html
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = public_value(report)
    (directory/'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    fields = ('stage', 'case', 'mode', 'repetition', 'status', 'reason', 'artifact')
    with (directory/'report.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(report['results'])
    latency_rows = []
    for result in report['results']:
        for name, stats in result.get('metrics', {}).get('latency_ms', {}).items():
            if stats:
                latency_rows.append(dict(stage=result['stage'], case=result['case'],
                                         repetition=result['repetition'], metric=name, **stats))
    with (directory/'latency.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=('stage','case','repetition','metric','count','mean','min','max','p50','p90','p99'))
        writer.writeheader(); writer.writerows(latency_rows)
    esc = lambda x: html.escape(str(x))
    body = ['<!doctype html><meta charset="utf-8"><title>PTZ modular benchmark</title>',
            '<style>body{font:15px system-ui;margin:2em;max-width:1300px}td,th{border:1px solid #ccc;padding:8px;text-align:left}table{border-collapse:collapse}pre{white-space:pre-wrap}svg{max-width:100%}</style>',
            '<h1>PTZ modular benchmark</h1><p>PASS applies only to the named case and its evidence scope. Unknown accuracy remains null. Mock timings are not hardware timings.</p>',
            '<p><a href="report.json">JSON</a> · <a href="report.csv">CSV</a> · <a href="latency.csv">Latency CSV</a> · <a href="manifest.json">Manifest</a></p>',
            '<table><tr>'+''.join('<th>'+f+'</th>' for f in fields)+'</tr>']
    for r in report['results']:
        body.append('<tr>'+''.join('<td>'+(
            '<a href="'+esc(r[f])+'/timeline.html">timeline / evidence</a>' if f == 'artifact' and r.get(f)
            else esc(r.get(f, '')))+'</td>' for f in fields)+'</tr>')
    body.append('</table><h2>p90 latency (ms)</h2>')
    for row in latency_rows:
        maximum = max((v['p90'] for v in latency_rows), default=1) or 1
        width = max(1, 650*row['p90']/maximum)
        body.append('<p>'+esc(f"stage {row['stage']} / {row['case']} / {row['metric']}")+
                    f'<br><svg width="850" height="24" role="img" aria-label="p90 {row["p90"]:.3f} ms"><rect width="{width}" height="20" fill="#367ab8"/><text x="{width+5}" y="16">{row["p90"]:.3f} ms</text></svg></p>')
    body.append('<h2>Evidence and limitations</h2><pre>'+esc(json.dumps(report, indent=2, ensure_ascii=False))+'</pre>')
    (directory/'report.html').write_text('\n'.join(body), encoding='utf8')
