"""Offline reports and empirical pulse lookup. Never contacts a camera."""
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

def truth(v):
    return v is True or str(v).lower()=='true'

def read_table(session,table):
    path = Path(session)/f'data/{table}.csv'
    if not path.exists():
        return []
    with path.open(newline='') as f:
        return list(csv.DictReader(f))

def num(row,key):
    try:
        v = float(row[key])
        return v if math.isfinite(v) else None
    except (ValueError,TypeError,KeyError):
        return None

def stats(values):
    values = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    if not values:
        return dict(n=0,mean=None,median=None,std=None,p90=None,mean_ci95=None)
    import numpy as np
    ci = None
    if len(values)>=5:
        rng = np.random.default_rng(1749)
        means = np.mean(rng.choice(values,(2000,len(values)),replace=True),axis=1)
        ci = np.quantile(means,[.025,.975]).tolist()
    return dict(n=len(values),mean=statistics.mean(values),median=statistics.median(values),
                std=statistics.stdev(values) if len(values)>1 else None,
                p90=float(np.quantile(values,.9)),mean_ci95=ci)

def generate_profile(rows,metadata,commands=()):
    groups = defaultdict(list)
    command_map = {c.get('command_id'):c for c in commands if c.get('command_type')!='STOP'}
    for r in rows:
        if not truth(r.get('measurement_valid')) or num(r,'ratio_before') is None or num(r,'ratio_after') is None:
            continue
        pulse,gain = num(r,'pulse_ms'),num(r,'effective_gain_per_ms')
        if pulse is None or pulse<=0 or gain is None:
            continue
        direction = r.get('zoom_direction')
        if (direction=='zoom_in' and gain<=0) or (direction=='zoom_out' and gain>=0):
            continue
        c = command_map.get(r.get('command_id'))
        # A profile requires independently logged, acknowledged motor operations.
        if c is None or c.get('status')!='ACKNOWLEDGED':
            continue
        actual = num(c,'actual_duration_ms')
        if actual is None or abs(actual-pulse)>max(20,pulse*.25):
            continue
        key = (r.get('region'),direction,pulse)
        groups[key].append(r)
    regions = []
    for (region,direction,pulse),sample in sorted(groups.items()):
        values = [num(r,'effective_gain_per_ms') for r in sample]
        s = stats(values)
        cv = s['std']/abs(s['mean']) if s['std'] is not None and s['mean'] else None
        # Measured conditions, not a probability of correct motor prediction.
        reliable = s['n']>=5 and cv is not None and cv<=.25 and metadata.get('log_drops',0)==0
        positions = [num(r,'zoom_position_before') for r in sample if num(r,'zoom_position_before') is not None]
        regions.append(dict(region=region,direction=direction,pulse_ms=pulse,
                            median_log_gain_per_ms=s['median'],sample_count=s['n'],
                            confidence='empirical_consistent' if reliable else 'insufficient_or_variable',
                            reliable=reliable,statistics=s,
                            measured_position_range=[min(positions),max(positions)] if positions else None,
                            baseline_groups=sorted(set(r.get('baseline_group','') for r in sample))))
    cfg = metadata.get('config',{})
    return dict(profile_version=1,session_id=metadata.get('session_id'),
                camera_identifier=cfg.get('camera_identifier','unknown'),
                zoom_position_available=any(num(r,'zoom_position_before') is not None for r in rows),
                measurement_type='person_bbox_ratio',focus_policy=metadata.get('focus_policy','unknown'),
                max_pulse_ms=cfg.get('max_pulse_ms',500),regions=regions,
                limitations=['Image gain includes posture, distance, focus, detector and RTSP effects.',
                             'Region names are user-confirmed experimental states, not optical magnification.',
                             'Lookup interpolates only locally; verify each pulse with fresh measurements.'])

