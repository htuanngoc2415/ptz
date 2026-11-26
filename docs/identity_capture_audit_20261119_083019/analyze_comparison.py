from pathlib import Path
import json,collections
root=Path(__file__).resolve().parent
base=root/'capture_706c13123cb84c2294eecf5fa4c3850f'
read=lambda p:[json.loads(l) for l in p.read_text().splitlines()]
baseline=read(base/'comparison/baseline.jsonl');identity=read(base/'comparison/identity.jsonl');live=read(Path('logs/identity_capture')/base.name/'frames.jsonl')
assert len(baseline)==len(identity)==len(live)==1667
counts=collections.Counter();spans=[]
for b,i,l in zip(baseline,identity,live):
 assert b['frame_id']==i['frame_id']==l['frame_id']
 bs,ins=b['selected_candidate'],i['selected_candidate']
 category='both_none' if bs is None and ins is None else 'identity_only' if bs is None else 'baseline_only' if ins is None else 'same_candidate' if bs==ins else 'different_candidate'
 counts[category]+=1
 if not spans or spans[-1]['category']!=category:
  spans.append(dict(category=category,first_frame=i['frame_id'],start_s=i['timestamp']-identity[0]['timestamp'],frames=0))
 spans[-1].update(last_frame=i['frame_id'],end_s=i['timestamp']-identity[0]['timestamp'],frames=spans[-1]['frames']+1)
report=dict(comparison_counts=counts,spans=spans,identity_reasons=dict(collections.Counter(r['reason'] for r in identity)),identity_states=dict(collections.Counter(r['state'] for r in identity)),live_selected=sum(r['selected_candidate'] is not None for r in live),replay_live_selection_agreements=sum(b['selected_candidate']==l['selected_candidate'] for b,l in zip(baseline,live)),physical_commands=0,ground_truth=False)
(root/'comparison_details.json').write_text(json.dumps(report,indent=2));print(json.dumps({k:v for k,v in report.items() if k!='spans'},indent=2))
