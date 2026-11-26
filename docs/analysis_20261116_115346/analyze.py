from pathlib import Path
import json,re,collections,datetime,hashlib
root=Path(__file__).resolve().parents[2];logs=root/'logs';out=Path(__file__).parent
sources=[logs/'ptz_20261116_115346.log',logs/'ptz_20261116_115346.jsonl']+list((logs/'camera').glob('camera_control_20261116_115349_764314.log*'))+list((logs/'latency').glob('latency_trace_20261116_115349_764314.jsonl*'))
cam=[];reports=[];errors=[]
for p in sources[2:]:
 for l in p.read_text().splitlines():
  x=json.loads(l[l.index('{'):])
  if 'camera_control' in p.name:cam.append(x)
  if x['event']=='LATENCY_REPORT':reports.append(x)
  if x['event'] in ('REID_ERROR','REID_PHASE_ERROR'):errors.append(x)
last=max(reports,key=lambda x:x['timestamp']);lines=sources[0].read_text().splitlines();start=datetime.datetime.fromisoformat(lines[0][:23]);end=datetime.datetime.fromisoformat(next(x['timestamp'] for x in cam if x['event']=='SHUTDOWN_REQUESTED' and x.get('shutdown_phase')=='gui')).replace(tzinfo=None)
d=collections.Counter();state='SEARCHING';prev=start;trans=[];recovery=[];lost=None
for l in lines:
 m=re.search(r'\[STATE\] (\w+) -> (\w+)',l)
 if not m:continue
 t=datetime.datetime.fromisoformat(l[:23]);d[state]+=(t-prev).total_seconds();prev=t;state=m[2];trans.append(dict(time=str(t),old=m[1],new=m[2]))
 if m[2]=='SEARCHING':lost=t
 if m[2]=='TARGET_LOCKED' and lost:
  recovery.append(dict(lost=str(lost),recovered=str(t),seconds=(t-lost).total_seconds()));lost=None
d[state]+=(end-prev).total_seconds();held=sum(d[s] for s in ('TARGET_LOCKED','PERSON_ZOOM','VERIFIED'));app=[json.loads(l) for l in sources[1].read_text().splitlines()]
r=dict(start=str(start),end=str(end),duration_seconds=(end-start).total_seconds(),state_seconds=d,held_seconds=held,held_percent=100*held/(end-start).total_seconds(),hold_count=sum(t['new']=='OCCLUDED_HOLD' for t in trans),recoveries=recovery,unrecovered_seconds=(end-lost).total_seconds() if lost else 0,app_events=collections.Counter(x['event'] for x in app),camera_events=collections.Counter(x['event'] for x in cam),recognition=collections.Counter(re.search(r'\[RECOGNITION\] (\w+)',l)[1] for l in lines if '[RECOGNITION]' in l),final_latency_report=last,reid_error_events=errors,source_hashes={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
(out/'metrics.json').write_text(json.dumps(r,indent=2));print(json.dumps({k:v for k,v in r.items() if k not in ('final_latency_report','source_hashes','reid_error_events','camera_events')},indent=2))
