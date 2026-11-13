"""One manually confirmed bounded pulse. Default dry-run never imports a backend.
Optional offline video displacement is pixels only, not an angular calibration.
"""
import argparse
import json
import signal
import threading
import time
from pathlib import Path


def run_pulse(camera, direction, speed, duration, emit, emergency):
    if not 1 <= speed <= 3 or not .02 <= duration <= .24:
        raise ValueError('Calibration limits: speed 1..3, duration .02.. .24 s')
    if direction not in ('pan_left','pan_right','tilt_up','tilt_down'):
        raise ValueError('Unsupported direction')
    started=False
    try:
        status=camera.read_status()
        if not status.get('connected') or emergency.is_set():
            emit(dict(event='ABORT',reason='DISCONNECTED_OR_CANCELLED'));return False
        started=True
        emit(dict(event='START_REQUEST',direction=direction,speed=speed,duration=duration))
        ok,_=camera.command(direction,speed)
        emit(dict(event='START_ACK',ack=bool(ok)))
        if not ok:return False
        emergency.wait(duration)
        return not emergency.is_set()
    except Exception as exc:
        emit(dict(event='ERROR',error_type=type(exc).__name__));return False
    finally:
        if started:
            # STOP must be attempted even when START raises or ACK is lost.
            emit(dict(event='STOP_REQUEST',emergency=emergency.is_set()))
            for attempt in range(3):
                try:ok,_=camera.stop()
                except Exception:ok=False
                emit(dict(event='STOP_ACK',ack=bool(ok),attempt=attempt+1))
                if ok:break
            if not ok:raise RuntimeError('STOP unconfirmed; inspect camera and use physical stop')


def video_displacement(path):
    import cv2
    import numpy as np
    cap=cv2.VideoCapture(str(path));previous=None;rows=[];index=0
    try:
        while True:
            ok,frame=cap.read()
            if not ok:break
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY).astype(np.float32)
            if previous is not None:
                shift,response=cv2.phaseCorrelate(previous,gray)
                rows.append(dict(frame=index,video_seconds=cap.get(cv2.CAP_PROP_POS_MSEC)/1000,
                    dx_pixels=shift[0],dy_pixels=shift[1],correlation=response))
            previous=gray;index+=1
    finally:cap.release()
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--direction',choices=['pan_left','pan_right','tilt_up','tilt_down'],default='pan_right')
    p.add_argument('--speed',type=int,choices=[1,2,3],default=1)
    p.add_argument('--duration',type=float,default=.12)
    p.add_argument('--log',type=Path,default=Path('pan_calibration.jsonl'))
    p.add_argument('--video',type=Path,help='Offline video only; phase correlation is approximate')
    args=p.parse_args()
    if not .02<=args.duration<=.24:p.error('duration must be .02.. .24 seconds')
    def emit(row):
        row=dict(timestamp=time.time(),monotonic=time.monotonic(),**row)
        with args.log.open('a') as f:f.write(json.dumps(row)+'\n')
        print(json.dumps(row))
    if args.video:
        rows=video_displacement(args.video)
        output=args.log.with_suffix('.displacement.json')
        output.write_text(json.dumps(dict(units='pixels; no angle/zoom calibration',samples=rows),indent=2))
    if not args.execute:
        emit(dict(event='DRY_RUN',direction=args.direction,speed=args.speed,duration=args.duration));return
    if input('Close GUI/other motor clients. Type MOVE to authorize one physical pulse: ').strip()!='MOVE':
        emit(dict(event='ABORT',reason='NOT_CONFIRMED'));return
    import config
    from camera.hanet_controller import HanetController
    if config.PTZ_BACKEND!='hanet':raise RuntimeError('This tool supports HANET only')
    emergency=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:emergency.set())
    signal.signal(signal.SIGTERM,lambda *_:emergency.set())
    def keyboard_stop():
        try:input('Press Enter or Ctrl-C for emergency STOP.\n');emergency.set()
        except EOFError:emergency.set()
    threading.Thread(target=keyboard_stop,daemon=True).start()
    camera=HanetController(config.PTZ_IP,config.PTZ_USERNAME,config.PTZ_PASSWORD,
        port=config.PTZ_PORT,timeout=.5,autoconnect=False)
    try:run_pulse(camera,args.direction,args.speed,args.duration,emit,emergency)
    finally:camera.close()
if __name__=='__main__':main()
