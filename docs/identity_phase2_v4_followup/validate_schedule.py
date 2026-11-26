"""Real v4 OSNet verification of preselected offline schedule frames, no live timing claim."""
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
import json,cv2,torch
import config
from ai.target_identity import TargetIdentityManager,IdentityPolicy
from runtime.identity_shadow import ShadowScorer
from tools.benchmark_identity_phase2 import score,observe
from tools.identity_replay import offline_guard,digest
from person_search.reid import OSNetReID
from tools.identity_dataset import load_capture,verified_image
from tools.analyze_identity_live import stats
OUT=Path(__file__).resolve().parent
read=lambda p:[json.loads(l) for l in p.read_text().splitlines()]
with offline_guard():
 cv2.setNumThreads(1);torch.set_num_threads(1)
 cache=Path('docs/identity_shadow_audit_20261119_191352/capture_cache');meta=json.loads((cache/'manifest.json').read_text())
 assert digest(cache/'observations.jsonl')==meta['observations_sha256']
 for p,h in meta['model_sha256'].items():assert digest(p)==h
 rows={r['frame_id']:r for r in read(cache/'observations.jsonl')}
 cap=Path(meta['video']);_,_,frames=load_capture(cap);images={r['frame_id']:r for r in frames}
 cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config']);cfg.IDENTITY_SHADOW_BATCHED=True
 model=OSNetReID(cfg.REID_MODEL_PATH,device='cuda:0',batch_size=cfg.REID_BATCH_SIZE)
 refs=torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device=model.device)
 scorer=ShadowScorer(cfg,model,refs)
 log=read(Path('logs/identity_shadow/shadow_36b5020b7c7843828b19de10f27d711e.jsonl'))
 initial=next(r for r in log if r['event']=='SHADOW_SESSION');resets=[r for r in log if r['event']=='SHADOW_RESET'];policy=replace(IdentityPolicy(**next(r['policy'] for r in log if r['event']=='SHADOW_POLICY')),acquire=initial['initial_threshold'])
 plans={k:read(OUT/f'191352_{k}_0.jsonl') for k in ('fixed200','adaptive')}
 fresh={};costs=[];maxdiff=0
 for fid in sorted({r['frame_id'] for plan in plans.values() for r in plan}):
  row=rows[fid];im=cv2.imread(str(verified_image(cap,images[fid])))
  people=[{k:p[k] for k in ('candidate_id','track_id','bbox','confidence')} for p in row['candidates']]
  cost=score(scorer,im,people,float('inf'));costs.append(dict(frame_id=fid,**cost));fresh[fid]=people
  for a,b in zip(row['candidates'],people):maxdiff=max(maxdiff,abs(a['reid_score']-b['reid_score']))
 assert maxdiff<1e-5,maxdiff
 summaries={}
 for kind,plan in plans.items():
  m=TargetIdentityManager(meta['reference_embeddings'][0],policy,session_id='real_verify',reference_views=meta['reference_embeddings'][1:]);idx=0;mismatches=[];by={r['frame_id']:r for r in plan};decisions=[]
  for row in rows.values():
   now=row['timestamp']+row['observation_age_s']
   if row['timestamp']<initial['monotonic_ts']:continue
   while idx<len(resets) and resets[idx]['monotonic_ts']<=now:m.interrupt();m.policy=replace(m.policy,acquire=m.policy.reacquire);idx+=1
   if not row['candidates']:m.interrupt()
   fid=row['frame_id']
   if fid not in by:continue
   expected=by[fid];r=observe(m,row,fresh[fid],expected['age_s']);decisions.append(r)
   if any(r[k]!=expected[k] for k in ('selected_candidate','reason','state')):mismatches.append(fid)
  ids=set(by);own=[c for c in costs if c['frame_id'] in ids]
  summaries[kind]=dict(frames=len(plan),mismatches=mismatches,total_outer_hold_ms=sum(c['held_ms'] for c in own),wall_ms=stats([c['wall_ms'] for c in own]),passes=sum(c['passes'] for c in own))
  with (OUT/f'{kind}_real_decisions.jsonl').open('w') as f:
   for r in decisions:f.write(json.dumps(r)+'\n')
 (OUT/'real_schedule_validation.json').write_text(json.dumps(dict(schedules=summaries,unique_inference_frames=len(fresh),max_score_difference=maxdiff,costs=costs,ground_truth=False,physical_commands=0,scope='Real OSNet on preselected schedule union, shared measurements on common frames. Policy age fixed to cached scenario for parity, not actual live completion time.'),indent=2))
 print(json.dumps(summaries,indent=2))
