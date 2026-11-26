"""Isolate existing Phase2 flags on the new capture, shared measured crop quality."""
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from collections import Counter
import json,cv2
import config
from ai.target_identity import TargetIdentityManager,IdentityPolicy,Phase2Policy
from runtime.identity_events import crop_quality
from tools.benchmark_identity_phase2 import observe
from tools.identity_replay import offline_guard,digest
from tools.identity_dataset import load_capture,verified_image
out=Path(__file__).resolve().parent
with offline_guard():
 cache=Path('docs/identity_shadow_audit_20261119_191352/capture_cache');meta=json.loads((cache/'manifest.json').read_text());assert digest(cache/'observations.jsonl')==meta['observations_sha256']
 rows=[json.loads(l) for l in (cache/'observations.jsonl').read_text().splitlines()];cap=Path(meta['video']);_,_,fr=load_capture(cap);images={r['frame_id']:r for r in fr}
 cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});vars(cfg).update(meta['config'])
 policy=IdentityPolicy.from_config(cfg)
 p=Phase2Policy.from_config(cfg)
 variants={'off':p,'filter':replace(p,filtering=True),'memory':replace(p,filtering=True,memory=True),'association':replace(p,filtering=True,memory=True,association=True)}
 managers={k:TargetIdentityManager(meta['reference_embeddings'][0],policy,session_id=k,reference_views=meta['reference_embeddings'][1:],phase2=v) for k,v in variants.items()}
 results={k:[] for k in variants};memory={k:0 for k in variants}
 for row in rows:
  frame=cv2.imread(str(verified_image(cap,images[row['frame_id']])));people=crop_quality(frame,row['candidates'],cfg)
  for k,m in managers.items():
   r=observe(m,row,people,row['observation_age_s']);results[k].append(r);memory[k]=max(memory[k],len(m.distractors))
 summary={}
 for k,rs in results.items():
  counts=Counter()
  for a,b in zip(results['off'],rs):
   x,y=a['selected_candidate'],b['selected_candidate'];counts['same' if x==y else 'gain' if x is None else 'loss' if y is None else 'different']+=1
  summary[k]=dict(selected=sum(r['selected_candidate'] is not None for r in rs),reasons=dict(Counter(r['reason'] for r in rs)),versus_off=dict(counts),max_memory_entries=memory[k])
 (out/'filter_ablation.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
