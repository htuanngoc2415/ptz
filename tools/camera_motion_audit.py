#!/usr/bin/env python3
"""Read-only offline temporal audit. Command exposure is NOT observed camera motion."""
import argparse,bisect,collections,csv,datetime,hashlib,json,re,statistics
from pathlib import Path


def load_camera(paths):
    rows=[json.loads(line[line.index('{'):]) for path in paths for line in path.read_text().splitlines() if '{' in line]
    return sorted(rows,key=lambda r:r['monotonic_ns'])


def command_intervals(rows):
    active={};intervals=[]
    for r in rows:
        event=r['event'];code=r.get('rpc_code');t=r['monotonic_ns']/1e9
        if event=='PAN_TILT_START_SENT':active[code]=t
        elif event=='PAN_TILT_STOP_SENT' and code in active:
            intervals.append((active.pop(code),t,code))
    return sorted(intervals),active


def exposure_at(t, intervals):
    if any(a<=t<b for a,b,_ in intervals):return 'command_active'
    ends=[b for a,b,_ in intervals if b<=t]
    gap=t-max(ends) if ends else float('inf')
    if gap<.3:return 'post_stop_0_300ms'
    if gap<.8:return 'post_stop_300_800ms'
    return 'command_quiet_gt800ms'  # not measured standstill


def exposure_seconds(start,end,intervals):
    cuts=sorted({start,end}|{max(start,min(end,t)) for a,b,_ in intervals for t in (a,b,b+.3,b+.8)})
    seconds=collections.defaultdict(float)
    for a,b in zip(cuts,cuts[1:]):seconds[exposure_at((a+b)/2,intervals)]+=b-a
    return dict(seconds)


def audit(root,stamp,appstamp,out):
    cameras=sorted((root/'logs/camera').glob('camera_control_'+stamp+'.log*'))
    traces=sorted((root/'logs/latency').glob('latency_trace_'+stamp+'.jsonl*'))
    app=root/'logs'/('ptz_'+appstamp+'.log')
    rows=load_camera(cameras);trace=[json.loads(l) for p in traces for l in p.read_text().splitlines()]
    trace.sort(key=lambda r:r['monotonic_ns'])
    offsets=[datetime.datetime.fromisoformat(r['timestamp']).timestamp()-r['monotonic_ns']/1e9 for r in rows]
    offset=statistics.median(offsets)
    wall=lambda s:datetime.datetime.strptime(s[:23],'%Y-%m-%d %H:%M:%S.%f').replace(tzinfo=datetime.timezone(datetime.timedelta(hours=7))).timestamp()
    lines=app.read_text().splitlines();start=wall(lines[0])-offset
    end=next(r['monotonic_ns']/1e9 for r in rows if r['event']=='SHUTDOWN_REQUESTED' and r.get('shutdown_phase')=='gui')
    intervals,unfinished=command_intervals(rows);exposures=exposure_seconds(start,end,intervals)
    transitions=[]
    for l in lines:
        m=re.search(r'\[STATE\] (\w+) -> (\w+)',l)
        if m:transitions.append(dict(wall_time=l[:23],monotonic=wall(l)-offset,previous=m[1],state=m[2]))
    counts={k:collections.Counter() for k in ('HOLD','FULL_LOSS')};events=[]
    decisions=[r for r in rows if r['event']=='PAN_TILT_DECISION'];times=[r['monotonic_ns']/1e9 for r in decisions]
    detail_events={'TRACKER_OVERLAP_DIAGNOSTICS','PERSON_DETECTION_RESULT','REID_RESULT_REJECTED','TARGET_ID_CHANGED','STALE_RESULT','TARGET_STATE'}
    for tr in transitions:
        kind='HOLD' if tr['state']=='OCCLUDED_HOLD' else 'FULL_LOSS' if tr['previous']=='OCCLUDED_HOLD' and tr['state']=='SEARCHING' else None
        if kind is None:continue
        t=tr['monotonic'];bucket=exposure_at(t,intervals);counts[kind][bucket]+=1
        i=bisect.bisect_right(times,t)-1;obs=decisions[i] if i>=0 else {}
        frame_t=obs.get('received_ns',0)/1e9 or obs.get('observation_time')
        dt=t-times[i] if i>=0 else None
        details=[r for r in trace if abs(r['monotonic_ns']/1e9-t)<=.8 and (r['event'] in detail_events or 'STALE' in r['event'] or 'OVERLAP' in r['event'])]
        event=dict(tr,kind=kind,command_exposure=bucket,observed_motion=None,root_cause='UNKNOWN',certainty='UNVERIFIED',
                   previous_pan_decision_age_s=dt,frame_receive_time=frame_t,
                   frame_exposure=exposure_at(frame_t,intervals) if frame_t is not None else None,
                   previous_pan_decision={k:obs.get(k) for k in ('frame_id','target_bbox','decision_reason','tracking_state','identity_state','observation_age_ms','normalized_error_x','normalized_error_y','target_track_id')},
                   nearby_trace_events=details,
                   zoom_command_within_800ms=any('Zoom' in c and a<=t and b>=t-.8 for a,b,c in intervals))
        events.append(event)
    rates={k:{name:{'events':counts[k][name],'exposure_s':seconds,'rate_per_s':counts[k][name]/seconds if seconds else None} for name,seconds in exposures.items()} for k in counts}
    loss=[t for t in transitions if t['previous']=='OCCLUDED_HOLD' and t['state']=='SEARCHING']
    for t in loss:
        hold=next((r for r in reversed(transitions) if r['monotonic']<t['monotonic'] and r['state']=='OCCLUDED_HOLD'),None)
        t['preceding_hold_time']=hold['wall_time'] if hold else None
        t['hold_to_loss_s']=t['monotonic']-hold['monotonic'] if hold else None
        t['hold_onset_command_exposure']=exposure_at(hold['monotonic'],intervals) if hold else None
        later=next((r for r in transitions if r['monotonic']>t['monotonic'] and r['state']=='TARGET_LOCKED'),None)
        t['recovery_s']=later['monotonic']-t['monotonic'] if later else None
    result=dict(clock_mapping={'wall_epoch_minus_monotonic_median':offset,'pair_offset_range_ms':(max(offsets)-min(offsets))*1000,'app_timestamp_resolution_ms':1,'capture_sensor_timestamp':'unavailable','video_clock_mapping':'OSD approximate only; unsynchronized monitor recording'},
        window=[start,end],exposure_seconds=exposures,rates=rates,events=events,losses=loss,command_intervals=intervals,unfinished=unfinished,
        latency_retained=[trace[0]['timestamp'],trace[-1]['timestamp']],
        observed_motion_rates=None,fragmentation_rate=None,ground_truth_available=False,
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [app,*cameras,*traces]})
    out.mkdir(parents=True,exist_ok=True);(out/'timeline.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    with (out/'loss_events.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['wall_time','monotonic','kind','command_exposure','zoom_nearby','root_cause'])
        w.writerows([e['wall_time'],e['monotonic'],e['kind'],e['command_exposure'],e['zoom_command_within_800ms'],e['root_cause']] for e in events)
    print(json.dumps({'exposure_seconds':exposures,'rates':rates,'losses':loss,'offset_range_ms':result['clock_mapping']['pair_offset_range_ms']},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--stamp',default='20261113_112028_272959');p.add_argument('--appstamp',default='20261113_112025');p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();audit(a.root,a.stamp,a.appstamp,a.out)
