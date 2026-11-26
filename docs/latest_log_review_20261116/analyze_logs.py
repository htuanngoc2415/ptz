import json,re,glob,csv,collections,datetime,pathlib
out={}
for key,stamp in [('094907','094911_446316'),('101152','101155_841439')]:
 p=pathlib.Path(f'ptz/logs/ptz_20261116_{key}.log'); lines=p.read_text().splitlines(); dt=lambda l:datetime.datetime.fromisoformat(l[:23]); start=dt(lines[0]); end=next((dt(l) for l in lines if 'GUI shutdown requested' in l),dt(lines[-1])); state='SEARCHING'; prev=start; dwell=collections.Counter(); transitions=[]; losses=[]; pending=None
 for n,l in enumerate(lines,1):
  m=re.search(r'\[STATE\] (\w+) -> (\w+)',l)
  if m:
   t=dt(l); dwell[state]+=(t-prev).total_seconds(); state=m[2];prev=t;transitions.append([str(t),m[1],state,n])
   if state=='SEARCHING':pending=t
   elif state=='TARGET_LOCKED' and pending: losses.append([str(pending),str(t),(t-pending).total_seconds()]);pending=None
 dwell[state]+=(end-prev).total_seconds()
 rows=[]; coverage=[]
 for f in glob.glob(f'ptz/logs/latency/latency_trace_20261116_{stamp}.jsonl*'):
  rr=[json.loads(l) for l in open(f)]; rows+=rr;coverage.append([f,len(rr),rr[0].get('timestamp'),rr[-1].get('timestamp')])
 rows.sort(key=lambda r:r.get('monotonic_ns',0)); reports=[r for r in rows if r['event']=='LATENCY_REPORT']; decisions=[r for r in rows if r['event']=='IDENTITY_TRACKING_DECISION']; finals=[r for r in rows if r['event']=='IDENTITY_METRICS_FINAL']; cam=[]
 for f in glob.glob(f'ptz/logs/camera/camera_control_20261116_{stamp}.log*'):
  for l in open(f):
   try:cam.append(json.loads(l[l.index('{'):]))
   except (ValueError,json.JSONDecodeError):pass
 gui_end=next((r['timestamp'] for r in cam if r['event']=='SHUTDOWN_REQUESTED' and r.get('shutdown_phase')=='gui'),None)
 if gui_end:
  cutoff=datetime.datetime.fromisoformat(gui_end).replace(tzinfo=None); dwell[state]-=(end-cutoff).total_seconds(); end=cutoff
 events=collections.Counter(r['event'] for r in cam)
 metrics=(finals[-1] if finals else decisions[-1] if decisions else {}).get('metrics',{}).copy()
 for k,v in list(metrics.items()):
  if isinstance(v,list) and v and isinstance(v[0],(int,float)):metrics[k]={'count':len(v),'mean':sum(v)/len(v),'max':max(v)}
 result=dict(start=str(start),end=str(end),duration=(end-start).total_seconds(),dwell=dict(dwell),transitions=transitions,recoveries=losses,unresolved_since=str(pending) if pending else None,recognition=[l for l in lines if '[RECOGNITION]' in l],warnings=[l for l in lines if '[WARNING]' in l or '[ERROR]' in l],coverage=coverage,identity_decisions=len(decisions),identity_final=len(finals),identity_metrics=metrics,identity_reasons=dict(collections.Counter(r.get('reason') for r in decisions)),identity_gmc=dict(collections.Counter(str((r.get('camera_movement') or {}).get('applied')) for r in decisions)),camera_events=dict(events),camera_errors=[r for r in cam if any(s in r['event'] for s in ['ERROR','UNCONFIRMED','FAULT'])],shutdown=[r for r in cam if 'SHUTDOWN' in r['event']],counters=reports[-1].get('counters_and_rates',{}) if reports else {},summary=list(csv.DictReader(open(f'ptz/logs/latency/latency_summary_20261116_{stamp}.csv'))))
 out[key]=result
pathlib.Path('ptz/docs/latest_log_review_20261116/analysis.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
for k,r in out.items():
 print(k,json.dumps({x:r[x] for x in ['duration','dwell','recoveries','unresolved_since','identity_decisions','identity_final','identity_metrics','identity_reasons','identity_gmc','warnings','camera_errors']},ensure_ascii=False))
 print('CAM',json.dumps({e:n for e,n in r['camera_events'].items() if any(s in e for s in ['ACK','SENT','DISPATCH','SHUTDOWN'])}))
 print('RECOGNITION',*r['recognition'],sep='\n')
 print('SHUTDOWN',r['shutdown'])
 print('PERF',[(x['stage'],x['mean'],x['p95']) for x in r['summary'] if x['stage'] in ['yolo_total_ms','reid_total_ms','identity_track_association_ms','receive_to_gui_submit_ms','pan_tilt_rpc_latency_ms']])
 print('COUNTERS', {x:v for x,v in r['counters'].items() if not x.endswith('_ms')})
