#!/usr/bin/env python3
"""Offline join of decoded frame arrivals to same-session PTZ command logs.

Writes a separate JSONL sidecar. No camera connection and no control decisions.
Expected motion/settling are command proxies, never physical motor telemetry.
"""
import argparse,datetime,json,statistics,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from camera_motion_audit import load_camera,command_intervals,exposure_at


def enrich(row,events,intervals,clock_offset):
    result=dict(row)
    result.update(ptz_command_active=None,expected_camera_moving=None,zoom_transition=None,
                  settling_estimate=None,command_join_reason='CLOCK_OR_COVERAGE_UNVERIFIED')
    observed=row.get('observation_monotonic');wall=row.get('observation_wall_time')
    if observed is None or wall is None or not events:return result
    offset=datetime.datetime.fromisoformat(wall).timestamp()-observed
    t=row['receive_timestamp']
    if abs(offset-clock_offset)>1 or not events[0]['monotonic_ns']/1e9<=t<=events[-1]['monotonic_ns']/1e9:return result
    # Unmatched starts are not silently treated as standstill.
    _,pending=command_intervals([e for e in events if e['monotonic_ns']/1e9<=t])
    active=bool(pending)
    bucket='command_active' if active else exposure_at(t,intervals)
    zoom=any('Zoom' in c for c in pending) or any('Zoom' in c and b<=t<b+.8 for _,b,c in intervals)
    result.update(ptz_command_active=active,expected_camera_moving=active or bucket=='post_stop_0_300ms',
                  zoom_transition=zoom,settling_estimate=bucket,command_join_reason='ARRIVAL_TIME_JOIN',
                  command_time_basis='same_boot_monotonic; capture time unknown',
                  expected_motion_is_proxy=True,settling_thresholds_seconds=[.3,.8])
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle',type=Path,required=True);p.add_argument('--camera-logs',type=Path,nargs='+',required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    source=a.bundle/'frames.jsonl'
    if a.out.resolve()==source.resolve():p.error('output must be a separate sidecar')
    events=load_camera(a.camera_logs);intervals,_=command_intervals(events)
    offset=statistics.median(datetime.datetime.fromisoformat(r['timestamp']).timestamp()-r['monotonic_ns']/1e9 for r in events)
    with source.open() as src,a.out.open('w') as dst:
        for line in src:dst.write(json.dumps(enrich(json.loads(line),events,intervals,offset))+'\n')


if __name__=='__main__':main()
