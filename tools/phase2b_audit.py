"""Frame-level Phase2B audit before policy changes. No camera or labels inferred."""
from pathlib import Path
from types import SimpleNamespace
from collections import Counter
import csv,json,cv2,numpy as np
import config
from ai.target_identity import TargetIdentityManager,IdentityPolicy,Phase2Policy
from ai.reid_target import iou
from ai.overlap_policy import overlap_geometry
from runtime.identity_events import crop_quality
from tools.benchmark_identity_phase2 import observe
from tools.identity_dataset import load_capture,verified_image
from tools.identity_replay import offline_guard,digest

def audit(cache,out):
 meta=json.loads((cache/'manifest.json').read_text());assert digest(cache/'observations.jsonl')==meta['observations_sha256']
 for f,h in {**meta['baseline_source_sha256'],**meta['model_sha256']}.items():assert digest(f)==h,f
 rows=[json.loads(l) for l in (cache/'observations.jsonl').read_text().splitlines()];cap=Path(meta['video']);_,_,frames=load_capture(cap);images={r['frame_id']:r for r in frames}
 cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config']);cfg.IDENTITY_CANDIDATE_FILTERING_ENABLED=True;cfg.IDENTITY_DISTRACTOR_MEMORY_ENABLED=True;cfg.IDENTITY_MULTI_CUE_ENABLED=True
 policy=IdentityPolicy.from_config(cfg);p2=Phase2Policy.from_config(cfg)
 def manager(phase2):return TargetIdentityManager(meta['reference_embeddings'][0],policy,session_id='audit',reference_views=meta['reference_embeddings'][1:],phase2=phase2)
 b,c=manager(Phase2Policy()),manager(p2);failures=[];allrows=[];qualities=[];prev={};timeline=[]
 for row in rows:
  fid,ts=row['frame_id'],row['timestamp'];im=cv2.imread(str(verified_image(cap,images[fid])));people=crop_quality(im,row['candidates'],cfg)
  qualities.append(dict(frame_id=fid,persons=[{k:p[k] for k in ('candidate_id','crop_quality','phase2_quality')} for p in people]))
  anchor=c.last_trusted;state=c.state.value;pending=c._pending_count;pending_observation=c._pending;previous_ts=c.last_timestamp
  rb=observe(b,row,people,row['observation_age_s']);rc=observe(c,row,people,row['observation_age_s'])
  timeline.append(dict(frame_id=fid,timestamp=ts,reason=rc['reason'],selected=rc['selected_candidate'],tracks=[p['track_id'] for p in people]))
  best=next((p for p in people if p['candidate_id']==rb['selected_candidate']),None)
  if best:
   score=max(float(np.dot(best['embedding'],v)) for v in c.reference_views);others=[p for p in people if p is not best];old=prev.get(best['track_id']);auditrow=next(a for a in rc['candidate_audit'] if a['candidate_id']==best['candidate_id'])
   evidence=dict(frame_id=fid,timestamp=ts,image=str((cap/images[fid]['image']).resolve()),candidate_id=best['candidate_id'],track_id=best['track_id'],bbox=best['bbox'],frame_shape=row['frame_shape'],reason=rc['reason'],state_before=state,state_after=rc['state'],b_selected=rb['selected_candidate'],c_selected=rc['selected_candidate'],pending_before=pending,pending_after=c._pending_count,pending_gap_s=ts-pending_observation[0] if pending_observation else None,pending_iou=iou(best['bbox'],pending_observation[1].bbox) if pending_observation else None,pending_appearance=float(np.dot(best['embedding'],pending_observation[2])) if pending_observation else None,anchor_frame=anchor.frame_id if anchor else None,anchor_track_id=anchor.candidate.track_id if anchor else None,anchor_age_s=ts-anchor.timestamp if anchor else None,same_trusted_track=best['track_id']==anchor.candidate.track_id if anchor else None,anchor_iou=iou(best['bbox'],anchor.candidate.bbox) if anchor else None,anchor_similarity=float(np.dot(best['embedding'],anchor.candidate.embedding)) if anchor else None,reference_similarity=score,reference_threshold=auditrow.get('reference_threshold'),continuity_mode=auditrow.get('continuity_mode'),same_track_score_delta=score-old['score'] if old else None,same_track_score_dt=ts-old['timestamp'] if old else None,sample_gap_s=ts-previous_ts if previous_ts is not None else None,quality=best['crop_quality'],quality_evidence=best['phase2_quality'],competitors=[dict(candidate_id=p['candidate_id'],track_id=p['track_id'],reference_similarity=max(float(np.dot(p['embedding'],v)) for v in c.reference_views),**overlap_geometry(best['bbox'],p['bbox'])) for p in others],memory=[dict(track_id=e.candidate.track_id,age_s=ts-e.candidate.evidence_timestamp,count=e.count) for e in c.distractors],pose='UNKNOWN_REQUIRES_REVIEW',visibility='UNKNOWN_REQUIRES_REVIEW',identity_correct=None,ground_truth=False)
   allrows.append(evidence)
   if rc['selected_candidate'] is None:
    episode=[x for x in timeline if anchor is None or x['frame_id']>anchor.frame_id]
    evidence['first_hold_since_anchor']=episode[0] if episode else None
    evidence['intervening_reasons']=dict(Counter(x['reason'] for x in episode))
    failures.append(evidence)
  prev={p['track_id']:dict(score=max(float(np.dot(p['embedding'],v)) for v in c.reference_views),timestamp=ts) for p in people}
 for name,data in [('failures',failures),('all_b_selected',allrows),('quality',qualities)]:
  with (out/(name+'.jsonl')).open('w') as f:
   for r in data:f.write(json.dumps(r,allow_nan=False)+'\n')
 with (out/'failures.csv').open('w') as f:
  keys=list(failures[0]);w=csv.DictWriter(f,keys);w.writeheader()
  for r in failures:w.writerow({k:json.dumps(v) if isinstance(v,(dict,list)) else v for k,v in r.items()})
 summary={}
 for reason in sorted({r['reason'] for r in failures}):
  group=[r for r in failures if r['reason']==reason];ages=[r['anchor_age_s'] for r in group if r['anchor_age_s'] is not None]
  summary[reason]=dict(n=len(group),same_trusted_track=sum(r['same_trusted_track'] is True for r in group),different_trusted_track=sum(r['same_trusted_track'] is False for r in group),no_anchor=sum(r['anchor_frame'] is None for r in group),anchor_within_1s=sum(a<=1 for a in ages),anchor_age_median_s=float(np.median(ages)) if ages else None,anchor_age_max_s=max(ages,default=None),high_reference_ge_pending=sum(r['reference_similarity']>=.7 for r in group),max_reference=float(max(r['reference_similarity'] for r in group)),unknown_identity=len(group))
 (out/'audit_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
if __name__=='__main__':
 with offline_guard():audit(Path('docs/identity_shadow_audit_20261119_191352/capture_cache'),Path('docs/identity_phase2b'))
