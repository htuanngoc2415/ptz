"""Compare the edited manager with the exact pre-Phase-2 snapshot, flags OFF."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import config
import ai.target_identity as new
from tools.identity_replay import digest, write_json, offline_guard

root=Path(__file__).resolve().parents[2]
snapshot=root.parent/'validation/identity_phase2_before/target_identity.py'
spec=importlib.util.spec_from_file_location('frozen_phase1',snapshot)
old=importlib.util.module_from_spec(spec);sys.modules[spec.name]=old;spec.loader.exec_module(old)
caches=[Path(p) for p in (
 'docs/identity_shadow_audit_20261119_173204/capture_cache',
 'docs/identity_shadow_v3/capture_cache',
 'docs/identity_capture_audit_20261119_083019/capture_706c13123cb84c2294eecf5fa4c3850f/cache',
 'docs/identity_capture_audit_20261119/capture_eb68f45a3a914ba897f811ba3c0129b5/cache',
 'docs/identity_capture_audit_20261119/capture_9b6f72b28e284599a83a2ca918947993/cache')]
results=[]
with offline_guard():
 for cache in caches:
  meta=json.loads((cache/'manifest.json').read_text());assert digest(cache/'observations.jsonl')==meta['observations_sha256']
  cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config'])
  modules=(old,new)
  managers=[m.TargetIdentityManager(meta['reference_embeddings'][0],m.IdentityPolicy.from_config(cfg),
      session_id='offline',target_uid='fixed',reference_views=meta['reference_embeddings'][1:]) for m in modules]
  count=0
  for line in (cache/'observations.jsonl').open():
   r=json.loads(line);decisions=[]
   for module,manager in zip(modules,managers):
    candidates=[module.Candidate(p['candidate_id'],p['track_id'],tuple(p['bbox']),p['confidence'],
       tuple(p['embedding']),r['frame_id'],r['timestamp'],'offline') for p in r['candidates']]
    decisions.append(manager.observe(session_id='offline',frame_id=r['frame_id'],timestamp=r['timestamp'],
        now=r['timestamp']+r['observation_age_s'],candidates=candidates))
   assert decisions[0]==decisions[1],(str(cache),r['frame_id'])
   count+=1
  results.append(dict(cache=str(cache),frames=count,full_output_mismatches=0))
 write_json(Path(__file__).parent/'off_parity.json',dict(results=results,
    snapshot_sha256=digest(snapshot),current_sha256=digest(root/'ai/target_identity.py'),physical_commands=0))
 print(json.dumps(results))
