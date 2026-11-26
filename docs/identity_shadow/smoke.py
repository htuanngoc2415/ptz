"""Offline smoke with real OSNet and recorded PNGs; NOT a live latency test."""
import json,time
from pathlib import Path
from types import SimpleNamespace
import cv2,torch
import config
from tools.identity_replay import offline_guard
from tools.identity_dataset import load_capture,verified_image
from runtime.identity_shadow import IdentityShadow
from person_search.reid import OSNetReID
from ai.reid.gallery import TargetEmbeddingGallery

out=Path('docs/identity_shadow')
with offline_guard():
 cv2.setNumThreads(1);torch.set_num_threads(1)
 cap=Path('logs/identity_capture/capture_706c13123cb84c2294eecf5fa4c3850f')
 meta,_,rows=load_capture(cap)
 cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
 cfg.IDENTITY_SHADOW_DIRECTORY=str(out/'offline_smoke');cfg.IDENTITY_SHADOW_INTERVAL=0
 model=OSNetReID(cfg.REID_MODEL_PATH,device='cuda:0',batch_size=cfg.REID_BATCH_SIZE)
 refs=torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device='cuda:0')
 selector=SimpleNamespace(target_uid='offline_smoke_target',embedding=refs[0],reid=model,gallery=TargetEmbeddingGallery(refs),stage='OFFLINE_SMOKE')
 shadow=IdentityShadow(cfg);shadow.start()
 try:
  for row in rows[30:42]:
   frame=cv2.imread(str(verified_image(cap,row)));persons=[dict(p) for p in row['candidates']]
   selected=next((p for p in persons if p['candidate_id']==row['selected_candidate']),None)
   shadow.submit(frame,persons,selector,selected,row['frame_id'],time.monotonic())
   deadline=time.monotonic()+10
   while shadow.busy and time.monotonic()<deadline:time.sleep(.01)
   assert not shadow.busy
 finally:shadow.close()
 assert shadow.error is None,shadow.error
 assert shadow.saved>0
 result=dict(source='offline PNG smoke; synthetic receive times',saved=shadow.saved,stale=shadow.stale,errors=shadow.error,physical_commands=0,path=str(shadow.path))
 (out/'smoke_result.json').write_text(json.dumps(result,indent=2));print(result)