def estimate_pulse_duration(current_ratio,target_ratio,zoom_state,direction,profile):
    if current_ratio<=0 or target_ratio<=0 or direction not in ('zoom_in','zoom_out'):
        raise ValueError('Invalid ratios or direction')
    desired = math.log(target_ratio/current_ratio)
    if desired==0:
        return dict(duration_ms=0,confidence='at_target',reason='AT_TARGET')
    if (desired>0)!=(direction=='zoom_in'):
        raise ValueError('Direction disagrees with target')
    region = (zoom_state or {}).get('region')
    position = (zoom_state or {}).get('zoom_position')
    candidates = []
    for r in profile.get('regions',[]):
        if not r.get('reliable') or r['direction']!=direction:
            continue
        if region is not None and r['region']==region:
            bounds = r.get('measured_position_range')
            if bounds and (position is None or not bounds[0]<=position<=bounds[1]):
                continue
            candidates.append(r)
        elif region is None and position is not None and r.get('measured_position_range'):
            lo,hi = r['measured_position_range']
            if lo<=position<=hi:
                candidates.append(r)
    if not candidates:
        return dict(duration_ms=None,confidence='unknown',reason='UNKNOWN_STATE_OR_INSUFFICIENT_PROFILE')
    g = statistics.median(r['median_log_gain_per_ms'] for r in candidates)
    if not g or desired/g<=0:
        return dict(duration_ms=None,confidence='unknown',reason='INCONSISTENT_GAIN')
    # Never extrapolate beyond durations actually tested; under-correct and remeasure.
    duration = max(1,min(desired/g,profile.get('max_pulse_ms',500),max(r['pulse_ms'] for r in candidates)))
    return dict(duration_ms=duration,confidence='local_empirical_estimate',reason='REMEASURE_AFTER_PULSE')

