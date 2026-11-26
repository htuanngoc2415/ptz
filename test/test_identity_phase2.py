"""Adversarial scripted identities; not evidence of real-video accuracy."""
from dataclasses import replace
import unittest
import numpy as np
import config
from ai.target_identity import Candidate, IdentityPolicy, Phase2Policy, TargetIdentityManager
from runtime.identity_events import EventReID, crop_quality


def vector(score=1., axis=1):
    v = np.zeros(512)
    v[0], v[axis] = score, np.sqrt(1-score*score)
    return tuple(v)


class Phase2Tests(unittest.TestCase):
    def setUp(self):
        self.m = TargetIdentityManager(vector(), IdentityPolicy.from_config(config),
            session_id='s', target_uid='original', phase2=Phase2Policy(True, True, True))
        self.fid = 0

    def step(self, specs=None, *, dt=.1, **kw):
        self.fid += 1
        t = self.fid*dt
        cs = [Candidate(str(i), p.get('track', i+1), p.get('box', (i*200+10,10,i*200+60,160)),
            .9, vector(p.get('score', 1. if i == 0 else .1),p.get('axis',1)), self.fid,t,'s',
            crop_quality=p.get('quality','measured_clear'),occluded=p.get('occluded',False))
            for i,p in enumerate([{},{}] if specs is None else specs)]
        args = dict(session_id='s',frame_id=self.fid,timestamp=t,now=t,candidates=cs)
        args.update(kw)
        return self.m.observe(**args)

    def learn(self):
        for _ in range(3):
            result = self.step()
        return result

    def test_low_score_once_not_negative(self):
        r = self.step()
        self.assertEqual(r['candidate_audit'][1]['identity_class'],'IDENTITY_UNCERTAIN')

    def test_repeated_separated_clean_negative(self):
        r = self.learn()
        self.assertEqual(r['candidate_audit'][1]['identity_class'],'CONFIRMED_DISTRACTOR')
        self.assertEqual(len(self.m.distractors),1)
        self.assertEqual(len(self.m.distractors[0].trajectory),3)

    def test_blur_occlusion_and_missing_positive_do_not_learn(self):
        for specs in ([{}, {'quality':'unknown'}], [{}, {'occluded':True}], [{'score':.1}]):
            self.setUp()
            for _ in range(5):
                r=self.step(specs)
            self.assertFalse(self.m.distractors)
            self.assertNotEqual(r['candidate_audit'][-1]['identity_class'],'CONFIRMED_DISTRACTOR')

    def test_crossing_swapped_ids_cannot_steal_target(self):
        self.learn()
        r=self.step([{'box':(210,10,260,160),'score':.1,'track':1},
                     {'box':(10,10,60,160),'score':1.,'track':2}])
        self.assertIsNone(r['selected_candidate'])  # three new confirmations needed
        for _ in range(2):
            r=self.step([{'box':(210,10,260,160),'score':.1,'track':1},
                         {'box':(10,10,60,160),'score':1.,'track':2}])
        self.assertEqual((r['selected_candidate'],r['target_uid']),('1','original'))
        self.assertEqual(r['candidate_audit'][0]['identity_class'],'CONFIRMED_DISTRACTOR')

    def test_strong_overlap_holds_and_does_not_update_memory(self):
        self.learn(); before=list(self.m.distractors)
        r=self.step([{}, {'box':(25,10,75,160)}])
        self.assertIsNone(r['selected_candidate'])
        self.assertEqual(r['reason'],'PERSON_OVERLAP')
        self.assertEqual(list(self.m.distractors),before)
        self.assertFalse(self.m.gallery)

    def test_same_id_high_reference_conflicting_appearance_holds(self):
        self.step([{'score':.8,'axis':1}]);self.step([{'score':.8,'axis':1}]);self.step([{'score':.8,'axis':1}])
        r=self.step([{'score':.8,'axis':2}])
        self.assertEqual(r['reason'],'APPEARANCE_HISTORY_CONFLICT')

    def test_two_similar_people_remain_ambiguous(self):
        r=self.step([{'score':.91},{'score':.90}])
        self.assertEqual(r['reason'],'AMBIGUOUS_MARGIN')
        self.assertFalse(self.m.distractors)

    def test_memory_expires_and_bound(self):
        self.m.phase2=replace(self.m.phase2,capacity=2)
        for _ in range(3):self.step([{}, {}, {}, {}])
        self.assertLessEqual(len(self.m.distractors),2)
        self.fid=100
        self.step([{}])
        self.assertFalse(self.m.distractors)

    def test_bad_session_stale_out_of_order_do_not_mutate(self):
        self.learn(); before=list(self.m.distractors)
        for args in (dict(session_id='wrong'),dict(now=100.),dict(frame_id=1)):
            r=self.step(**args)
            self.assertEqual(r['action'],'REJECT')
            self.assertEqual(list(self.m.distractors),before)

    def test_invalid_competitor_never_teaches_negative(self):
        self.fid=1
        for n in range(3):
            t=1+n*.1
            cs=[Candidate(str(i),i,(i*200,0,i*200+50,100),.9,
                vector(1 if i == 0 else .1),n,t,'bad' if i==2 else 's',crop_quality='measured_clear') for i in range(3)]
            r=self.m.observe(session_id='s',frame_id=n,timestamp=t,now=t,candidates=cs)
        self.assertEqual(r['reason'],'INCOMPLETE_COMPETITOR_EVIDENCE')
        self.assertFalse(self.m.distractors)

    def test_flags_off_exact_old_output(self):
        a=TargetIdentityManager(vector(),self.m.policy,session_id='s',target_uid='x')
        b=TargetIdentityManager(vector(),self.m.policy,session_id='s',target_uid='x',phase2=Phase2Policy())
        for n in range(5):
            c=Candidate('a',1,(0,0,50,100),.9,vector(),n,n*.1,'s')
            kw=dict(session_id='s',frame_id=n,timestamp=n*.1,now=n*.1,candidates=[c])
            self.assertEqual(a.observe(**kw),b.observe(**kw))

    def test_unknown_quality_does_not_create_positive(self):
        for _ in range(4):r=self.step([{'quality':'unknown'}])
        self.assertEqual(r['reason'],'UNCERTAIN_CROP_QUALITY')
        self.assertIsNone(self.m.last_trusted)

    def test_known_negative_with_high_reference_stays_conflicted(self):
        self.learn()
        # Same appearance family as the learned negative, now reference score high.
        r=self.step([{'box':(210,10,260,160),'score':.75,'track':1}])
        self.assertEqual(r['reason'],'DISTRACTOR_TARGET_CONFLICT')
        self.assertIsNone(r['selected_candidate'])

    def test_minor_contact_requires_previously_learned_negative(self):
        target={'box':(10,10,210,210)}
        other={'box':(215,100,245,130),'score':.1}
        for _ in range(3):self.step([target,other])
        r=self.step([target,dict(other,box=(205,100,235,130))])
        self.assertEqual(r['selected_candidate'],'0')
        self.setUp()
        for _ in range(3):self.step([target])
        r=self.step([target,dict(other,box=(205,100,235,130))])
        self.assertEqual(r['reason'],'PERSON_OVERLAP')

    def test_track_id_reused_with_new_appearance_not_negative_by_id(self):
        self.learn()
        r=self.step([{'score':.1,'track':2,'box':(210,10,260,160),'axis':2}])
        self.assertEqual(r['candidate_audit'][0]['identity_class'],'IDENTITY_UNCERTAIN')

    def test_unapproved_gallery_stays_empty(self):
        self.m.policy=replace(self.m.policy,gallery_enabled=True)
        self.learn()
        self.assertFalse(self.m.gallery)

    def test_memory_feature_dependencies(self):
        for flags in ((False,True,False),(True,False,True)):
            with self.assertRaises(ValueError):Phase2Policy(*flags)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.manager=TargetIdentityManager(vector(),IdentityPolicy.from_config(config),session_id='s')
        self.events=EventReID(config,'s',enabled=True)
        self.frame=np.zeros((200,400,3),dtype=np.uint8)

    def offer(self,n,people=None,**kw):
        p=people or [dict(candidate_id=str(n),track_id=1,bbox=(10,10,60,160),confidence=.9,crop_quality='measured_clear')]
        args=dict(frame=self.frame,persons=p,frame_id=n,timestamp=n*.1,now=n*.1,session='s',manager=self.manager)
        args.update(kw)
        return self.events.offer(**args)

    def test_disabled_no_work(self):
        self.events.enabled=False
        self.assertFalse(self.offer(1));self.assertIsNone(self.events.pending)

    def test_no_embeddings_leak_from_cache_and_frame_snapshot(self):
        p=dict(candidate_id='x',track_id=1,bbox=(10,10,60,160),confidence=.9,crop_quality='measured_clear',embedding=[99])
        self.offer(1,[p]);self.frame[:]=255
        r=self.events.take(.1,'s')
        self.assertNotIn('embedding',r.persons[0]);self.assertEqual(r.frame.max(),0)

    def test_poor_new_frame_does_not_evict_clean(self):
        self.offer(1)
        self.offer(2,[dict(candidate_id='x',track_id=1,bbox=(10,10,60,160),confidence=.9,crop_quality='unknown')])
        self.assertEqual(self.events.take(.2,'s').frame_id,1)

    def test_expiry_session_and_reset_cancel(self):
        self.offer(1);self.assertIsNone(self.events.take(2.,'s'))
        self.offer(3);self.assertIsNone(self.events.take(.3,'other'))
        self.offer(4);self.events.reset('new');self.assertIsNone(self.events.take(.4,'new'))

    def test_approach_before_overlap_then_separation(self):
        def pair(x):return [dict(candidate_id=str(i),track_id=i,bbox=(v,10,v+50,160),confidence=.9,crop_quality='measured_clear') for i,v in enumerate((10,x))]
        self.offer(1,pair(70));self.assertIn('APPROACHING_OVERLAP',self.events.last_events)
        self.events.take(.1,'s')
        self.offer(2,pair(25));self.assertIn('ACTIVE_OVERLAP',self.events.last_events)
        self.assertIsNone(self.events.pending)
        self.offer(3,pair(200));self.assertIn('POST_OVERLAP_SEPARATION',self.events.last_events)

    def test_rate_cap_and_periodic(self):
        self.offer(1);self.assertIsNotNone(self.events.take(.1,'s'))
        self.offer(2);self.assertIsNone(self.events.take(.2,'s'))
        self.offer(8);self.assertIn('PERIODIC_IDENTITY_CHECK',self.events.last_events)
        self.assertIsNotNone(self.events.take(.8,'s'))

    def test_bad_timestamp_rejected(self):
        self.assertFalse(self.offer(1,now=float('nan')))
        self.offer(2);self.assertFalse(self.offer(1))

    def test_track_change_and_reacquisition_events(self):
        for n in range(3):
            c=Candidate('a',1,(10,10,60,160),.9,vector(),n,n*.1,'s')
            self.manager.observe(session_id='s',frame_id=n,timestamp=n*.1,now=n*.1,candidates=[c])
        self.manager.interrupt()
        self.offer(4,[dict(candidate_id='new',track_id=9,bbox=(10,10,60,160),confidence=.9,crop_quality='measured_clear')])
        self.assertIn('TRACK_ID_CHANGE',self.events.last_events)
        self.assertIn('TARGET_REACQUISITION',self.events.last_events)

    def test_only_one_pending_and_priority_is_bounded(self):
        for n in range(1,20):self.offer(n)
        self.assertEqual(self.events.pending.frame_id,19)
        self.assertLessEqual(self.events.pending.priority,6)

    def test_reset_same_session_and_late_result_invalid(self):
        self.offer(1);request=self.events.take(.1,'s')
        self.assertTrue(self.events.valid_result(request,.2,self.manager))
        self.assertFalse(self.events.valid_result(request,1.,self.manager))
        self.events.reset('s')
        self.assertFalse(self.events.valid_result(request,.2,self.manager))

    def test_crop_quality_blur_border_and_partial_shape_unknown(self):
        random=np.random.default_rng(1).integers(0,256,(200,400,3),dtype=np.uint8)
        for frame,box in ((self.frame,(10,10,60,160)),(random,(0,10,60,160)),
                          (random,(10,10,160,60))):
            result=crop_quality(frame,[dict(bbox=box)],config)
            self.assertEqual(result[0]['crop_quality'],'unknown')


if __name__ == '__main__':
    unittest.main()
