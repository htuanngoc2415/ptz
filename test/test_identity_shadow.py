import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import numpy as np
import config
from runtime.identity_shadow import IdentityShadow


class Scorer:
    gate = None
    fail = False
    embedding = None
    def __init__(self,cfg,model,refs):
        self.refs=refs.tolist()
    def score(self,frame,persons,valid):
        if self.gate is not None:self.gate.wait(2)
        if self.fail:raise RuntimeError('test scoring failure')
        for p in persons:p['embedding']=self.refs[0] if self.embedding is None else self.embedding
        return persons


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.IDENTITY_SHADOW_DIRECTORY=self.temp.name
        self.cfg.IDENTITY_SHADOW_INTERVAL=0
        self.cfg.IDENTITY_SHADOW_MAX_RECORDS=20
        self.v=np.zeros(512);self.v[0]=1
        self.selector=SimpleNamespace(target_uid='target',embedding=self.v,reid=object(),gallery=None,stage='TARGET_LOCKED')
        self.persons=[dict(track_id=1,bbox=(0,0,20,40),confidence=.9)]
        self.frame=np.zeros((50,50,3),dtype=np.uint8)
        Scorer.gate=None;Scorer.fail=False;Scorer.embedding=None
        self.shadow=IdentityShadow(self.cfg,Scorer)
        self.fid=0
    def tearDown(self):
        if Scorer.gate is not None:Scorer.gate.set()
        self.shadow.close();self.temp.cleanup()
    def submit(self,stamp=None):
        self.fid+=1
        return self.shadow.submit(self.frame,self.persons,self.selector,self.persons[0],self.fid,time.monotonic() if stamp is None else stamp)
    def idle(self):
        deadline=time.monotonic()+3
        while self.shadow.busy and time.monotonic()<deadline:time.sleep(.005)
        self.assertFalse(self.shadow.busy)
    def rows(self):
        return [json.loads(l) for l in self.shadow.path.read_text().splitlines()]
    def test_default_off_no_thread_or_files(self):
        self.assertFalse(self.submit());self.assertIsNone(self.shadow.thread)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])
    def test_matches_baseline_without_mutating_inputs(self):
        self.shadow.start()
        for _ in range(3):self.assertTrue(self.submit());self.idle()
        self.shadow.close()
        rows=[r for r in self.rows() if r['event']=='SHADOW_COMPARISON']
        self.assertEqual(len(rows),3);self.assertFalse(rows[-1]['different'])
        self.assertEqual(rows[-1]['decision']['action'],'MATCH')
        self.assertFalse(rows[-1]['decision']['ptz_authorized'])
        self.assertEqual(rows[-1]['physical_commands'],0)
        self.assertNotIn('embedding',self.persons[0]);self.assertEqual(self.selector.stage,'TARGET_LOCKED')
    def test_one_inflight_and_stop_discards_completion(self):
        Scorer.gate=threading.Event();self.shadow.start();self.assertTrue(self.submit())
        self.assertFalse(self.submit());self.shadow.stop();Scorer.gate.set();self.shadow.close()
        self.assertEqual(self.shadow.saved,0)
    def test_reset_during_job_discards_result(self):
        Scorer.gate=threading.Event();self.shadow.start();self.submit()
        self.shadow.invalidate();Scorer.gate.set();self.idle()
        self.assertEqual(self.shadow.saved,0)
    def test_target_change_during_job_discards_old_result(self):
        Scorer.gate=threading.Event();self.shadow.start();self.submit()
        self.selector.target_uid='new';self.selector.embedding=self.v.copy();self.submit()
        Scorer.gate.set();self.idle();self.assertEqual(self.shadow.saved,0)
        self.submit();self.idle();self.shadow.close()
        rows=[r for r in self.rows() if r['event']=='SHADOW_COMPARISON']
        self.assertEqual(rows[-1]['decision']['target_uid'],'new')
    def test_expired_and_future_jobs_not_admitted(self):
        self.shadow.start()
        self.assertFalse(self.submit(time.monotonic()-5));self.assertFalse(self.submit(time.monotonic()+5))
        self.assertEqual(self.shadow.saved,0)
    def test_delayed_completion_rejected(self):
        self.cfg.MAX_CONTROL_OBSERVATION_AGE=.03
        Scorer.gate=threading.Event();self.shadow.start();self.submit();time.sleep(.05)
        Scorer.gate.set();self.idle();self.assertEqual(self.shadow.saved,0)
        self.assertGreater(self.shadow.stale,0)
    def test_failure_is_contained_and_restart_works(self):
        Scorer.fail=True;self.shadow.start();self.submit();self.idle();self.shadow.close()
        self.assertIn('test scoring failure',self.shadow.error)
        Scorer.fail=False;self.assertTrue(self.shadow.start());self.submit();self.idle()
        self.assertEqual(self.shadow.saved,1)
    def test_sampling_and_record_budget(self):
        self.cfg.IDENTITY_SHADOW_INTERVAL=10;self.cfg.IDENTITY_SHADOW_MAX_RECORDS=1
        self.shadow.start();self.submit();self.idle();self.assertFalse(self.shadow.enabled)
        self.assertEqual(self.shadow.saved,1)
    def test_sampling_interval_skips_even_when_worker_idle(self):
        self.cfg.IDENTITY_SHADOW_INTERVAL=10
        self.shadow.start();self.assertTrue(self.submit());self.idle()
        self.assertFalse(self.submit());self.assertEqual(self.shadow.dropped,1)
    def test_disk_error_is_contained(self):
        p=Path(self.temp.name)/'file';p.write_text('x');self.cfg.IDENTITY_SHADOW_DIRECTORY=str(p)
        self.shadow.start();self.shadow.thread.join(2)
        self.assertFalse(self.shadow.enabled);self.assertIsNotNone(self.shadow.error)

    def test_reset_retains_identity_but_requires_strong_reacquisition(self):
        self.shadow.start()
        for _ in range(3):self.submit();self.idle()
        self.shadow.invalidate()
        v=self.v.copy();v[0]=.67;v[1]=np.sqrt(1-.67**2);Scorer.embedding=v.tolist()
        for _ in range(4):self.submit();self.idle()
        self.shadow.close();rows=self.rows()
        self.assertEqual(sum(r['event']=='SHADOW_SESSION' for r in rows),1)
        self.assertEqual(sum(r['event']=='SHADOW_RESET' for r in rows),1)
        decisions=[r['decision'] for r in rows if r['event']=='SHADOW_COMPARISON']
        self.assertIsNotNone(decisions[-1]['uncertainty_age_s'])
        self.assertTrue(all(r['reason']=='LOW_REFERENCE_SIMILARITY' for r in decisions[-4:]))
        self.assertEqual(decisions[-1]['candidate_audit'][0]['reference_threshold'],.7)

    def test_start_during_baseline_reacquisition_uses_strong_threshold(self):
        self.selector.reacquiring=True
        v=self.v.copy();v[0]=.67;v[1]=np.sqrt(1-.67**2);Scorer.embedding=v.tolist()
        self.shadow.start()
        for _ in range(4):self.submit();self.idle()
        self.shadow.close()
        rs=[r for r in self.rows() if r['event']=='SHADOW_COMPARISON']
        self.assertTrue(all(r['decision']['selected_candidate'] is None for r in rs))

    def test_new_target_creates_new_identity_session(self):
        self.shadow.start();self.submit();self.idle()
        self.selector.target_uid='replacement';self.selector.embedding=self.v.copy()
        self.submit();self.idle();self.shadow.close()
        sessions=[r for r in self.rows() if r['event']=='SHADOW_SESSION']
        self.assertEqual(len(sessions),2)
        self.assertNotEqual(sessions[0]['session_id'],sessions[1]['session_id'])

    def test_lifecycle_timestamps_and_reason_counters(self):
        self.shadow.start();self.submit(time.monotonic()-5);self.submit();self.idle();self.shadow.close()
        rows=self.rows()
        self.assertLessEqual(rows[0]['monotonic_ts'],rows[-1]['monotonic_ts'])
        self.assertEqual(rows[-1]['rejection_reasons']['INPUT_AGE'],1)


