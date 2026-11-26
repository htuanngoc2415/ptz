"""Real OSNet parity and paired resource benchmark; offline only."""
import json,time,threading
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch
import cv2,numpy as np,torch
import config
from runtime.identity_shadow import ShadowScorer
from runtime.scheduling import scheduler
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import offline_guard,digest
from person_search.reid import OSNetReID
from ai.target_identity import TargetIdentityManager,IdentityPolicy,Candidate
out=Path(__file__).resolve().parent
caches=[Path(p) for p in (
 'docs/identity_shadow_audit_20261119_173204/capture_cache',
 'docs/identity_shadow_v3/capture_cache',
 'docs/identity_capture_audit_20261119_083019/capture_706c13123cb84c2294eecf5fa4c3850f/cache',
 'docs/identity_capture_audit_20261119/capture_eb68f45a3a914ba897f811ba3c0129b5/cache',
 'docs/identity_capture_audit_20261119/capture_9b6f72b28e284599a83a2ca918947993/cache')]
def stats(a):return dict(n=len(a),median=float(np.median(a)),p95=float(np.percentile(a,95)),mean=float(np.mean(a)),max=float(max(a)))
with offline_guard():
 cv2.setNumThreads(1);torch.set_num_threads(1)
 model=OSNetReID(config.REID_MODEL_PATH,device='cuda:0',batch_size=config.REID_BATCH_SIZE)
 totals=[];bench={'old':[],'batched':[]};examples=[]
 for ci,cache in enumerate(caches):
  meta=json.loads((cache/'manifest.json').read_text());assert digest(cache/'observations.jsonl')==meta['observations_sha256']
  for path,h in meta['baseline_source_sha256'].items():assert digest(path)==h,path
  for path,h in meta['model_sha256'].items():assert digest(path)==h,path
  cap=Path(meta['video']);_,_,frames=load_capture(cap);byid={r['frame_id']:r for r in frames};rows=[json.loads(l) for l in (cache/'observations.jsonl').read_text().splitlines()]
  cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config']);cfg.IDENTITY_SHADOW_BATCHED=True
  refs=torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device=model.device);scorer=ShadowScorer(cfg,model,refs)
  policy=IdentityPolicy.from_config(cfg);managers=[TargetIdentityManager(meta['reference_embeddings'][0],policy,session_id='offline',reference_views=meta['reference_embeddings'][1:]) for _ in range(2)]
  max_vector=max_score=0.;decision_mismatches=[];candidates_count=0
  selected_bench=set(np.linspace(0,len(rows)-1,30,dtype=int)) if ci<2 else set()
  for index,row in enumerate(rows):
   frame=cv2.imread(str(verified_image(cap,byid[row['frame_id']])));new=[dict(p) for p in row['candidates']]
   scorer.score(frame,new,lambda:True)
   for a,b in zip(row['candidates'],new):
    candidates_count+=1
    max_vector=max(max_vector,float(np.max(np.abs(np.array(a['embedding'])-b['embedding']))))
    for key in ('raw_similarity','normalized_similarity','reid_score'):
     if a[key] is None:assert b[key] is None
     else:max_score=max(max_score,abs(a[key]-b[key]))
   decisions=[]
   for m,people in zip(managers,(row['candidates'],new)):
    cs=[Candidate(p['candidate_id'],p['track_id'],tuple(p['bbox']),p['confidence'],tuple(p['embedding']),row['frame_id'],row['timestamp'],m.session_id) for p in people]
    decisions.append(m.observe(session_id=m.session_id,frame_id=row['frame_id'],timestamp=row['timestamp'],now=row['timestamp']+row['observation_age_s'],candidates=cs))
   keys=('selected_candidate','reason','state','action')
   if any(decisions[0][k]!=decisions[1][k] for k in keys):decision_mismatches.append(row['frame_id'])
   scorer.discard()
   if index in selected_bench:
    # Interleave order across images; record outer resource holds only, no double counting.
    for mode in (('old','batched') if index%2 else ('batched','old')):
     cfg.IDENTITY_SHADOW_BATCHED=mode=='batched';runner=ShadowScorer(cfg,model,refs);holds=[];original=scheduler.use
     @contextmanager
     def timed(*args,**kw):
      nested=scheduler.owner==threading.get_ident()
      with original(*args,**kw) as granted:
       start=time.perf_counter()
       try:yield granted
       finally:
        if granted and not nested:holds.append((time.perf_counter()-start)*1000)
     with patch.object(scheduler,'use',timed):
      start=time.perf_counter();runner.score(frame,[dict(p) for p in row['candidates']],lambda:True);elapsed=(time.perf_counter()-start)*1000
     bench[mode].append(dict(frame_id=row['frame_id'],capture=str(cap),wall_ms=elapsed,held_ms=sum(holds),longest_hold_ms=max(holds,default=0),passes=runner.completed_passes))
    cfg.IDENTITY_SHADOW_BATCHED=True
  result=dict(capture=str(cap),frames=len(rows),candidates=candidates_count,max_vector_abs_diff=max_vector,max_score_abs_diff=max_score,decision_mismatches=decision_mismatches)
  totals.append(result);print(json.dumps(result),flush=True)
  assert max_vector<1e-5 and max_score<1e-5 and not decision_mismatches,result
 result=dict(scope='real CUDA OSNet, cached evidence from five captures; paired sequential benchmark, not live FPS',parity=totals,benchmark={mode:{k:stats([r[k] for r in values]) for k in ('wall_ms','held_ms','longest_hold_ms','passes')} for mode,values in bench.items()},physical_commands=0)
 (out/'validation_result.json').write_text(json.dumps(result,indent=2));(out/'benchmark_rows.json').write_text(json.dumps(bench,indent=2));print(json.dumps(result,indent=2),flush=True)
