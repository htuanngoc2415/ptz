import json,collections
from pathlib import Path
import numpy as np
root=Path('docs/identity_capture_audit_20261119');cap=json.loads((root/'audit.json').read_text())['captures'];events=[]
for p in Path('logs/latency').glob('latency_trace_20261119_081714_185749.jsonl*'):
 for line in p.open():
  try:events.append(json.loads(line))
  except ValueError:pass
events.sort(key=lambda r:r['monotonic_ns']);gui=[r for r in events if r['event']=='GUI_SUBMITTED'];slow=[r for r in gui if r['duration_ms']>500]
def stat(rows):
 a=[r['duration_ms'] for r in rows];return dict(n=len(a),median=float(np.median(a)),p95=float(np.percentile(a,95)),max=max(a)) if a else None
inside=lambda r:any(c['start']<=r['timestamp_monotonic']<=c['end'] for c in cap)
result=dict(retained_trace_start=events[0]['timestamp'],retained_trace_end=events[-1]['timestamp'],gui_record=stat([r for r in gui if inside(r)]),gui_outside_record=stat([r for r in gui if not inside(r)]),slow_gui=[{k:r.get(k) for k in ['timestamp','frame_id','duration_ms']}|dict(recording=inside(r)) for r in slow],phase1={})
for p in root.glob('capture_*/comparison'):
 report=json.loads((p/'report.json').read_text());rows=[json.loads(l) for l in (p/'identity.jsonl').read_text().splitlines()];base=[json.loads(l) for l in (p/'baseline.jsonl').read_text().splitlines()]
 result['phase1'][p.parent.name]=dict(selected={k:v['selected_observations'] for k,v in report['metrics'].items()},reasons=dict(collections.Counter(r['reason'] for r in rows)),first_selected={k:next((r['timestamp']-data[0]['timestamp'] for r in data if r['selected_candidate']),None) for k,data in [('baseline',base),('identity',rows)]})
(root/'latency_and_replay.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
