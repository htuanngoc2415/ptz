from pathlib import Path
from collections import Counter
import json,cv2,numpy as np
out=Path(__file__).resolve().parent;ab=out/'abcd/capture_1';read=lambda p:[json.loads(l) for l in p.read_text().splitlines()]
rs={k:read(ab/(k+'.jsonl')) for k in 'ABCD'};by={k:{r['frame_id']:r for r in v} for k,v in rs.items()}
cap=Path('logs/identity_capture/capture_18e5ba0fa26d492cbb7c19839cccb4b2');frames={r['frame_id']:r for r in read(cap/'frames.jsonl')}
lost=[r for r in rs['C'] if r['selected_candidate'] is None and by['B'][r['frame_id']]['selected_candidate'] is not None]
extra=[r for r in rs['D'] if r['selected_candidate'] is not None and by['C'][r['frame_id']]['selected_candidate'] is None]
quality=[]
for name,choices in [('c_lost_b',lost),('d_extra_c',extra)]:
 chosen=choices if len(choices)<=16 else [choices[i] for i in np.linspace(0,len(choices)-1,16,dtype=int)];tiles=[]
 for r in chosen:
  fid=r['frame_id'];f=frames[fid];im=cv2.imread(str(cap/f['image']));sel=(by['B'][fid] if name=='c_lost_b' else by['D'][fid])['selected_candidate']
  for p in f['candidates']:
   x1,y1,x2,y2=map(int,p['bbox']);cv2.rectangle(im,(x1,y1),(x2,y2),(0,255,0) if p['candidate_id']==sel else (0,220,255),2)
  im=cv2.resize(im,(352,240));cv2.putText(im,str(fid)+' '+r['reason'][:25],(3,230),0,.36,(0,0,255),1);tiles.append(im)
  quality.append(dict(group=name,frame_id=fid,selected=sel,reason=r['reason']))
 if tiles:
  while len(tiles)%4:tiles.append(np.zeros_like(tiles[0]))
  cv2.imwrite(str(out/(name+'.jpg')),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
summary=dict(c_lost_b_count=len(lost),c_lost_b_reasons=dict(Counter(r['reason'] for r in lost)),d_extra_c=extra,reviewed_selection=quality,ground_truth=False)
(out/'failure_review.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({k:v for k,v in summary.items() if k not in ('d_extra_c','reviewed_selection')},indent=2));print('D extra',[(r['frame_id'],r['selected_candidate']) for r in extra])
