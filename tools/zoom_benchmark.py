#!/usr/bin/env python3
"""Default: read-only probe. --mock proves command count without a camera.

Motor test requires BOTH --execute-motor and --supervised; accepts only interior
historically measured PositionABS coordinates. This measures lens travel only;
face/identity A/B measurements require runtime traces on the same scene.
"""
import argparse
import json
from pathlib import Path
import sys
import time
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from camera.dahua_rpc import ZoomRPC
from camera.ptz_controller import PTZController
from camera.zoom_prediction import PositionABSMapping
from control.auto_zoom import AutoZoomController
from telemetry import latency, zoom_trace


class MockResponse:
    def __init__(self, data): self.data = data
    def raise_for_status(self): pass
    def json(self): return self.data


class MockFirmware:
    """In-process HTTP transport. No sockets; ACK precedes simulated travel."""
    def __init__(self):
        from requests.cookies import RequestsCookieJar
        self.cookies = RequestsCookieJar()
        self.headers = {}
        self.calls = []
        self.logins = 0
        self.zoom = 3.
        self.target = None
        self.moving_until = 0
        self.mapping = PositionABSMapping()
    def close(self): pass
    def post(self, url, data, timeout):
        p = json.loads(data)
        self.calls.append(p)
        m, a = p['method'], p.get('params', {})
        reply = {'result':True}
        if m == 'global.login':
            self.logins += 1
            reply = ({'result':False,'session':1,'params':{'realm':'mock','random':'mock','encryption':'Default'}}
                     if self.logins == 1 else {'result':True,'session':1})
        elif m == 'ptz.factory.instance': reply = {'result':2}
        elif m == 'ptz.getCurrentProtocolCaps':
            reply['params'] = {'caps':{'MoveAbsolutely':{'PTZ':['Zoom']}}}
        elif m == 'configManager.getConfig':
            reply['params'] = {'table':[[{'Speed':100,'Mode':2,'AutoFocusTrace':1}]]}
        elif m == 'ptz.start' and a['code'] == 'PositionABS':
            self.target = self.mapping.command_to_zoom((a['arg3']-1)/127)
            self.moving_until = time.monotonic()+.18
        elif m == 'ptz.getStatus':
            moving = self.target is not None and time.monotonic() < self.moving_until
            if self.target is not None and not moving:
                self.zoom = self.target
            reply['params'] = {'status':{'ZoomValue':self.zoom*10,'PTZZoomHD':1234,
                'ZoomStatus':'Moving' if moving else 'Idle','FocusStatus':'Idle','PTZFocusHD':10000}}
        return MockResponse(reply)


def mock_run(directory):
    record = zoom_trace.configure(directory=directory,enabled=True,evidence_source='mock')
    firmware = MockFirmware()
    factory = lambda **kw: ZoomRPC(**kw, session_factory=lambda:firmware)
    camera = PTZController('mock','mock','mock',rpc_factory=factory,autoconnect=False)
    controller = AutoZoomController(camera,wait_after_zoom=0,feedback_timeout=.8)
    locked = time.perf_counter_ns()
    correlation = dict(lock_session_id='mock_one_major',target_track_id=1,locked_ns=locked)
    controller._correlation = correlation
    latency.context.set(correlation)
    controller.set_enabled(True)
    ack_was_not_completion = False
    try:
        camera.read_status()
        latency.profiler.event('TARGET_LOCKED')
        deadline = time.monotonic()+2
        for frame in range(200):
            camera.read_status()
            now = time.monotonic()
            ratio = .5 if controller.last_stop_reason == 'POSITION_REACHED' else .1
            controller.observe_stability(ratio, frame, now, target_kind='PERSON')
            with latency.correlated(frame_id=frame):
                controller.update((1000,1000),{'bbox':(300,200,600,200+ratio*1000)},.5,.02,target_name='PERSON')
            if firmware.target is not None and now < firmware.moving_until:
                ack_was_not_completion |= controller.busy and controller.last_stop_reason is None
            if controller.phase == 'ZOOM_HOLD' and controller._stability_reported:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Mock did not reach HOLD')
            time.sleep(.02)
        starts = [p for p in firmware.calls if p['method']=='ptz.start']
        codes = [p['params']['code'] for p in starts]
        result = dict(evidence_source='mock', absolute_commands=codes.count('PositionABS'),
                      pulse_commands=sum(c in ('ZoomTele','ZoomWide') for c in codes),
                      ack_preceded_completion=ack_was_not_completion, final_state=controller.phase,
                      corrections=controller.correction_count, trace=str(record.trace_path),summary=str(record.summary_path))
        if result['absolute_commands'] != 1 or result['pulse_commands'] != 0 or not ack_was_not_completion or controller.phase != 'ZOOM_HOLD':
            raise AssertionError(result)
    finally:
        controller.close()
        camera.close()
        record.close()
    if record.error:
        raise RuntimeError(record.error)
    return result


