from pathlib import Path
from collections import Counter
import json
import numpy as np,cv2
out=Path(__file__).resolve().parent
cap=Path('logs/identity_capture/capture_91efcb317617412bb9d9a5a02faede59')
read=lambda p:[json.loads(l) for l in p.read_text().splitlines()]
baseline=read(out/'full_replay/baseline.jsonl');identity=read(out/'full_replay/identity.jsonl');frames=read(cap/'frames.jsonl');byid={r['frame_id']:r for r in frames}
categories=Counter();reasons=Counter();groups={};spans=[]
for b,i in zip(baseline,identity):
 assert b['frame_id']==i['frame_id']
 a,c=b['selected_candidate'],i['selected_candidate']
 cat='both_hold' if a is None and c is None else 'identity_only' if a is None else 'baseline_only' if c is None else 'same_candidate' if a==c else 'different_candidate'
 categories[cat]+=1;groups.setdefault(cat,[]).append(i)
 if cat=='baseline_only':reasons[i['reason']]+=1
 if c is None:
  if not spans or spans[-1]['closed']:spans.append(dict(start=i['timestamp'],first_frame=i['frame_id'],samples=0,reasons=Counter(),closed=False))
  spans[-1].update(end=i['timestamp'],last_frame=i['frame_id'],samples=spans[-1]['samples']+1)
  spans[-1]['reasons'][i['reason']]+=1
 elif spans:spans[-1]['closed']=True
for s in spans:s['duration_s']=s['end']-s['start']
result=dict(scope='all-frame offline policy replay; no live scheduling or resets reproduced',categories=categories,baseline_only_reasons=reasons,longest_identity_holds=sorted(spans,key=lambda s:s['duration_s'],reverse=True)[:5],ground_truth=False)
(out/'replay_analysis.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
for cat in ('baseline_only','identity_only','different_candidate'):
 rows=groups.get(cat,[])
 if not rows:continue
 tiles=[]
 for index in np.linspace(0,len(rows)-1,min(12,len(rows)),dtype=int):
  r=rows[index];f=byid[r['frame_id']];im=cv2.imread(str(cap/f['image']))
  for p in f['candidates']:
   color=(0,255,0) if p['candidate_id']==r['selected_candidate'] else (180,180,180)
   x,y,u,v=map(int,p['bbox']);cv2.rectangle(im,(x,y),(u,v),color,2)
  im=cv2.resize(im,(352,240));cv2.putText(im,f"{f['frame_id']} {r['reason'][:22]}",(5,230),0,.36,(0,0,255),1);tiles.append(im)
 while len(tiles)%4:tiles.append(np.zeros_like(tiles[0]))
 cv2.imwrite(str(out/(cat+'.jpg')),np.vstack([np.hstack(tiles[k:k+4]) for k in range(0,len(tiles),4)]))