def analyze(session):
    session = Path(session)
    meta = json.loads((session/'data/session.json').read_text())
    frames,commands,measurements,trials = [read_table(session,t) for t in ('frames','commands','measurements','trials')]
    valid = [r for r in measurements if truth(r.get('measurement_valid'))]
    profile = generate_profile(measurements,meta,commands)
    for d in ('plots','reports','calibration'):
        (session/d).mkdir(exist_ok=True)
    (session/'calibration/zoom_profile.json').write_text(json.dumps(profile,indent=2,allow_nan=False))
    latency = stats([num(r,'estimated_response_latency_ms') for r in valid])
    ratios = [num(r,'person_ratio_raw') for r in frames if truth(r.get('measurement_valid'))]
    grouped = defaultdict(list)
    for r in valid:
        grouped[(r.get('region'),r.get('zoom_direction'),r.get('pulse_ms'),r.get('baseline_group'))].append(r)
    lines = ['# Zoom Calibration & Profiling Report','',f'Session: {meta["session_id"]}',
             f'Measurements: {len(measurements)}; valid: {len(valid)}; invalid: {len(measurements)-len(valid)}.',
             f'Invalid frames: {sum(not truth(r.get("measurement_valid")) for r in frames)} / {len(frames)}.',
             f'Recording drops: {meta.get("frame_drops",0)}; log drops: {meta.get("log_drops",0)}.',
             '', '## Observation','',
             f'Valid person ratios: {min(ratios):.4f}–{max(ratios):.4f}.' if ratios else 'No valid person ratios measured.',
             'Positions use ptz.getStatus.PTZZoomHD when present; missing values remain unknown.',
             '', '## Calculated metrics','',
             'Command-to-visible-change latency (receive timeline estimate, ms): '+json.dumps(latency),
             '', '|Region|Direction|Pulse ms|Baseline group|N|Median log gain/ms|Std|Mean CI95|',
             '|---|---|---:|---|---:|---:|---:|---|']
    summaries = []
    for key, sample in sorted(grouped.items()):
        s = stats([num(r,'effective_gain_per_ms') for r in sample])
        summaries.append(dict(region=key[0],direction=key[1],pulse_ms=key[2],baseline_group=key[3],statistics=s))
        lines.append('|'+ '|'.join(map(str,(*key,s['n'],s['median'],s['std'],s['mean_ci95'])))+'|')
    lines += ['', '## Region, duration and direction comparisons','',
              'Compare normalized gain only within matched baseline groups, direction and pulse duration. '
              'Different region labels are experimental states; their bbox ratios do not identify focal length.',
              'Mean/median/std and bootstrap mean CI95 are reported per condition when at least five samples exist. '
              'Intervals assume independent trials; repeated measurements of one subject may be correlated.']
    # Describe range of observed condition medians without asserting causality or significance.
    medians = [s['statistics']['median'] for s in summaries if s['statistics']['median'] is not None]
    if medians:
        lines += [f'Observed condition median log gain/ms range: {min(medians):.8g} to {max(medians):.8g}.']
    region_latency = defaultdict(list)
    for r in valid:
        region_latency[r.get('region')].append(num(r,'estimated_response_latency_ms'))
    lines += ['Receive-timeline latency by experimental region: '+json.dumps({k:stats(v) for k,v in region_latency.items()})]
    controller = defaultdict(list)
    for r in trials:
        if r.get('mode')=='target' and truth(r.get('measurement_valid')):
            controller[r.get('strategy')].append(r)
    lines += ['', '## Controller tests','']
    if not controller:
        lines += ['No valid target controller tests; no ranking is possible.']
    for strategy, sample in controller.items():
        lines += [strategy+': '+json.dumps({k:stats([num(r,k) for r in sample]) for k in
                  ('time_to_target_s','command_count','overshoot','settling_time_s','motor_active_ms')})]
    lines += ['', '## Hypothesis','',
              'Differences may reflect sensitivity, startup/stop delays or direction asymmetry. '
              'Backlash and lens nonlinearity require controlled, repeated experiments.',
              '', '## Conclusion','',
              'These data are descriptive. No causal/statistical claim of lens nonlinearity or backlash is made. '
              'No controller is declared superior without replicated matched initial ratios, positions and scene conditions.',
              '', '## Limitations','',
              'Person distance, posture, detector errors, camera movement, clipping, focus/image quality and RTSP latency confound bbox gain. '
              'Center-motion checks cannot detect movement toward the camera or reliably distinguish partial-body detections; '
              'operator must verify full-body visibility and a stationary subject.',
              'RTSP PTS is retained but is not a synchronized camera timestamp. Receive time is not capture time. '
              'Fresh-frame gating and settling reduce backlog effects but do not establish true motor latency.',
              'Motor start/stop timestamps remain unknown; actual_duration_ms is software send-to-STOP duration. '
              'Recording is CFR with a per-frame mapping in recording.csv; drops and irregular arrival must be included in replay.',
              'Focus/AF tracking are unchanged. No synthetic calibration results are included.',
              '', '## Invalid measurement reasons','']
    reasons = defaultdict(int)
    for r in measurements:
        if not truth(r.get('measurement_valid')):
            reasons[r.get('invalid_reason') or 'UNKNOWN'] += 1
    lines += [json.dumps(dict(reasons),indent=2)]
    (session/'reports/report.md').write_text('\n'.join(lines)+'\n')
    (session/'reports/statistics.json').write_text(json.dumps(dict(latency=latency,conditions=summaries),indent=2,allow_nan=False))
    _plots(session,frames,commands,valid,trials)
    return session/'reports/report.md'

