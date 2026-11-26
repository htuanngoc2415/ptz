"""Deterministic policy contract benchmark; NOT real tracking accuracy."""
import argparse,json,copy
from types import SimpleNamespace
from pathlib import Path
import numpy as np
import config
from tools.occlusion_association import Observation,OcclusionAssociation
from tools.occlusion_benchmark import baseline


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    ref=np.zeros(512);ref[0]=1;other=np.zeros(512);other[1]=1
    cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});cfg.REID_SEARCH_INTERVAL=0.
    scenarios=['front_clear','stationary_front','moving_front','partial','full_occlusion','crossing_separation','similar_clothing','moving_ptz','detection_missing']
    summary={};all_records=[]
    for scenario in scenarios:
        a=baseline(cfg,ref);b=OcclusionAssociation(ref,scenario);records=[]
        for fid in range(23):
            box=(100,50,170,250);offset=(fid-3)*2 if scenario=='moving_front' and fid>=3 else 0
            box=(box[0]+offset,box[1],box[2]+offset,box[3])
            target=dict(candidate_id='target',track_id=1,bbox=box,confidence=.95,reid_score=1.,embedding=ref.tolist())
            distractor=dict(candidate_id='other',track_id=2,bbox=(125+offset,50,195+offset,250),confidence=.95,reid_score=0.,embedding=other.tolist())
            people=[target] if fid<3 else [target,distractor];visible=True
            if fid>=3:
                if scenario=='similar_clothing':distractor.update(embedding=ref.tolist(),reid_score=1.)
                if scenario=='full_occlusion':people=[distractor];visible=False
                if scenario=='detection_missing':people=[]
                if scenario=='crossing_separation' and fid>=12:
                    target['track_id']=2;distractor['track_id']=1;distractor['bbox']=(350,50,420,250)
            a.frame_id=fid;a.frame_age_ms=0.
            selected=a.select_person(None,copy.deepcopy(people),fid*.1);a.finish_frame(selected,None)
            ar=dict(bbox=selected['bbox'] if selected else None,selected_candidate=selected['candidate_id'] if selected else None,control_eligible=selected is not None)
            observations=[]
            for p in people:
                embedding=p['embedding'];parts={'0:0':embedding,'1:0':embedding}
                support=.5
                if scenario=='partial' and fid>=3:support=.2
                observations.append(Observation(p['candidate_id'],p['track_id'],p['bbox'],p['confidence'],embedding,parts,support,'isolated_parts',True))
            br=b.observe(scenario,fid,fid*.1,fid*.1,observations,
                camera_motion='MOVING' if scenario=='moving_ptz' and fid>=3 else 'STATIC',
                camera_transform=[[1,0,0],[0,1,0]] if scenario=='moving_ptz' else None)
            if fid>=3:records.append(dict(frame_id=fid,visible=visible,A=ar,B=br))
        stats={}
        for name in ('A','B'):
            visible=sum(r['visible'] for r in records)
            correct=sum(r['visible'] and r[name]['selected_candidate']=='target' for r in records)
            wrong=[r[name]['selected_candidate'] not in (None,'target') for r in records]
            stats[name]=dict(localization_recall=correct/visible if visible else None,
                confirmed_switch_episodes=sum(v and (i==0 or not wrong[i-1]) for i,v in enumerate(wrong)),
                visible_target_unlocalized_s=(visible-correct)*.1,
                shadow_control_interruption_s=sum(not r[name]['control_eligible'] for r in records)*.1)
        summary[scenario]=stats
        all_records.append(dict(scenario=scenario,frames=records))
    (args.out/'summary.json').write_text(json.dumps(dict(synthetic=True,physical_commands=0,
        warning='Ground truth and part evidence are scripted. These metrics test policy contracts only; not detector, segmentation or OSNet accuracy.',scenarios=summary),indent=2))
    (args.out/'frames.json').write_text(json.dumps(all_records,indent=2))

if __name__=='__main__':main()
