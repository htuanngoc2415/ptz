"""Synthetic inputs are test fixtures only, never experimental results."""
import csv
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from zoom_calibration.config import load, validate
from zoom_calibration.measurement import TargetMeasurement,ratio,gains
from zoom_calibration.session import Session
from zoom_calibration.control import Motor
from zoom_calibration.engine import Calibration
from zoom_calibration.analysis import analyze,generate_profile,estimate_pulse_duration
from zoom_calibration.capture import Capture
from zoom_calibration.recording import Recorder
from zoom_calibration.replay import replay_session,annotate
from zoom_calibration.pipeline import Pipeline

class FakeCamera:
    def __init__(self,delay=0,fail_start=False,fail_stop=False):
        self.calls=[]
        self.delay,self.fail_start,self.fail_stop = delay,fail_start,fail_stop
        self.active=0
        self.maximum_active=0
    def start(self,d):
        self.active+=1; self.maximum_active=max(self.active,self.maximum_active)
        self.calls.append(d)
        time.sleep(self.delay)
        if self.fail_start:
            raise TimeoutError('mock start timeout')
    def stop(self):
        self.calls.append('STOP'); self.active=0
        if self.fail_stop:
            raise TimeoutError('mock stop timeout')
    def telemetry(self):
        return dict(zoom_position=None,timestamp=time.monotonic())
    def close(self):
        self.calls.append('CLOSE')

class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg=load()
        self.cfg.update(command_timeout_s=.05,stream_watchdog_s=.2,samples_per_step=3,settle_ms=5,wait_ms=0,sample_timeout_s=.5,repetitions=1)
        self.session=Session(self.tmp.name,self.cfg)
        self.motor=None
    def tearDown(self):
        if self.motor:
            self.motor.close()
        self.session.close()
        self.tmp.cleanup()
    def make_motor(self,camera):
        self.motor=Motor(lambda:camera,self.cfg,self.session)
        self.assertTrue(self.motor.ready.wait(1))
        return self.motor

