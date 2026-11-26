"""One supervised pulse, mock by default. Never auto-discovers hardware capability.

Angular/video measurements must use a common clock and an external calibrated
reference. RPC ACK timestamps are not exposure or physical motor timestamps.
"""
import argparse,csv,json,math,sys,time,signal,threading,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
DIRECTIONS=('pan_left','pan_right','tilt_up','tilt_down')

class MockMotor:
    def command(self,direction,speed):return True,'MOCK START'
    def stop(self):return True,'MOCK STOP'
    def close(self):return True

def validate(direction,speed,duration):
    if direction not in DIRECTIONS or type(speed) is not int or not 1<=speed<=8:
        raise ValueError('Supported driver commands: pan/tilt; integer step 1..8')
    if not math.isfinite(duration) or not .02<=duration<=.5:
        raise ValueError('Pulse must be 0.02..0.5 seconds')

def pulse(motor,direction,speed,duration,cancel=None,emit=lambda x:None):
    validate(direction,speed,duration)
    cancel=cancel or threading.Event();events=[];logging_errors=[]
    def record(event,**kw):
        r=dict(event=event,monotonic_s=time.monotonic(),wall_time_ns=time.time_ns(),**kw)
        events.append(r)
        try:emit(r)
        except Exception as exc:logging_errors.append(type(exc).__name__)
        return r['monotonic_s']
    ok=False;stopped=False;error=None
    try:
        if cancel.is_set():raise RuntimeError('Cancelled before START')
        start=record('START_REQUEST',direction=direction,step=speed)
        ok,_=motor.command(direction,speed)
        ack=record('START_RETURN',acknowledged=bool(ok),rpc_ms=(time.monotonic()-start)*1000)
        if not ok:raise RuntimeError('START unconfirmed; STOP required')
        # Bound requested time from START request, not from delayed ACK.
        cancel.wait(max(0.,start+duration-time.monotonic()))
    except BaseException as exc:
        error=type(exc).__name__
    finally:
        # All exceptions including Ctrl-C attempt STOP. Backend retains its latch.
        stop=record('STOP_REQUEST',cancelled=cancel.is_set())
        try:stopped,_=motor.stop()
        except Exception as exc:error=error or type(exc).__name__
        record('STOP_RETURN',acknowledged=bool(stopped),rpc_ms=(time.monotonic()-stop)*1000)
    return dict(direction=direction,step=speed,requested_duration_s=duration,
                start_ack=bool(ok),stop_ack=bool(stopped),error=error,events=events,
                physical_motor_stopped=None,angular_displacement_deg=None,
                startup_delay_s=None,acceleration_deg_s2=None,deceleration_deg_s2=None,
                video_timestamps=None,logging_errors=logging_errors)

