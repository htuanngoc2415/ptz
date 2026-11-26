from pathlib import Path
from collections import Counter
import json,csv,hashlib
import numpy as np
out=Path(__file__).resolve().parent
shadow=[json.loads(l) for l in Path('logs/identity_shadow/shadow_d3b836c5ae5c492ba7ddb95282250359.jsonl').read_text().splitlines()]
rs=[r for r in shadow if r['event']=='SHADOW_COMPARISON'];start,end=shadow[0]['monotonic_ts'],shadow[-1]['monotonic_ts']
tr=[]
for p in Path('logs/latency').glob('latency_trace_20261119_173207_536257.jsonl*'):tr.extend(json.loads(l) for l in p.read_text().splitlines())
tr.sort(key=lambda r:r['monotonic_ns'])
def stats(a):
 return dict(n=len(a),median=float(np.median(a)),p95=float(np.percentile(a,95)),max=float(max(a))) if len(a) else None
lo=tr[0]['timestamp_monotonic'];hi=tr[-1]['timestamp_monotonic']
windows={}
for name,a,b in [('retained_on',max(lo,start),end),('retained_off_after',end,hi),('last_15s_on',end-15,end),('first_15s_off',end,end+15)]:
 events=[r for r in tr if a<=r['timestamp_monotonic']<b]
 gui=[r for r in events if r['event']=='GUI_SUBMITTED'];detect=[r for r in events if r['event']=='PERSON_DETECTION_RESULT']
 windows[name]=dict(start=a,end=b,duration_s=b-a,gui_samples=len(gui),gui_submit_hz=len(gui)/(b-a),detection_results=len(detect),detection_hz=len(detect)/(b-a),gui_latency_ms=stats([r['duration_ms'] for r in gui]))
summary={r['stage']:r for r in csv.DictReader(Path('logs/latency/latency_summary_20261119_173207_536257.csv').open())}
resets=[r for r in shadow if r['event']=='SHADOW_RESET'];policy=next(r for r in shadow if r['event']=='SHADOW_POLICY')
footer=shadow[-1]
result=dict(source_hash_matches={p:hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in policy['source_sha256'].items()},reset_count=len(resets),reset_sessions=sorted(set(r['session_id'] for r in resets)),reset_thresholds=sorted(set(r['threshold'] for r in resets)),completed_jobs=len(rs),rejected_jobs=footer['stale'],completion_fraction=len(rs)/(len(rs)+footer['stale']),sample_hz=len(rs)/(end-start),gap_over_1s=sum(b['timestamp']-a['timestamp']>1 for a,b in zip(rs,rs[1:])),pass_discard_fraction=footer['discarded_passes']/footer['completed_passes'],gui_full_session=summary['receive_to_gui_submit_ms'],trace_windows=windows,caveat='Trace retains only end of ON and OFF-after. Scene and workload differ; rates are observed event rates, not a causal FPS benchmark.')
(out/'detail.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
