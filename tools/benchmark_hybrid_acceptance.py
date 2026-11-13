#!/usr/bin/env python3
"""Hardware-free acceptance evidence. Camera, face detection and embeddings are mocks.

Zoom dispatch timings are measured on the local ready-controller path. Delayed
recognition uses a simulated clock; its 1100 ms frame age is not a hardware timing.
"""
import argparse
from contextlib import ExitStack
import json
import importlib.util
import sysconfig
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
# Match the desktop launcher's ABI-checked system-package fallback.
if importlib.util.find_spec('gi') is None:
    system_packages=Path('/usr/lib/python3/dist-packages')
    extension=sysconfig.get_config_var('EXT_SUFFIX')
    if extension and (system_packages/'gi'/('_gi'+extension)).is_file():
        sys.path.append(str(system_packages))
import config
from camera.dahua_rpc import ZoomRPC
from camera.ptz_controller import PTZController
from control.auto_zoom import AutoZoomController
from runtime.inference_worker import InferenceWorker
from runtime.face_worker import FaceWorker, FaceJob
from runtime.target_session import TargetSession
from telemetry import latency, zoom_trace
from tools.zoom_benchmark import MockFirmware


def run(directory):
    trace=zoom_trace.configure(directory=directory,enabled=True,evidence_source='mock')
    firmware=MockFirmware()
    camera=PTZController('mock','mock','mock',autoconnect=False,
        rpc_factory=lambda **kw:ZoomRPC(**kw,session_factory=lambda:firmware))
    zoom=AutoZoomController(camera,wait_after_zoom=0,feedback_timeout=.8)
    try:
        camera.read_status();zoom.set_enabled(True)
        locked=time.perf_counter_ns()
        correlation=dict(lock_session_id='mock_no_face',target_track_id=7,locked_ns=locked)
        context=SimpleNamespace(auto_zoom=zoom,reid_target=SimpleNamespace(locked=True,crossing_hold=False,
            lock_session_id='mock_no_face',track_id=7,locked_ns=locked),
            tracking_reset=threading.Event(),get_motor_speed=lambda:100)
        with latency.correlated(**correlation):
            latency.profiler.event('TARGET_LOCKED')
            InferenceWorker(context).start_person_zoom_on_lock(dict(bbox=(100,100,200,200)),(1000,1000),1,time.monotonic())
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            camera.read_status()
            if zoom.last_stop_reason=='POSITION_REACHED':
                for i in range(3):
                    zoom.observe_stability(.5,i+2,time.monotonic(),target_kind='PERSON')
                zoom.update((1000,1000),dict(bbox=(100,100,300,600)),.5,.04,target_name='PERSON')
                break
            time.sleep(.01)
        if zoom.phase != 'ZOOM_HOLD':
            raise AssertionError('Mock absolute move did not reach HOLD')
    finally:
        zoom.close();camera.close()
    session=TargetSession();session.publish('mock_delayed_identity',7)
    matcher=Mock();matcher.match.return_value=dict(identity='registered_mock',score=.9,matched=True)
    recognizer=Mock()
    cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
    cfg.FACE_RECOGNITION_INTERVAL=0
    worker=FaceWorker(Mock(),recognizer,matcher,cfg,session.matches,session.matches)
    worker.session='mock_delayed_identity';worker.track_id=7
    person=dict(bbox=(0,0,200,400),track_id=7)
    face=dict(bbox=(40,20,140,120),landmarks=np.zeros((5,2)))
    image=np.zeros((400,400,3),np.uint8)
    now=[time.monotonic()]
    job=FaceJob(1,now[0]-.712,image,person,None,(person,),'mock','mock_delayed_identity',7,0,time.perf_counter_ns())
    def embedding(_):
        now[0]+=.388
        return np.ones(512)
    recognizer.extract_embedding.side_effect=embedding
    try:
        with ExitStack() as stack:
            stack.enter_context(patch('runtime.face_worker.time.monotonic',side_effect=lambda:now[0]))
            stack.enter_context(patch('ai.face.face_detector.detect_face_inside_person',return_value=[face]))
            stack.enter_context(patch('ai.sharpness.calculate_face_sharpness',return_value=200))
            stack.enter_context(patch('ai.face.face_alignment.align_face',return_value=image))
            with latency.correlated(**job.correlation()):
                worker._process(job)
                motor_valid=worker.valid(job)
                observation=worker.take_identity()
            if motor_valid or observation is None:
                raise AssertionError('Delayed identity/motor freshness not separated')
    finally:
        worker.close();trace.close()
    if trace.error:
        raise RuntimeError(trace.error)
    rows=[json.loads(l) for l in trace.trace_path.read_text().splitlines()]
    request=next(r for r in rows if r['event']=='ZOOM_REQUEST_CREATED')
    commands=[r for r in rows if r['event']=='ZOOM_COMMAND_SENT' and r.get('rpc_method')=='ptz.start']
    result=dict(evidence_source='mock',lock_to_zoom_request_ms=(request['monotonic_ns']-locked)/1e6,
        lock_to_first_zoom_command_ms=(commands[0]['monotonic_ns']-locked)/1e6,
        absolute_commands=sum(r['rpc_code']=='PositionABS' for r in commands),
        pulse_commands=sum(r['rpc_code'] in ('ZoomTele','ZoomWide') for r in commands),
        no_face_zoom_final_state=zoom.phase,simulated_recognition_processing_ms=388,
        simulated_frame_age_ms=1100,identity_observation_accepted=observation is not None,
        delayed_bbox_valid_for_motor=motor_valid,identity_confirmed=observation['recognition']['confirmed'],
        trace=str(trace.trace_path),summary=str(trace.summary_path))
    if result['absolute_commands']!=1 or result['pulse_commands']!=0:
        raise AssertionError(result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,default=ROOT/'logs/hybrid_mock')
    a=p.parse_args()
    result=run(a.output_dir)
    a.output_dir.mkdir(parents=True,exist_ok=True)
    (a.output_dir/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__': main()
