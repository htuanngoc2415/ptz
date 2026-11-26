"""Independent per-frame localization metrics; no labels => no accuracy claim."""
import argparse,json,math
from pathlib import Path
from ai.reid_target import iou


def evaluate(records,labels,source_sha):
    if labels.get('source_sha256')!=source_sha or labels.get('source')!='independent_manual' or not labels.get('reviewer'):
        raise ValueError('Source-bound independent manual labels required')
    indexed={r['frame_id']:r for r in records};seen=set();groups={}
    for label in labels['frames']:
        fid=label['frame_id']
        if fid in seen or fid not in indexed or not label.get('reviewed'):raise ValueError('Duplicate/unreviewed/unknown frame')
        seen.add(fid);row=indexed[fid]
        if abs(row['timestamp']-label['timestamp'])>1e-5:raise ValueError('Timestamp mismatch')
        if label['visibility'] not in ('VISIBLE','PARTIAL'):continue
        box=label['bbox']
        if len(box)!=4 or not all(math.isfinite(v) for v in box) or box[2]<=box[0] or box[3]<=box[1]:raise ValueError('Invalid GT bbox')
        stratum=label['camera_motion']
        if stratum not in ('static','moving','unknown'):raise ValueError('Unknown camera stratum')
        for variant in ('A','B','C'):
            result=row.get(variant)
            if result is None:continue
            key=stratum+':'+variant;g=groups.setdefault(key,dict(visible=0,localized=0,ious=[],reviewed_wrong=0))
            pred=result.get('bbox');score=iou(box,pred) if pred else 0.
            g['visible']+=1;g['localized']+=int(score>=.5);g['ious'].append(score)
    return {key:dict(reviewed_visible_frames=g['visible'],localization_recall_iou50=g['localized']/g['visible'],
        mean_iou=sum(g['ious'])/len(g['ious']),identity_switch_count=None,lost_target_duration=None,
        ptz_control_interruption=None,limitation='Sparse labels and box IoU cannot certify identity switches or continuous durations') for key,g in groups.items()}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--labels',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();summary=json.loads((a.run/'summary.json').read_text())
    rows=[json.loads(l) for l in (a.run/'decisions.jsonl').read_text().splitlines()]
    with a.out.open('x') as f:json.dump(evaluate(rows,json.loads(a.labels.read_text()),summary.get('source_capture_sha256') or summary.get('source_sha256')),f,indent=2)

if __name__=='__main__':main()
