"""Evaluate independently reviewed bbox labels, never tracker-derived GT."""
import argparse,json,math
from pathlib import Path
import numpy as np
from tools.phase3_local import iou


def evaluate(run, labels):
    summary=json.loads((run/'summary.json').read_text())
    predictions=[json.loads(s) for s in (run/'observations.jsonl').read_text().splitlines()]
    by_id={r['frame_id']:r for r in predictions}
    if labels.get('schema')!='phase3_manual_bbox_v1' or not labels.get('reviewer','').strip():raise ValueError('Independent manual reviewer required')
    if labels.get('source_sha256')!=summary['source_sha256'] or labels.get('width')!=summary['coordinate_width']:raise ValueError('Source/coordinate mismatch')
    seen=set();valid=[]
    for r in labels['rows']:
        fid=r['frame_id']
        if fid in seen or fid not in by_id:raise ValueError('Duplicate/unknown frame')
        seen.add(fid)
        if not math.isfinite(r['media_timestamp']) or abs(r['media_timestamp']-by_id[fid]['media_timestamp'])>1e-6:raise ValueError('PTS mismatch')
        if not r.get('reviewed'):continue
        if r['identity']!='target' or r['visibility']=='UNKNOWN':continue
        b=r.get('bbox')
        if r['visibility'] in ('visible','partial'):
            if b is None or len(b)!=4 or not np.isfinite(b).all() or not (0<=b[0]<b[2]<=labels['width'] and 0<=b[1]<b[3]<=labels['height']):raise ValueError('Invalid visible bbox')
        elif r['visibility'] in ('full_occlusion','out_of_frame'):
            if b is not None:raise ValueError('Invisible target must not have guessed bbox')
        else:raise ValueError('Invalid visibility')
        valid.append(r)
    result=dict(reviewed_target_frames=len(valid),ground_truth_basis='manual_review_external_not_tracker',
        recall_definition='center error <= 0.1 * GT bbox diagonal; missing prediction counts failure',
        drift_definition='IoU < .2 in visible evaluated frame; episode count only on contiguous reviewed frames',
        takeover_definition='prediction IoU with annotated distractor > .3 and > target IoU; unlabelled distractors unknown',
        anchor_frames_excluded=True,variants={})
    for variant in summary['variants']:
        groups={}
        for name in ['all','static','moving','unknown']:
            subset=[r for r in valid if name=='all' or ('static' if r['camera_motion']=='static' else 'unknown' if r['camera_motion']=='UNKNOWN' else 'moving')==name]
            errors=[];ious=[];success=visible=missing=occ=occ_valid=0;drifts=takeovers=0;last_drift=last_takeover=None;takeover_evaluable=0
            for r in sorted(subset,key=lambda r:r['frame_id']):
                pred=by_id[r['frame_id']]['outputs'][variant]
                # All variants share anchor timestamps; don't score manual initializations.
                if by_id[r['frame_id']]['outputs']['B']['tracking_status']=='ANCHORED':continue
                b=pred['bbox'];gt=r.get('bbox')
                if gt is None:
                    occ+=1;occ_valid+=b is not None;continue
                visible+=1
                if b is None:missing+=1;continue
                e=float(np.linalg.norm((np.array(b[:2])+b[2:]-np.array(gt[:2])-gt[2:])/2));overlap=iou(b,gt)
                errors.append(e);ious.append(overlap);success+=e<=.1*np.linalg.norm(np.array(gt[2:])-gt[:2])
                if overlap<.2:
                    drifts+=last_drift!=r['frame_id']-1;last_drift=r['frame_id']
                if 'distractor_bboxes' in r:
                    takeover_evaluable+=1
                    if any(iou(b,d)>.3 and iou(b,d)>overlap for d in r['distractor_bboxes']):
                        takeovers+=last_takeover!=r['frame_id']-1;last_takeover=r['frame_id']
            groups[name]=dict(visible_frames=visible,valid_predictions=len(errors),missing_predictions=missing,
                center_error_mean_px=float(np.mean(errors)) if errors else None,center_error_p95_px=float(np.percentile(errors,95)) if errors else None,
                mean_iou=float(np.mean(ious)) if ious else None,localization_recall=success/visible if visible else None,
                drift_episodes=drifts if visible else None,takeover_episodes=takeovers if takeover_evaluable else None,
                distractor_evaluable_frames=takeover_evaluable,invisible_frames=occ,localization_emitted_while_invisible=occ_valid,
                recovery_accuracy=None,wrong_direction_time=None,
                caveat='Sparse review does not measure unreviewed drift/duration. Recovery and direction need contiguous event labels.')
        result['variants'][variant]=groups
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--labels',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    result=evaluate(Path(a.run),json.loads(Path(a.labels).read_text()));Path(a.out).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
if __name__=='__main__':main()
