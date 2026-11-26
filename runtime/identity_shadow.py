"""Opt-in, bounded shadow observer. No controller or motor object is accepted.

A single worker recomputes appearance with the shared serialized ReID model.
Results are diagnostic proposals for the sampled source frame, never commands.
"""
import json
import copy
import hashlib
from pathlib import Path
import queue
import threading
import time
from uuid import uuid4
from dataclasses import replace, asdict
from collections import Counter, deque
from runtime.scheduling import opportunistic_inference, BackgroundBusy, scheduler

from ai.target_identity import Candidate, IdentityPolicy, TargetIdentityManager


class ShadowScorer:
    """Resume a bounded frame's missing pass without repeating completed inference."""
    def __init__(self, cfg, model, refs):
        from ai.reid_target import ReIDTargetSelector
        from ai.reid.gallery import TargetEmbeddingGallery
        self.cfg, self.model = cfg, model
        self.batched = bool(getattr(cfg, "IDENTITY_SHADOW_BATCHED", True))
        self.selector = ReIDTargetSelector(cfg, model)
        self.selector.gallery = TargetEmbeddingGallery(refs)
        self.refs = refs.detach().cpu().tolist()
        self.discard()

    def discard(self):
        self.persons = None
        self.crops = self.variants = self.qualities = self.indices = None
        self.raw = self.normalized = None
        self.batch_job = None
        self.completed_passes = 0

    def _extract(self, crops, valid):
        with opportunistic_inference():
            with scheduler.use('reid', valid=valid) as granted:
                if not granted:
                    raise TimeoutError('Shadow frame expired before inference')
                embeddings = self.model.extract_embeddings(crops)
                scores = self.selector.gallery.score(embeddings).detach().cpu().tolist()
                vectors = embeddings.detach().cpu().tolist()
        self.completed_passes += 1
        return scores, vectors

    def score(self, frame, persons, valid):
        from ai.reid.gallery import illumination_normalized
        if not valid():
            raise TimeoutError('Shadow frame expired')
        if persons is not self.persons:
            self.discard()
            self.persons = persons
            self.crops = [self.selector.crop(frame, p) for p in persons]
            self.qualities = [self.selector.quality.analyze(frame, p) for p in persons]
            self.indices = [i for i, q in enumerate(self.qualities) if q and
                (q['person_mean_luma'] < getattr(self.cfg, 'REID_NORMALIZE_LUMA', 90) or
                 q['local_contrast'] < getattr(self.cfg, 'REID_NORMALIZE_CONTRAST', 35))]
            if not getattr(self.cfg, 'REID_NORMALIZATION_ENABLED', False):
                self.indices = []
            self.variants = [illumination_normalized(self.crops[i],
                getattr(self.cfg, 'REID_NORMALIZE_GAMMA', .75),
                getattr(self.cfg, 'REID_NORMALIZE_CLAHE', 1.5)) for i in self.indices]
        if not persons:
            return persons
        if self.batched:
            from runtime.identity_shadow_batch import PreparedShadowBatch
            if self.batch_job is None:
                self.batch_job = PreparedShadowBatch(self.model,self.selector.gallery,self.crops+self.variants)
            try:
                scores, vectors = self.batch_job.run(valid)
            finally:
                self.completed_passes = self.batch_job.completed_passes
            size = len(self.crops)
            self.raw = scores[:size],vectors[:size]
            self.normalized = (scores[size:],vectors[size:]) if self.indices else None
        else:
            if self.raw is None:
                self.raw = self._extract(self.crops, valid)
            if self.indices and self.normalized is None:
                self.normalized = self._extract(self.variants, valid)
        normalized = dict(zip(self.indices, zip(*self.normalized))) if self.normalized else {}
        mode, weight = getattr(self.cfg, 'REID_FUSION', 'max'), getattr(self.cfg, 'REID_RAW_WEIGHT', .6)
        if mode not in ('max', 'weighted') or not 0 <= weight <= 1:
            raise ValueError('Invalid ReID fusion configuration')
        for i, (p, r, vector) in enumerate(zip(persons, *self.raw)):
            n, nv = normalized.get(i, (None, None))
            score = r if n is None else max(r, n) if mode == 'max' else weight*r+(1-weight)*n
            p.update(raw_similarity=r, normalized_similarity=n, reid_score=score,
                     quality=self.qualities[i], embedding=nv if n is not None and n > r else vector)
        return persons


