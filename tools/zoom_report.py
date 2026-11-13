#!/usr/bin/env python3
"""Read-only evidence reports; compare actual A/B CSV rows without speed claims."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re


def legacy_report(camera_log, app_log):
    rows=[]
    for line in camera_log.read_text().splitlines():
        try: rows.append(json.loads(line[line.index('{'):]))
        except (ValueError,json.JSONDecodeError): pass
    requests=[r for r in rows if r.get('event')=='ZOOM_REQUEST_CREATED']
    responses=[r for r in rows if r.get('event')=='ZOOM_RPC_RESPONSE']
    return dict(evidence_source='existing_runtime_logs',camera_log=str(camera_log),app_log=str(app_log),
        zoom_requests=len(requests),requests_with_absolute_target=sum(r.get('zoom_position_requested') is not None for r in requests),
        acknowledged_ptz_start=sum(r.get('rpc_method')=='ptz.start' and r.get('acknowledged') is True for r in responses),
        acknowledged_ptz_stop=sum(r.get('rpc_method')=='ptz.stop' and r.get('acknowledged') is True for r in responses),
        stop_reasons=dict(Counter(re.findall(r'\[ZOOM\] STOP reason=(\w+)',app_log.read_text()))),
        interpretation='Pulse path inferred by joining null target requests with the audited controller branch; old RPC logs omit code.',
        autofocus_interruption='UNVERIFIED: old logs do not establish autonomous firmware AF causality')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--legacy-camera',type=Path)
    p.add_argument('--legacy-app',type=Path)
    p.add_argument('--summary',type=Path,nargs='+')
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.legacy_camera and a.legacy_app:
        result=legacy_report(a.legacy_camera,a.legacy_app)
    elif a.summary:
        result=[dict(source=str(path),sessions=list(csv.DictReader(path.open()))) for path in a.summary]
    else:
        p.error('Provide both legacy logs or --summary A.csv B.csv')
    text=json.dumps(result,indent=2,ensure_ascii=False)+'\n'
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(text)
    print(text)

if __name__=='__main__': main()
