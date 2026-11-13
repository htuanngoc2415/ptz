"""Deterministic angular plant; assumptions are NOT HANET calibration. No camera I/O."""
import argparse
import json
from dataclasses import dataclass
from types import SimpleNamespace
import config
from control.pan_policy import plan_pan

@dataclass
class Motor:
    position: float = 0.
    velocity: float = 0.
    demand: float = 0.
    pending: tuple = None
    latency: float = .04
    acceleration: float = 100.
    deceleration: float = 160.
    # Deliberately nonlinear degrees/s; replace only using measured calibration.
    rates = {1:8., 2:19., 3:35.}
    def command(self, now, speed, sign):
        self.pending = (now+self.latency, self.rates[speed]*sign)
    def stop(self, now):
        self.pending = (now+self.latency, 0.)
    def step(self, now, dt):
        if self.pending and now >= self.pending[0]:
            self.demand = self.pending[1]; self.pending = None
        rate = self.deceleration if abs(self.demand) < abs(self.velocity) else self.acceleration
        self.velocity += max(-rate*dt,min(rate*dt,self.demand-self.velocity))
        self.position += self.velocity*dt


def simulate(offset, scenario, adaptive):
    cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
    cfg.AUTO_PAN_TILT_ADAPTIVE = adaptive
    m = Motor(); dt=.01; horizon=12.; fov=60.; axis=None
    active=False; deadline=0.; ready=0.; next_frame=0.; starts=stops=violations=0
    errors=[]; intervals=[]; durations=[]; previous_start=None; centered=None; overshoot=0.
    history=[]; measured_error=offset; missing=manual=busy=False; age=0.
    for step in range(int(horizon/dt)):
        t=step*dt
        velocity = 3. if scenario=='moving' else 12. if scenario=='fast' else 6. if scenario=='sudden_stop' and t<3 else 0.
        if not history: target=offset*fov
        target += velocity*dt
        error=(target-m.position)/fov; history.append(error); errors.append(abs(error))
        missing=scenario=='missing' and t>=2
        manual=scenario=='manual' and t>=2
        busy=scenario=='busy' and 1<=t<4
        if t >= next_frame:
            next_frame=t+1/9
            age=.5 if scenario=='delayed' and t>=2 else .04
            measured_error=history[max(0,len(history)-1-round(age/dt))]
            box=None if missing else (500+measured_error*1000-30,400,500+measured_error*1000+30,600)
            p=plan_pan(box,(1000,1000),cfg,previous_axis=axis,age=age,
                manual=manual,busy=busy,settling=t<ready,active=active)
            if active and p.action=='STOP' and p.reason not in ('MOTOR_BUSY','COOLDOWN'):
                m.stop(t);active=False;stops+=1;ready=t+cfg.AUTO_PAN_TILT_SETTLE
            if not active and p.action=='MOVE':
                if missing or manual or busy or age>cfg.MAX_CONTROL_OBSERVATION_AGE: violations+=1
                m.command(t,p.speed,1 if p.direction=='pan_right' else -1)
                active=True;deadline=t+p.duration;starts+=1;axis=p.axis;durations.append(p.duration)
                if previous_start is not None: intervals.append(t-previous_start)
                previous_start=t
        if active and t>=deadline:
            m.stop(t);active=False;stops+=1;ready=t+cfg.AUTO_PAN_TILT_SETTLE
        m.step(t,dt)
        if centered is None and abs(error)<=cfg.AUTO_PAN_TILT_RELEASE: centered=t
        overshoot=max(overshoot,max(0.,-error))
    if active: m.stop(horizon);stops+=1
    return dict(offset=offset,scenario=scenario,adaptive=adaptive,centering_seconds=centered,
        mean_abs_error=sum(errors)/len(errors),final_error=errors[-1],overshoot=overshoot,
        command_rate=starts/horizon,starts=starts,stops=stops,safety_violations=violations,
        requested_duty_cycle=sum(durations)/horizon,
        mean_start_interval=sum(intervals)/len(intervals) if intervals else None,
        assumed_command_latency=m.latency,ownership_blocked_seconds=3 if scenario=='busy' else 0)


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);args=p.parse_args()
    rows=[simulate(offset,scenario,adaptive) for offset in (.05,.15,.30,.50)
          for scenario in ('stationary','moving','fast','sudden_stop','missing','delayed','busy','manual')
          for adaptive in (False,True)]
    with open(args.output,'w') as f: json.dump(dict(model='Synthetic, not hardware calibration',
        parameters=dict(dt=.01,horizon=12,fov_degrees=60,frame_hz=9,latency=.04,
        acceleration=100,deceleration=160,speed_map=Motor.rates),results=rows),f,indent=2)
if __name__=='__main__':main()
