"""Scripted unit evidence; these embeddings are NOT a video accuracy benchmark."""
import dataclasses
import unittest
import numpy as np
import config
from ai.target_identity import Candidate, IdentityPolicy, IdentityState, TargetIdentityManager


def vector(score=1.):
    v = np.zeros(512)
    v[0], v[1] = score, np.sqrt(1-score*score)
    return tuple(v)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.policy = IdentityPolicy.from_config(config)
        self.m = TargetIdentityManager(vector(), self.policy, session_id='s', target_uid='person-A')
        self.fid = -1

    def c(self, track=1, score=1., x=10, **kwargs):
        return dict(track=track, score=score, x=x, **kwargs)

    def step(self, specs=None, **overrides):
        self.fid += 1
        t = self.fid*.1
        candidates = []
        for i, spec in enumerate([self.c()] if specs is None else specs):
            spec = dict(spec)
            x, track, score = spec.pop('x'), spec.pop('track'), spec.pop('score')
            candidates.append(Candidate(str(i), track, (x,10,x+50,150), .9,
                vector(score), self.fid, t, 's', **spec))
        args = dict(session_id='s', frame_id=self.fid, timestamp=t, now=t, candidates=candidates)
        args.update(overrides)
        return self.m.observe(**args)

    def lock(self):
        for _ in range(self.policy.confirmations):
            r = self.step()
        self.assertEqual(r['action'], 'MATCH')
        return r

    def test_changed_mot_id_reacquires_same_uid(self):
        self.lock()
        for _ in range(self.policy.confirmations):
            r = self.step([self.c(track=9)])
        self.assertEqual((r['action'],r['track_id'],r['target_uid']), ('REACQUIRE',9,'person-A'))
        self.assertEqual([t['track_id'] for t in self.m.tracklets], [1,9])

    def test_id_can_change_during_confirmation_with_multiple_cues(self):
        for track in (10,11,12):
            r = self.step([self.c(track=track)])
        self.assertEqual(r['action'], 'MATCH')

    def test_reused_id_is_not_identity(self):
        self.lock()
        r = self.step([self.c(score=0.)])
        self.assertIsNone(r['selected_candidate'])
        self.assertEqual(r['reason'], 'LOW_REFERENCE_SIMILARITY')
        self.assertEqual(self.m.last_trusted.candidate.embedding, vector())

    def test_crossing_overlap_rejects_even_high_similarity(self):
        self.lock()
        r = self.step([self.c(),self.c(track=2,score=.4,x=25)])
        self.assertEqual(r['reason'], 'PERSON_OVERLAP')
        self.assertEqual(r['state'], 'AMBIGUOUS')

    def test_partial_occlusion_no_gallery_contamination(self):
        self.m.policy = dataclasses.replace(self.policy, gallery_enabled=True)
        self.lock()
        before = list(self.m.gallery)
        r = self.step([self.c(occluded=True,quality_approved=True)])
        self.assertEqual(r['reason'], 'OCCLUDED_CROP')
        self.assertEqual(list(self.m.gallery), before)

    def test_loss_keeps_history_uncertainty_and_recovery(self):
        self.lock()
        trusted = self.m.last_trusted
        a = self.step([])
        b = self.step([])
        self.assertEqual(a['state'], 'LOST_IN_FOV')
        self.assertGreater(b['uncertainty_age_s'], a['uncertainty_age_s'])
        self.assertIs(self.m.last_trusted, trusted)
        for _ in range(self.policy.confirmations):
            r = self.step([self.c(track=7)])
        self.assertEqual(r['action'], 'REACQUIRE')

    def test_similar_distractor_no_arbitrary_top1(self):
        self.lock()
        r = self.step([self.c(score=.91),self.c(track=2,score=.90,x=300)])
        self.assertEqual(r['reason'], 'AMBIGUOUS_MARGIN')
        self.assertIsNone(self.m.active_track_id)

    def test_policy_can_reject_high_osnet_score(self):
        self.lock()
        r = self.step([self.c(policy_rejection='MERGED_BOX')])
        self.assertEqual(r['reason'], 'POLICY:MERGED_BOX')

    def test_stale_old_session_and_frame_do_not_mutate(self):
        self.lock()
        saved = (self.m.last_frame_id,self.m.last_trusted,len(self.m.gallery),self.m.state)
        for override, reason in [({'session_id':'old'},'SESSION_MISMATCH'),
                                 ({'frame_id':0},'OUT_OF_ORDER_OBSERVATION'),
                                 ({'timestamp':0,'now':10},'STALE_OR_FUTURE_OBSERVATION'),
                                 ({'timestamp':10,'now':0},'STALE_OR_FUTURE_OBSERVATION')]:
            self.assertEqual(self.step(**override)['reason'], reason)
            self.assertEqual((self.m.last_frame_id,self.m.last_trusted,len(self.m.gallery),self.m.state),saved)

    def test_delayed_embedding_from_old_frame_rejected(self):
        self.lock()
        stale = Candidate('stale',1,(10,10,60,150),.9,vector(),0,0.,'s')
        r = self.step(candidates=[stale])
        self.assertIsNone(r['selected_candidate'])
        self.assertEqual(r['candidate_audit'][0]['reason'],'EVIDENCE_FRAME_OR_SESSION_MISMATCH')

    def test_missing_competitor_embedding_cannot_inflate_margin(self):
        self.lock()
        stale = Candidate('unknown',2,(200,10,250,150),.9,None,3,.3,'s')
        a = Candidate('target',1,(10,10,60,150),.9,vector(),3,.3,'s')
        r = self.m.observe(session_id='s',frame_id=3,timestamp=.3,now=.3,candidates=[a,stale])
        self.assertEqual(r['reason'],'INCOMPLETE_COMPETITOR_EVIDENCE')

    def test_no_candidate_and_explicit_absence_states(self):
        self.lock()
        for absence, state in [('occluded','OCCLUDED_PREDICTED'),('out_of_fov','OUT_OF_FOV')]:
            r = self.step([],absence=absence)
            self.assertEqual(r['state'],state)
            self.assertIsNone(r['bbox'])
            self.assertFalse(r['ptz_authorized'])

    def test_original_immutable_gallery_requires_review(self):
        original = np.asarray(vector()).copy()
        self.m = TargetIdentityManager(original,dataclasses.replace(self.policy,gallery_enabled=True),session_id='s')
        original[:] = 0
        self.lock()
        self.assertEqual(len(self.m.gallery),0)
        self.step([self.c(quality_approved=True)])
        self.assertEqual(len(self.m.gallery),1)
        self.assertEqual(self.m.original_reference,vector())
        self.assertIsInstance(self.m.original_reference,tuple)
        with self.assertRaises(AttributeError):
            self.m.target_uid = 'changed'
        with self.assertRaises(AttributeError):
            self.m.session_id = 'changed'

    def test_long_gap_breaks_confirmation(self):
        self.step()
        c = Candidate('new',1,(10,10,60,150),.9,vector(),1,2.,'s')
        r = self.step(timestamp=2,now=2,candidates=[c])
        self.assertIsNone(r['selected_candidate'])
        self.assertEqual(self.m._pending_count,1)

    def test_local_only_observation_cannot_confirm(self):
        for i in range(4):
            c = Candidate('flow',1,(10,10,60,150),.9,None,i,i*.1,'s')
            r = self.m.observe(session_id='s',frame_id=i,timestamp=i*.1,now=i*.1,candidates=[c])
            self.assertIsNone(r['selected_candidate'])

    def test_invalid_embedding_and_policy(self):
        with self.assertRaises(ValueError):
            TargetIdentityManager(np.zeros(512),self.policy)
        with self.assertRaises(ValueError):
            dataclasses.replace(self.policy,margin=float('nan'))

    def test_distractor_history_is_unverified_and_bounded(self):
        self.lock()
        self.step([self.c(),self.c(track=2,score=.2,x=300)])
        self.assertEqual(self.m.distractor_history[-1]['status'],'UNVERIFIED_ALTERNATIVE')
        self.assertEqual(self.m.distractor_history.maxlen,128)

    def lock_side_view(self):
        for _ in range(self.policy.confirmations):
            r = self.step([self.c(score=.85)])
        self.assertIsNotNone(r['selected_candidate'])

    def brief_overlap(self):
        r = self.step([self.c(score=.6), self.c(track=2,score=.2,x=25)])
        self.assertEqual(r['reason'], 'PERSON_OVERLAP')

    def test_brief_overlap_resumes_only_after_clear_multi_frame_evidence(self):
        self.lock_side_view()
        anchor = self.m.last_trusted
        self.brief_overlap()
        for _ in range(self.policy.confirmations-1):
            r = self.step([self.c(score=.6)])
            self.assertIsNone(r['selected_candidate'])
            self.assertIs(self.m.last_trusted, anchor)
            self.assertEqual(r['candidate_audit'][0]['continuity_mode'], 'bounded_resume')
        r = self.step([self.c(score=.6)])
        self.assertEqual(r['action'], 'REACQUIRE')
        self.assertFalse(r['ptz_authorized'])
        self.assertEqual(len(self.m.gallery), 0)

    def test_resume_cannot_extend_anchor_with_rejected_frames(self):
        self.lock_side_view()
        anchor = self.m.last_trusted
        for _ in range(12):
            self.brief_overlap()
        for _ in range(4):
            r = self.step([self.c(score=.6)])
            self.assertEqual(r['reason'], 'LOW_REFERENCE_SIMILARITY')
        self.assertIs(self.m.last_trusted, anchor)

    def test_resume_requires_track_geometry_and_anchor_appearance(self):
        for spec in (self.c(track=9,score=.6), self.c(track=None,score=.6),
                     self.c(score=.6,x=300), self.c(score=-.6)):
            self.setUp(); self.lock_side_view(); self.brief_overlap()
            r = self.step([spec])
            self.assertIsNone(r['selected_candidate'])
            self.assertEqual(r['candidate_audit'][0]['continuity_mode'], 'reacquire')

    def test_resume_keeps_margin_and_current_overlap_gates(self):
        self.lock_side_view(); self.brief_overlap()
        r = self.step([self.c(score=.6),self.c(track=2,score=.59,x=300)])
        self.assertEqual(r['reason'], 'AMBIGUOUS_MARGIN')
        self.brief_overlap()
        self.assertIsNone(self.m.active_track_id)

    def test_absence_requires_strong_reacquisition(self):
        self.lock_side_view(); self.step([])
        for _ in range(4):
            r = self.step([self.c(score=.6)])
            self.assertEqual(r['reason'], 'LOW_REFERENCE_SIMILARITY')

    def test_same_id_and_reference_score_cannot_replace_anchor_appearance(self):
        self.lock_side_view(); self.brief_overlap()
        v = list(vector(.6)); v[1] *= -1
        fid = self.fid+1; t = fid*.1
        c = Candidate('impostor',1,(10,10,60,150),.9,tuple(v),fid,t,'s')
        r = self.step(candidates=[c])
        self.assertEqual(r['reason'], 'LOW_REFERENCE_SIMILARITY')
        self.assertEqual(r['candidate_audit'][0]['continuity_mode'], 'reacquire')


if __name__ == '__main__':
    unittest.main()