class Measurements(unittest.TestCase):
    def setUp(self):
        self.cfg=load()
        self.tracker=TargetMeasurement(self.cfg)
    def detection(self,h=200,x=400,conf=.95):
        return dict(bbox=(x,100,x+100,100+h),confidence=conf)
    def test_ratio_and_gain(self):
        self.assertAlmostEqual(ratio((1,10,30,210),1000),.2)
        g=gains(.2,.3,120)
        self.assertAlmostEqual(g['relative_gain'],.5)
        self.assertAlmostEqual(g['effective_gain_per_ms'],__import__('math').log(1.5)/120)
        for args in [(0,.2,120),(.2,.3,0)]:
            with self.assertRaises(ValueError): gains(*args)
    def test_raw_and_median(self):
        self.tracker.select(self.detection())
        rows=[self.tracker.update([self.detection(h)],1000,1000) for h in [200,202,198]]
        self.assertAlmostEqual(rows[-1]['person_ratio_raw'],.198)
        self.assertAlmostEqual(rows[-1]['person_ratio_filtered'],.2)
    def test_clipping_and_loss_latch(self):
        self.tracker.select(self.detection())
        self.tracker.update([],1000,1000)
        self.assertFalse(self.tracker.update([self.detection()],1000,1000)['measurement_valid'])
        self.tracker.select(dict(bbox=(400,0,500,200),confidence=.95))
        r=self.tracker.update([dict(bbox=(400,0,500,200),confidence=.95)],1000,1000)
        self.assertTrue(r['bbox_clipped']);self.assertFalse(r['measurement_valid'])
    def test_outlier_low_confidence_motion(self):
        for d,reason in [(self.detection(350),'UNEXPLAINED_SIZE_OUTLIER'),
                         (self.detection(conf=.4),'LOW_CONFIDENCE'),
                         (self.detection(x=465),'EXCESSIVE_TARGET_MOTION')]:
            self.tracker=TargetMeasurement(self.cfg)
            self.tracker.select(self.detection())
            self.tracker.update([self.detection()],1000,1000)
            r=self.tracker.update([d],1000,1000)
            self.assertFalse(r['measurement_valid'])
            # Large motion may fail association before center-motion test.
            self.assertTrue(r['invalid_reason'])
    def test_config_limits(self):
        self.cfg['pulse_ms']=2000
        with self.assertRaises(ValueError):validate(self.cfg)

    def test_nonfinite_measurements_rejected(self):
        for value in [float('nan'), float('inf'), -float('inf')]:
            with self.assertRaises(ValueError):
                ratio((1, 10, 30, value), 1000)
            with self.assertRaises(ValueError):
                gains(.2, value, 120)
            self.tracker.select(self.detection())
            invalid = self.detection(conf=value)
            row = self.tracker.update([invalid], 1000, 1000)
            self.assertFalse(row['measurement_valid'])
            self.assertEqual(row['invalid_reason'], 'MALFORMED_DETECTION_OR_FRAME')
            self.assertFalse(self.tracker.update([self.detection()], 1000, 1000)['tracking_valid'])

    def test_configuration_types_and_finite_limits(self):
        for key, value in [('wait_ms', float('nan')), ('settle_ms', float('inf')),
                           ('zoom_position_max', float('nan')), ('filter_window', 2.5),
                           ('pulse_ms', True), ('pulse_options', []),
                           ('pulse_options', [120.5]), ('allow_baseline_return', 'false')]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate(dict(self.cfg, **{key: value}))

    def test_stream_gap_requires_target_confirmation(self):
        pipeline = Pipeline.__new__(Pipeline)
        pipeline.lock = threading.RLock()
        pipeline.tracker = self.tracker
        pipeline.session = unittest.mock.Mock()
        pipeline.engine = unittest.mock.Mock()
        pipeline.motor = None
        self.tracker.select(self.detection())
        self.assertTrue(self.tracker.update([self.detection()], 1000, 1000)['measurement_valid'])
        pipeline.error('RTSP_UNAVAILABLE_TimeoutError')
        pipeline.engine.cancel.assert_called_once()
        self.assertFalse(self.tracker.update([self.detection()], 1000, 1000)['measurement_valid'])
        self.tracker.select(self.detection())
        self.assertTrue(self.tracker.update([self.detection()], 1000, 1000)['measurement_valid'])

class MotorTests(Base):
    def test_pulse_export(self):
        cam=FakeCamera(); m=self.make_motor(cam)
        r=m.pulse('zoom_in',60,'trial').result(2)
        self.assertEqual(r['status'],'ACKNOWLEDGED')
        self.assertGreaterEqual(r['actual_duration_ms'],50)
        self.assertEqual(cam.maximum_active,1)
        self.assertNotIn('motor_start_timestamp',r)
        self.assertIn('STOP',cam.calls)
    def test_stop_preempts_and_cancels_queued(self):
        cam=FakeCamera();m=self.make_motor(cam)
        first=m.pulse('zoom_in',500)
        second=m.pulse('zoom_out',60)
        deadline=time.monotonic()+1
        while 'zoom_in' not in cam.calls and time.monotonic()<deadline:time.sleep(.005)
        m.stop('EMERGENCY_STOP')
        r=first.result(2)
        self.assertEqual(r['status'],'INTERRUPTED')
        self.assertLess(r['actual_duration_ms'],300)
        self.assertEqual(second.result(2)['status'],'FAILED')
        self.assertNotIn('zoom_out',cam.calls)
    def test_timeout_latch(self):
        cam=FakeCamera(delay=.08);m=self.make_motor(cam)
        r=m.pulse('zoom_in',60).result(2)
        self.assertEqual(r['status'],'FAILED')
        self.assertTrue(m.fault)
        with self.assertRaises(RuntimeError):m.pulse('zoom_in',60).result()
        self.assertEqual(cam.calls[-1],'STOP')
    def test_missing_start_ack_always_stop(self):
        cam=FakeCamera(fail_start=True);m=self.make_motor(cam)
        r=m.pulse('zoom_in',60).result(2)
        self.assertEqual(r['status'],'FAILED');self.assertTrue(m.fault)
        self.assertEqual(cam.calls[-1],'STOP')
    def test_invalid_pulse(self):
        m=self.make_motor(FakeCamera())
        for duration in [0,-1,10000,float('nan'),float('inf'),True,'120']:
            with self.assertRaises(ValueError):m.pulse('zoom_in',duration).result()
    def test_unconfirmed_stop_blocks(self):
        m=self.make_motor(FakeCamera(fail_stop=True))
        with self.assertRaises(RuntimeError):m.pulse('zoom_in',60).result()
    def test_guard_no_movement(self):
        cam=FakeCamera();m=self.make_motor(cam)
        r=m.pulse('zoom_in',60,guard=lambda:False).result(2)
        self.assertEqual(r['status'],'FAILED');self.assertNotIn('zoom_in',cam.calls)

