"""Experimental state machine; every trial includes an independently measured baseline."""
import random
import statistics
import threading
import time
from uuid import uuid4
from .measurement import gains
from .config import public_config

class Calibration:
    def __init__(self, cfg, session, motor, observations):
        self.cfg, self.session, self.motor, self.observations = cfg,session,motor,observations
        self.cancelled = threading.Event()
        self.state = 'IDLE'
        self.trial_id = None
        self.thread = None
        self.progress = ''
        self.valid = 0
        self.invalid = 0
        self.profile = None
        self.mode = None
    def cancel(self, reason='CANCELLED'):
        self.cancelled.set()
        self.state = 'PAUSED: '+reason
        if self.motor:
            self.motor.stop(reason)
    def start(self, mode, direction='zoom_in', ms=None):
        if self.thread and self.thread.is_alive():
            raise RuntimeError('Calibration already running')
        if self.motor is None or self.motor.fault:
            raise RuntimeError('Connect camera and acknowledge STOP first')
        if mode in ('pulse','bidirectional') and not self.cfg['allow_baseline_return']:
            raise ValueError('Enable measured baseline return before repeated pulse tests')
        if mode not in ('manual','sweep','pulse','bidirectional','target'):
            raise ValueError('Unknown calibration mode')
        if ms is not None and not 1<=ms<=self.cfg['max_pulse_ms']:
            raise ValueError('Pulse duration exceeds configured limit')
        self.session.event('EXPERIMENT_CONFIG',str(public_config(self.cfg)))
        self.mode = mode
        self.cancelled.clear()
        self.thread = threading.Thread(target=self._run,args=(mode,direction,ms),name='calibration-engine',daemon=True)
        self.thread.start()
    def safe(self, direction='zoom_in'):
        rows = self.observations()
        if not rows or self.session.failed.is_set() or self.cancelled.is_set():
            return False
        r = rows[-1]
        if not r['measurement_valid'] or time.monotonic()-r['receive_timestamp']>self.cfg['max_frame_age_s']:
            return False
        if not self.cfg['min_person_ratio']<r['person_ratio_raw']<self.cfg['max_person_ratio']:
            return False
        position = self.motor.telemetry().get('zoom_position') if self.motor else None
        low,high = self.cfg.get('zoom_position_min'),self.cfg.get('zoom_position_max')
        if low is not None or high is not None:
            if position is None:
                return False
            if (low is not None and direction=='zoom_out' and position<=low) or (high is not None and direction=='zoom_in' and position>=high):
                return False
        return True
    def stable(self, after=0):
        self.state = 'WAIT_FRESH_STABLE_FRAMES'
        deadline = time.monotonic()+self.cfg['sample_timeout_s']
        n = self.cfg['samples_per_step']
        while time.monotonic()<deadline:
            if self.cancelled.is_set():
                raise RuntimeError('CANCELLED')
            if not self.safe():
                raise RuntimeError('INVALID_OR_STALE_TARGET_OR_LIMIT')
            now = time.monotonic()
            rows = [r for r in self.observations() if r['receive_timestamp']>after
                    and now-r['receive_timestamp']<=self.cfg['max_frame_age_s']]
            if any(not r['measurement_valid'] for r in rows[-n:]):
                raise RuntimeError('INVALID_MEASUREMENT')
            sample = rows[-n:]
            if len(sample)==n:
                values = [r['person_ratio_raw'] for r in sample]
                median = statistics.median(values)
                if (max(values)-min(values))/median <= self.cfg['stability_fraction']:
                    centers = [((r['bbox_x1']+r['bbox_x2'])/2/r['frame_width'],
                                (r['bbox_y1']+r['bbox_y2'])/2/r['frame_height']) for r in sample]
                    if max(x[0] for x in centers)-min(x[0] for x in centers)>self.cfg['max_center_motion'] or max(x[1] for x in centers)-min(x[1] for x in centers)>self.cfg['max_center_motion']:
                        raise RuntimeError('TARGET_MOVED_DURING_MEASUREMENT')
                    return dict(ratio=median, rows=sample, zoom_position=self.motor.telemetry().get('zoom_position'))
            self.cancelled.wait(.03)
        raise RuntimeError('STABILITY_TIMEOUT')
    def _pulse(self, direction, ms):
        self.state = 'PULSE_'+direction.upper()
        f = self.motor.pulse(direction,ms,self.trial_id,guard=lambda:self.safe(direction))
        deadline = time.monotonic()+2*self.cfg['command_timeout_s']+ms/1000+3
        while not f.done():
            if time.monotonic()>deadline:
                self.cancel('COMMAND_TIMEOUT')
                raise TimeoutError('COMMAND_TIMEOUT')
            time.sleep(.02)
        r = f.result()
        if r['status']!='ACKNOWLEDGED':
            raise RuntimeError('COMMAND_'+r['status'])
        if self.cancelled.is_set():
            raise RuntimeError('CANCELLED')
        return r
    def measure(self,direction,ms,group):
        row = dict(trial_id=self.trial_id,region=self.cfg['region'],pulse_ms=ms,
                   zoom_direction=direction,baseline_group=group,measurement_valid=False)
        try:
            before = self.stable(time.monotonic())
            row.update(ratio_before=before['ratio'],zoom_position_before=before['zoom_position'])
            command = self._pulse(direction,ms)
            row['command_id'] = command['command_id']
            after_time = command['stop_response_timestamp']+(self.cfg['wait_ms']+self.cfg['settle_ms'])/1000
            # Receive-time gating is a necessary condition, not proof of camera capture time.
            if self.cancelled.wait(max(0,after_time-time.monotonic())):
                raise RuntimeError('CANCELLED')
            after = self.stable(after_time)
            b,a = before['ratio'],after['ratio']
            bc = before['rows'][-1]
            ac = after['rows'][-1]
            # Optical zoom moves off-center objects radially; calibrated subject must be centered.
            for sample in (before['rows'],after['rows']):
                if any(abs((r['bbox_x1']+r['bbox_x2'])/2/r['frame_width']-.5)>.15 for r in sample):
                    raise RuntimeError('TARGET_NOT_CENTERED_OR_MOVED')
            if abs((ac['bbox_x1']+ac['bbox_x2'])/2/ac['frame_width']-(bc['bbox_x1']+bc['bbox_x2'])/2/bc['frame_width'])>self.cfg['max_center_motion']:
                raise RuntimeError('TARGET_MOVED_BETWEEN_BASELINES')
            threshold = max(.01*b,3*statistics.pstdev(r['person_ratio_raw'] for r in before['rows']))
            latency = None
            timeline = [r for r in self.observations() if r['receive_timestamp']>command['send_timestamp']
                        and r['receive_timestamp']<=after['rows'][-1]['receive_timestamp']]
            for index in range(len(timeline)-2):
                consecutive = timeline[index:index+3]
                if all(r['measurement_valid'] and (r['person_ratio_raw']-b)*(1 if direction=='zoom_in' else -1)>threshold for r in consecutive):
                    latency = (consecutive[0]['receive_timestamp']-command['send_timestamp'])*1000
                    break
            row.update(ratio_after=a,**gains(b,a,ms),sample_count=len(before['rows'])+len(after['rows']),
                       estimated_response_latency_ms=latency,zoom_position_after=after['zoom_position'],measurement_valid=True)
            # Retain a received-timeline exposure metric separately from requested pulse duration.
            self.valid += 1
            return before,after,command,row
        except Exception as exc:
            row['invalid_reason'] = str(exc)
            self.invalid += 1
            raise
        finally:
            self.session.log('measurements',row)
    def restore(self,baseline):
        if not self.cfg['allow_baseline_return']:
            raise RuntimeError('MEASURED_BASELINE_RETURN_NOT_AUTHORIZED')
        self.state = 'RETURN_AND_REMEASURE_BASELINE'
        for _ in range(self.cfg['max_return_steps']):
            measured = self.stable(time.monotonic())
            ratio_match = abs(measured['ratio']/baseline['ratio']-1)<=self.cfg['baseline_tolerance']
            position_match = True
            if baseline['zoom_position'] is not None:
                p = measured['zoom_position']
                position_match = p is not None and abs(p-baseline['zoom_position'])<=max(1,abs(baseline['zoom_position'])*self.cfg['baseline_tolerance'])
            if ratio_match and position_match:
                return measured
            if baseline['zoom_position'] is not None and measured['zoom_position'] is not None:
                direction = 'zoom_out' if measured['zoom_position']>baseline['zoom_position'] else 'zoom_in'
            else:
                direction = 'zoom_out' if measured['ratio']>baseline['ratio'] else 'zoom_in'
            c = self._pulse(direction,min(60,self.cfg['max_pulse_ms']))
            self.cancelled.wait((self.cfg['settle_ms']+self.cfg['wait_ms'])/1000)
            if c['status']!='ACKNOWLEDGED':
                raise RuntimeError('BASELINE_RETURN_FAILED')
        raise RuntimeError('BASELINE_EQUIVALENCE_NOT_REACHED')
    def _run(self,mode,direction,ms):
        group = uuid4().hex
        trial = None
        try:
            baseline = self.stable(time.monotonic())
            if mode in ('pulse','bidirectional'):
                tasks = [(d,p) for _ in range(self.cfg['repetitions'])
                         for d in (('zoom_in','zoom_out') if mode=='bidirectional' else (direction,))
                         for p in self.cfg['pulse_options']]
                random.Random(group).shuffle(tasks)
                self.session.event('PULSE_ORDER',str(tasks))
            elif mode=='sweep':
                sweep_repeats = self.cfg['repetitions'] if self.cfg['allow_baseline_return'] else 1
                tasks = [(direction,self.cfg['pulse_ms'])]*(self.cfg['max_steps']*sweep_repeats)
            else:
                tasks = [(direction,ms or self.cfg['pulse_ms'])]
            if mode=='target':
                self._target(group)
            else:
                for i,(d,p) in enumerate(tasks):
                    self.progress = f'{i+1}/{len(tasks)}'
                    self.trial_id = uuid4().hex
                    trial = dict(trial_id=self.trial_id,mode=mode,region=self.cfg['region'],baseline_group=group)
                    if mode=='sweep' and i and i%self.cfg['max_steps']==0:
                        self.restore(baseline)
                    if mode in ('pulse','bidirectional'):
                        measured = self.restore(baseline)
                        if abs(measured['ratio']/baseline['ratio']-1)>self.cfg['baseline_tolerance']:
                            raise RuntimeError('BASELINE_MISMATCH')
                    b,a,c,_ = self.measure(d,p,group)
                    trial.update(initial_ratio=b['ratio'],final_ratio=a['ratio'],command_count=1,
                                 motor_active_ms=c.get('actual_duration_ms'),measurement_valid=True)
                    self.session.log('trials',trial)
                    trial = None
                    if mode=='sweep' and abs(a['ratio']/b['ratio']-1)<.001:
                        self.state = 'FINISHED_NO_MEASURABLE_RESPONSE'
                        break
            self.state = 'FINISHED'
        except Exception as exc:
            self.state = 'PAUSED: '+str(exc)
            self.invalid += 1
            self.session.event('CALIBRATION_PAUSED',str(exc))
            if trial:
                trial.update(measurement_valid=False,invalid_reason=str(exc))
                self.session.log('trials',trial)
        finally:
            self.motor.stop('CALIBRATION_END')
            self.trial_id = None
    def _target(self,group):
        self.trial_id = uuid4().hex
        target = self.cfg['target_ratio']
        strategy = self.cfg['strategy']
        row = dict(trial_id=self.trial_id,mode='target',strategy=strategy,target_ratio=target,
                   region=self.cfg['region'],baseline_group=group,command_count=0,motor_active_ms=0,
                   tracking_failures=0,measurement_valid=False)
        started = time.monotonic()
        peak = 0
        try:
            initial = self.stable(time.monotonic())
            row['initial_ratio'] = initial['ratio']
            for i in range(self.cfg['max_steps']):
                sample = self.stable(time.monotonic())
                ratio = sample['ratio']
                peak = max(peak,ratio-target)
                row.update(final_ratio=ratio,overshoot=max(0,peak))
                if abs(ratio-target)<=self.cfg['target_tolerance']:
                    row.update(time_to_target_s=time.monotonic()-started,measurement_valid=True,
                               settling_time_s=sample['rows'][-1]['receive_timestamp']-sample['rows'][0]['receive_timestamp'])
                    return
                d = 'zoom_in' if ratio<target else 'zoom_out'
                ms = self.cfg['pulse_ms']
                if strategy=='coarse_to_fine':
                    ms = 250 if abs(ratio-target)>.20 else 120 if abs(ratio-target)>.10 else 60
                elif strategy=='adaptive':
                    if not self.profile:
                        raise RuntimeError('LOAD_RELIABLE_PROFILE_FIRST')
                    from .analysis import estimate_pulse_duration
                    position = self.motor.telemetry().get('zoom_position')
                    estimate = estimate_pulse_duration(ratio,target,dict(region=self.cfg['region'],zoom_position=position),d,self.profile)
                    if estimate['duration_ms'] is None:
                        raise RuntimeError(estimate['reason'])
                    ms = estimate['duration_ms']
                ms = max(1,min(self.cfg['max_pulse_ms'],ms))
                b,a,c,_ = self.measure(d,ms,group)
                peak = max(peak,a['ratio']-target)
                row.update(final_ratio=a['ratio'],overshoot=max(0,peak))
                row['command_count'] += 1
                row['motor_active_ms'] += c.get('actual_duration_ms',0)
                self.progress = f'{i+1}/{self.cfg["max_steps"]}'
            raise RuntimeError('TARGET_NOT_REACHED_WITHIN_STEP_LIMIT')
        except Exception as exc:
            row.update(invalid_reason=str(exc),tracking_failures=int(not self.safe()))
            raise
        finally:
            self.session.log('trials',row)
    def close(self):
        self.cancel('SHUTDOWN')
        if self.thread:
            self.thread.join(2*self.cfg['command_timeout_s']+5)
