"""Matched A/B/C/E1/E2/E3 offline ablations, full and fixed200 source samples.

E variants emit provisional localization separately; verified selection uses C
unchanged. E3 is exploratory, not promotion based on unlabelled E1/E2 counts.
"""
import argparse,json,time,copy
from pathlib import Path
from types import SimpleNamespace
from collections import Counter
import cv2,numpy as np
import config
from ai.target_identity import Candidate,TargetIdentityManager,IdentityPolicy,Phase2Policy
from runtime.identity_events import crop_quality
from tools.identity_continuity import ContinuityVerifier,ContinuityPolicy
from tools.identity_replay import offline_guard,digest,replay_cache
from tools.identity_dataset import load_capture,verified_image
from tools.analyze_identity_live import stats
from tools.identity_metrics import evaluate
read=lambda p:[json.loads(l) for l in p.read_text().splitlines()]

def summarize(events,source_rows):
 durations=[b['timestamp']-a['timestamp'] for a,b in zip(events,events[1:])]+[0.]
 # These are sample-interval occupancy proxies, not continuously measured visible-target time.
 result=dict(samples=len(events),selected=sum(e['selected_candidate'] is not None for e in events),
  provisional=sum(e.get('provisional',False) for e in events),localization=sum(e.get('localization_candidate',e['selected_candidate']) is not None for e in events),
  reasons=dict(Counter(e['reason'] for e in events)),continuation_reasons=dict(Counter(e.get('continuation_reason') for e in events)),
  anchor_age_s=stats([e['anchor_age_s'] for e in events if e.get('anchor_age_s') is not None]),
  policy_ms=stats([e['policy_ms'] for e in events if 'policy_ms' in e]),
  identity_hold_interval_s=sum(dt for e,dt in zip(events,durations) if e['selected_candidate'] is None),
  localization_hold_interval_s=sum(dt for e,dt in zip(events,durations) if e.get('localization_candidate',e['selected_candidate']) is None),
  provisional_interval_s=sum(dt for e,dt in zip(events,durations) if e.get('provisional')),
  ambiguous_interval_s=sum(dt for e,dt in zip(events,durations) if e['state'] in ('AMBIGUOUS','VISIBLE_UNVERIFIED')),
  reid_invocations_this_replay=0,reid_scope='shared cached OSNet evidence',reid_cached_ms=stats([r['reid_ms'] for r in source_rows]),
  age_ms=stats([r['observation_age_s']*1000 for r in source_rows]),gap_s=stats(np.diff([r['timestamp'] for r in source_rows])),
  identity_accuracy=None,correct_target_tracking_time_s=None,wrong_target_tracking_time_s=None,false_acceptance=None,false_rejection=None,id_switches=None,fragmentation=None,recovery_latency_s=None,
  live_cpu_gpu_contention=None,physical_commands=0)
 episodes=0;recovered=0;in_hold=False;seen=False;latencies=[];begin=None
 for e in events:
  if e['selected_candidate'] is not None:
   if in_hold:recovered+=1;latencies.append(e['timestamp']-begin)
   in_hold=False;seen=True
  elif seen and not in_hold:episodes+=1;in_hold=True;begin=e['timestamp']
 result.update(hold_episodes_after_first_selection=episodes,reacquired_episodes=recovered,reacquisition_fraction=recovered/episodes if episodes else None,policy_recovery_latency_s=stats(latencies),association_conflicts=sum(n for k,n in result['continuation_reasons'].items() if k and ('CONFLICT' in k or 'RISK' in k)))
 return result

