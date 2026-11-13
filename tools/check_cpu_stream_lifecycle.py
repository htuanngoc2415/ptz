#!/usr/bin/env python3
"""Read-only RTSP lifecycle checks; induced receiver faults, never camera writes."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import gc
import weakref

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from camera.rtsp_player import CPUStreamPlayer, Gst
from runtime.frame_manager import FrameManager
from telemetry.resources import sample


def wait_for(predicate, timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():
            return True
        time.sleep(.05)
    return False


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--cycles',type=int,default=4)
    ap.add_argument('--cycle-seconds',type=float,default=8)
    ap.add_argument('--check-gc',action='store_true',help='Diagnostic only: collect Python cycles after each close')
    args=ap.parse_args()
    if args.cycles < 1 or args.cycle_seconds <= 0:
        ap.error('cycles and cycle-seconds must be positive')
    config=json.loads((ROOT/'camera.json').read_text())
    config.update(reconnect_initial=.2,reconnect_max=1,video_stall_timeout=2)
    mailbox=FrameManager()
    result={}
    uri=config['uri']
    config['uri']='rtsp://127.0.0.1:1/unavailable'
    p=CPUStreamPlayer(config,mailbox.publish,disconnect_callback=mailbox.clear)
    try:
        p.start()
        result['connection_failure_detected']=wait_for(lambda:p.diagnostics()['counts'].get('failures',0)>0)
        config['uri']=uri
        result['connection_recovered']=wait_for(lambda:mailbox.latest() is not None)
        generation=p.diagnostics()['generation']
        pipeline=p.player
        result['eos_injected']=bool(pipeline and pipeline.send_event(Gst.Event.new_eos()))
        result['eos_recovered']=wait_for(lambda:p.diagnostics()['generation']>generation and mailbox.latest() is not None)
        generation=p.diagnostics()['generation']
        pipeline=p.player
        if pipeline:
            pipeline.set_state(Gst.State.PAUSED)
        result['stall_recovered']=wait_for(lambda:p.diagnostics()['generation']>generation and mailbox.latest() is not None)
    finally:
        start=time.monotonic()
        p.stop()
        result['stop_seconds']=time.monotonic()-start
        result['diagnostics']=p.diagnostics()
        result['worker_stopped']=not p._thread.is_alive()
    result['cycles']=[]
    refs=[]
    for i in range(args.cycles):
        mailbox.clear()
        p=CPUStreamPlayer(config,mailbox.publish)
        refs.append(weakref.ref(p))
        try:
            p.start()
            ready=wait_for(lambda:mailbox.latest() is not None)
            time.sleep(args.cycle_seconds)
            active_rss=sample()[0]['rss_mb']
        finally:
            start=time.monotonic();p.stop();stop_seconds=time.monotonic()-start
        mailbox.clear()
        time.sleep(.2)
        result['cycles'].append(dict(ready=ready,active_rss_mb=active_rss,
            stopped_rss_mb=sample()[0]['rss_mb'], stop_seconds=stop_seconds,
            diagnostics=p.diagnostics()))
        del p
        if args.check_gc:
            gc.collect()
        result['cycles'][-1]['rss_after_gc_mb']=sample()[0]['rss_mb'] if args.check_gc else None
        result['cycles'][-1]['receiver_objects_alive']=sum(ref() is not None for ref in refs)
    result['remaining_capture_threads']=[t.name for t in threading.enumerate() if t.name=='gst-cpu-capture']
    result['memory_scope']=f'{args.cycles} short create/close cycles, {args.cycle_seconds}s each; GC diagnostic={args.check_gc}; not a long-duration leak proof'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    passed = all(result.get(k) for k in ('connection_failure_detected','connection_recovered',
        'eos_recovered','stall_recovered','worker_stopped'))
    passed = passed and all(c['ready'] and not c['diagnostics']['worker_alive']
        and c['receiver_objects_alive'] == 0 for c in result['cycles'])
    return 0 if passed and not result['remaining_capture_threads'] else 2


if __name__=='__main__':
    sys.exit(main())
