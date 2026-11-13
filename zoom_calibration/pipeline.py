"""Bounded capture/inference/recording fanout, independent of GUI cadence."""
from collections import deque
import queue
import threading
import time
from .capture import Capture
from .measurement import TargetMeasurement
from .recording import Recorder
from .config import resolve

class Pipeline:
    def __init__(self,cfg,session,motor=None,engine=None,detector_factory=None):
        self.cfg,self.session,self.motor,self.engine = cfg,session,motor,engine
        self.detector_factory = detector_factory
        self.queue = queue.Queue(maxsize=1)
        self.rows = deque(maxlen=2000)
        self.lock = threading.RLock()
        self.tracker = TargetMeasurement(cfg)
        self.recorder = Recorder(session,cfg)
        self.capture = None
        self.latest = None
        self.closed = threading.Event()
        self.last_measurement = None
        self.capture_count = 0
        self.inference_count = 0
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._infer,name='person-inference',daemon=True)
        self.thread.start()
        self.watchdog = threading.Thread(target=self._watch,name='stream-watchdog',daemon=True)
        self.watchdog.start()
    def observations(self):
        with self.lock:
            return list(self.rows)
    def error(self,reason):
        with self.lock:
            self.tracker.invalidate()
        self.session.event('PIPELINE_ERROR',reason)
        if self.engine:
            self.engine.cancel(reason)
        elif self.motor:
            self.motor.stop(reason)
    def connect(self,source,offline=False,camera=None):
        if self.capture:
            raise RuntimeError('Stream already started; reset session to reconnect/replay')
        self.capture = Capture(source,self.submit,self.error,offline,camera)
        self.capture.start()
    def select(self,index,frame_id):
        with self.lock:
            if not self.latest or self.latest['frame_id']!=frame_id:
                return False
            self.tracker.select(self.latest['detections'][index])
            self.session.event('TARGET_CONFIRMED',str(self.tracker.target_id))
            return True
    def submit(self,packet):
        self.capture_count += 1
        packet.update(current_command=self.motor.current if self.motor else 'OFFLINE',
                      trial_id=self.engine.trial_id if self.engine else None,
                      command_id=self.motor.command_id if self.motor else None)
        if self.motor:
            telemetry = self.motor.telemetry()
            if telemetry:
                telemetry['telemetry_timestamp'] = telemetry.pop('timestamp')
                packet.update(telemetry)
        self.recorder.submit(packet)
        try:
            self.queue.put_nowait(packet)
        except queue.Full:
            try:
                old = self.queue.get_nowait()
                self.session.log('frames',self._base(old)|dict(measurement_valid=False,invalid_reason='INFERENCE_FRAME_DROPPED'))
            except queue.Empty:
                pass
            self.queue.put_nowait(packet)
    def _base(self,p):
        h,w = p['image'].shape[:2]
        return {k:v for k,v in p.items() if k!='image'} | dict(frame_width=w,frame_height=h)
    def _infer(self):
        detector = None
        try:
            if self.detector_factory is None:
                from ai.detector import PersonDetector
            else:
                PersonDetector = self.detector_factory
            # Resolve config path; reject missing weights rather than trigger downloads.
            path = resolve(self.cfg['model_path'])
            if not path.is_file():
                raise FileNotFoundError('Configured person model does not exist')
            detector = PersonDetector(model_path=str(path),device=self.cfg['device'],conf_threshold=self.cfg['confidence'])
        except Exception as exc:
            self.error('DETECTOR_LOAD_'+type(exc).__name__)
        while not self.closed.is_set():
            try:
                p = self.queue.get(timeout=.1)
            except queue.Empty:
                continue
            row = self._base(p)
            row['inference_start_timestamp'] = time.monotonic()
            detections = []
            reason = ''
            if time.monotonic()-p['receive_timestamp']>self.cfg['max_frame_age_s']:
                reason = 'FRAME_TOO_OLD'
            elif detector is None:
                reason = 'DETECTOR_UNAVAILABLE'
            else:
                try:
                    detections = detector.detect(p['image'])
                    if detector.last_error:
                        reason = 'DETECTOR_ERROR'
                except Exception as exc:
                    reason = 'INFERENCE_'+type(exc).__name__
            row['inference_end_timestamp'] = time.monotonic()
            row['frame_age_s'] = row['inference_end_timestamp']-p['receive_timestamp']
            if not reason and row['frame_age_s']>self.cfg['max_frame_age_s']:
                reason = 'FRAME_TOO_OLD_AFTER_INFERENCE'
            with self.lock:
                if reason:
                    self.tracker.invalidate()
                measured = self.tracker.update(detections,row['frame_width'],row['frame_height']) if not reason else dict(measurement_valid=False,invalid_reason=reason,tracking_valid=False)
                row.update(measured)
                row['measurement_timestamp'] = time.monotonic()
                self.rows.append(row)
                self.latest = dict(row,image=p['image'],detections=detections)
                self.last_measurement = row['measurement_timestamp']
            self.session.log('frames',row)
            self.inference_count += 1
            if not row['measurement_valid'] and self.engine and self.engine.thread and self.engine.thread.is_alive():
                self.error(row['invalid_reason'])
    def _watch(self):
        flagged = False
        while not self.closed.wait(.1):
            c = self.capture
            stale = c and not c.offline and (c.last_receive is None or time.monotonic()-c.last_receive>self.cfg['stream_watchdog_s'])
            infer_stale = self.motor and c and (self.last_measurement is None or time.monotonic()-self.last_measurement>self.cfg['stream_watchdog_s'])
            bad = stale or infer_stale or self.session.failed.is_set() or self.recorder.failed.is_set()
            if bad and not flagged:
                self.error('STREAM_INFERENCE_RECORDING_OR_LOG_WATCHDOG')
            flagged = bool(bad)
    def close(self):
        self.closed.set()
        if self.capture:
            self.capture.close()
        self.thread.join(5)
        while not self.queue.empty():
            p = self.queue.get_nowait()
            self.session.log('frames',self._base(p)|dict(measurement_valid=False,invalid_reason='SHUTDOWN_BEFORE_INFERENCE'))
        self.watchdog.join(1)
        self.recorder.close()