def measured_motion(rows,start_s,stop_s):
    """External angle samples; no inferred encoder/FOV or fabricated motor speed."""
    import numpy as np
    t=np.array([float(r['t_s']) for r in rows]);a=np.array([float(r['angle_deg']) for r in rows])
    if len(t)<5 or not np.isfinite(t).all() or not np.isfinite(a).all() or not np.all(np.diff(t)>0):
        raise ValueError('Need >=5 finite strictly ordered samples')
    if not (math.isfinite(start_s) and math.isfinite(stop_s) and t[0]<=start_s<stop_s<t[-1]):
        raise ValueError('Samples must bracket START and STOP on the same clock')
    # Unwrap pan positions crossing 360; angles must already be calibrated.
    a=np.rad2deg(np.unwrap(np.deg2rad(a)));v=np.gradient(a,t);acc=np.gradient(v,t)
    active=(t>=start_s)&(t<=stop_s);moving=np.flatnonzero(active & (abs(v)>2.))
    if active.sum()<2:raise ValueError('Insufficient samples during pulse')
    return dict(measurement_kind='external_angle_samples',samples=len(t),
        angular_displacement_deg=float(np.interp(stop_s,t,a)-np.interp(start_s,t,a)),
        total_displacement_including_coast_deg=float(a[-1]-np.interp(start_s,t,a)),
        mean_speed_deg_s=float(np.mean(abs(v[active]))),peak_speed_deg_s=float(max(abs(v[active]))),
        startup_delay_s=float(t[moving[0]]-start_s) if len(moving) else None,
        acceleration_abs_peak_deg_s2=float(max(abs(acc[active]))),
        post_stop_deceleration_abs_peak_deg_s2=float(max(abs(acc[t>stop_s]))),
        video_timestamps=[r.get('video_pts_s') for r in rows],
        caveat='Numerical derivatives, noise-sensitive; threshold onset 2 deg/s. Not firmware telemetry.')

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--direction',choices=DIRECTIONS,default='pan_right');p.add_argument('--speed',type=int,default=1)
    p.add_argument('--duration',type=float,default=.12);p.add_argument('--physical',action='store_true')
    p.add_argument('--previous-low-speed',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--measurements',type=Path);p.add_argument('--start-s',type=float);p.add_argument('--stop-s',type=float)
    args=p.parse_args();validate(args.direction,args.speed,args.duration)
    if args.output.suffix != ".json":p.error("Use a .json output path; CSV and JSONL use the same stem")
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Never overwrite earlier operator evidence.
    if args.output.exists() or args.output.with_suffix('.jsonl').exists() or args.output.with_suffix('.csv').exists():p.error('Output exists')
    if args.measurements:
        if args.physical:p.error('Analyze separately from physical motion')
        if args.start_s is None or args.stop_s is None:p.error('Supply --start-s and --stop-s in sample clock')
        with args.measurements.open() as f:result=measured_motion(list(csv.DictReader(f)),args.start_s,args.stop_s)
        args.output.write_text(json.dumps(result,indent=2));return
    device=None
    if args.physical:
        import config
        if config.PTZ_BACKEND!='hanet':p.error('Only audited HANET backend supported')
        device=hashlib.sha256(f'{config.PTZ_IP}:{config.PTZ_PORT}'.encode()).hexdigest()[:20]
        if not sys.stdin.isatty():p.error('Physical mode requires an interactive operator terminal')
        if args.speed>1:
            if not args.previous_low_speed:p.error('Run and visually validate step 1 first')
            prior=json.loads(args.previous_low_speed.read_text())
            if not (prior.get('physical') and prior.get('step')==1 and prior.get('stop_ack') and prior.get('operator_observed_safe') and prior.get('device')==device and prior.get('direction')==args.direction):p.error('Need same-device/direction physical step-1 record with confirmed STOP and operator review')
        print('Close the PTZ GUI and every other motor controller. This tool sends ONE pulse.')
        print('No position feedback: operator must verify a clear movement sector, cable limits and have a hardware/app STOP available.')
        print('Duration is a requested bound, not a hard motor guarantee if network/ACK blocks. Ctrl-C requests STOP.')
        phrase=f'MOVE {args.direction} STEP {args.speed} FOR {args.duration:.3f}s'
        if input(f'Type {phrase} after checking the safe sector: ').strip()!=phrase:p.error('Not armed')
        from camera.hanet_controller import HanetController
        motor=HanetController(ip=config.PTZ_IP,username=config.PTZ_USERNAME,password=config.PTZ_PASSWORD,
            port=config.PTZ_PORT,timeout=config.PTZ_TIMEOUT,speed=args.speed,autoconnect=False)
    else:motor=MockMotor()
    cancel=threading.Event();old={}
    for sig in (signal.SIGINT,signal.SIGTERM):old[sig]=signal.signal(sig,lambda *_:cancel.set())
    try:
        with args.output.with_suffix('.jsonl').open('x') as f:
            def emit(r):f.write(json.dumps(r)+'\n');f.flush()
            # Explicit operator arming above authorizes recovering a persisted STOP.
            if args.physical:
                ready,_=motor.stop()
                if not ready:raise RuntimeError('Preflight STOP unconfirmed; no START allowed')
            result=pulse(motor,args.direction,args.speed,args.duration,cancel,emit)
        result.update(physical=args.physical,device=device,operator_observed_safe=False)
        if args.physical and result['stop_ack']:
            result['operator_observed_safe']=input('Visually stopped within safe sector? Type YES: ').strip()=='YES'
        args.output.write_text(json.dumps(result,indent=2))
        fields=['event','monotonic_s','wall_time_ns','direction','step','acknowledged','rpc_ms','cancelled']
        with args.output.with_suffix('.csv').open('x',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(result['events'])
        if not result['stop_ack'] or result['error']:raise SystemExit('Pulse failed; inspect STOP status and backend latch')
    finally:
        motor.close()
        for sig,handler in old.items():signal.signal(sig,handler)
if __name__=='__main__':main()