class OpportunisticSchedulerTests(unittest.TestCase):
    def test_background_never_waits_or_runs_while_foreground_owns_resource(self):
        from runtime.scheduling import InferenceScheduler,opportunistic_inference,BackgroundBusy
        scheduler=InferenceScheduler();entered=threading.Event();release=threading.Event()
        def foreground():
            with scheduler.use('detection'):entered.set();release.wait(2)
        thread=threading.Thread(target=foreground);thread.start();self.assertTrue(entered.wait(1))
        try:
            with opportunistic_inference():
                with self.assertRaises(BackgroundBusy):
                    with scheduler.use('reid'):self.fail('background model must not execute')
        finally:release.set();thread.join(2)
        with scheduler.use('reid') as granted:self.assertTrue(granted)

    def test_waiting_foreground_has_priority_and_context_does_not_leak(self):
        from runtime.scheduling import InferenceScheduler,opportunistic_inference,BackgroundBusy
        scheduler=InferenceScheduler();scheduler.waiting_foreground=1
        with opportunistic_inference():
            with self.assertRaises(BackgroundBusy):
                with scheduler.use('reid'):self.fail('waiting foreground must win')
        scheduler.waiting_foreground=0
        with scheduler.use('reid'):
            self.assertEqual(scheduler.owner_branch,'reid')
        with opportunistic_inference():
            with scheduler.use('reid'):
                self.assertEqual(scheduler.owner_branch,'identity_shadow')
                with scheduler.use('reid') as granted:self.assertTrue(granted)
        self.assertIsNone(scheduler.owner);self.assertEqual(scheduler.depth,0)


