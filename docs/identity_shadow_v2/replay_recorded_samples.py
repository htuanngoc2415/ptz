"""Controlled counterfactual on recorded shadow sample times, not a live FPS test."""
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from collections import Counter
import json,time
import cv2,torch
import config
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import offline_guard
from runtime.identity_shadow import ShadowScorer
from ai.target_identity import TargetIdentityManager,IdentityPolicy,Candidate
from person_search.reid import OSNetReID

out=Path('docs/identity_shadow_v2');cap=Path('logs/identity_capture/capture_1b1ecfbd76204aefa35e048b8e6ed40a')
with offline_guard():
 cv2.setNumThreads(1);torch.set_num_threads(1)
 meta,summary,frames=load_capture(cap);byid={r['frame_id']:r for r in frames}
 original=[json.loads(l) for l in Path('logs/identity_shadow/shadow_5660ef2737454217807c15be0d38a41b.jsonl').read_text().splitlines()]
 samples=[r for r in original if r['event']=='SHADOW_COMPARISON']
 cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
 for k,v in meta['config'].items():setattr(cfg,k,v)
 model=OSNetReID(cfg.REID_MODEL_PATH,device='cuda:0',batch_size=cfg.REID_BATCH_SIZE)
 refs=torch.tensor(meta['reference_embeddings'],dtype=torch.float32,device='cuda:0')
 scorer=ShadowScorer(cfg,model,refs);policy=replace(IdentityPolicy.from_config(cfg),gallery_enabled=False)
 new=TargetIdentityManager(scorer.refs[0],policy,session_id='counterfactual',reference_views=scorer.refs[1:])
 previous=None;counts=Counter();reasons=Counter();resets=0
 with (out/'counterfactual.jsonl').open('x') as f:
  for s in samples:
   row=byid[s['frame_id']];sid=s['decision']['session_id']
   if sid!=previous:
    old=TargetIdentityManager(scorer.refs[0],policy,session_id='old',reference_views=scorer.refs[1:])
    if previous is not None:
     new.interrupt();new.policy=replace(new.policy,acquire=new.policy.reacquire);resets+=1
    previous=sid
   persons=[dict(candidate_id=p['candidate_id'],track_id=p['track_id'],bbox=tuple(p['bbox']),confidence=p['confidence']) for p in row['candidates']]
   scorer.score(cv2.imread(str(verified_image(cap,row))),persons,lambda:True)
   result={}
   for name,manager in [('old_recomputed',old),('new',new)]:
    cs=[Candidate(p['candidate_id'],p['track_id'],p['bbox'],p['confidence'],tuple(p['embedding']),s['frame_id'],s['timestamp'],manager.session_id) for p in persons]
    result[name]=manager.observe(session_id=manager.session_id,frame_id=s['frame_id'],timestamp=s['timestamp'],now=s['timestamp']+s['age_ms']/1000,candidates=cs)
   a,b,c=s['decision']['selected_candidate'],result['old_recomputed']['selected_candidate'],result['new']['selected_candidate']
   counts['samples']+=1;counts['old_recomputed_matches_logged']+=int(a==b)
   counts['logged_selected']+=int(a is not None);counts['new_selected']+=int(c is not None)
   counts['baseline_hold_new_selected']+=int(s['baseline_selected'] is None and c is not None)
   counts['changed_selection']+=int(a!=c);reasons[result['new']['reason']]+=1
   f.write(json.dumps(dict(frame_id=s['frame_id'],timestamp=s['timestamp'],baseline_selected=s['baseline_selected'],logged_shadow_selected=a,**result),allow_nan=False)+'\n')
 (out/'counterfactual_summary.json').write_text(json.dumps(dict(counts=counts,reasons=reasons,resets=resets,source_capture_sha256=summary['frames_sha256'],scope='same 471 sample times and observed reset boundaries; real OSNet recomputation; no live scheduling prediction',ground_truth=False,physical_commands=0),indent=2))
 print((out/'counterfactual_summary.json').read_text())
