"""OSNet parity and forced between-pass contention; offline, no camera/motor."""
import json, time, threading
from pathlib import Path
from types import SimpleNamespace
import cv2, torch, numpy as np
import config
from tools.identity_replay import offline_guard, digest
from tools.identity_dataset import load_capture, verified_image
from runtime.identity_shadow import IdentityShadow, ShadowScorer
from runtime.scheduling import scheduler
from person_search.reid import OSNetReID
from ai.reid_target import ReIDTargetSelector
from ai.reid.gallery import TargetEmbeddingGallery
out=Path('docs/identity_shadow_v3')
cap=Path('logs/identity_capture/capture_91efcb317617412bb9d9a5a02faede59')
with offline_guard():
 cv2.setNumThreads(1);torch.set_num_threads(1)
 meta,summary,rows=load_capture(cap)
 for p,h in meta['baseline_source_sha256'].items():assert digest(p)==h,p
 for p,h in meta['model_sha256'].items():assert digest(p)==h,p
 cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
 for k,v in meta['config'].items():setattr(cfg,k,v)
 cfg.IDENTITY_SHADOW_DIRECTORY=str(out/'offline_smoke');cfg.IDENTITY_SHADOW_INTERVAL=0
 model=OSNetReID(cfg.REID_MODEL_PATH,device='cuda:0',batch_size=cfg.REID_BATCH_SIZE)
 refs=torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device='cuda:0')
 class RecordingModel:
  def __init__(self):self.calls=[]
  def extract_embeddings(self,crops):
   result=model.extract_embeddings(crops);self.calls.append(result.detach().cpu().tolist());return result
 recorded=RecordingModel();baseline=ReIDTargetSelector(cfg,recorded);baseline.embedding=refs[0];baseline.gallery=TargetEmbeddingGallery(refs)
 scorer=ShadowScorer(cfg,model,refs);checks=[]
 choices=[rows[i] for i in np.linspace(0,len(rows)-1,12,dtype=int)]
 for row in choices:
  frame=cv2.imread(str(verified_image(cap,row)));a=[dict(p) for p in row['candidates']];b=[dict(p) for p in a]
  recorded.calls.clear()
  if a:baseline._scores(frame,a)
  scorer.score(frame,b,lambda:True);ni=0
  for i,(p,q) in enumerate(zip(a,b)):
   v=recorded.calls[0][i]
   if p['normalized_similarity'] is not None:
    if p['normalized_similarity']>p['raw_similarity']:v=recorded.calls[1][ni]
    ni+=1
   for k in ('raw_similarity','normalized_similarity','reid_score','quality'):assert p[k]==q[k],(row['frame_id'],k)
   np.testing.assert_array_equal(v,q['embedding'])
  checks.append(dict(frame_id=row['frame_id'],candidates=len(a),passes=scorer.completed_passes))
  scorer.discard()
 # Force foreground ownership after raw completes, before normalized admission.
 row=next(r for r in rows if r['candidates']);frame=cv2.imread(str(verified_image(cap,row)))
 entered=threading.Event();release=threading.Event();threads=[]
 def foreground():
  with scheduler.use('detection'):
   entered.set();release.wait(3)
 class ContendedScorer(ShadowScorer):
  def _extract(self,crops,valid):
   result=super()._extract(crops,valid)
   if self.completed_passes==1:
    thread=threading.Thread(target=foreground);threads.append(thread);thread.start()
    assert entered.wait(1)
   return result
 # Ensure this case requests a normalized pass; parity above used capture config.
 cfg.REID_NORMALIZATION_ENABLED=True;cfg.REID_NORMALIZE_LUMA=256
 selector=SimpleNamespace(target_uid='offline_target',embedding=refs[0],reid=model,gallery=TargetEmbeddingGallery(refs),stage='OFFLINE')
 shadow=IdentityShadow(cfg,ContendedScorer);shadow.start()
 try:
  people=[dict(p) for p in row['candidates']]
  assert shadow.submit(frame,people,selector,None,row['frame_id'],time.monotonic())
  assert entered.wait(2)
  deadline=time.monotonic()+1
  while not shadow.admission_retries and time.monotonic()<deadline:time.sleep(.001)
  assert shadow.admission_retries>0
  release.set()
  deadline=time.monotonic()+2
  while shadow.busy and time.monotonic()<deadline:time.sleep(.005)
 finally:
  release.set();shadow.close()
  for thread in threads:thread.join(2)
 assert shadow.error is None,shadow.error
 assert (shadow.saved,shadow.stale,shadow.completed_passes,shadow.discarded_passes)==(1,0,2,0),shadow.status()
 assert scheduler.owner is None
 result=dict(scope='offline real CUDA OSNet; synthetic receive timestamp; controlled foreground contention, not live FPS',source_capture_sha256=summary['frames_sha256'],exact_parity=checks,contention=dict(saved=shadow.saved,stale=shadow.stale,retries=shadow.admission_retries,completed_passes=shadow.completed_passes,discarded_passes=shadow.discarded_passes,path=str(shadow.path)),physical_commands=0)
 (out/'validation_result.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
