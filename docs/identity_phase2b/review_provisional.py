from pathlib import Path
import json,cv2,numpy as np
out=Path(__file__).resolve().parent
pack=json.loads((out/'review/pack.json').read_text());review=[]
for s in pack['sets']:
 split=s['split'];by={r['frame_id']:r for r in s['frames']}
 union={}
 for cadence in ('all','fixed200'):
  for l in (out/'benchmark_final'/split/cadence/'E3.jsonl').read_text().splitlines():
   r=json.loads(l)
   if r.get('provisional'):union[r['frame_id']]=r
 rows=sorted(union.values(),key=lambda r:r['frame_id'])
 for start in range(0,len(rows),16):
  tiles=[]
  for r in rows[start:start+16]:
   f=by[r['frame_id']];im=cv2.imread(str(out/'review'/f['image']))
   for c in f['candidates']:
    x,y,x2,y2=map(int,c['bbox']);cv2.rectangle(im,(x,y),(x2,y2),(0,255,0) if c['candidate_id']==r['localization_candidate'] else (0,220,255),2)
   im=cv2.resize(im,(352,240));cv2.putText(im,str(r['frame_id'])+' '+r['continuation_reason'].replace('BOUNDED_','')[:20],(4,230),0,.36,(0,0,255),1);tiles.append(im)
   review.append(dict(split=split,frame_id=r['frame_id'],localization_candidate=r['localization_candidate'],reason=r['continuation_reason'],anchor_age_s=r['continuation_evidence']['anchor_age_s'],human_gt=None))
  while len(tiles)%4:tiles.append(np.zeros_like(tiles[0]))
  cv2.imwrite(str(out/f'{split}_provisional_{start//16}.jpg'),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
(out/'provisional_review.json').write_text(json.dumps(review,indent=2));print(len(review))
