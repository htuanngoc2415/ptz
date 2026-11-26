from pathlib import Path
import json,collections,statistics,hashlib
root=Path(__file__).resolve().parents[2];out=Path(__file__).parent
paths=sorted((root/'logs/camera').glob('camera_control_20261116_134925_430677.log*'));rows=[]
for p in paths:
 for line in p.open():
  if '{' in line:rows.append(json.loads(line[line.index('{'):]))
rows.sort(key=lambda r:r['monotonic_ns'])
def stats(a):
 a=sorted(a)
 if not a:return None
 def q(p):
  f=(len(a)-1)*p;i=int(f);return a[i]+(a[min(i+1,len(a)-1)]-a[i])*(f-i)
 return dict(n=len(a),mean=statistics.mean(a),p50=q(.5),p95=q(.95),max=max(a))
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
(out/'metrics.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
