"""Controlled admission-load replay; real OSNet, recorded foreground hold windows.
Foreground work is simulated by holding the scheduler, NOT rerunning YOLO/face.
This measures a reproducible contention fixture, not camera performance.
"""
import json,time,threading
from pathlib import Path
from types import SimpleNamespace
import cv2,numpy as np,torch
import config
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import offline_guard
from runtime.scheduling import scheduler
from runtime.identity_shadow import IdentityShadow,ShadowScorer
from person_search.reid import OSNetReID
from ai.reid.gallery import TargetEmbeddingGallery
out=Path(__file__).resolve().parent
cap=Path('logs/identity_capture/capture_ca0c81d0b16441a8b0c7b48b7b0a698f')
def stats(values):return dict(n=len(values),median=float(np.median(values)),p95=float(np.percentile(values,95)),max=float(max(values))) if values else None
with offline_guard():
 cv2.setNumThreads(1);torch.set_num_threads(1)
 meta,_,frames=load_capture(cap);cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config'])
 model=OSNetReID(cfg.REID_MODEL_PATH,device='cuda:0',batch_size=cfg.REID_BATCH_SIZE)
 refs=torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device=model.device)
 traces=[]
 for p in Path('logs/latency').glob('latency_trace_20261119_173207_536257.jsonl*'):traces.extend(json.loads(l) for l in p.read_text().splitlines())
 start=24968.5;duration=12.;end=start+duration
 holds=[]
 for r in traces:
  if r['event']!='RESOURCE_RELEASED' or r.get('nested') is not False or r.get('resource')!='accelerator' or r['branch']=='identity_shadow':continue
  a=r['start_ns']/1e9;b=r['monotonic_ns']/1e9
  if b>start and a<end:holds.append((max(start,a)-start,min(end,b)-max(start,a)))
 holds.sort();assert holds and sum(d for _,d in holds)<duration
 inputs=[(r,cv2.imread(str(verified_image(cap,r)))) for r in frames if start<=r['timestamp']<end]
 # Warm both paths before the timed fixture.
 for mode in (False,True):
  cfg.IDENTITY_SHADOW_BATCHED=mode;ShadowScorer(cfg,model,refs).score(inputs[0][1],[dict(p) for p in inputs[0][0]['candidates']],lambda:True)
 results=[]
 for repeat in range(3):
  for mode in ((False,True) if repeat%2==0 else (True,False)):
   cfg.IDENTITY_SHADOW_BATCHED=mode;cfg.IDENTITY_SHADOW_DIRECTORY=str(out/'contention_logs');cfg.IDENTITY_SHADOW_INTERVAL=.2
   selector=SimpleNamespace(target_uid='offline_contention',embedding=refs[0],gallery=TargetEmbeddingGallery(refs),reid=model,stage='OFFLINE',reacquiring=False)
   shadow=IdentityShadow(cfg);shadow.start();origin=time.monotonic()+.1;waits=[];cancel=threading.Event()
   def foreground():
    for offset,length in holds:
     if cancel.wait(max(0,origin+offset-time.monotonic())):return
     begin=time.monotonic()
     with scheduler.use('detection'):
      waits.append((time.monotonic()-begin)*1000);cancel.wait(length)
   thread=threading.Thread(target=foreground);thread.start()
   try:
    for row,frame in inputs:
     due=origin+row['timestamp']-start+row['observation_age_s']
     cancel.wait(max(0,due-time.monotonic()))
     people=[dict(p) for p in row['candidates']];selected=next((p for p in people if p['candidate_id']==row['selected_candidate']),None)
     shadow.submit(frame,people,selector,selected,row['frame_id'],origin+row['timestamp']-start)
    cancel.wait(max(0,origin+duration-time.monotonic()))
   finally:
    shadow.close();cancel.set();thread.join(3)
   assert shadow.error is None,shadow.error
   assert not thread.is_alive() and scheduler.owner is None
   rows=[json.loads(l) for l in shadow.path.read_text().splitlines()];samples=[r for r in rows if r['event']=='SHADOW_COMPARISON']
   gaps=[b['timestamp']-a['timestamp'] for a,b in zip(samples,samples[1:])]
   result=dict(repeat=repeat,batched=mode,saved=shadow.saved,stale=shadow.stale,passes=shadow.completed_passes,discarded_passes=shadow.discarded_passes,retries=shadow.admission_retries,sample_gaps=stats(gaps),gaps_over_1s=sum(g>1 for g in gaps),age_ms=stats([r['age_ms'] for r in samples]),foreground_wait_ms=stats(waits),log=str(shadow.path))
   results.append(result);print(json.dumps(result),flush=True)
 result=dict(scope='controlled scheduler contention, real CUDA OSNet, simulated foreground hold times; NOT live FPS or accuracy',recorded_window=[start,end],duration_s=duration,input_frames=len(inputs),foreground_intervals=len(holds),foreground_hold_s=sum(d for _,d in holds),runs=results,physical_commands=0)
 (out/'contention_result.json').write_text(json.dumps(result,indent=2))