class EngineTests(Base):
    def test_tracking_loss_and_cancel(self):
        cam=FakeCamera();m=self.make_motor(cam)
        rows=[dict(measurement_valid=False,receive_timestamp=time.monotonic(),person_ratio_raw=.2)]
        engine=Calibration(self.cfg,self.session,m,lambda:rows)
        engine.start('sweep');engine.thread.join(2)
        self.assertIn('PAUSED',engine.state)
        self.assertNotIn('zoom_in',cam.calls)
        engine.cancel('TEST_CANCEL')
        time.sleep(.08)
        self.assertEqual(cam.calls[-1],'STOP')
    def test_pulse_requires_baseline_permission(self):
        m=self.make_motor(FakeCamera())
        engine=Calibration(self.cfg,self.session,m,lambda:[])
        with self.assertRaises(ValueError):engine.start('pulse')
    def test_stability_rejects_stale(self):
        m=self.make_motor(FakeCamera())
        rows=[dict(measurement_valid=True,receive_timestamp=time.monotonic()-30,person_ratio_raw=.2)]
        engine=Calibration(self.cfg,self.session,m,lambda:rows)
        with self.assertRaises(RuntimeError):engine.stable()
    def test_end_to_end_measured_pulse(self):
        cam=FakeCamera();m=self.make_motor(cam)
        rows=[];stop=threading.Event()
        engine=Calibration(self.cfg,self.session,m,lambda:list(rows))
        def frames():
            i=0
            while not stop.wait(.01):
                i+=1
                value=.22 if 'zoom_in' in cam.calls else .2
                rows.append(dict(frame_id=i,receive_timestamp=time.monotonic(),measurement_valid=True,
                                 person_ratio_raw=value,bbox_x1=450,bbox_x2=550,bbox_y1=100,bbox_y2=100+value*1000,
                                 frame_width=1000,frame_height=1000,target_id=1))
        t=threading.Thread(target=frames);t.start()
        try:
            time.sleep(.06)
            engine.start('manual','zoom_in',60);engine.thread.join(3)
            self.assertEqual(engine.state,'FINISHED');self.assertEqual(engine.valid,1)
        finally:
            stop.set();t.join();engine.close()
    def test_position_limit_unknown(self):
        m=self.make_motor(FakeCamera())
        self.cfg['zoom_position_max']=300
        rows=[dict(measurement_valid=True,receive_timestamp=time.monotonic(),person_ratio_raw=.2)]
        engine=Calibration(self.cfg,self.session,m,lambda:rows)
        self.assertFalse(engine.safe())

