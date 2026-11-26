"""Offline command-count comparison on IDENTICAL recorded observations.
B/D use the pre-existing experimental adaptive-pulse setting, NOT a calibrated
velocity controller. No simulation of motor physics or fabricated identity metrics.
"""
import sys,json,argparse,collections
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
from control.pan_policy import plan_pan

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--observations',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    rows=[json.loads(l) for l in a.observations.open()];reports={}
    for label,adaptive,local in [('A',False,False),('B_pulse_proxy',True,False),('C',False,True),('D_pulse_proxy',True,True)]:
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()});cfg.AUTO_PAN_TILT_ADAPTIVE=adaptive
        reasons=collections.Counter();durations=[];speeds=[];axis=None
        for r in rows:
            is_local=r.get('source')=='LOCAL_VISUAL_ONLY'
            if is_local:
                # No calibrated identity admission for local flow exists yet.
                reasons['LOCAL_NOT_AUTHORIZED' if local else 'LOCAL_DISABLED']+=1;continue
            plan=plan_pan(r.get('bbox'),r.get('shape',[480,704]),cfg,previous_axis=axis,
                state=r.get('state','SEARCHING'),verified=r.get('verified',False),age=r.get('age_s',float('inf')))
            axis=plan.axis;reasons[plan.reason]+=1
            if plan.action=='MOVE':durations.append(plan.duration);speeds.append(plan.speed)
        reports[label]=dict(decision_counts=dict(reasons),move_proposals=len(durations),speed_histogram=dict(collections.Counter(speeds)),
            proposed_pulse_mean_s=sum(durations)/len(durations) if durations else None,
            physical_recenter_time=None,overshoot=None,correct_target_time=None,id_switches=None,physical_speed=None,
            note='Independent policy proposals; not scheduled/actual commands. B/D uncalibrated pulse proxies, not implemented calibrated controllers.')
    a.output.write_text(json.dumps(reports,indent=2))
if __name__=='__main__':main()
