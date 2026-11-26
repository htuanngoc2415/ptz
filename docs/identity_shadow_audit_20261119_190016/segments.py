import json,cv2,numpy as np
from pathlib import Path
from collections import Counter
out=Path(__file__).resolve().parent
cap=Path('logs/identity_capture/capture_b4602881389f406b8db527c0bc96750d')
frames=[json.loads(l) for l in (cap/'frames.jsonl').read_text().splitlines()]
rs={n:[json.loads(l) for l in (out/'full_replay'/f'{n}.jsonl').read_text().splitlines()] for n in ('baseline','identity')}
segments={}
for label,lo,hi in [('crossing',1206,1515),('overlap',2043,2294),('long_hold',3941,4633)]:
 rows=[r for r in frames if lo<=r['frame_id']<=hi];tiles=[];ids=[]
 for index in np.linspace(0,len(rows)-1,12,dtype=int):
  r=rows[index];ids.append(r['frame_id']);im=cv2.imread(str(cap/r['image']))
  for p in r['candidates']:
   x1,y1,x2,y2=map(int,p['bbox']);cv2.rectangle(im,(x1,y1),(x2,y2),(0,255,255),2)
  im=cv2.resize(im,(352,240));cv2.putText(im,str(r['frame_id']),(5,230),0,.5,(0,0,255),1);tiles.append(im)
 cv2.imwrite(str(out/(label+'.jpg')),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
 segments[label]=dict(first=lo,last=hi,duration_s=rows[-1]['timestamp']-rows[0]['timestamp'],reviewed_frames=ids,reasons=dict(Counter(r['reason'] for r in rs['identity'] if lo<=r['frame_id']<=hi)))
comparison=Counter()
for a,b in zip(rs['baseline'],rs['identity']):
 x,y=a['selected_candidate'],b['selected_candidate'];comparison['both_hold' if x is None and y is None else 'identity_only' if x is None else 'baseline_only' if y is None else 'same' if x==y else 'different']+=1
(out/'replay_analysis.json').write_text(json.dumps(dict(comparison=dict(comparison),segments=segments),indent=2))
print(json.dumps(dict(comparison=dict(comparison),segments=segments),indent=2))