def _plots(session,frames,commands,measurements,trials):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    valid_frames = [r for r in frames if truth(r.get('measurement_valid')) and num(r,'timestamp') is not None]
    t0 = min((num(r,'timestamp') for r in valid_frames),default=0)
    def save(name,title,xlabel,ylabel,series,band=False):
        fig,ax = plt.subplots(figsize=(9,4))
        count = 0
        for label,x,y,style in series:
            pairs = [(a,b) for a,b in zip(x,y) if a is not None and b is not None]
            if pairs:
                a,b = zip(*pairs)
                ax.plot(a,b,style,label=label)
                count += len(pairs)
        if band:
            ax.axhspan(.46,.54,color='green',alpha=.12,label='50% target band: 46–54%')
        ax.set(title=f'{title} (valid n={count})',xlabel=xlabel,ylabel=ylabel)
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=8)
        else:
            ax.text(.5,.5,'Insufficient measured data',ha='center',transform=ax.transAxes)
        fig.tight_layout()
        fig.savefig(session/f'plots/{name}.png',dpi=130)
        plt.close(fig)
    times = [num(r,'timestamp')-t0 for r in valid_frames]
    ratios = [num(r,'person_ratio_raw') for r in valid_frames]
    # Command annotations are plotted at true send timestamps, not interpolated motor positions.
    fig,ax = plt.subplots(figsize=(10,4))
    if times:
        ax.plot(times,ratios,label='Raw person ratio')
    seen = set()
    for c in commands:
        t = num(c,'send_timestamp')
        if t is None:
            continue
        kind = c.get('command_type')
        color = {'zoom_in':'green','zoom_out':'red','STOP':'gray'}.get(kind,'orange')
        ax.axvline(t-t0,color=color,alpha=.35,label=kind if kind not in seen else None)
        seen.add(kind)
    ax.set(title=f'Person ratio and RPC command sends (valid n={len(times)})',xlabel='Receive elapsed time (s)',ylabel='Person height / frame height')
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(session/'plots/01_ratio_time.png',dpi=130); plt.close(fig)
    has_pos = any(num(r,'zoom_position') is not None for r in valid_frames)
    save('02_ratio_position','Person ratio by measured position' if has_pos else 'Person ratio by received frame index',
         'PTZZoomHD (raw API units)' if has_pos else 'Frame index (not optical zoom)', 'Person ratio',
         [('Person ratio',[num(r,'zoom_position' if has_pos else 'frame_id') for r in valid_frames],ratios,'.')])
    names = sorted(set(r.get('region','Unknown') for r in measurements))
    save('03_relative_gain_region','Relative response by experimental region','Region index: '+str(dict(enumerate(names))),'Relative ratio gain',
         [(d,[names.index(r.get('region','Unknown')) for r in measurements if r['zoom_direction']==d],
           [num(r,'relative_gain') for r in measurements if r['zoom_direction']==d],'.') for d in ('zoom_in','zoom_out')])
    save('04_pulse_gain','Pulse response by region/direction','Requested pulse duration (ms)','Log ratio gain',
         [(region+'/'+d,[num(r,'pulse_ms') for r in measurements if r['region']==region and r['zoom_direction']==d],
           [num(r,'log_ratio_gain') for r in measurements if r['region']==region and r['zoom_direction']==d],'.')
          for region in names for d in ('zoom_in','zoom_out')])
    save('05_direction','Direction comparison','Before person ratio','Log gain / requested ms',
         [(d,[num(r,'ratio_before') for r in measurements if r['zoom_direction']==d],
           [num(r,'effective_gain_per_ms') for r in measurements if r['zoom_direction']==d],'.') for d in ('zoom_in','zoom_out')])
    lat = [num(r,'estimated_response_latency_ms') for r in measurements]
    fig,ax = plt.subplots(figsize=(9,4))
    values = [v for v in lat if v is not None]
    if values:
        ax.hist(values,bins=min(20,len(values)),label='Receive-timeline latency estimate'); ax.legend()
    else:
        ax.text(.5,.5,'No measured latency samples',ha='center',transform=ax.transAxes)
    ax.set(title=f'Command-to-visible-change latency (n={len(values)})',xlabel='Estimated latency (ms)',ylabel='Trial count')
    fig.tight_layout(); fig.savefig(session/'plots/06_latency.png',dpi=130); plt.close(fig)
    target_ids = {r['trial_id'] for r in trials if r.get('mode')=='target'}
    save('07_target_convergence','Target convergence','Receive elapsed time (s)','Person ratio',
         [(trial,[num(r,'timestamp')-t0 for r in valid_frames if r.get('trial_id')==trial],
           [num(r,'person_ratio_raw') for r in valid_frames if r.get('trial_id')==trial],'-') for trial in sorted(target_ids)],True)
    save('08_controller_comparison','Controller trials; inspect baseline equivalence','Initial person ratio','Time to target (s)',
         [(s,[num(r,'initial_ratio') for r in trials if r.get('strategy')==s and truth(r.get('measurement_valid'))],
           [num(r,'time_to_target_s') for r in trials if r.get('strategy')==s and truth(r.get('measurement_valid'))],'.')
          for s in ('fixed','coarse_to_fine','adaptive')])