class ResumableScorerTests(unittest.TestCase):
    def test_missing_pass_resumes_and_matches_baseline_scoring(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        import torch
        from ai.reid_target import ReIDTargetSelector
        from ai.reid.gallery import TargetEmbeddingGallery
        from runtime.identity_shadow import ShadowScorer
        from runtime.scheduling import BackgroundBusy
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        cfg.REID_NORMALIZATION_ENABLED=True
        cfg.IDENTITY_SHADOW_BATCHED=False  # Explicitly exercise the legacy v3 resume path.
        ref=torch.zeros((1,512));ref[0,0]=1
        class Model:
            calls=0
            def extract_embeddings(self,crops):
                self.calls+=1
                v=ref.repeat(len(crops),1).clone();v[:,0]=.6 if self.calls==1 else .8
                v[:,1]=(1-v[:,0]**2).sqrt()
                return v
        frame=np.zeros((50,50,3),dtype=np.uint8)
        persons=[dict(track_id=1,bbox=(0,0,20,40),confidence=.9)]
        original=[dict(p) for p in persons]
        baseline=ReIDTargetSelector(cfg,Model());baseline.gallery=TargetEmbeddingGallery(ref)
        baseline._scores(frame,original)
        model=Model();scorer=ShadowScorer(cfg,model,ref)
        attempts=[]
        @contextmanager
        def admission(*args,**kwargs):
            attempts.append(1)
            if len(attempts) in (1,3):raise BackgroundBusy('simulated foreground')
            yield True
        with patch('runtime.identity_shadow.scheduler.use',admission):
            with self.assertRaises(BackgroundBusy):scorer.score(frame,persons,lambda:True)
            self.assertEqual(model.calls,0)
            with self.assertRaises(BackgroundBusy):scorer.score(frame,persons,lambda:True)
            self.assertEqual(model.calls,1)
            result=scorer.score(frame,persons,lambda:True)
        self.assertEqual(model.calls,2);self.assertEqual(scorer.completed_passes,2)
        for key in ('raw_similarity','normalized_similarity','reid_score','quality'):
            self.assertEqual(result[0][key],original[0][key])
        self.assertAlmostEqual(result[0]['embedding'][0],.8)
        with self.assertRaises(TimeoutError):scorer.score(frame,persons,lambda:False)
        self.assertEqual(model.calls,2)
        scorer.discard();self.assertIsNone(scorer.raw);self.assertIsNone(scorer.persons)


class RetryWorkerTests(unittest.TestCase):
    setUp = ShadowTests.setUp
    tearDown = ShadowTests.tearDown
    submit = ShadowTests.submit
    idle = ShadowTests.idle
    rows = ShadowTests.rows
    def busy_scorer(self,always=False):
        from runtime.scheduling import BackgroundBusy
        class BusyScorer(Scorer):
            calls=0
            completed_passes=0
            def score(s,frame,persons,valid):
                s.calls+=1
                if always or s.calls==1:
                    s.completed_passes=1
                    raise BackgroundBusy('foreground active')
                s.completed_passes=2
                return super().score(frame,persons,valid)
            def discard(s):s.completed_passes=0
        self.shadow.scorer_factory=BusyScorer
        return BusyScorer
    def test_retry_completes_same_frame_without_dropping_job(self):
        self.busy_scorer();self.shadow.start();self.submit();self.idle();self.shadow.close()
        self.assertEqual(self.shadow.saved,1);self.assertEqual(self.shadow.admission_retries,1)
        self.assertEqual(self.shadow.dropped,0);self.assertEqual(self.shadow.completed_passes,2)
        self.assertEqual(self.shadow.discarded_passes,0)
    def test_continuous_contention_expires_without_publishing(self):
        self.busy_scorer(always=True);self.cfg.MAX_CONTROL_OBSERVATION_AGE=.05
        self.shadow.start();self.submit();self.idle();self.shadow.close()
        self.assertEqual(self.shadow.saved,0);self.assertEqual(self.shadow.rejections['RESULT_AGE'],1)
        self.assertEqual(self.shadow.discarded_passes,1)
    def test_stop_during_retry_wakes_worker(self):
        self.busy_scorer(always=True);self.shadow.start();self.submit()
        deadline=time.monotonic()+1
        while not self.shadow.admission_retries and time.monotonic()<deadline:time.sleep(.001)
        self.assertGreater(self.shadow.admission_retries,0)
        self.assertTrue(self.shadow.close());self.assertEqual(self.shadow.saved,0)
    def test_reset_during_retry_cancels_frame(self):
        self.busy_scorer(always=True);self.shadow.start();self.submit()
        deadline=time.monotonic()+1
        while not self.shadow.admission_retries and time.monotonic()<deadline:time.sleep(.001)
        self.assertGreater(self.shadow.admission_retries,0)
        self.shadow.invalidate();self.idle();self.shadow.close()
        self.assertEqual(self.shadow.saved,0);self.assertEqual(self.shadow.rejections['SUPERSEDED'],1)
