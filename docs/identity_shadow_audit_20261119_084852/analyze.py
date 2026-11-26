from pathlib import Path
import json,collections,datetime,hashlib
import numpy as np,cv2
from tools.identity_dataset import load_capture,verified_image
out=Path(__file__).resolve().parent
cap=Path('logs/identity_capture/capture_1b1ecfbd76204aefa35e048b8e6ed40a')
meta,summ,frames=load_capture(cap)
for r in frames:
 im=cv2.imread(str(verified_image(cap,r)));assert list(im.shape)==r['frame_shape']
shadow=[json.loads(l) for l in Path('logs/identity_shadow/shadow_5660ef2737454217807c15be0d38a41b.jsonl').read_text().splitlines()];rs=[r for r in shadow if r['event']=='SHADOW_COMPARISON'];byid={r['frame_id']:r for r in frames}
def stats(a):
 return dict(n=len(a),median=float(np.median(a)),p95=float(np.percentile(a,95)),max=float(max(a))) if len(a) else None
cats=collections.Counter();mismatches=[];spans=[]
for k,r in enumerate(rs):
 b=r['baseline_selected'];s=r['decision']['selected_candidate']
 category='both_hold' if b is None and s is None else 'shadow_only' if b is None else 'baseline_only' if s is None else 'same_candidate' if b==s else 'different_candidate'
 cats[category]+=1;r['category']=category
 assert r['different']==(b!=s)
 assert r['age_ms']<=800.1 and not r['decision']['ptz_authorized'] and r['physical_commands']==0
 if r['frame_id'] in byid:
  f=byid[r['frame_id']]
  if f['timestamp']!=r['timestamp'] or f['selected_candidate']!=b:mismatches.append(r['frame_id'])
 if r['different']:
  if not spans or spans[-1]['last_index']!=k-1 or spans[-1]['category']!=category:
   spans.append(dict(category=category,start=r['timestamp'],first_frame=r['frame_id'],frames=0))
  spans[-1].update(end=r['timestamp'],last_frame=r['frame_id'],frames=spans[-1]['frames']+1,last_index=k)
start,end=rs[0]['timestamp'],rs[-1]['timestamp']
logs=[json.loads(l) for l in Path('logs/ptz_20261119_084852.jsonl').read_text().splitlines()];groups={x:[] for x in ['before','shadow_sample_interval','after']}
for r in logs:
 if r['event']=='FRAME_METRICS':groups['before' if r['monotonic_ts']<start else 'after' if r['monotonic_ts']>end else 'shadow_sample_interval'].append(r)
metrics={k:{f:stats([r[f] for r in rows]) for f in ['frame_age_ms','yolo_ms','reid_ms']} for k,rows in groups.items()}
tr=[]
for p in Path('logs/latency').glob('latency_trace_20261119_084855_454390.jsonl*'):
 for line in p.open():
  try:tr.append(json.loads(line))
  except ValueError:pass
tr.sort(key=lambda r:r['monotonic_ns']);gui=[r for r in tr if r['event']=='GUI_SUBMITTED']
result=dict(capture=dict(path=str(cap),summary=summ,duration=frames[-1]['timestamp']-frames[0]['timestamp'],interval_s=stats(np.diff([r['timestamp'] for r in frames]))),shadow=dict(start=shadow[0],stop=shadow[-1],samples=len(rs),duration=end-start,interval_s=stats(np.diff([r['timestamp'] for r in rs])),processing_ms=stats([r['processing_ms'] for r in rs]),age_ms=stats([r['age_ms'] for r in rs]),categories=dict(cats),reasons=dict(collections.Counter(r['decision']['reason'] for r in rs)),sessions=[r for r in shadow if r['event']=='SHADOW_SESSION'],matched_capture_frames=sum(r['frame_id'] in byid for r in rs),metadata_mismatches=mismatches,difference_spans=spans),metrics=metrics,latency=dict(retained_start=tr[0]['timestamp'],retained_end=tr[-1]['timestamp'],during=stats([r['duration_ms'] for r in gui if start<=r['timestamp_monotonic']<=end]),outside=stats([r['duration_ms'] for r in gui if not start<=r['timestamp_monotonic']<=end])))
(out/'audit.json').write_text(json.dumps(result,indent=2))
for category in ['baseline_only','shadow_only','different_candidate']:
 choices=[r for r in rs if r['category']==category and r['frame_id'] in byid]
 if not choices:continue
 tiles=[]
 for idx in np.linspace(0,len(choices)-1,min(12,len(choices)),dtype=int):
  r=choices[idx];f=byid[r['frame_id']];im=cv2.imread(str(cap/f['image']))
  for c in f['candidates']:
   x1,y1,x2,y2=map(int,c['bbox']);color=(0,255,0) if c['candidate_id']==r['decision']['selected_candidate'] else (0,160,255) if c['candidate_id']==r['baseline_selected'] else (180,180,180)
   cv2.rectangle(im,(x1,y1),(x2,y2),color,2)
  im=cv2.resize(im,(352,240));cv2.putText(im,f"{r['frame_id']} +{r['timestamp']-start:.1f}s {r['decision']['reason'][:17]}",(5,230),0,.38,(0,0,255),1);tiles.append(im)
 while len(tiles)%4:tiles.append(np.zeros_like(tiles[0]))
 cv2.imwrite(str(out/(category+'.jpg')),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
print(json.dumps({k:v for k,v in result.items() if k!='shadow'},indent=2));print(json.dumps({k:v for k,v in result['shadow'].items() if k!='difference_spans'},indent=2))
