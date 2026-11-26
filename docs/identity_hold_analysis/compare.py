from pathlib import Path
import json,collections
root=Path(__file__).resolve().parent
read=lambda p:[json.loads(l) for l in p.read_text().splitlines()]
summary={}
for final in (root/'final').iterdir():
 old_root=Path('docs')/('identity_capture_audit_20261119_083019' if '706c' in final.name else 'identity_capture_audit_20261119')/final.name/'comparison'
 old=read(old_root/'identity.jsonl');new=read(final/'identity.jsonl');base=read(final/'baseline.jsonl')
 assert [{k:v for k,v in r.items() if k != 'target_uid'} for r in read(old_root/'baseline.jsonl')]==[{k:v for k,v in r.items() if k != 'target_uid'} for r in base]
 counts=collections.Counter();spans=[];t=new[0]['timestamp'];len_seen=0
 for o,n,b in zip(old,new,base):
  assert o['frame_id']==n['frame_id']==b['frame_id']
  if n['selected_candidate']:
   a=next(a for a in n['candidate_audit'] if a['accepted']);assert a['reason']=='CONFIRMED_MULTI_CUE'
  if o['selected_candidate'] is None and n['selected_candidate'] is not None:counts['added_selection']+=1
  if o['selected_candidate'] is not None and n['selected_candidate'] is None:counts['removed_selection']+=1
  if n['selected_candidate'] and b['selected_candidate'] and n['selected_candidate']!=b['selected_candidate']:counts['different_from_baseline']+=1
  hold=bool(b['selected_candidate']) and not n['selected_candidate']
  if hold:
   if not spans or spans[-1]['last_index']!=len_seen-1:
    spans.append(dict(start_s=n['timestamp']-t,frames=0))
   spans[-1].update(end_s=n['timestamp']-t,frames=spans[-1]['frames']+1,last_index=len_seen)
  len_seen+=1
 windows=[]
 for a,z in [(27.166,32.533),(70.964,81.699),(141.632,144.264)]:
  segment=[n for n in new if a<=n['timestamp']-t<=z]
  if segment:windows.append(dict(start=a,end=z,frames=len(segment),selected=sum(bool(n['selected_candidate']) for n in segment),reasons=dict(collections.Counter(n['reason'] for n in segment))))
 summary[final.name]=dict(old_selected=sum(bool(r['selected_candidate']) for r in old),new_selected=sum(bool(r['selected_candidate']) for r in new),changes=dict(counts),longest_remaining_holds=sorted(spans,key=lambda s:s['end_s']-s['start_s'],reverse=True)[:4],windows=windows)
(root/'comparison_summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
