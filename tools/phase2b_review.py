"""Build a blind local review pack and validate human labels; no inferred GT."""
import argparse,hashlib,json,re,shutil
from pathlib import Path
import cv2,numpy as np
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import digest,offline_guard

SPLITS={'development':'191352','retrospective_holdout':'190016'}
VISIBILITY={'VISIBLE','PARTIAL_OCCLUSION','FULL_OCCLUSION','OUT_OF_FRAME','UNKNOWN'}
def validate(pack,annotations):
 if annotations.get('schema')!='phase2b_manual_v1' or not annotations.get('reviewer','').strip():raise ValueError('Human reviewer and manual schema required')
 if annotations.get('pack_sha256')!=pack['pack_sha256']:raise ValueError('Wrong review pack checksum')
 index={(s['split'],r['frame_id']):r for s in pack['sets'] for r in s['frames']};seen=set();known=0;unknown=0
 for a in annotations['frames']:
  key=(a['split'],a['frame_id'])
  if key in seen or key not in index:raise ValueError('Duplicate or foreign annotation frame')
  seen.add(key);r=index[key]
  if a.get('reviewed') is not True:raise ValueError('Unreviewed row cannot enter manual export')
  if a.get('visibility') not in VISIBILITY:raise ValueError('Unknown visibility value')
  ids=a.get('candidate_identities',{})
  if set(ids)!={c['candidate_id'] for c in r['candidates']}:raise ValueError('Missing/foreign candidate IDs')
  if any(not isinstance(v,str) or not re.fullmatch(r'target|other_[1-9][0-9]*|UNKNOWN',v) for v in ids.values()):raise ValueError('Invalid identity label')
  if list(ids.values()).count('target')>1:raise ValueError('Multiple target boxes require UNKNOWN/review')
  if a['visibility'] in ('OUT_OF_FRAME','FULL_OCCLUSION') and 'target' in ids.values():raise ValueError('Invisible target cannot have observed target bbox')
  if a['visibility']=='UNKNOWN' or 'UNKNOWN' in ids.values():unknown+=1
  else:known+=1
 return dict(reviewed=len(seen),known=known,unknown=unknown,unreviewed=len(index)-len(seen),identity_accuracy_eligible=known>0)

def build(out):
 out.mkdir(parents=True,exist_ok=False);sets=[]
 failures={json.loads(l)['frame_id'] for l in Path('docs/identity_phase2b/failures.jsonl').read_text().splitlines()}
 existing=json.loads(Path('docs/identity_phase2_v4_followup/abcd/capture_1/review/annotations_template.json').read_text())
 for split,tag in SPLITS.items():
  cache=Path(f'docs/identity_shadow_audit_20261119_{tag}/capture_cache');meta=json.loads((cache/'manifest.json').read_text());cap=Path(meta['video']);cm,summary,rows=load_capture(cap)
  folder=out/split;folder.mkdir();shutil.copy2(cap/'target_reference.png',folder/'reference.png')
  priority={r['frame_id'] for r in rows[::max(1,len(rows)//40)]}
  if split=='development':priority|=failures|{r['frame_id'] for r in existing['frames']}
  lastids=None
  for i,r in enumerate(rows):
   ids={c['track_id'] for c in r['candidates']}
   if ids!=lastids:
    priority.update(x['frame_id'] for x in rows[max(0,i-2):i+3])
   lastids=ids
  exported=[]
  for r in rows:
   im=cv2.imread(str(verified_image(cap,r)));assert list(im.shape)==r['frame_shape'];dest=folder/(str(r['frame_id'])+'.jpg');assert cv2.imwrite(str(dest),im,[cv2.IMWRITE_JPEG_QUALITY,88])
   exported.append(dict(frame_id=r['frame_id'],timestamp=r['timestamp'],image=f'{split}/{dest.name}',image_sha256=digest(dest),source_image_sha256=r['image_sha256'],priority=r['frame_id'] in priority,candidates=[dict(candidate_id=c['candidate_id'],bbox=c['bbox']) for c in r['candidates']]))
  sets.append(dict(split=split,cache=str(cache),capture_sha256=summary['frames_sha256'],observations_sha256=meta['observations_sha256'],reference=f'{split}/reference.png',reference_sha256=cm['reference_sha256'],frames=exported,priority_frames=len(priority)))
 pack=dict(schema='phase2b_review_v1',sets=sets,annotation_status='AWAITING_INDEPENDENT_REVIEW',split_limitation='Separate captures, but both previously inspected in research; retrospective holdout is NOT unseen confirmatory evidence. No annotation-derived tuning.',coverage_limitation='Pose/crossing/occlusion tags require reviewer confirmation; events inferred from geometry are not ground truth.')
 pack['pack_sha256']=hashlib.sha256(json.dumps(pack,sort_keys=True).encode()).hexdigest()
 (out/'pack.json').write_text(json.dumps(pack,indent=2));template=Path('tools/phase2b_review_template.html').read_text();(out/'index.html').write_text(template.replace('__PACK_DATA__',json.dumps(pack).replace('</','<\\/')))
 print(json.dumps({s['split']:dict(frames=len(s['frames']),priority=s['priority_frames']) for s in sets}))
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--build',type=Path);p.add_argument('--pack',type=Path);p.add_argument('--annotations',type=Path);a=p.parse_args()
 with offline_guard():
  if a.build:build(a.build)
  elif a.pack and a.annotations:print(json.dumps(validate(json.loads(a.pack.read_text()),json.loads(a.annotations.read_text())),indent=2))
  else:p.error('Use --build DIR or --pack FILE --annotations FILE')
