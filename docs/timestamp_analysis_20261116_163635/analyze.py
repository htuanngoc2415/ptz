from pathlib import Path
import json,collections,statistics,hashlib
root=Path(__file__).resolve().parents[2];out=Path(__file__).parent
paths=sorted((root/'logs/camera').glob('camera_control_20261116_163635_205025.log*'));rows=[]
for p in paths:
 for line in p.open():
  if '{' in line:rows.append(json.loads(line[line.index('{'):]))
rows.sort(key=lambda r:r['monotonic_ns'])
def stats(a):
 a=sorted(a)
 if not a:return None
 def q(p):
  f=(len(a)-1)*p;i=int(f);return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(f-i)
 return dict(n=len(a),mean=statistics.mean(a),p50=q(.5),p95=q(.95),p99=q(.99),max=max(a))
sent={};pairs=[];commands=collections.defaultdict(dict)
for r in rows:
 e=r['event'];rid=r.get('rpc_command_id')
 if e in ('PAN_TILT_START_SENT','PAN_TILT_STOP_SENT'):sent[(rid,r['operation'])]=r
 if e in ('PAN_TILT_START_ACK','PAN_TILT_STOP_ACK'):
  s=sent.get((rid,r['operation']))
  if s:pairs.append(dict(operation=r['operation'],code=r['rpc_code'],ms=(r['monotonic_ns']-s['monotonic_ns'])/1e6,sent=s['timestamp'],ack=r['timestamp']))
 if r.get('command_id'):commands[r['command_id']][e]=r
turn={'kCmdLeft','kCmdRight','kCmdUp','kCmdDown'}
metrics={}
for op in ('START','STOP'):
 metrics[op+'_sent_to_ack_ms']=stats([p['ms'] for p in pairs if p['operation']==op and p['code'] in turn])
for name,first,last in [('dispatch_to_wire_ms','PAN_TILT_DISPATCH','PAN_TILT_WIRE_DISPATCH'),('dispatch_to_rpc_result_ms','PAN_TILT_DISPATCH','PAN_TILT_RPC_RESULT'),('start_return_to_stop_return_ms','PAN_TILT_RPC_RESULT','PAN_TILT_STOP')]:
 metrics[name]=stats([(c[last]['monotonic_ns']-c[first]['monotonic_ns'])/1e6 for c in commands.values() if first in c and last in c])
for field in ('observation_age_ms','detection_to_dispatch_ms','inter_pulse_wait_ms'):
 metrics[field]=stats([r[field] for r in rows if r['event']=='PAN_TILT_DISPATCH' and r.get(field) is not None])
metrics['receive_to_rpc_result_ms']=stats([c['PAN_TILT_DISPATCH']['observation_age_ms']+(c['PAN_TILT_RPC_RESULT']['monotonic_ns']-c['PAN_TILT_DISPATCH']['monotonic_ns'])/1e6 for c in commands.values() if 'PAN_TILT_DISPATCH' in c and 'PAN_TILT_RPC_RESULT' in c])
result=dict(coverage=[rows[0]['timestamp'],rows[-1]['timestamp']],files=[str(p.relative_to(root)) for p in paths],metrics=metrics,event_counts=collections.Counter(r['event'] for r in rows),examples=[p for p in pairs if p['operation']=='START' and p['code'] in turn][:3],unconfirmed=[{k:r.get(k) for k in ('event','timestamp','rpc_code','exception_type','exception_message')} for r in rows if 'UNCONFIRMED' in r['event']],hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
(out/'metrics.json').write_text(json.dumps(result,indent=2));

# Separate successful START from a rejected worker intent.
metrics['successful_receive_to_rpc_ms']=stats([c['PAN_TILT_DISPATCH']['observation_age_ms']+(c['PAN_TILT_RPC_RESULT']['monotonic_ns']-c['PAN_TILT_DISPATCH']['monotonic_ns'])/1e6 for c in commands.values() if 'PAN_TILT_DISPATCH' in c and c.get('PAN_TILT_RPC_RESULT',{}).get('acknowledged') is True])
dispatches=[r for r in rows if r['event']=='PAN_TILT_DISPATCH']
for name,a,b in [('frame_to_detection_ms','observation_time','detection_completed_timestamp'),('detection_to_decision_ms','detection_completed_timestamp','target_decision_timestamp'),('decision_to_publish_ms','target_decision_timestamp','observation_published_timestamp'),('publish_to_consume_ms','observation_published_timestamp','observation_consumed_timestamp')]:
 metrics[name]=stats([(r[b]-r[a])*1000 for r in dispatches if r.get(a) is not None and r.get(b) is not None])
metrics['consume_to_dispatch_ms']=stats([r['consume_to_dispatch_ms'] for r in dispatches if r.get('consume_to_dispatch_ms') is not None])
result['rejected']=[r for r in rows if r['event']=='PAN_TILT_RPC_RESULT' and not r.get('acknowledged')]
result['stop_reasons']=collections.Counter(r.get('stop_reason') for r in rows if r['event']=='PAN_TILT_STOP')
result['decisions']=collections.Counter(r.get('decision_reason') for r in rows if r['event']=='PAN_TILT_DECISION')
result['wire_codes']=collections.Counter(r.get('rpc_code') for r in rows if r['event']=='PAN_TILT_START_SENT')
result['toggles']=[r for r in rows if r['event'] in ('PAN_TILT_TOGGLE','ZOOM_TOGGLE')]
result['shutdown']=[r for r in rows if r['event'].startswith('SHUTDOWN')]
traces=[]
for p in (root/'logs/latency').glob('latency_trace_20261116_163635_205025.jsonl*'):
 traces.extend(json.loads(l) for l in p.open())
 result['hashes'][p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
traces.sort(key=lambda r:r['monotonic_ns'])
result['trace_coverage']=[traces[0]['timestamp'],traces[-1]['timestamp']]
result['trace_counts']=collections.Counter(r['event'] for r in traces)
result['last_latency_report']=[r for r in traces if r['event']=='LATENCY_REPORT'][-1]
result['retained_publish_to_consume_ms']=stats([r['publish_to_consume_ms'] for r in traces if r['event']=='PAN_OBSERVATION_CONSUMED'])
result['examples_commands']=[list(c.values()) for c in commands.values() if 'PAN_TILT_DISPATCH' in c][:1]
tracking=[json.loads(l) for l in (root/'logs/ptz_20261116_163631.jsonl').open()]
result['tracking_counts']=collections.Counter(r['event'] for r in tracking)
result['tracking_events']=[r for r in tracking if r['event'] in ('TARGET_LOCKED','TARGET_LOST','TARGET_REACQUIRED','STATE_TRANSITION')]
(out/'metrics.json').write_text(json.dumps(result,indent=2))
print(json.dumps(dict(metrics=metrics,stop_reasons=result['stop_reasons'],wire_codes=result['wire_codes'],tracking_counts=result['tracking_counts'],report_other={k:v for k,v in result['last_latency_report'].items() if k!='statistics'}),indent=2))
