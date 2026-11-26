"""Evaluate confirmed identity AND separate provisional localization on reviewed GT.

UNKNOWN is excluded, never mapped to distractor. Unreviewed intervals break metrics.
"""
import argparse,json
from pathlib import Path
from tools.phase2b_review import validate
from tools.identity_metrics import evaluate
from tools.identity_replay import offline_guard

def evaluate_reviewed(rows,manual,split,*,localization=False):
 labels=[];by={r['frame_id']:r for r in rows}
 for a in manual['frames']:
  if a['split']!=split or a['frame_id'] not in by:continue
  if a['visibility']=='UNKNOWN' or 'UNKNOWN' in a['candidate_identities'].values():continue
  labels.append(dict(frame_id=a['frame_id'],target_visible=a['visibility'] in ('VISIBLE','PARTIAL_OCCLUSION'),candidate_identities=a['candidate_identities']))
 selected_rows=[dict(r,selected_candidate=r.get('localization_candidate',r['selected_candidate']) if localization else r['selected_candidate']) for r in rows]
 if not labels:
  result=evaluate(selected_rows);result.update(known_frames=0,false_acceptance_count=None,false_rejection_count=None);return result
 result=evaluate(selected_rows,dict(source='manual',target_identity='target',frames=labels))
 lb={r['frame_id']:r for r in labels};fa=fr=0
 for r in selected_rows:
  a=lb.get(r['frame_id'])
  if a is None:continue
  identity=a['candidate_identities'].get(r['selected_candidate'])
  fa+=identity is not None and identity!='target'
  fr+=a['target_visible'] and identity!='target'
 result.update(known_frames=len(labels),false_acceptance_count=fa,false_rejection_count=fr,
   false_rejection_definition='Visible GT target without correct selected/localization candidate, includes detector miss; not solely an association error',
   track_scope='provisional+confirmed localization' if localization else 'confirmed identity only')
 return result

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pack',type=Path,required=True);p.add_argument('--annotations',type=Path,required=True);p.add_argument('--benchmark',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
 with offline_guard():
  pack=json.loads(a.pack.read_text());manual=json.loads(a.annotations.read_text());status=validate(pack,manual);results={}
  for s in pack['sets']:
   for cadence in ('all','fixed200'):
    for variant in ('A','B','C','E1','E2','E3'):
     path=a.benchmark/s['split']/cadence/(variant+'.jsonl');rows=[json.loads(l) for l in path.read_text().splitlines()]
     # Same cache provenance is checked before attaching external labels to outcomes.
     meta=json.loads((path.parent/'cache/manifest.json').read_text())
     if meta['source_capture_sha256']!=s['capture_sha256']:raise ValueError('Benchmark/review capture mismatch')
     results[f"{s['split']}/{cadence}/{variant}"]={kind:evaluate_reviewed(rows,manual,s['split'],localization=kind=='localization') for kind in ('identity','localization')}
  with a.output.open('x') as f:json.dump(dict(annotation_status=status,metrics=results,limitations=['Sparse review is not population accuracy','Temporal metrics integrate only adjacent reviewed sample intervals','Retrospective holdout was previously inspected, not unseen evidence']),f,indent=2)
