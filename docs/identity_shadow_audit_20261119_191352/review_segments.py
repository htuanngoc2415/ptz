from pathlib import Path
from collections import Counter
import json,cv2,numpy as np
out=Path(__file__).resolve().parent
cap=Path('logs/identity_capture/capture_18e5ba0fa26d492cbb7c19839cccb4b2')
frames=[json.loads(l) for l in (cap/'frames.jsonl').read_text().splitlines()];by={r['frame_id']:r for r in frames}
rs=[json.loads(l) for l in Path('logs/identity_shadow/shadow_36b5020b7c7843828b19de10f27d711e.jsonl').read_text().splitlines()];rs=[r for r in rs if r['event']=='SHADOW_COMPARISON'];sr={r['frame_id']:r for r in rs}
segments=[]
for name,lo,hi in [('hold1',2765,3381),('hold2',3488,4040),('hold3',1420,1962),('hold4',4589,5068),('hold5',2089,2522),('hold6',4118,4520),('all_selected',0,9999)]:
 choices=[r for r in frames if lo<=r['frame_id']<=hi]
 if name=='all_selected':choices=[by[r['frame_id']] for r in rs if r['decision']['selected_candidate'] is not None]
 selected=choices if name=='all_selected' else [choices[i] for i in np.linspace(0,len(choices)-1,12,dtype=int)]
 tiles=[]
 for r in selected:
  im=cv2.imread(str(cap/r['image']));sid=sr.get(r['frame_id'],{}).get('decision',{}).get('selected_candidate')
  for p in r['candidates']:
   x1,y1,x2,y2=map(int,p['bbox']);color=(0,255,0) if p['candidate_id']==sid else (0,220,255);cv2.rectangle(im,(x1,y1),(x2,y2),color,2)
  im=cv2.resize(im,(352,240));cv2.putText(im,str(r['frame_id']),(5,230),0,.5,(0,0,255),1);tiles.append(im)
 while len(tiles)%4:tiles.append(np.zeros_like(tiles[0]))
 cv2.imwrite(str(out/(name+'.jpg')),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
 segments.append(dict(name=name,first=choices[0]['frame_id'],last=choices[-1]['frame_id'],span_s=choices[-1]['timestamp']-choices[0]['timestamp'],reviewed_frames=[r['frame_id'] for r in selected],reasons=dict(Counter(r['decision']['reason'] for r in rs if lo<=r['frame_id']<=hi))))
(out/'review_segments.json').write_text(json.dumps(segments,indent=2))
