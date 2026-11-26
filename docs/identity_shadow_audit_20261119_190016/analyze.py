from pathlib import Path
from collections import Counter
import json,numpy as np,cv2
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import digest
out=Path(__file__).resolve().parent
cap=Path('logs/identity_capture/capture_b4602881389f406b8db527c0bc96750d')
meta,summary,frames=load_capture(cap);byid={r['frame_id']:r for r in frames}
shadow=[json.loads(l) for l in Path('logs/identity_shadow/shadow_21a42713603243019cfefd8e47793591.jsonl').read_text().splitlines()]
rows=[r for r in shadow if r['event']=='SHADOW_COMPARISON']
policy=next(r for r in shadow if r['event']=='SHADOW_POLICY')
source={p:digest(p)==h for p,h in {**policy['source_sha256'],**meta['baseline_source_sha256']}.items()};assert all(source.values())
weights={p:digest(p)==h for p,h in meta['model_sha256'].items()};assert all(weights.values())
for r in frames:
 im=cv2.imread(str(verified_image(cap,r)));assert list(im.shape)==r['frame_shape']
categories=Counter();mismatch=[];missing=[]
for r in rows:
 b,s=r['baseline_selected'],r['decision']['selected_candidate']
 cat='both_hold' if b is None and s is None else 'shadow_only' if b is None else 'baseline_only' if s is None else 'same' if b==s else 'different_candidate'
 r['category']=cat;categories[cat]+=1
 if r['frame_id'] not in byid:missing.append(r['frame_id'])
 else:
  f=byid[r['frame_id']]
  if f['timestamp']!=r['timestamp'] or f['selected_candidate']!=b:mismatch.append(r['frame_id'])
assert not mismatch
for name,choices in [('overview',frames),('baseline_only',[r for r in rows if r['category']=='baseline_only' and r['frame_id'] in byid]),('selected',[r for r in rows if r['decision']['selected_candidate'] and r['frame_id'] in byid])]:
 if not choices:continue
 chosen=[choices[i] for i in np.linspace(0,len(choices)-1,min(16,len(choices)),dtype=int)];tiles=[]
 for r in chosen:
  f=byid[r['frame_id']];im=cv2.imread(str(cap/f['image']))
  for c in f['candidates']:
   x1,y1,x2,y2=map(int,c['bbox']);color=(0,255,0) if c['candidate_id']==r.get('decision',{}).get('selected_candidate') else (0,220,255)
   cv2.rectangle(im,(x1,y1),(x2,y2),color,2);cv2.putText(im,c['candidate_id'],(x1,max(18,y1)),0,.5,color,1)
  im=cv2.resize(im,(352,240));cv2.putText(im,str(f['frame_id'])+' '+r.get('decision',{}).get('reason','')[:24],(4,230),0,.36,(0,0,255),1);tiles.append(im)
 while len(tiles)%4:tiles.append(np.zeros_like(tiles[0]))
 cv2.imwrite(str(out/(name+'.jpg')),np.vstack([np.hstack(tiles[i:i+4]) for i in range(0,len(tiles),4)]))
result=dict(capture=str(cap),summary=summary,duration_s=frames[-1]['timestamp']-frames[0]['timestamp'],frame_bounds=[frames[0]['frame_id'],frames[-1]['frame_id']],source_matches=source,model_matches=weights,categories=dict(categories),reasons=dict(Counter(r['decision']['reason'] for r in rows)),baseline_only_reasons=dict(Counter(r['decision']['reason'] for r in rows if r['category']=='baseline_only')),matched=len(rows)-len(missing),missing_frames=missing,metadata_mismatches=mismatch,sessions=[r for r in shadow if r['event']=='SHADOW_SESSION'],resets=[r for r in shadow if r['event']=='SHADOW_RESET'],record_flags={k:v for k,v in meta['config'].items() if k.startswith('IDENTITY_')},ground_truth=False)
(out/'capture_audit.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
