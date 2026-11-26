import unittest
import os
import runpy
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import torch
import config
from person_search.reid import OSNetReID
from ai.reid.gallery import TargetEmbeddingGallery
from runtime.identity_shadow_batch import PreparedShadowBatch
from runtime.scheduling import scheduler, BackgroundBusy


def model_fixture(batch_size=2):
    class Network(torch.nn.Module):
        calls=0
        def forward(self,x):
            self.calls+=1
            out=torch.zeros((len(x),512));out[:,0]=1;out[:,1]=x.mean((1,2,3))
            return out
    model=object.__new__(OSNetReID)
    model.model=Network().eval();model.device=torch.device('cpu');model.batch_size=batch_size
    model.prepared=0;model.owners=[]
    def preprocess(im):
        model.prepared+=1;model.owners.append(scheduler.owner)
        return torch.tensor(im.copy(),dtype=torch.float32).permute(2,0,1)/255
    model.preprocess=preprocess
    refs=torch.zeros((1,512));refs[0,0]=1
    return model,TargetEmbeddingGallery(refs)


class PreparedBatchTests(unittest.TestCase):
    def test_default_v4_and_explicit_v3_rollback(self):
        for override, expected in ((None, True), ('0', False), ('1', True)):
            with self.subTest(override=override), patch.dict(os.environ):
                os.environ.pop('PTZ_SHADOW_BATCHED', None)
                if override is not None:
                    os.environ['PTZ_SHADOW_BATCHED'] = override
                self.assertEqual(runpy.run_path(config.__file__)['IDENTITY_SHADOW_BATCHED'], expected)
    def test_resume_missing_minibatch_without_repeating_work(self):
        model,gallery=model_fixture();crops=[np.full((4,4,3),i*40,np.uint8) for i in range(5)]
        job=PreparedShadowBatch(model,gallery,crops);admissions=[]
        @contextmanager
        def admission(*args,**kw):
            admissions.append(1)
            if len(admissions)==2:raise BackgroundBusy('foreground')
            yield True
        with patch('runtime.identity_shadow_batch.scheduler.use',admission):
            with self.assertRaises(BackgroundBusy):job.run(lambda:True)
            self.assertEqual(model.model.calls,1);self.assertEqual(model.prepared,4)
            scores,vectors=job.run(lambda:True)
        self.assertEqual(model.model.calls,3);self.assertEqual(model.prepared,5)
        self.assertEqual(job.completed_passes,3);self.assertIsNone(job.prepared)
        self.assertEqual(len(vectors),5);self.assertEqual(scores,sorted(scores,reverse=True))
        self.assertTrue(all(v is None for v in model.owners))
    def test_expiry_before_admission_does_not_run_model(self):
        model,gallery=model_fixture();job=PreparedShadowBatch(model,gallery,[np.zeros((4,4,3),np.uint8)])
        checks=[]
        def valid():checks.append(1);return len(checks)==1
        with self.assertRaises(TimeoutError):job.run(valid)
        self.assertEqual(model.prepared,1);self.assertEqual(model.model.calls,0)
        self.assertIsNone(scheduler.owner)
    def test_foreground_priority_keeps_prepared_cpu_batch(self):
        model,gallery=model_fixture();job=PreparedShadowBatch(model,gallery,[np.zeros((4,4,3),np.uint8)])
        with scheduler.condition:scheduler.waiting_foreground+=1
        try:
            with self.assertRaises(BackgroundBusy):job.run(lambda:True)
            self.assertEqual(model.model.calls,0);self.assertEqual(model.prepared,1)
        finally:
            with scheduler.condition:scheduler.waiting_foreground-=1
        job.run(lambda:True)
        self.assertEqual(model.prepared,1);self.assertEqual(model.model.calls,1)
    def test_backend_and_eval_checks(self):
        model,gallery=model_fixture()
        with self.assertRaises(TypeError):PreparedShadowBatch(object(),gallery,[])
        model.model.train()
        with self.assertRaises(ValueError):PreparedShadowBatch(model,gallery,[])
    def test_invalid_output_is_rejected(self):
        model,gallery=model_fixture();model.model.forward=lambda x:torch.zeros((len(x),512))
        with self.assertRaises(RuntimeError):PreparedShadowBatch(model,gallery,[np.zeros((4,4,3),np.uint8)]).run(lambda:True)
        self.assertIsNone(scheduler.owner)
    def test_empty_no_inference(self):
        model,gallery=model_fixture();self.assertEqual(PreparedShadowBatch(model,gallery,[]).run(lambda:True),([],[]))
        self.assertEqual(model.model.calls,0)
    def test_scorer_preserves_candidate_order_and_fusion(self):
        from runtime.identity_shadow import ShadowScorer
        cfg=SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        # Keep input sizes equal for this tiny fixture; real model resizes all crops.
        frame=np.zeros((40,40,3),np.uint8);frame[:,20:]=120
        people=[dict(bbox=(0,0,20,40),confidence=.9),dict(bbox=(20,0,40,40),confidence=.9)]
        for normalize in (False,True):
            for fusion in ('max','weighted'):
                with self.subTest(normalize=normalize,fusion=fusion):
                    cfg.REID_NORMALIZATION_ENABLED=normalize;cfg.REID_FUSION=fusion
                    model,gallery=model_fixture(3);cfg.IDENTITY_SHADOW_BATCHED=False
                    old=ShadowScorer(cfg,model,gallery.embeddings).score(frame,[dict(p) for p in people],lambda:True)
                    cfg.IDENTITY_SHADOW_BATCHED=True
                    scorer=ShadowScorer(cfg,model,gallery.embeddings)
                    new=scorer.score(frame,[dict(p) for p in people],lambda:True)
                    for a,b in zip(old,new):
                        for k in ('raw_similarity','normalized_similarity','reid_score','quality','embedding'):self.assertEqual(a[k],b[k])
                    scorer.discard();self.assertIsNone(scorer.batch_job)
