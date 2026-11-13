#!/usr/bin/env python3
"""Stage-by-stage PTZ evidence runner. Offline by default; never changes production settings."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from telemetry.benchmark import (GroundTruth, distribution, public_value,
                                 summarize_trace, write_report)

# Explicit allowlist: interactive scripts are never discovered or imported.
STAGES = {
    0: ('Camera / RTSP', ['test_dahua_camera.RpcTests', 'test_dahua_camera.AdapterTests',
        'test_mpv_player', 'test_camera_entrypoints', 'test_startup_logging', 'test_image_settings']),
    1: ('Optical Zoom', ['test_absolute_zoom_audit', 'test_dahua_camera.AbsoluteZoomIntegrationTests',
        'zoom_calibration.tests.test_tool']),
    2: ('YOLO', ['test_cuda_failure']),
    3: ('OSNet ReID', ['test_reid_target', 'test_continuous_reid']),
    4: ('Person Tracking', ['test_crossing_retention', 'test_hybrid_latency.PersonTrackingTests']),
    5: ('Target Rebinding', ['test_pipeline_identity_zoom.IdentityValidityTests',
        'test_hybrid_latency.SessionAndControlTests', 'test_zoom_latency_safety']),
    6: ('Auto Zoom', ['test_auto_zoom', 'test_zoom_ownership', 'test_zoom_oscillation',
        'test_pipeline_identity_zoom.ZoomIndependenceTests']),
    7: ('Face Detection / Tracking', ['test_event_face_pipeline', 'test_hybrid_latency.FaceBranchTests']),
    8: ('Auto Focus', ['test_sharpness', 'test_dynamic_focus', 'test_control_regression', 'test_laplacian_monitor']),
    9: ('Face Recognition', ['test_face_identity', 'test_event_face_pipeline']),
    10: ('GUI / Digital PTZ', ['test_face_hold_digital', 'test_final_framing']),
    11: ('End-to-End', ['test_runtime_architecture', 'test_hybrid_latency', 'test_modular_benchmark']),
}
REPLAY_STAGES = {0, 2, 3, 4, 7, 8, 9, 10}
SCENARIOS = ('unknown', 'stationary', 'walking', 'multiple_people', 'similar_clothing', 'front_light',
             'backlight', 'low_light', 'short_occlusion', 'long_occlusion', 'crossing',
             'zoom_stationary', 'zoom_moving', 'lost_return', 'local_id_change',
             'stale_frame', 'stale_job', 'session_change', 'pending_command', 'back_view', 'obstacle')
CASES = ('unit', 'replay', 'embedding', 'trace', 'mock', 'stream', 'rpc', 'motor', 'manual', 'e2e', 'atomic-rebind')
SCHEMA = 1


def save(path, value):
    Path(path).write_text(json.dumps(public_value(value), indent=2, ensure_ascii=False, allow_nan=False)+'\n')


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def source_files():
    for folder in ('ai','app','camera','control','runtime','gui','telemetry','tools','test',
                   'zoom_calibration','person_search'):
        yield from (ROOT/folder).rglob('*.py')
    yield from ROOT.glob('*.py')
    yield ROOT/'requirements.txt'


def manifest(args):
    files = {str(p.relative_to(ROOT)): sha256(p) for p in sorted(source_files())}
    commit = subprocess.run(['git','rev-parse','HEAD'], cwd=ROOT, capture_output=True, text=True)
    models = {}
    for folder in ('models', 'person_search/models'):
        for p in sorted((ROOT/folder).rglob('*')):
            if p.is_file():
                models[str(p.relative_to(ROOT))] = dict(bytes=p.stat().st_size, sha256=sha256(p))
    packages = {}
    for name in ('numpy','opencv-python','torch','torchvision','ultralytics','onnxruntime','onnxruntime-gpu','python-mpv'):
        try: packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: packages[name] = None
    import config
    cfg = {k: public_value(v) for k,v in vars(config).items()
           if k.isupper() and isinstance(v,(str,int,float,bool,tuple,list,dict,Path,type(None)))}
    inputs = {}
    for key in ('video','ground_truth','detections','target','candidate','trace','limits'):
        path = getattr(args,key,None)
        if path and Path(path).is_file():
            inputs[key] = dict(path=str(Path(path).resolve()),sha256=sha256(path))
    for key,path in (('face_database',config.FACE_DATABASE_PATH),
                     ('zoom_calibration',config.ZOOM_CONTROL.get('calibration_path'))):
        if path and Path(path).is_file():
            inputs[key]=dict(path=str(Path(path).resolve()),sha256=sha256(path))
    return dict(schema_version=SCHEMA, timestamp=datetime.now(timezone.utc).isoformat(),
                python=sys.version, executable=sys.executable, platform=platform.platform(),
                cpu_count=os.cpu_count(),cpu_affinity=sorted(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,
                commit_hash=commit.stdout.strip() if commit.returncode == 0 else None,
                commit_status='available' if commit.returncode == 0 else 'UNAVAILABLE',
                source_sha256=files, source_tree_sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
                model_versions=models, packages=packages, configuration=cfg,
                arguments=vars(args), inputs=inputs,
                environment={k:v for k,v in os.environ.items() if k.startswith('PTZ_')})


def deny_network():
    """Defense in depth for offline Python adapters; mocked HTTP transport still works."""
    def audit(event, arguments):
        if event in ('socket.connect','socket.connect_ex','socket.getaddrinfo','socket.sendto'):
            # Libraries probing connectivity expect a network OSError. A custom
            # RuntimeError would abort unrelated model imports such as YOLO.
            raise OSError('OFFLINE_NETWORK_FORBIDDEN')
    sys.addaudithook(audit)


def setup_imports():
    # Same ABI-checked GI fallback as the existing test runner.
    import sysconfig
    if importlib.util.find_spec('gi') is None:
        ext = sysconfig.get_config_var('EXT_SUFFIX')
        packages = Path('/usr/lib/python3/dist-packages')
        if ext and (packages/'gi'/('_gi'+ext)).is_file():
            sys.path.append(str(packages))
    # test/camera.py is a legacy executable, not the production camera package.
    if str(ROOT) in sys.path: sys.path.remove(str(ROOT))
    sys.path.insert(0,str(ROOT))
    if str(ROOT/'test') not in sys.path: sys.path.insert(1,str(ROOT/'test'))


class EvidenceResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []
        self.started = {}

    def startTest(self, test):
        self.started[test.id()] = time.perf_counter()
        super().startTest(test)

    def record(self, test, status, reason=''):
        self.records.append(dict(test=test.id(), status=status, reason=reason,
            duration_ms=(time.perf_counter()-self.started.get(test.id(),time.perf_counter()))*1000))

    def addSuccess(self, test):
        super().addSuccess(test); self.record(test,'PASS')

    def addFailure(self, test, err):
        super().addFailure(test,err); self.record(test,'FAIL',self._exc_info_to_string(err,test))

    def addError(self, test, err):
        super().addError(test,err)
        self.record(test,'NOT_RUN' if issubclass(err[0],(ImportError,ModuleNotFoundError)) else 'FAIL',
                    self._exc_info_to_string(err,test))

    def addSkip(self, test, reason):
        super().addSkip(test,reason); self.record(test,'NOT_RUN',reason)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test,err); self.record(test,'NOT_RUN','expected failure: '+self._exc_info_to_string(err,test))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test); self.record(test,'FAIL','unexpected success')

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test,subtest,err)
        if err is not None:
            self.record(subtest,'FAIL',self._exc_info_to_string(err,test))


def unit_child(spec, output, name_filter):
    deny_network(); setup_imports()
    suite = unittest.defaultTestLoader.loadTestsFromName(spec)
    if name_filter:
        def flatten(node):
            for test in node:
                if isinstance(test,unittest.TestSuite): yield from flatten(test)
                else: yield test
        suite = unittest.TestSuite(t for t in flatten(suite) if name_filter in t.id())
    result = unittest.TextTestRunner(verbosity=2,resultclass=EvidenceResult).run(suite)
    save(output,dict(tests_run=result.testsRun,records=result.records))
    return 0


def unit_run(stage, args, folder):
    records, runs = [], 0
    for spec in STAGES[stage][1]:
        target = folder/(spec+'.json')
        command = [sys.executable,str(Path(__file__).resolve()),'_unit',spec,str(target),args.test_filter or '']
        with (folder/(spec+'.log')).open('w') as log:
            try:
                proc = subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=args.timeout)
                if target.exists():
                    data=json.loads(target.read_text()); records.extend(data['records']); runs+=data['tests_run']
                else:
                    records.append(dict(test=spec,status='FAIL',reason=f'child exit {proc.returncode}; no result'))
            except subprocess.TimeoutExpired:
                records.append(dict(test=spec,status='FAIL',reason='test subprocess timeout'))
    counts=Counter(r['status'] for r in records)
    status='FAIL' if counts['FAIL'] else 'NOT_RUN' if counts['NOT_RUN'] or not runs else 'PASS'
    save(folder/'tests.json',dict(tests_run=runs,counts=counts,records=records))
    return dict(status=status,reason=f'{runs} unittest cases; {dict(counts)}; mock/logic evidence only',
                evidence='mock_unit',tests_run=runs,test_counts=dict(counts),
                metrics={'test_runtime_ms':distribution(r.get('duration_ms') for r in records)})


def read_rows(path):
    with Path(path).open() as stream:
        for number, line in enumerate(stream,1):
            if line.strip():
                try:
                    row=json.loads(line)
                    if not isinstance(row,dict): raise ValueError('expected JSON object')
                    yield row
                except (ValueError,TypeError) as exc:
                    raise ValueError(f'Invalid JSONL line {number}') from exc


def acceptance(metrics, limits):
    """Explicit metric gates only; missing evidence never satisfies a threshold."""
    if not limits:
        return 'NOT_RUN','Measured; no acceptance limits supplied'
    checks=[]
    for path,rule in limits.items():
        if not isinstance(rule,dict) or not rule or set(rule)-{'min','max'}:
            raise ValueError('Limits require metric.path: {min: number, max: number}')
        value=metrics
        for part in path.split('.'):
            value=value.get(part) if isinstance(value,dict) else None
        import math
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in rule.values()):
            raise ValueError('Acceptance limits must be finite numbers')
        if 'min' in rule and 'max' in rule and rule['min'] > rule['max']:
            raise ValueError('Acceptance min exceeds max')
        if type(value) not in (int,float) or not math.isfinite(value): checks.append((path,'NOT_RUN'))
        else: checks.append((path,'PASS' if value>=rule.get('min',-float('inf')) and value<=rule.get('max',float('inf')) else 'FAIL'))
    status='FAIL' if any(s=='FAIL' for _,s in checks) else 'NOT_RUN' if any(s=='NOT_RUN' for _,s in checks) else 'PASS'
    return status, '; '.join(f'{p}={s}' for p,s in checks)


def trace_run(args,folder):
    if not args.trace:
        return dict(status='NOT_RUN',reason='Supply --trace existing JSONL; no live application launched')
    rows=list(read_rows(args.trace))
    if not rows:
        return dict(status='NOT_RUN',reason='Trace is empty')
    with (folder/'events.jsonl').open('w') as stream:
        for row in rows: stream.write(json.dumps(public_value(row),allow_nan=False)+'\n')
    metrics=summarize_trace(rows)
    status,reason=acceptance(metrics,args.limit_values)
    return dict(status=status,reason=reason,evidence='imported_trace',execution_status='PASS',metrics=metrics)


def timeline(folder):
    """Normalize adapter and existing profiler events without inventing tokens."""
    import csv
    import html
    fields=('timestamp','monotonic_ns','event','frame_id','session_id','track_id',
            'track_generation','bbox','similarity','metric','duration_ms','reason')
    rows=[]
    paths=[folder/'events.jsonl']+sorted((folder/'latency').glob('latency_trace_*.jsonl'))
    paths+=sorted((folder/'zoom').glob('zoom_trace_*.jsonl'))
    for path in paths:
        if path.exists():
            for original in read_rows(path):
                row=dict(original)
                row['session_id']=row.get('session_id') or row.get('lock_session_id')
                row['track_id']=row.get('track_id',row.get('target_track_id'))
                row['reason']=row.get('reason') or row.get('reject_reason') or row.get('command_rejection_reason')
                row['source_file']=str(path.relative_to(folder))
                rows.append(public_value(row))
    rows.sort(key=lambda r:r.get('monotonic_ns') or 0)
    with (folder/'timeline.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)
    frame_rows=[r for r in rows if r.get('event')=='FRAME']
    if frame_rows:
        with (folder/'recording.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=('frame_id','video_index','source_pts_s','receive_timestamp',
                'monotonic_ns','timestamp'),extrasaction='ignore');writer.writeheader();writer.writerows(frame_rows)
    body=['<!doctype html><meta charset="utf-8"><title>Evidence timeline</title>',
          '<style>body{font:14px system-ui}table{border-collapse:collapse}td,th{padding:5px;border:1px solid #ccc}pre{white-space:pre-wrap}</style>',
          '<h1>Evidence timeline</h1><p><a href="timeline.csv">All rows CSV</a> · <a href="result.json">Result JSON</a></p>',
          '<p>Missing session/track/generation remain empty. Showing first 1000 rows; CSV contains all rows.</p>',
          '<table><tr>'+''.join('<th>'+f+'</th>' for f in fields)+'</tr>']
    for row in rows[:1000]:
        body.append('<tr>'+''.join('<td>'+html.escape(str(row.get(f,'')))+'</td>' for f in fields)+'</tr>')
    body.append('</table>')
    if (folder/'tests.json').exists():
        body.append('<h2>Unit evidence</h2><pre>'+html.escape((folder/'tests.json').read_text())+'</pre>')
    (folder/'timeline.html').write_text('\n'.join(body))
    return rows


def load_detections(path):
    if not path: return None
    data=json.loads(Path(path).read_text())
    # Reuse strict box/frame validation. This is INPUT, never ground truth.
    validated=GroundTruth(data)
    return validated.frames


def embedding_run(stage,args,folder):
    """Independent OSNet/ArcFace latency; prealigned ArcFace input is explicit."""
    if stage not in (3,9):
        return dict(status='NOT_IMPLEMENTED',reason='Embedding case supports stage 3/9 only')
    if not args.target or not args.target.is_file():
        return dict(status='NOT_RUN',reason='Supply --target image; ArcFace requires a prealigned 112x112 BGR image')
    import cv2
    import config as cfg
    import numpy as np
    from telemetry import latency
    image=cv2.imread(str(args.target))
    candidate=cv2.imread(str(args.candidate)) if args.candidate else None
    if image is None or (args.candidate and candidate is None): raise ValueError('Cannot read embedding input')
    if stage==9 and any(im.shape[:2]!=(112,112) for im in (image,candidate) if im is not None):
        raise ValueError('ArcFace embedding input must already be aligned 112x112; no implicit resizing/alignment')
    if stage==3:
        from person_search.reid import OSNetReID
        model=OSNetReID(cfg.REID_MODEL_PATH,device=cfg.REID_DEVICE,batch_size=cfg.REID_BATCH_SIZE)
        name='osnet'
    else:
        from ai.face import FaceRecognizer
        model=FaceRecognizer(cfg.FACE_RECOGNITION_MODEL,
            providers=['CPUExecutionProvider'] if str(cfg.DEVICE)=='cpu' or cfg.ARCFACE_DEVICE=='cpu' else ['CUDAExecutionProvider'])
        name='arcface'
    def embedding(im):
        value=model.extract_embedding(im)
        return value.detach().cpu().numpy() if hasattr(value,'detach') else value
    reference=embedding(image) if candidate is not None else None
    profiler=latency.configure(directory=str(folder),enabled=True,sample_limit=max(4096,args.frames*10))
    rows=[];started=time.perf_counter()
    try:
        for index in range(args.frames):
            tick=time.perf_counter()
            with latency.correlated(frame_id=index+1): value=embedding(candidate if candidate is not None else image)
            rows.append(dict(event='EMBEDDING',timestamp=datetime.now(timezone.utc).isoformat(),
                monotonic_ns=time.perf_counter_ns(),frame_id=index+1,session_id=None,track_id=None,track_generation=None,
                metric=name+'_ms',duration_ms=(time.perf_counter()-tick)*1000,
                similarity=float(np.dot(reference,value)) if reference is not None else None))
        metrics=summarize_trace(rows)
        metrics.update(model_calls={name:args.frames},reference_calls=int(reference is not None),
                       samples=args.frames,throughput_fps=args.frames/(time.perf_counter()-started),
                       cosine_similarity=rows[-1]['similarity'],accuracy=None)
        status,reason=acceptance(metrics,args.limit_values)
        return dict(status=status,reason=reason,execution_status='PASS',evidence='image_embedding',metrics=metrics,
                    scope='Repeated same image; latency and optional cosine only, no acquisition/identity accuracy',
                    providers=model.session.get_providers() if hasattr(model,'session') else [str(model.device)])
    finally:
        profiler.close()
        with (folder/'events.jsonl').open('w') as stream:
            for row in rows: stream.write(json.dumps(public_value(row),allow_nan=False)+'\n')


def replay_run(stage,args,folder,online=False):
    selected = set(args.modules or [stage])
    if not selected <= REPLAY_STAGES:
        return dict(status='NOT_IMPLEMENTED',reason='Replay supports stages 0,2,3,4,7,8,9,10; production E2E/motor orchestration requires live app evidence')
    if stage not in selected:
        raise ValueError('Primary stage must be enabled in --modules')
    if not online and (not args.video or not Path(args.video).is_file()):
        return dict(status='NOT_RUN',reason='Supply --video local file (URLs are forbidden offline)')
    if selected & {3,4} and 2 not in selected and not args.detections:
        return dict(status='NOT_RUN',reason='Independent ReID/tracker requires --detections; integrated use --modules 2,3,4')
    if 3 in selected and not args.target:
        return dict(status='NOT_RUN',reason='OSNet requires --target reference image; no default target is silently chosen')
    if 9 in selected and 7 not in selected:
        return dict(status='NOT_RUN',reason='ArcFace replay requires --modules 7,9 for detected landmarks and alignment')
    if selected & {8,10} and not args.roi:
        return dict(status='NOT_RUN',reason='Supply --roi X1 Y1 X2 Y2 for quality/digital PTZ; fixed ROI is not target identity')
    import cv2
    import config as cfg
    import numpy as np
    from telemetry import latency
    from event_logging import setup_logging
    setup_logging(folder)
    import logging
    model_errors=[]
    class ModelErrors(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR: model_errors.append(record.getMessage())
    error_handler=ModelErrors()
    logging.getLogger('ptz.events').addHandler(error_handler)
    models={}; model_calls=Counter()
    def synchronize():
        if str(cfg.DEVICE) != 'cpu' and selected & {2,3}:
            import torch
            torch.cuda.synchronize(cfg.DEVICE)
    if 2 in selected:
        from ai.detector import PersonDetector
        if not Path(cfg.PERSON_MODEL_PATH).is_file(): raise FileNotFoundError('Person checkpoint missing')
        models['yolo']=PersonDetector(cfg.PERSON_MODEL_PATH,conf_threshold=cfg.PERSON_CONF_THRESHOLD,
            iou_threshold=cfg.PERSON_IOU_THRESHOLD,class_id=cfg.PERSON_CLASS_ID,device=cfg.DEVICE,imgsz=cfg.PERSON_IMGSZ)
    if 3 in selected:
        from person_search.reid import OSNetReID
        models['reid']=OSNetReID(cfg.REID_MODEL_PATH,device=cfg.REID_DEVICE,batch_size=cfg.REID_BATCH_SIZE)
        reference=models['reid'].extract_embedding(args.target)
    if 4 in selected:
        from runtime.person_tracking import PersonTracker
        models['tracker']=PersonTracker(cfg.REID_TRACK_IOU,cfg.REID_ASSOC_MARGIN,cfg.TARGET_LOST_FRAMES,cfg.REID_LOST_GRACE_SEC)
    if 7 in selected:
        from ai.face import FaceDetector
        models['scrfd']=FaceDetector(cfg.FACE_DETECT_MODEL,
            providers=['CPUExecutionProvider'] if str(cfg.DEVICE)=='cpu' else None,
            input_size=cfg.FACE_DET_INPUT_SIZE,conf_threshold=cfg.FACE_DET_CONF,nms_threshold=cfg.FACE_DET_NMS)
    if 8 in selected:
        from ai.quality.focus_quality import FocusQualityAnalyzer
        models['quality']=FocusQualityAnalyzer(cfg.FOCUS_LAPLACIAN,cfg.FOCUS_TENENGRAD,
            cfg.FOCUS_EDGE_DENSITY,cfg.FOCUS_MIN_ROI_SIZE)
    if 9 in selected:
        from ai.face import FaceRecognizer,FaceDatabase,FaceMatcher
        models['arcface']=FaceRecognizer(cfg.FACE_RECOGNITION_MODEL,
            providers=['CPUExecutionProvider'] if str(cfg.DEVICE)=='cpu' or cfg.ARCFACE_DEVICE=='cpu' else ['CUDAExecutionProvider'])
        models['matcher']=FaceMatcher(FaceDatabase(cfg.FACE_DATABASE_PATH),cfg.FACE_RECOGNITION_THRESHOLD)
    if 10 in selected:
        from control.digital_ptz import DigitalPTZController
        models['digital']=DigitalPTZController(enabled=True,
            min_zoom=cfg.DIGITAL_PTZ_MIN_ZOOM,max_zoom=cfg.DIGITAL_PTZ_MAX_ZOOM,
            target_height_ratio=cfg.DIGITAL_PTZ_TARGET_HEIGHT_RATIO,
            center_smoothing=cfg.DIGITAL_PTZ_CENTER_SMOOTHING,
            deadzone_x=cfg.DIGITAL_PTZ_DEADZONE_X,deadzone_y=cfg.DIGITAL_PTZ_DEADZONE_Y,
            zoom_smoothing=cfg.DIGITAL_PTZ_ZOOM_SMOOTHING,lost_hold_time=cfg.DIGITAL_PTZ_LOST_HOLD,
            return_smoothing=cfg.DIGITAL_PTZ_RETURN_SMOOTHING)
    detections=load_detections(args.detections) if selected & {3,4} else None
    gt=GroundTruth(json.loads(Path(args.ground_truth).read_text()),args.iou) if args.ground_truth else None
    if gt and stage not in (2,4,7):
        raise ValueError('BBox ground truth supported for primary stages 2,4,7 only; it is not ReID/ArcFace identity truth')
    profiler=latency.configure(directory=str(folder),enabled=True,sample_limit=max(4096,args.frames*30),max_bytes=128_000_000)
    trace=[]; writers=[]; source=None; receiver=None; glib_loop=None; glib_thread=None
    received=0; dropped=0; frame_id=0; processed=0; started=time.perf_counter(); cpu_start=time.process_time()
    metrics_times={}; first_receive=None; last_receive=None
    def event(name,**fields):
        row=dict(timestamp=datetime.now(timezone.utc).isoformat(),monotonic_ns=time.perf_counter_ns(),
                 event=name,frame_id=frame_id,session_id=None,track_id=None,track_generation=None,
                 bbox=None,similarity=None,model_latency_ms=None,scheduler_wait_ms=None,
                 rpc=None,telemetry=None,state_transition=None,reject_reason=None,**{})
        row.update(fields);trace.append(public_value(row))
    def timed(name,fn):
        synchronize(); tick=time.perf_counter(); result=fn(); synchronize()
        elapsed=(time.perf_counter()-tick)*1000
        metrics_times.setdefault(name,[]).append(elapsed)
        event('MODULE_COMPLETE',module=name,metric=name+'_ms',duration_ms=elapsed,model_latency_ms=elapsed)
        return result
    try:
        if online:
            from camera.stream import RTSPPlayer
            from runtime.frame_manager import FrameManager
            import threading
            mailbox=FrameManager()
            # Production callbacks supply RGB; all model wrappers consume BGR.
            reconnect={'started':None}
            reconnect_lock=threading.Lock()
            def disconnected(*unused):
                with reconnect_lock:
                    if reconnect['started'] is None: reconnect['started']=time.perf_counter_ns()
                event('RTSP_DISCONNECTED')
            def publish(rgb):
                packet=mailbox.publish(rgb)
                with reconnect_lock:
                    lost_at=reconnect['started'];reconnect['started']=None
                if lost_at is not None:
                    event('RTSP_RECOVERED',frame_id=packet.frame_id,metric='reconnect_ms',
                          duration_ms=(time.perf_counter_ns()-lost_at)/1e6)
            receiver=RTSPPlayer(json.loads(args.config.read_text()),publish,
                lambda msg:event('RTSP_STATUS',status=msg),disconnected)
            if json.loads(args.config.read_text()).get('video_backend','gstreamer')=='gstreamer':
                from gi.repository import GLib
                glib_loop=GLib.MainLoop(); glib_thread=threading.Thread(target=glib_loop.run,daemon=True);glib_thread.start()
            receiver.start(); fps=args.record_fps
        else:
            source=cv2.VideoCapture(str(Path(args.video).resolve()))
            if not source.isOpened(): raise RuntimeError('Cannot decode replay video')
            fps=source.get(cv2.CAP_PROP_FPS)
            if not np.isfinite(fps) or fps<=0: raise ValueError('Video FPS unknown; cannot assign replay timeline')
        while processed < args.frames:
            if online:
                if time.perf_counter()-started >= args.seconds: break
                packet=mailbox.latest(after=frame_id)
                if packet is None: time.sleep(.005);continue
                dropped+=max(0,packet.frame_id-frame_id-1)
                frame_id=packet.frame_id;image=cv2.cvtColor(packet.image,cv2.COLOR_RGB2BGR)
                received=packet.receive_timestamp;pts=None
                frame_age=packet.age_ms()
            else:
                tick=time.perf_counter();ok,image=source.read()
                if not ok:
                    expected=source.get(cv2.CAP_PROP_FRAME_COUNT)
                    if expected>0 and frame_id+1<expected and frame_id<args.frames:
                        raise RuntimeError(f'Video decode ended early at frame {frame_id} / {expected}')
                    break
                metrics_times.setdefault('decode_read',[]).append((time.perf_counter()-tick)*1000)
                frame_id+=1;received=time.monotonic();pts=(frame_id-1)/fps;frame_age=None
                if args.pace:
                    time.sleep(max(0,started+pts-time.perf_counter()))
            first_receive=received if first_receive is None else first_receive;last_receive=received
            processed+=1
            h,w=image.shape[:2]
            roi=args.roi
            if roi and not (0<=roi[0]<roi[2]<=w and 0<=roi[1]<roi[3]<=h):
                raise ValueError('ROI outside original frame')
            event('FRAME',source_pts_s=pts,receive_timestamp=received,capture_timestamp=None,
                  frame_age_ms=frame_age,video_index=processed-1)
            if detections is not None and 2 not in selected and frame_id not in detections:
                raise ValueError(f'No detection-input annotation for frame {frame_id}; missing input is not an empty scene')
            persons=[dict(p) for p in detections.get(frame_id,[])] if detections is not None else []
            with latency.correlated(frame_id=frame_id):
                if 'yolo' in models:
                    persons=timed('yolo',lambda:models['yolo'].detect(image));model_calls['yolo']+=1
                    if models['yolo'].last_error: raise RuntimeError(models['yolo'].last_error)
                for person in persons:
                    x1,y1,x2,y2=person['bbox']
                    if not (0<=x1<x2<=w and 0<=y1<y2<=h): raise ValueError('Detection bbox outside frame')
                if 'tracker' in models:
                    persons=timed('tracking',lambda:models['tracker'].update(persons,pts if pts is not None else received))
                if 'reid' in models and persons:
                    crops=[image[int(p['bbox'][1]):int(p['bbox'][3]),int(p['bbox'][0]):int(p['bbox'][2])] for p in persons]
                    emb=timed('reid',lambda:models['reid'].extract_embeddings(crops))
                    model_calls['reid']+=(len(crops)+models['reid'].batch_size-1)//models['reid'].batch_size
                    scores=(emb*reference).sum(dim=-1).detach().cpu().tolist()
                    for p,score in zip(persons,scores): p['similarity']=score
                faces=[]
                if 'scrfd' in models:
                    if roi:
                        from ai.face.face_detector import detect_face_inside_person
                        faces=timed('scrfd',lambda:detect_face_inside_person(models['scrfd'],image,dict(bbox=roi),
                            cfg.FACE_PERSON_ROI_TOP_RATIO))
                    else: faces=timed('scrfd',lambda:models['scrfd'].detect(image))
                    model_calls['scrfd']+=1
                if 'arcface' in models:
                    from ai.face.face_alignment import align_face
                    for face in faces:
                        aligned=align_face(image,face['landmarks'])
                        if aligned is None:
                            event('REJECT',reject_reason='ALIGNMENT_FAILED');continue
                        embedding=timed('arcface',lambda:models['arcface'].extract_embedding(aligned));model_calls['arcface']+=1
                        match=models['matcher'].match(embedding)
                        event('RECOGNITION',bbox=face['bbox'],recognition=match)
                if 'quality' in models:
                    quality=timed('quality',lambda:models['quality'].analyze(image,dict(bbox=roi)))
                    event('FOCUS_QUALITY',bbox=roi,quality=quality)
                for p in persons: event('PERSON',bbox=p['bbox'],track_id=p.get('track_id'),similarity=p.get('similarity'),person_ratio=(p['bbox'][3]-p['bbox'][1])/h)
                for face in faces: event('FACE',bbox=face['bbox'],landmarks=face['landmarks'])
                if gt: gt.observe(frame_id,faces if stage==7 else persons)
            overlay=image.copy()
            for p in persons+faces:
                x1,y1,x2,y2=map(int,p['bbox']);cv2.rectangle(overlay,(x1,y1),(x2,y2),(0,255,0),2)
            cv2.putText(overlay,f'frame={frame_id} replay={not online}',(12,28),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,255,255),2)
            if 'digital' in models:
                models['digital'].update(image.shape,dict(bbox=roi),now=pts if pts is not None else received)
                digital,_=timed('digital',lambda:models['digital'].render(image))
                if processed==1: cv2.imwrite(str(folder/'digital_snapshot.jpg'),digital)
            if args.record:
                if not writers:
                    for name in ('original','overlay'):
                        writer=cv2.VideoWriter(str(folder/(name+'.mp4')),cv2.VideoWriter_fourcc(*'mp4v'),fps,(w,h))
                        writers.append(writer)
                        if not writer.isOpened(): raise RuntimeError('MP4 encoder unavailable')
                writers[0].write(image);writers[1].write(overlay)
        wall=time.perf_counter()-started
        metrics=summarize_trace(trace)
        metrics['latency_ms'].update({name+'_ms':distribution(v) for name,v in metrics_times.items()})
        metrics.update(frames=processed,model_calls=dict(model_calls),wall_seconds=wall,
            reference_model_calls=1 if 'reid' in models else 0,
            throughput_fps=processed/wall if wall else None,source_fps=fps,
            received_interval_fps=(processed-1)/(last_receive-first_receive) if processed>1 and last_receive>first_receive else None,
            dropped_frames=dropped if online else 0,cpu_process_percent=100*(time.process_time()-cpu_start)/wall,
            dropped_frames_scope='mailbox gaps between consumed frames; excludes pending at stop' if online else 'no replay frames skipped',
            gpu_utilization=None,ground_truth=gt.summary() if gt else None,
            tracking_recovery_ms=None,false_reacquisition=None)
        required_models={2:'yolo',3:'reid',7:'scrfd',9:'arcface'}
        unexercised=[name for s,name in required_models.items() if s in selected and not model_calls[name]]
        metrics['unexercised_models']=unexercised
        metrics['metric_availability']=dict(sensor_frame_latency='NOT_RUN: no synchronized sensor timestamp',
            gpu_utilization='NOT_IMPLEMENTED: no GPU sampler adapter',
            identity_accuracy='NOT_IMPLEMENTED: bbox GT is not identity-recognition GT',
            bbox_accuracy='MEASURED' if gt and gt.evaluated else 'NOT_RUN: no evaluated bbox ground truth',
            settling='NOT_RUN: replay cannot measure physical motors')
        counters=profiler.snapshot()
        metrics['profiler_counters']=counters
        for entry in profiler.summary():
            metrics['latency_ms'][entry['stage']]={k:v for k,v in entry.items() if k not in ('stage','percentile_window_count','p95')}
        status,reason=acceptance(metrics,args.limit_values)
        if processed==0: status,reason='FAIL','No frames decoded/received'
        elif counters.get('dropped_log_events',0): status,reason='FAIL','Profiler dropped events; evidence incomplete'
        elif model_errors: status,reason='FAIL','Production model logged errors; see console/log evidence'
        elif unexercised: status,reason='NOT_RUN','No eligible observations for models: '+', '.join(unexercised)
        execution_ok=processed>0 and not model_errors and not counters.get('dropped_log_events',0)
        return dict(status=status,reason=reason,execution_status='PASS' if execution_ok else 'FAIL',
            evidence='online_receive' if online else 'video_replay',metrics=metrics,
            modules=sorted(selected),scope='Sequential isolated adapters; no target selection, auto motor, event-driven face scheduler or GUI event loop',
            recording_scope='decoded input frames, not compressed RTSP bitstream; events video_index maps both videos',
            model_errors=model_errors,
            providers={k:v.session.get_providers() for k,v in models.items() if hasattr(v,'session')})
    finally:
        if source is not None: source.release()
        if receiver is not None: receiver.stop()
        if glib_loop is not None: glib_loop.quit();glib_thread.join(2)
        for writer in writers: writer.release()
        profiler.close()
        logging.getLogger('ptz.events').removeHandler(error_handler)
        with (folder/'events.jsonl').open('w') as stream:
            for row in trace: stream.write(json.dumps(row,allow_nan=False)+'\n')


@contextmanager
def camera_lease(args):
    """Advisory runner lock plus explicit operator attestation and local app check.

    Legacy apps do not honor this lock. The attestation covers other machines
    and vendor clients, which cannot be detected by this process.
    """
    if not args.exclusive:
        raise ValueError('Online requires --exclusive: operator confirms all other camera controllers are closed')
    import fcntl
    cfg=json.loads(args.config.read_text())
    key=hashlib.sha256(str((cfg.get('ptz_ip'),cfg.get('ptz_port',80))).encode()).hexdigest()[:20]
    path=Path('/tmp')/('ptz-benchmark-'+key+'.lock')
    with path.open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Camera is leased by another benchmark')
        own={os.getpid(),os.getppid()}
        for item in Path('/proc').iterdir():
            if not item.name.isdecimal() or int(item.name) in own: continue
            try:
                argv=(item/'cmdline').read_bytes().decode(errors='replace').split('\0')
                cwd=(item/'cwd').resolve()
            except (PermissionError,FileNotFoundError,ProcessLookupError): continue
            if not argv or not any('python' in Path(a).name for a in argv[:1]): continue
            scripts={'main.py','test/camera.py','test/detect.py','test/detect_v2.py','test/ha_ptz/main.py',
                     'tools/camera_diagnostics.py','tools/zoom_benchmark.py','tools/probe_zoom.py'}
            if any(str((cwd/a).resolve()) in {str(ROOT/s) for s in scripts} for a in argv[1:] if a.endswith('.py')) or 'zoom_calibration' in argv:
                raise RuntimeError(f'Potential camera application active: PID {item.name}; close it first')
        try: yield
        finally: fcntl.flock(lock,fcntl.LOCK_UN)


def online_run(stage,args,folder):
    if args.case not in ('stream','rpc','motor'):
        return dict(status='NOT_IMPLEMENTED',reason='Online supports stage 0 stream/rpc and supervised stage 1/8 motor diagnostics')
    if args.case=='motor' and (not args.confirm_motor or stage not in (1,8)):
        return dict(status='NOT_RUN',reason='Motor requires stage 1/8 and --confirm-motor plus --exclusive')
    with camera_lease(args):
        if stage==0 and args.case=='stream': return replay_run(stage,args,folder,online=True)
        if stage==0 and args.case=='rpc':
            from camera.dahua_rpc import ZoomRPC
            from camera.ptz_controller import PTZController
            cfg=json.loads(args.config.read_text())
            if cfg.get('ptz_backend', 'dahua') != 'dahua':
                raise ValueError('Dahua-only diagnostic; use tools/check_camera.py for HANET')
            camera=PTZController(cfg['ptz_ip'],cfg['ptz_username'],cfg['ptz_password'],
                port=cfg.get('ptz_port',80),channel=cfg.get('ptz_channel',0),autoconnect=False,
                rpc_factory=lambda **kw:ZoomRPC(**kw,read_only=True))
            rows=[]
            try:
                for index in range(args.frames):
                    tick=time.perf_counter();status=camera.read_status()
                    rows.append(dict(event='RPC_TELEMETRY',timestamp=datetime.now(timezone.utc).isoformat(),
                        monotonic_ns=time.perf_counter_ns(),metric='rpc_status_ms',duration_ms=(time.perf_counter()-tick)*1000,
                        telemetry=status,frame_id=None,session_id=None,track_id=None,track_generation=None))
                    if time.perf_counter()-tick < .1: time.sleep(.1-(time.perf_counter()-tick))
            finally:
                try:
                    if camera._rpc: camera._rpc.close(stop_motors=False)
                finally: camera._command_worker.close()
            with (folder/'events.jsonl').open('w') as stream:
                for row in rows: stream.write(json.dumps(public_value(row))+'\n')
            metrics=summarize_trace(rows);status,reason=acceptance(metrics,args.limit_values)
            return dict(status=status,reason=reason,execution_status='PASS',evidence='hardware_read_only',metrics=metrics)
        if args.case=='motor' and stage==1:
            if args.position is None: return dict(status='NOT_RUN',reason='Supply interior --position (.15,1); no automatic default motion')
            from tools.zoom_benchmark import motor_run
            evidence=motor_run(args.config,args.position,folder)
            return dict(status='NOT_RUN',reason='Lens endpoint measured; visual settling/ratio requires synchronized scene evidence',
                        execution_status='PASS',evidence='supervised_hardware',measurements=evidence)
        if args.case=='motor' and stage==8:
            if args.focus is None: return dict(status='NOT_RUN',reason='Supply --focus 0..32768; this measures manual focus travel, not target AF accuracy')
            from tools.camera_diagnostics import main
            main(['--config',str(args.config),'--execute','--focus',str(args.focus),'--output',str(folder/'focus.csv')])
            return dict(status='NOT_RUN',reason='Focus travel recorded; target sharpness/settling acceptance needs annotation',
                        execution_status='PASS',evidence='supervised_hardware')
    return dict(status='NOT_IMPLEMENTED',reason='Stage/case combination is not implemented')


def execute(stage,args,folder):
    if args.mode=='online': return online_run(stage,args,folder)
    if args.case=='unit': return unit_run(stage,args,folder)
    if args.case=='replay': return replay_run(stage,args,folder)
    if args.case=='embedding': return embedding_run(stage,args,folder)
    if args.case=='trace': return trace_run(args,folder)
    if args.case=='mock' and stage in (1,6,11):
        if stage==1:
            from tools.zoom_benchmark import mock_run
            measured=mock_run(folder)
        else:
            from tools.benchmark_hybrid_acceptance import run
            measured=run(folder)
        rows=[]
        if measured.get('trace'): rows=list(read_rows(measured['trace']))
        return dict(status='PASS',reason='Existing mock assertions passed; no camera/model accuracy measured',
                    evidence='mock_integration',measurements=measured,metrics=summarize_trace(rows))
    if args.case=='manual':
        return dict(status='NOT_RUN',reason='Operator protocol: docs/modular_benchmark.md; attach annotated trace/video and acceptance limits')
    if args.case=='atomic-rebind' and stage==5:
        return dict(status='NOT_IMPLEMENTED',reason='Production has session/track validation but no shared track_generation atomic-rebind API; existing guards are tested by case unit')
    return dict(status='NOT_IMPLEMENTED',reason='No adapter for this stage/case/mode; use list and docs/modular_benchmark.md')


def comparison(paths,output):
    reports=[json.loads((p/'report.json').read_text()) for p in paths]
    manifests=[json.loads((p/'manifest.json').read_text()) for p in paths]
    baseline=manifests[0]
    mismatches=[]
    for p,m in zip(paths[1:],manifests[1:]):
        for key in ('inputs','model_versions','packages','configuration','platform'):
            if m.get(key)!=baseline.get(key): mismatches.append(f'{p}: {key} differs')
        for key in ('mode','case','modules','scenario','distance','frames','pace','roi','iou','record','test_filter'):
            if m['arguments'].get(key)!=baseline['arguments'].get(key): mismatches.append(f'{p}: {key} differs')
    rows=[]
    def values(report):
        groups={}
        for r in report['results']:
            for name,stat in r.get('metrics',{}).get('latency_ms',{}).items():
                if stat: groups.setdefault((r['stage'],r['case'],name),[]).append(stat['p90'])
        return {k:distribution(v)['p50'] for k,v in groups.items()}
    first=values(reports[0])
    for p,report in zip(paths[1:],reports[1:]):
        for key,value in values(report).items():
            base=first.get(key)
            rows.append(dict(run=str(p),stage=key[0],case=key[1],metric=key[2],baseline_p90_median_ms=base,
                current_p90_median_ms=value,delta_ms=value-base if base is not None else None,
                delta_percent=100*(value-base)/base if base else None))
    result=dict(comparable=not mismatches,mismatches=mismatches,rows=rows,
        conclusion='Descriptive only; no automatic optimization or statistical significance claim')
    output.mkdir(parents=True,exist_ok=True);save(output/'comparison.json',result)
    import csv,html
    with (output/'comparison.csv').open('w',newline='') as stream:
        if rows:
            writer=csv.DictWriter(stream,fieldnames=rows[0]);writer.writeheader();writer.writerows(rows)
    (output/'comparison.html').write_text('<!doctype html><meta charset="utf-8"><h1>Session comparison</h1><pre>'+html.escape(json.dumps(result,indent=2))+'</pre>')
    return 0


def parse_stages(value):
    if value=='all': return list(STAGES)
    try: stages=list(dict.fromkeys(int(s) for s in value.split(',')))
    except ValueError: raise argparse.ArgumentTypeError('Use stage numbers separated by commas or all')
    if not stages or not set(stages)<=STAGES.keys(): raise argparse.ArgumentTypeError('Stages must be 0..11')
    return stages


def main(argv=None):
    if argv is None: argv=sys.argv[1:]
    if argv and argv[0]=='_unit': return unit_child(argv[1],argv[2],argv[3])
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    subs.add_parser('list')
    compare=subs.add_parser('compare');compare.add_argument('runs',nargs='+',type=Path);compare.add_argument('--output',type=Path,required=True)
    p=subs.add_parser('run')
    p.add_argument('--stage',type=parse_stages,required=True)
    p.add_argument('--case',choices=CASES,default='unit')
    p.add_argument('--mode',choices=('offline','online'),default='offline')
    p.add_argument('--repeat',type=int,default=1)
    p.add_argument('--output',type=Path,default=ROOT/'logs/modular_benchmark')
    p.add_argument('--video',type=Path);p.add_argument('--ground-truth',type=Path);p.add_argument('--detections',type=Path)
    p.add_argument('--target',type=Path);p.add_argument('--candidate',type=Path);p.add_argument('--trace',type=Path);p.add_argument('--limits',type=Path)
    p.add_argument('--modules',type=parse_stages,help='Only these replay adapters are enabled; defaults to selected stage')
    p.add_argument('--roi',type=int,nargs=4);p.add_argument('--iou',type=float,default=.5)
    p.add_argument('--frames',type=int,default=100);p.add_argument('--seconds',type=float,default=15)
    p.add_argument('--pace',action='store_true');p.add_argument('--record',action='store_true');p.add_argument('--record-fps',type=float,default=15)
    p.add_argument('--scenario',choices=SCENARIOS,default='unknown');p.add_argument('--distance',choices=('3','7','14','15','unknown'),default='unknown')
    p.add_argument('--notes',default='');p.add_argument('--test-filter',help='Substring of unittest test ID')
    p.add_argument('--timeout',type=float,default=120)
    p.add_argument('--config',type=Path,default=ROOT/'camera.json')
    p.add_argument('--exclusive',action='store_true',help='Confirm other camera-control applications/clients are closed')
    p.add_argument('--confirm-motor',action='store_true',help='Explicit supervised consent to the specified physical movement')
    p.add_argument('--position',type=float);p.add_argument('--focus',type=float)
    args=parser.parse_args(argv)
    if args.command=='list':
        for stage,(name,specs) in STAGES.items():
            cases=['unit','trace','manual']
            if stage in REPLAY_STAGES: cases.append('replay')
            if stage in (3,9): cases.append('embedding')
            if stage in (1,6,11): cases.append('mock')
            if stage==0: cases.extend(['online:stream','online:rpc'])
            if stage in (1,8): cases.append('online:motor')
            if stage==5: cases.append('atomic-rebind:NOT_IMPLEMENTED')
            print(f'{stage:2} {name:26} {", ".join(cases)}')
            print('   unit: '+', '.join(specs))
        print('e2e: NOT_IMPLEMENTED; stage 11 mock is only hybrid integration, not whole production pipeline')
        return 0
    if args.command=='compare':
        if len(args.runs)<2: parser.error('compare needs at least two run directories')
        return comparison(args.runs,args.output)
    import math
    if min(args.repeat,args.frames)<=0 or any(not math.isfinite(x) or x<=0 for x in (args.seconds,args.timeout,args.record_fps)):
        parser.error('repeat, frames, seconds, timeout and recording FPS must be positive')
    if not 0<args.iou<=1: parser.error('IoU must be (0,1]')
    if args.mode=='offline' and (args.confirm_motor or args.position is not None or args.focus is not None):
        parser.error('Offline mode forbids motor options')
    if args.mode=='online' and len(args.stage)!=1: parser.error('Online runs one supervised stage at a time')
    if args.modules and args.case not in ('replay','stream'): parser.error('--modules applies only to replay/stream')
    args.limit_values=json.loads(args.limits.read_text()) if args.limits else {}
    if args.limit_values: acceptance({},args.limit_values)
    setup_imports()
    directory=args.output/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    save(directory/'manifest.json',manifest(args))
    report=dict(schema_version=SCHEMA,run_id=directory.name,results=[],limitations=[
        'Unit/mock PASS does not establish hardware performance or accuracy.',
        'Sensor latency, unlabeled identity accuracy and GPU utilization remain null when unmeasured.',
        'Replay is sequential adapter composition; production scheduling is assessed through existing mocks and imported traces.'])
    if args.mode=='offline': deny_network()
    for repeat in range(1,args.repeat+1):
        for stage in args.stage:
            folder=directory/f'stage_{stage}_{args.case}_{repeat}';folder.mkdir()
            started=time.perf_counter()
            try:
                # Each replay/model run is a separate process to avoid singleton logger,
                # CUDA context and model cache leakage across repetitions.
                if args.case!='unit':
                    worker=folder/'request.json';save(worker,vars(args))
                    env=dict(os.environ,PTZ_BENCHMARK_CHILD='1')
                    command=[sys.executable,str(Path(__file__).resolve()),'_case',str(stage),str(worker),str(folder)]
                    with (folder/'console.log').open('w') as log:
                        proc=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=None if args.case=='motor' and args.mode=='online' else args.timeout)
                    result=json.loads((folder/'result.json').read_text()) if (folder/'result.json').exists() else dict(status='FAIL',reason=f'child exited {proc.returncode}; see console.log')
                else: result=execute(stage,args,folder)
            except subprocess.TimeoutExpired:
                result=dict(status='FAIL',reason='Adapter timeout; inspect console.log (no motor case is forcibly timed out)')
            except Exception as exc:
                (folder/'error.txt').write_text(public_value(traceback.format_exc()))
                result=dict(status='FAIL',reason=f'{type(exc).__name__}: {exc}')
            result.update(stage=stage,name=STAGES[stage][0],case=args.case,mode=args.mode,repetition=repeat,
                          artifact=folder.name,elapsed_seconds=time.perf_counter()-started)
            try:
                timeline(folder)
            except Exception as exc:
                result.update(status='FAIL',reason=f'Timeline export failed: {type(exc).__name__}: {exc}')
            report['results'].append(result);save(folder/'result.json',result);write_report(directory,report)
            print(f'Stage {stage} {args.case} #{repeat}: {result["status"]}: {result["reason"]}',flush=True)
    print(f'Report: {directory}/report.html')
    return 1 if any(r['status']=='FAIL' for r in report['results']) else 2 if any(r['status'] in ('NOT_RUN','NOT_IMPLEMENTED') for r in report['results']) else 0


def case_child(argv):
    from types import SimpleNamespace
    stage=int(argv[1]);folder=Path(argv[3]);values=json.loads(Path(argv[2]).read_text())
    for key in ('output','video','ground_truth','detections','target','candidate','trace','limits','config'):
        if values.get(key): values[key]=Path(values[key])
    args=SimpleNamespace(**values)
    setup_imports()
    if args.mode=='offline': deny_network()
    try:
        result=execute(stage,args,folder)
    except (ImportError,ModuleNotFoundError,FileNotFoundError) as exc:
        result=dict(status='NOT_RUN',reason=f'Missing prerequisite: {type(exc).__name__}: {exc}')
    except Exception as exc:
        (folder/'error.txt').write_text(public_value(traceback.format_exc()))
        result=dict(status='FAIL',reason=f'{type(exc).__name__}: {exc}')
    save(folder/'result.json',result)
    return 0


if __name__=='__main__':
    raise SystemExit(case_child(sys.argv[1:]) if len(sys.argv)>1 and sys.argv[1]=='_case' else main())