class ExportTests(Base):
    def test_csv_null_and_json(self):
        self.session.log('frames',dict(frame_id=1,frame_pts=None,measurement_valid=False))
        self.session.close()
        with (self.session.path/'data/frames.csv').open() as f:r=list(csv.DictReader(f))[0]
        self.assertEqual(r['frame_pts'],'');self.assertEqual(r['session_id'],self.session.id)
        self.assertNotIn('camera_config',json.loads((self.session.path/'data/session.json').read_text())['config'])
    def test_empty_report_not_fake(self):
        self.session.close()
        path=analyze(self.session.path)
        self.assertIn('No valid person ratios',path.read_text())
        p=json.loads((self.session.path/'calibration/zoom_profile.json').read_text())
        self.assertEqual(p['regions'],[])
        self.assertEqual(len(list((self.session.path/'plots').glob('*.png'))),8)
    def test_profile_and_lookup(self):
        rows=[];commands=[]
        for i in range(6):
            rows.append(dict(command_id=str(i),measurement_valid=True,region='A',zoom_direction='zoom_in',pulse_ms=120,
                             ratio_before=.2,ratio_after=.22,baseline_group='same',**gains(.2,.22,120)))
            commands.append(dict(command_id=str(i),command_type='zoom_in',status='ACKNOWLEDGED',actual_duration_ms=120))
        p=generate_profile(rows,self.session.meta,commands)
        self.assertTrue(p['regions'][0]['reliable'])
        self.assertIsNotNone(estimate_pulse_duration(.2,.5,dict(region='A'),'zoom_in',p)['duration_ms'])
        self.assertIsNone(estimate_pulse_duration(.2,.5,None,'zoom_in',p)['duration_ms'])
        self.assertFalse(generate_profile(rows[:2],self.session.meta,commands)['regions'][0]['reliable'])
    def test_profile_rejects_bad_duration(self):
        rows=[dict(command_id='a',measurement_valid=True,region='A',zoom_direction='zoom_in',pulse_ms=60,
                   ratio_before=.2,ratio_after=.22,**gains(.2,.22,60))]*6
        commands=[dict(command_id='a',command_type='zoom_in',status='ACKNOWLEDGED',actual_duration_ms=500)]
        self.assertEqual(generate_profile(rows,self.session.meta,commands)['regions'],[])
    def test_offline_replay_never_connects(self):
        self.session.close()
        with patch('zoom_calibration.control.RPCAdapter',side_effect=AssertionError('hardware forbidden')):
            out=replay_session(self.session.path)
        self.assertTrue(out.is_file())

class IOTests(Base):
    def test_rtsp_reconnect(self):
        errors=[]
        c=Capture('unused',lambda p:None,errors.append)
        calls=[]
        def unavailable():
            calls.append(1)
            if len(calls)>1:c.closed.set()
            raise RuntimeError('mock disconnect')
        c._rtsp=unavailable
        c.start();c.thread.join(2)
        self.assertEqual(len(calls),2);self.assertEqual(len(errors),2)
        c.close()
    def test_recording_shutdown_annotation(self):
        import numpy as np
        import cv2
        r=Recorder(self.session,self.cfg);r.start()
        for i in range(3):
            p=dict(frame_id=i,image=np.zeros((48,64,3),np.uint8),receive_timestamp=time.monotonic())
            r.submit(p)
            self.session.log('frames',dict(frame_id=i,measurement_valid=False))
        r.close();self.session.close()
        self.assertFalse(r.thread.is_alive());self.assertFalse(r.failed.is_set())
        cap=cv2.VideoCapture(str(self.session.path/'video/original.mp4'))
        self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),3);cap.release()
        self.assertEqual(len(annotate(self.session.path)),1)

