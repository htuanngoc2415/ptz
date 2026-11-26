"""Early embedding request before geometric overlap; never an identity promotion."""
import math


class ApproachRisk:
    def __init__(self):self.previous={};self.binding=None
    def assess(self,target,persons,binding,timestamp):
        if self.binding!=binding:self.previous={};self.binding=binding
        if target is None:self.previous={};return False
        box=target['bbox'];cx=(box[0]+box[2])/2;cy=(box[1]+box[3])/2
        scale=max(1.,box[2]-box[0],box[3]-box[1]);current={};risk=False
        for p in persons:
            if p is target:continue
            b=p['bbox'];distance=math.hypot((b[0]+b[2])/2-cx,(b[1]+b[3])/2-cy)/scale
            key=p.get('track_id');old=self.previous.get(key)
            if old and 0<timestamp-old[1]<=.5 and distance<1.25 and distance<old[0]-.03:risk=True
            current[key]=(distance,timestamp)
        self.previous=current
        return risk