def run(tag,split,out):
 cache=Path(f'docs/identity_shadow_audit_20261119_{tag}/capture_cache');meta=json.loads((cache/'manifest.json').read_text());assert digest(cache/'observations.jsonl')==meta['observations_sha256']
 for p,h in {**meta['baseline_source_sha256'],**meta['model_sha256']}.items():assert digest(p)==h,p
 rows=read(cache/'observations.jsonl');cap=Path(meta['video']);_,_,frames=load_capture(cap);images={r['frame_id']:r for r in frames}
 cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config']);cfg.IDENTITY_CANDIDATE_FILTERING_ENABLED=True;cfg.IDENTITY_DISTRACTOR_MEMORY_ENABLED=True;cfg.IDENTITY_MULTI_CUE_ENABLED=True
 policy=IdentityPolicy.from_config(cfg);p2=Phase2Policy.from_config(cfg);quality={}
 for row in rows:
  im=cv2.imread(str(verified_image(cap,images[row['frame_id']])));quality[row['frame_id']]=crop_quality(im,row['candidates'],cfg)
 reports={}
 fixed={r['frame_id'] for r in read(Path(f'docs/identity_phase2_v4_followup/{tag}_fixed200_0.jsonl'))}
 for cadence in ('all','fixed200'):
  folder=out/split/cadence;folder.mkdir(parents=True,exist_ok=False);subset=rows if cadence=='all' else [r for r in rows if r['frame_id'] in fixed]
  # Materialize a shared subset cache for the unmodified baseline replay tool.
  subcache=folder/'cache';subcache.mkdir();f=subcache/'observations.jsonl'
  with f.open('w') as stream:
   for r in subset:stream.write(json.dumps(r)+'\n')
  sm=dict(meta,observations_sha256=digest(f),samples=len(subset));(subcache/'manifest.json').write_text(json.dumps(sm))
  replay_cache(subcache,folder/'AB',identity_enabled=True)
  base=read(folder/'AB/baseline.jsonl');breference=read(folder/'AB/identity.jsonl')
  def manager(phase2):return TargetIdentityManager(meta['reference_embeddings'][0],policy,session_id='phase2b',target_uid='offline',reference_views=meta['reference_embeddings'][1:],phase2=phase2)
  ms={'B':manager(Phase2Policy()),'C':manager(p2)}
  for k,flags in [('E1',ContinuityPolicy(True,False)),('E2',ContinuityPolicy(False,True)),('E3',ContinuityPolicy(True,True))]:ms[k]=ContinuityVerifier(manager(p2),flags)
  events={'A':base,**{k:[] for k in ms}}
  for row in subset:
   for name,m in ms.items():
    core=m.manager if isinstance(m,ContinuityVerifier) else m
    anchor=core.last_trusted
    cs=[Candidate(p['candidate_id'],p['track_id'],tuple(p['bbox']),p['confidence'],tuple(p['embedding']),row['frame_id'],row['timestamp'],core.session_id,crop_quality=p['crop_quality'],occluded=p.get('occluded',False),policy_rejection=p.get('policy_rejection')) for p in quality[row['frame_id']]]
    start=time.perf_counter();r=m.observe(session_id=core.session_id,frame_id=row['frame_id'],timestamp=row['timestamp'],now=row['timestamp']+row['observation_age_s'],candidates=cs);ms_elapsed=(time.perf_counter()-start)*1000
    r.update(frame_id=row['frame_id'],timestamp=row['timestamp'],observation_age_s=row['observation_age_s'],policy_ms=ms_elapsed,anchor_age_s=row['timestamp']-anchor.timestamp if anchor else None)
    assert not core.gallery and not r['ptz_authorized']
    if r.get('provisional'):
     assert core.last_trusted is anchor and r['selected_candidate'] is None and r['bbox'] is None
    events[name].append(r)
  for a,b in zip(events['B'],breference):assert all(a[k]==b[k] for k in ('selected_candidate','reason','state','action'))
  for name in ('E1','E2','E3'):
   for a,b in zip(events['C'],events[name]):assert all(a[k]==b[k] for k in ('selected_candidate','reason','state','action'))
  for name,rs in events.items():
   with (folder/(name+'.jsonl')).open('w') as f:
    for r in rs:f.write(json.dumps(r,allow_nan=False)+'\n')
  summary={k:summarize(rs,subset) for k,rs in events.items()};(folder/'report.json').write_text(json.dumps(summary,indent=2));reports[cadence]=summary
  print(split,cadence,[(k,x['selected'],x['provisional']) for k,x in summary.items()],flush=True)
 return reports
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 with offline_guard():
  cv2.setNumThreads(1)
  frozen={p:digest(p) for p in ('tools/identity_continuity.py','tools/benchmark_identity_phase2b.py','ai/target_identity.py','config.py')}
  result={'development':run('191352','development',a.output),'retrospective_holdout':run('190016','retrospective_holdout',a.output)}
  assert all(digest(p)==h for p,h in frozen.items())
  (a.output/'summary.json').write_text(json.dumps(dict(reports=result,frozen_source_sha256=frozen,ground_truth=False,physical_commands=0,split='Separate recordings; retrospective holdout previously inspected, not unseen validation',E3='Exploratory combination only; not endorsed without independent GT'),indent=2))
