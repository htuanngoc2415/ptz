"""Crossing regressions with deterministic embeddings and timestamps."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import torch
import config
from ai.reid_target import ReIDTargetSelector


class CrossingTests(unittest.TestCase):
    def setUp(self):
        settings = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        settings.TARGET_CONTINUOUS_REID = False
        settings.REID_NORMALIZATION_ENABLED = False
        settings.REID_DIAGNOSTICS = False
        self.model = Mock()
        self.score = .95
        def embeddings(crops):
            out = torch.zeros(len(crops),512)
            out[:,0] = self.score
            out[:,1] = (1-self.score**2)**.5
            return out
        self.model.extract_embeddings.side_effect = embeddings
        self.s = ReIDTargetSelector(settings,self.model)
        self.s.embedding = torch.eye(512)[0]
        self.s.error = None
        self.frame = np.full((400,400,3),120,np.uint8)
        self.target = {'bbox':(100,20,180,300)}
        for now in (0.,.1,.2): self.s.select_person(self.frame,[dict(self.target)],now)
        self.original = self.s.person_box

    def test_crossing_freezes_box_and_rejects_foreground_face(self):
        foreground = {'bbox':(125,0,250,390)}
        self.assertIsNone(self.s.select_person(self.frame,[self.target,foreground],.3))
        self.assertEqual(self.s.person_box,self.original)
        self.assertTrue(self.s.locked)
        self.assertTrue(self.s.crossing_hold)
        self.assertIsNone(self.s.select_face([{'bbox':(130,25,170,70)}],None))

    def test_intruder_cannot_inherit_box_after_target_disappears(self):
        other = {'bbox':(105,15,185,310)}
        self.s.select_person(self.frame,[self.target,other],.3)
        self.score = .2
        for now in (.4,.5,.6):
            self.assertIsNone(self.s.select_person(self.frame,[other],now))
        self.assertEqual(self.s.person_box,self.original)
        self.assertTrue(self.s.crossing_hold)

    def test_returning_target_requires_three_fresh_confirmations(self):
        other = {'bbox':(105,15,185,310)}
        self.s.select_person(self.frame,[self.target,other],.3)
        for now in (.4,.5):
            self.assertIsNone(self.s.select_person(self.frame,[self.target],now))
        self.assertIsNotNone(self.s.select_person(self.frame,[self.target],.6))
        self.assertFalse(self.s.crossing_hold)
        self.assertEqual(self.s.track_id,1)

    def test_low_score_alone_still_does_not_unlock(self):
        self.score = .1
        self.assertIsNotNone(self.s.select_person(self.frame,[self.target],2.))
        self.assertTrue(self.s.locked)