class IdentityShadow:
    def __init__(self, cfg, scorer_factory=ShadowScorer):
        self.cfg, self.scorer_factory = cfg, scorer_factory
        self.enabled = False
        self.thread = None
        self.halt = threading.Event()
        self.lock = threading.RLock()
        self.pending = queue.Queue(maxsize=1)
        self.busy = False
        self.epoch = 0
        self.binding = None
        self.last_submit = float('-inf')
        self.path = None
        self.error = None
        self.saved = self.dropped = self.stale = self.different = 0
        self.rejections = Counter()
        self.admission_retries = self.completed_passes = self.discarded_passes = 0
        self.started_at = self.stopped_at = None
        self.reset_serial = 0

    def start(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            self.halt.clear()
            self.enabled = True
            self.epoch += 1
            self.binding = None
            self.last_submit = float('-inf')
            self.error = None
            self.saved = self.dropped = self.stale = self.different = 0
            self.rejections.clear()
            self.admission_retries = self.completed_passes = self.discarded_passes = 0
            self.started_at, self.stopped_at = time.monotonic(), None
            self.reset_serial = 0
            self.path = Path(self.cfg.IDENTITY_SHADOW_DIRECTORY)/('shadow_'+uuid4().hex+'.jsonl')
            self.thread = threading.Thread(target=self._run, name='identity-shadow', daemon=True)
            self.thread.start()
            return True

    def invalidate(self):
        with self.lock:
            self.epoch += 1
            self.reset_serial += 1

    def stop(self):
        with self.lock:
            self.enabled = False
            self.epoch += 1
            if self.stopped_at is None:
                self.stopped_at = time.monotonic()
            self.halt.set()

    def _reject(self, reason, stale=False):
        with self.lock:
            self.rejections[reason] += 1
            if stale:
                self.stale += 1
            else:
                self.dropped += 1
        return False

    def close(self, timeout=2.):
        self.stop()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout)
        return not (self.thread and self.thread.is_alive())

    def status(self):
        state = 'ERROR: '+self.error if self.error else 'ON' if self.enabled else 'OFF'
        return (f'Shadow {state} | saved={self.saved} skip={self.dropped} stale={self.stale} '
                f'diff={self.different}\n{self.path or ""}')

    def submit(self, frame, persons, selector, selected, frame_id, timestamp):
        if not self.enabled:
            return False
        try:
            now = time.monotonic()
            with self.lock:
                if not self.enabled:
                    return False
                binding = (getattr(selector, 'target_uid', None), id(selector.embedding))
                if binding != self.binding:
                    self.epoch += 1
                    self.binding = binding
                    self.reset_serial = 0
                if self.busy:
                    return self._reject('WORKER_BUSY')
                if now-self.last_submit < self.cfg.IDENTITY_SHADOW_INTERVAL:
                    return self._reject('RATE_LIMIT')
                if not 0 <= now-timestamp <= self.cfg.MAX_CONTROL_OBSERVATION_AGE:
                    return self._reject('INPUT_AGE')
                if selector.embedding is None or selector.reid is None or not binding[0]:
                    return False
                if frame.nbytes > 24_000_000:
                    return self._reject('FRAME_SIZE')
                candidates = [dict(candidate_id=f'{frame_id}:{i}',track_id=p.get('track_id'),
                    bbox=tuple(p['bbox']),confidence=float(p['confidence'])) for i,p in enumerate(persons)]
                chosen = next((c['candidate_id'] for p,c in zip(persons,candidates) if p is selected), None)
                gallery = getattr(selector, 'gallery', None)
                refs = gallery.embeddings if gallery is not None else selector.embedding[None,:]
                job = dict(token=(self.epoch,self.binding), frame_id=frame_id,timestamp=timestamp,
                    frame=frame.copy(),candidates=candidates, refs=refs, model=selector.reid,
                    baseline_selected=chosen,baseline_state=selector.stage,target_uid=binding[0],
                    reset_serial=self.reset_serial,
                    baseline_reacquiring=bool(getattr(selector,'reacquiring',False)),
                    submitted=now)
                self.pending.put_nowait(job)
                self.busy = True
                self.last_submit = now
                return True
        except Exception as exc:
            self.error = type(exc).__name__+': '+str(exc)
            self.stop()
            return False

    def _valid(self, job):
        return self._invalid_reason(job) is None

    def _invalid_reason(self, job):
        if not self.enabled or self.halt.is_set():return 'STOPPED'
        if job['token'] != (self.epoch,self.binding):return 'SUPERSEDED'
        if not 0 <= time.monotonic()-job['timestamp'] <= self.cfg.MAX_CONTROL_OBSERVATION_AGE:return 'RESULT_AGE'
        return None

    def _run(self):
        active = None
        identity_binding = None
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self.path.open('x') as output:
                output.write(json.dumps(dict(event='SHADOW_START',source='identity_shadow',ground_truth=False,
                    physical_commands=0,interval_s=self.cfg.IDENTITY_SHADOW_INTERVAL,
                    max_age_s=self.cfg.MAX_CONTROL_OBSERVATION_AGE,
                    comparison='same sampled frame; baseline proposal before final motor gates',
                    additional_inference=True,wall_time_ns=time.time_ns(),
                    batched=bool(getattr(self.cfg,'IDENTITY_SHADOW_BATCHED',True)),
                    monotonic_ts=self.started_at,admission='opportunistic_resumable_until_frame_deadline'))+'\n')
                root = Path(__file__).resolve().parents[1]
                output.write(json.dumps(dict(event='SHADOW_POLICY',
                    policy=asdict(replace(IdentityPolicy.from_config(self.cfg),gallery_enabled=False)),
                    source_sha256={p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in
                        ('ai/target_identity.py','runtime/identity_shadow.py','runtime/identity_shadow_batch.py','runtime/scheduling.py','person_search/reid.py')}))+'\n')
                output.flush()
                while not self.halt.is_set():
                    try:
                        job = self.pending.get(timeout=.1)
                    except queue.Empty:
                        continue
                    published = False
                    job_retries = 0
                    try:
                        if not self._valid(job):
                            self._reject(self._invalid_reason(job) or 'RESULT_AGE',stale=True)
                            continue
                        if identity_binding != job['token'][1]:
                            scorer = self.scorer_factory(self.cfg,job['model'],job['refs'])
                            policy = replace(IdentityPolicy.from_config(self.cfg),gallery_enabled=False)
                            if job['baseline_reacquiring'] or job['reset_serial']:
                                policy = replace(policy,acquire=policy.reacquire)
                            manager = TargetIdentityManager(scorer.refs[0],
                                policy,
                                session_id=uuid4().hex,target_uid=job['target_uid'],reference_views=scorer.refs[1:])
                            identity_binding = job['token'][1]
                            active = job['token']
                            output.write(json.dumps(dict(event='SHADOW_SESSION',session_id=manager.session_id,
                                target_uid=manager.target_uid,monotonic_ts=time.monotonic(),
                                initial_threshold=policy.acquire,baseline_reacquiring=job['baseline_reacquiring'],
                                reference_sha256=hashlib.sha256(
                                    json.dumps(scorer.refs).encode()).hexdigest()))+'\n')
                        elif active != job['token']:
                            manager.interrupt()
                            manager.policy = replace(manager.policy,acquire=manager.policy.reacquire)
                            active = job['token']
                            output.write(json.dumps(dict(event='SHADOW_RESET',session_id=manager.session_id,
                                target_uid=manager.target_uid,monotonic_ts=time.monotonic(),
                                reset_serial=job['reset_serial'],history_retained=True,
                                threshold=manager.policy.reacquire))+'\n')
                        started = time.monotonic()
                        while True:
                            if not self._valid(job):
                                raise TimeoutError('Shadow frame invalid while waiting')
                            try:
                                persons = scorer.score(job['frame'],job['candidates'],lambda:self._valid(job))
                                break
                            except BackgroundBusy:
                                self.admission_retries += 1
                                job_retries += 1
                                # Worker waits without holding or queuing for the accelerator.
                                self.halt.wait(.01)
                        if not self._valid(job):
                            self._reject(self._invalid_reason(job) or 'RESULT_AGE',stale=True)
                            continue
                        candidates = [Candidate(p['candidate_id'],p['track_id'],p['bbox'],p['confidence'],
                            tuple(p['embedding']),job['frame_id'],job['timestamp'],manager.session_id) for p in persons]
                        # Commit identity state only if the completion is still valid.
                        draft = copy.copy(manager)
                        for name in ('history','gallery','distractor_history'):
                            setattr(draft,name,getattr(manager,name).copy())
                        draft.tracklets = deque((dict(t) for t in manager.tracklets),maxlen=manager.tracklets.maxlen)
                        result = draft.observe(session_id=manager.session_id,frame_id=job['frame_id'],
                            timestamp=job['timestamp'],now=time.monotonic(),candidates=candidates)
                        # Stop/reset during scoring or decision cannot publish a valid comparison.
                        if not self._valid(job):
                            self._reject(self._invalid_reason(job) or 'RESULT_AGE',stale=True)
                            continue
                        manager = draft
                        different = result['selected_candidate'] != job['baseline_selected']
                        row = dict(event='SHADOW_COMPARISON',frame_id=job['frame_id'],timestamp=job['timestamp'],
                            baseline_selected=job['baseline_selected'],baseline_state=job['baseline_state'],
                            baseline_reacquiring=job['baseline_reacquiring'],reset_serial=job['reset_serial'],
                            decision=result,different=different,physical_commands=0,ground_truth=False,
                            processing_ms=(time.monotonic()-started)*1000,
                            age_ms=(time.monotonic()-job['timestamp'])*1000,
                            skipped_total=self.dropped,stale_total=self.stale,
                            rejection_reasons=dict(self.rejections),admission_retries=job_retries,
                            completed_passes=getattr(scorer,'completed_passes',0))
                        output.write(json.dumps(row,allow_nan=False)+'\n');output.flush()
                        published = True
                        self.saved += 1
                        self.different += int(different)
                        if self.saved >= self.cfg.IDENTITY_SHADOW_MAX_RECORDS:
                            self.stop()
                    except TimeoutError:
                        self._reject(self._invalid_reason(job) or 'SCORER_TIMEOUT',stale=True)
                    finally:
                        if 'scorer' in locals():
                            passes = getattr(scorer,'completed_passes',0)
                            self.completed_passes += passes
                            if not published:self.discarded_passes += passes
                            if hasattr(scorer,'discard'):scorer.discard()
                        self.pending.task_done()
                        with self.lock:
                            self.busy = False
                output.write(json.dumps(dict(event='SHADOW_STOP',saved=self.saved,skipped=self.dropped,
                    stale=self.stale,different=self.different,physical_commands=0,
                    monotonic_ts=self.stopped_at,closed_monotonic_ts=time.monotonic(),
                    rejection_reasons=dict(self.rejections),admission_retries=self.admission_retries,
                    completed_passes=self.completed_passes,discarded_passes=self.discarded_passes))+'\n')
        except Exception as exc:
            self.error = type(exc).__name__+': '+str(exc)
            self.enabled = False
            self.halt.set()
        finally:
            while True:
                try:self.pending.get_nowait();self.pending.task_done()
                except queue.Empty:break
            with self.lock:
                self.busy = False