class AdditionalIntegration(Base):
    def test_stale_inference_frame_latches_identity(self):
        import numpy as np
        detection = dict(bbox=(20, 10, 40, 80), confidence=.95)
        class Detector:
            last_error = None
            def __init__(self, **kwargs):
                pass
            def detect(self, image):
                return [detection]
        pipeline = Pipeline(self.cfg, self.session, detector_factory=Detector)
        def submit(frame_id, age=0):
            pipeline.submit(dict(frame_id=frame_id, receive_timestamp=time.monotonic()-age,
                                 image=np.zeros((100, 100, 3), np.uint8)))
            deadline = time.monotonic()+1
            while time.monotonic()<deadline:
                with pipeline.lock:
                    if pipeline.latest and pipeline.latest['frame_id']==frame_id:
                        return dict(pipeline.latest)
                time.sleep(.005)
            self.fail('Inference worker did not process frame')
        try:
            with pipeline.lock:
                pipeline.tracker.select(detection)
            self.assertTrue(submit(1)['measurement_valid'])
            self.assertEqual(submit(2, 30)['invalid_reason'], 'FRAME_TOO_OLD')
            self.assertFalse(submit(3)['tracking_valid'])
            with pipeline.lock:
                pipeline.tracker.select(detection)
            self.assertTrue(submit(4)['measurement_valid'])
        finally:
            pipeline.close()

    def producer(self,cam):
        rows=[];done=threading.Event()
        def run():
            i=0
            while not done.wait(.01):
                i+=1
                value=.2+.02*(cam.calls.count('zoom_in')-cam.calls.count('zoom_out'))
                rows.append(dict(frame_id=i,receive_timestamp=time.monotonic(),measurement_valid=True,
                                 person_ratio_raw=value,bbox_x1=450,bbox_x2=550,bbox_y1=100,bbox_y2=100+value*1000,
                                 frame_width=1000,frame_height=1000,target_id=1))
        thread=threading.Thread(target=run);thread.start()
        return rows,done,thread
    def test_random_pulses_remeasure_equivalent_baseline(self):
        cam=FakeCamera();m=self.make_motor(cam)
        self.cfg['allow_baseline_return']=True
        rows,done,thread=self.producer(cam)
        engine=Calibration(self.cfg,self.session,m,lambda:list(rows))
        try:
            time.sleep(.06);engine.start('pulse');engine.thread.join(5)
            self.assertEqual(engine.state,'FINISHED');self.assertEqual(engine.valid,3)
        finally:
            done.set();thread.join();engine.close()
        self.session.close()
        from zoom_calibration.analysis import read_table
        measurements=read_table(self.session.path,'measurements')
        baselines=[float(r['ratio_before']) for r in measurements]
        self.assertEqual(len(baselines),3)
        self.assertTrue(all(abs(b-.2)<.0001 for b in baselines))
    def test_target_controller_metrics(self):
        cam=FakeCamera();m=self.make_motor(cam)
        self.cfg['pulse_ms']=60
        rows,done,thread=self.producer(cam)
        engine=Calibration(self.cfg,self.session,m,lambda:list(rows))
        try:
            time.sleep(.06);engine.start('target');engine.thread.join(8)
            self.assertEqual(engine.state,'FINISHED')
        finally:
            done.set();thread.join();engine.close()
        self.session.close()
        from zoom_calibration.analysis import read_table
        trial=read_table(self.session.path,'trials')[0]
        self.assertEqual(trial['measurement_valid'],'True')
        self.assertGreater(float(trial['command_count']),0)
        self.assertGreater(float(trial['time_to_target_s']),0)
    def test_pipeline_capture_context_and_stream_watchdog(self):
        import numpy as np
        from zoom_calibration.pipeline import Pipeline
        class Detector:
            last_error=None
            def __init__(self,**kw):pass
            def detect(self,image):return []
        cam=FakeCamera();m=self.make_motor(cam)
        p=Pipeline(self.cfg,self.session,m,detector_factory=Detector)
        engine=Calibration(self.cfg,self.session,m,p.observations);p.engine=engine
        try:
            p.submit(dict(frame_id=1,receive_timestamp=time.monotonic(),timestamp=time.monotonic(),image=np.zeros((32,32,3),np.uint8)))
            deadline=time.monotonic()+1
            while p.latest is None and time.monotonic()<deadline:time.sleep(.01)
            self.assertEqual(p.latest['current_command'],'STOP')
            self.assertIn('telemetry_timestamp',p.latest)
            class StaleCapture:
                offline=False
                last_receive=time.monotonic()-10
                def close(self):pass
            p.capture=StaleCapture()
            time.sleep(.25)
            self.assertIn('WATCHDOG',engine.state)
            self.assertEqual(cam.calls[-1],'STOP')
        finally:
            engine.close();p.close()


if __name__=='__main__':unittest.main()