def motor_run(config, position, directory):
    if not .15 < position < 1.:
        raise ValueError('Only interior verified positions (.15,1) allowed; endpoints forbidden')
    quantized = (PositionABSMapping.gui_value(position)-1)/127
    if not .15 < quantized < 1.:
        raise ValueError('Quantized GUI command would reach a boundary; choose an interior position')
    record = zoom_trace.configure(directory=directory,enabled=True,evidence_source='supervised_hardware')
    cfg = json.loads(config.read_text())
    if cfg.get('ptz_backend', 'dahua') != 'dahua':
        raise ValueError('Dahua-only diagnostic; use tools/check_camera.py for HANET')
    camera = PTZController(cfg['ptz_ip'],cfg['ptz_username'],cfg['ptz_password'],
        port=cfg.get('ptz_port',80),channel=cfg.get('ptz_channel',0),autoconnect=False)
    try:
        latency.context.set(dict(lock_session_id='supervised_lens_test'))
        camera.read_status()
        if not camera.supports_absolute_zoom:
            raise RuntimeError('ABSOLUTE_ZOOM_UNSUPPORTED or unverified: no movement')
        start = latency.profiler.event('ZOOM_STARTED',evidence='supervised_dispatch')
        ok, reason = camera.move_to_zoom_position(position)
        if not ok:
            raise RuntimeError(reason)
        latency.profiler.event('ZOOM_COMMAND_ACK')
        expected = PositionABSMapping().command_to_zoom((PositionABSMapping.gui_value(position)-1)/127)
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            s = camera.read_status()
            latency.profiler.event('ZOOM_STATUS',actual_zoom_position=s['zoom_position_raw'],actual_zoom_value=s['zoom'],
                motor_state=s['zoom_motion_state'],af_state=s['focus_motion_state'],zoom_value_error=s['zoom']-expected)
            if s['zoom_motion_state']=='Idle' and abs(s['zoom']-expected)<=.3:
                latency.profiler.elapsed('ZOOM_COMPLETED',start,'zoom_total_ms',evidence='measured_idle_and_target')
                return dict(completed=True,trace=str(record.trace_path),summary=str(record.summary_path))
            time.sleep(.15)
        raise TimeoutError('Absolute move did not reach reported endpoint')
    finally:
        camera.close()
        record.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=ROOT/'camera.json')
    p.add_argument('--output-dir',type=Path,default=ROOT/'logs')
    p.add_argument('--mock',action='store_true')
    p.add_argument('--execute-motor',action='store_true')
    p.add_argument('--supervised',action='store_true')
    p.add_argument('--position',type=float)
    a = p.parse_args()
    if a.mock:
        if a.execute_motor or a.position is not None:
            p.error('--mock cannot be combined with motor options')
        result = mock_run(a.output_dir)
    elif a.execute_motor:
        if not a.supervised or a.position is None:
            p.error('Motor movement requires --execute-motor --supervised --position')
        result = motor_run(a.config,a.position,a.output_dir)
    else:
        if a.position is not None:
            p.error('Position requires explicit supervised motor mode')
        from tools.probe_zoom import probe
        result = probe(json.loads(a.config.read_text()))
    print(json.dumps(result,indent=2))

if __name__ == '__main__':
    main()
