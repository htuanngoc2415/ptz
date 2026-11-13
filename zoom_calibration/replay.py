"""Offline replay/annotation. This module never imports camera control."""
import csv
import json
import statistics
from collections import deque
from pathlib import Path
from .analysis import read_table, num, truth, analyze
from .measurement import gains

def replay_session(source):
    """Recompute filtering and pulse gains using original receive/command timestamps.

    Original files remain intact. No identity redetection is claimed: raw logged
    target association is replayed. GUI Replay video performs fresh YOLO inference.
    """
    source = Path(source)
    frames = read_table(source,'frames')
    commands = read_table(source,'commands')
    meta = json.loads((source/'data/session.json').read_text())
    cfg = meta['config']
    result = []
    history = deque(maxlen=cfg['filter_window'])
    target = None
    for row in frames:
        raw = num(row,'person_ratio_raw')
        if row.get('target_id') != target or not truth(row.get('measurement_valid')):
            history.clear()
        target = row.get('target_id')
        if raw is not None and truth(row.get('measurement_valid')):
            history.append(raw)
            row['person_ratio_filtered_replayed'] = statistics.median(history)
        else:
            row['person_ratio_filtered_replayed'] = None
    originals = {r.get('command_id'):r for r in read_table(source,'measurements')}
    n = cfg['samples_per_step']
    for c in commands:
        if c.get('command_type') not in ('zoom_in','zoom_out'):
            continue
        row = dict(command_id=c['command_id'],trial_id=c.get('trial_id'),measurement_valid=False)
        send,stop,pulse = num(c,'send_timestamp'),num(c,'stop_response_timestamp'),num(c,'requested_duration_ms')
        original = originals.get(c['command_id'],{})
        row.update(region=original.get('region'),baseline_group=original.get('baseline_group'),pulse_ms=pulse,zoom_direction=c['command_type'])
        if send is None or stop is None or not pulse or c.get('status')!='ACKNOWLEDGED':
            row['invalid_reason'] = 'COMMAND_NOT_ACKNOWLEDGED_OR_INCOMPLETE'
        else:
            before = [f for f in frames if num(f,'receive_timestamp') is not None and send-cfg['max_frame_age_s']<num(f,'receive_timestamp')<send][-n:]
            after_time = stop+(cfg['settle_ms']+cfg['wait_ms'])/1000
            after = [f for f in frames if num(f,'receive_timestamp') is not None and after_time<num(f,'receive_timestamp')<after_time+cfg['sample_timeout_s']][:n]
            both = before+after
            if len(before)!=n or len(after)!=n or any(not truth(f.get('measurement_valid')) or num(f,'person_ratio_raw') is None for f in both):
                row['invalid_reason'] = 'INSUFFICIENT_VALID_FRESH_SAMPLES'
            elif len({f.get('target_id') for f in both})!=1:
                row['invalid_reason'] = 'TARGET_CHANGED'
            elif not truth(original.get('measurement_valid')):
                row['invalid_reason'] = 'ORIGINAL_TRIAL_INVALID_OR_UNMEASURED'
            else:
                b = [num(f,'person_ratio_raw') for f in before]
                a = [num(f,'person_ratio_raw') for f in after]
                if (max(b)-min(b))/statistics.median(b)>cfg['stability_fraction'] or (max(a)-min(a))/statistics.median(a)>cfg['stability_fraction']:
                    row['invalid_reason'] = 'UNSTABLE_SAMPLES'
                else:
                    row.update(measurement_valid=True,ratio_before=statistics.median(b),ratio_after=statistics.median(a),
                               **gains(statistics.median(b),statistics.median(a),pulse))
        result.append(row)
    out = source/'reports/replay.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(dict(source_session=meta['session_id'],mode='logged_target_measurement_replay',
                                  frames=frames,measurements=result),indent=2,allow_nan=False))
    return out

def annotate(session):
    """CFR output with same frame index mapping as original; timing lives in CSV."""
    import cv2
    session = Path(session)
    frames = {r['frame_id']:r for r in read_table(session,'frames')}
    mapping = read_table(session,'recording')
    outputs = []
    for source in sorted((session/'video').glob('original*.mp4')):
        segment = '1' if source.stem=='original' else source.stem.split('_')[-1]
        rows = {int(r['recording_index'].split(':')[-1]):r for r in mapping if r.get('recording_index','').startswith(segment+':') and not truth(r.get('dropped'))}
        cap = cv2.VideoCapture(str(source))
        writer = None
        out = source.with_name(source.name.replace('original','annotated'))
        try:
            if not cap.isOpened():
                raise RuntimeError('Cannot open original video')
            fps = cap.get(cv2.CAP_PROP_FPS) or 20
            index = 0
            while True:
                ok,im = cap.read()
                if not ok:
                    break
                if writer is None:
                    h,w = im.shape[:2]
                    writer = cv2.VideoWriter(str(out),cv2.VideoWriter_fourcc(*'mp4v'),fps,(w,h))
                    if not writer.isOpened():
                        raise RuntimeError('Annotated encoder unavailable')
                m = rows.get(index,{})
                f = frames.get(m.get('frame_id'),{})
                if num(f,'bbox_x1') is not None:
                    b = [int(float(f[k])) for k in ('bbox_x1','bbox_y1','bbox_x2','bbox_y2')]
                    cv2.rectangle(im,tuple(b[:2]),tuple(b[2:]),(0,220,80) if truth(f.get('measurement_valid')) else (0,150,255),2)
                text = f'frame {m.get("frame_id","unknown")} received {m.get("receive_timestamp","unknown")} ratio {f.get("person_ratio_raw","unknown")} {f.get("current_command","")}'
                cv2.putText(im,text,(10,24),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
                writer.write(im); index += 1
            outputs.append(str(out))
        finally:
            cap.release()
            if writer:
                writer.release()
    if not outputs:
        raise ValueError('No original MP4 recordings in session')
    return outputs
