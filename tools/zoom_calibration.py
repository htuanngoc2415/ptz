#!/usr/bin/env python3
"""Collect passive lens samples and build a measured, camera-bound monotonic LUT.

Position motors manually before sampling. Ratios are annotations of the original
uncropped image supplied by the operator; visual stability/fixed distance must be
explicitly confirmed. This tool never issues motor/config writes.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camera.dahua_rpc import ZoomRPC
from camera.zoom_prediction import ZoomCalibration, finite


def build(rows, heldout, ratio_kind):
    key = 'measured_'+ratio_kind+'_ratio'
    if len(rows) < 4 or not heldout:
        raise ValueError('Need >=3 fit samples plus >=1 explicitly held-out sample')
    base = rows[0]
    for row in rows:
        if (row.get('camera_id') != base.get('camera_id') or
            row.get('reference_target_id') != base.get('reference_target_id') or
            row.get('fixed_distance') is not True or row.get('stable') is not True or
            not row.get('timestamp') or not row.get('image_sha256')):
            raise ValueError('Mixed camera/reference or missing measured provenance/stability')
        if not 0 < finite(row.get(key), key) <= 1:
            raise ValueError('Missing/invalid original-frame ratio')
    if any(i < 0 or i >= len(rows) for i in heldout):
        raise ValueError('Invalid held-out row index')
    reference = min(rows, key=lambda r:r['zoom_value_x'])
    converted = [dict(row, magnification=row[key]/reference[key]) for row in rows]
    fit = sorted((r for i,r in enumerate(converted) if i not in heldout), key=lambda r:r['zoom_value_x'])
    validation = [r for i,r in enumerate(converted) if i in heldout]
    data = dict(schema_version=1, coordinate='zoom_value_x', camera_id=base['camera_id'],
                reference_target_id=base['reference_target_id'], fixed_distance=True,
                timestamp=datetime.now().astimezone().isoformat(), ratio_kind=ratio_kind,
                magnification_source='relative_original_frame_bbox_height', samples=fit)
    model = ZoomCalibration(data)
    data['holdout_samples'] = validation
    data['holdout_validation'] = model.validate_holdout(validation)
    return data


def collect(args):
    if not args.fixed_distance or not args.visual_stable:
        raise ValueError('Confirm --fixed-distance and --visual-stable for the same reference')
    if args.person_ratio is None and args.face_ratio is None:
        raise ValueError('At least one original-frame measured ratio required')
    for ratio in (args.person_ratio, args.face_ratio):
        if ratio is not None and not 0 < finite(ratio, 'ratio') <= 1:
            raise ValueError('Ratio outside (0,1]')
    # Reject old image annotations: telemetry must describe this image, not a past position.
    if not 0 <= time.time()-args.image.stat().st_mtime <= 30:
        raise ValueError('Use an original frame saved within the last 30 seconds at this stable position')
    cfg = json.loads(args.config.read_text())
    if cfg.get('ptz_backend', 'dahua') != 'dahua':
        raise ValueError('Dahua-only diagnostic; use tools/check_camera.py for HANET')
    rpc = ZoomRPC(cfg['ptz_ip'],cfg.get('ptz_port',80),cfg['ptz_username'],cfg['ptz_password'],
                  channel=cfg.get('ptz_channel',0),read_only=True)
    try:
        info = rpc._call('magicBox.getSystemInfo', {})
        camera_id = (info.get('params') or {}).get('serialNumber')
        if not camera_id:
            raise ValueError('Camera identity unavailable')
        observations = []
        for _ in range(5):
            s = rpc.status_raw()['params']['status']
            observations.append(s)
            time.sleep(.15)
        values = [finite(s.get('ZoomValue'),'ZoomValue')/10 for s in observations]
        if max(values)-min(values) > .1 or any(s.get('ZoomStatus') != 'Idle' or s.get('FocusStatus') != 'Idle' for s in observations):
            raise ValueError('Zoom/focus not observed idle and stable')
        last = observations[-1]
        return dict(requested_zoom_position=args.requested_position,
            actual_zoom_position=last.get('PTZZoomHD'), actual_position_coordinate='PTZZoomHD',
            zoom_value_x=values[-1], measured_face_ratio=args.face_ratio,
            measured_person_ratio=args.person_ratio, reference_target_id=args.reference,
            camera_id=camera_id, timestamp=datetime.now().astimezone().isoformat(),
            camera_motion_state=last.get('ZoomStatus'), focus_state=last.get('FocusStatus'),
            stable=True, fixed_distance=True, visual_stability_source='operator_confirmed',
            ratio_source='operator_annotation_original_uncropped_frame',
            image_sha256=hashlib.sha256(args.image.read_bytes()).hexdigest(),
            image_timestamp=datetime.fromtimestamp(args.image.stat().st_mtime).astimezone().isoformat())
    finally:
        rpc.close(stop_motors=False)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action',required=True)
    s = sub.add_parser('sample')
    s.add_argument('--config',type=Path,default=ROOT/'camera.json')
    s.add_argument('--output',type=Path,required=True)
    s.add_argument('--reference',required=True)
    s.add_argument('--image',type=Path,required=True)
    s.add_argument('--person-ratio',type=float)
    s.add_argument('--face-ratio',type=float)
    s.add_argument('--requested-position',type=float,help='Known normalized PositionABS command, optional metadata only')
    s.add_argument('--fixed-distance',action='store_true')
    s.add_argument('--visual-stable',action='store_true')
    b = sub.add_parser('build')
    b.add_argument('--samples',type=Path,required=True)
    b.add_argument('--holdout',required=True,help='Comma-separated zero-based sample row indices')
    b.add_argument('--ratio-kind',choices=['face','person'],required=True)
    b.add_argument('--output',type=Path,required=True)
    b.add_argument('--max-relative-error',type=float,default=.10)
    args = p.parse_args()
    if args.action == 'sample':
        row = collect(args)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with args.output.open('a') as stream:
            stream.write(json.dumps(row)+'\n')
    else:
        data = build([json.loads(line) for line in args.samples.read_text().splitlines() if line.strip()],
                     set(map(int,args.holdout.split(','))),args.ratio_kind)
        error = data['holdout_validation']['max_relative_error']
        if not 0 < args.max_relative_error <= .25 or error > args.max_relative_error:
            raise ValueError(f'Calibration rejected: holdout relative error={error}')
        data['validation_max_relative_error'] = args.max_relative_error
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(data,indent=2)+'\n')
        print(json.dumps(data['holdout_validation']))

if __name__ == '__main__':
    main()
