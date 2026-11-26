import unittest,json,copy
from dataclasses import replace
import numpy as np
from ai.target_identity import TargetIdentityManager,IdentityPolicy,Phase2Policy,Candidate
from tools.identity_continuity import ContinuityPolicy,ContinuityVerifier
from tools.phase2b_review import validate


def vector(score):
 v=np.zeros(512);v[0]=score;v[1]=(1-score*score)**.5;return tuple(v)

def fixture(flags=None):
 p=IdentityPolicy(.65,.5,.7,.08,3,.4,1.,.8,.35,.15)
 m=TargetIdentityManager(vector(1),p,session_id='test',phase2=Phase2Policy(filtering=True,memory=True,association=True))
 return ContinuityVerifier(m,flags)

def candidate(fid,ts,score=.8,quality='measured_clear',track=1,box=(10,10,30,60),**kw):
 return Candidate(str(fid)+':0',track,box,.95,vector(score),fid,ts,'test',crop_quality=quality,**kw)

def feed(v,fid,ts=None,cs=None,**kw):
 ts=fid*.1 if ts is None else ts
 return v.observe(session_id='test',frame_id=fid,timestamp=ts,now=ts+.01,candidates=cs if cs is not None else [candidate(fid,ts)],**kw)

def warm(v):
 for i in range(1,5):feed(v,i)
 assert v.manager.last_trusted is not None

class ContinuityTests(unittest.TestCase):
 def test_default_off_exact_parity(self):
  v=fixture();m=fixture().manager
  for i in range(1,15):
   cs=[candidate(i,i*.1,quality='unknown' if i>4 else 'measured_clear')]
   a=feed(v,i,cs=cs);b=m.observe(session_id='test',frame_id=i,timestamp=i*.1,now=i*.1+.01,candidates=cs)
   a.pop('target_uid');b.pop('target_uid');self.assertEqual(a,b)
 def test_quality_continuation_never_confirms_or_updates(self):
  v=fixture(ContinuityPolicy(quality_continuation=True));warm(v);m=v.manager;anchor=m.last_trusted;hist=tuple(m.history)
  r=feed(v,5,cs=[candidate(5,.5,quality='unknown')]);self.assertTrue(r['provisional']);self.assertIsNone(r['selected_candidate']);self.assertIsNone(r['bbox']);self.assertFalse(r['identity_updated']);self.assertFalse(r['ptz_authorized']);self.assertEqual(tuple(m.history),hist);self.assertIs(m.last_trusted,anchor);self.assertEqual(len(m.gallery),0)
 def test_similarity_is_separate_ablation(self):
  for flags,expected in [(ContinuityPolicy(quality_continuation=True),False),(ContinuityPolicy(low_similarity_continuation=True),True)]:
   v=fixture(flags);warm(v);r=feed(v,5,cs=[candidate(5,.5,score=.4)]);self.assertEqual(r['provisional'],expected)
 def test_quality_not_bypassed_by_similarity_flag(self):
  v=fixture(ContinuityPolicy(low_similarity_continuation=True));warm(v);self.assertFalse(feed(v,5,cs=[candidate(5,.5,quality='unknown')])['provisional'])
 def test_no_anchor_cannot_bootstrap(self):
  v=fixture(ContinuityPolicy(True,True));r=feed(v,1,cs=[candidate(1,.1,quality='unknown')]);self.assertFalse(r['provisional']);self.assertIsNone(v.manager.last_trusted)
 def test_budget_cannot_be_extended_by_provisional_chain(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);anchor=v.manager.last_trusted
  for i in range(5,16):
   r=feed(v,i,cs=[candidate(i,i*.1,quality='unknown')]);self.assertIs(v.manager.last_trusted,anchor)
   if i*.1+.01-anchor.timestamp>.8:self.assertFalse(r['provisional'])
 def test_stale_future_wrong_session_out_of_order_do_not_mutate(self):
  for patch in [dict(now=2.),dict(now=.49),dict(session_id='wrong'),dict(frame_id=4,timestamp=.4,now=.41)]:
   v=fixture(ContinuityPolicy(True,True));warm(v);anchor=v.manager.last_trusted
   args=dict(session_id='test',frame_id=5,timestamp=.5,now=.51,candidates=[candidate(5,.5,quality='unknown')]);args.update(patch);r=v.observe(**args);self.assertEqual(r['action'],'REJECT');self.assertIs(v.manager.last_trusted,anchor);self.assertFalse(v.risk_latched)
 def test_track_change_requires_strict_reverification(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);r=feed(v,5,cs=[candidate(5,.5,track=2,quality='unknown')]);self.assertFalse(r['provisional']);self.assertTrue(v.risk_latched)
 def test_duplicate_track_is_not_identity(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);a=candidate(5,.5,quality='unknown');b=replace(a,candidate_id='5:1',bbox=(100,10,120,60));self.assertFalse(feed(v,5,cs=[a,b])['provisional'])
 def test_id_reuse_conflicting_appearance(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);c=candidate(5,.5,quality='unknown');emb=list(vector(.8));emb[1]*=-1;c=replace(c,embedding=tuple(emb));r=feed(v,5,cs=[c]);self.assertFalse(r['provisional'])
 def test_crossing_latches_and_separation_does_not_auto_resume(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);a=candidate(5,.5,quality='unknown');b=replace(a,candidate_id='5:1',track_id=2,bbox=(20,10,40,60),embedding=vector(.2));r=feed(v,5,cs=[a,b]);self.assertFalse(r['provisional']);self.assertTrue(v.risk_latched)
  self.assertFalse(feed(v,6,cs=[candidate(6,.6,quality='unknown')])['provisional'])
  for i in (7,8):self.assertIsNone(feed(v,i)['selected_candidate'])
  self.assertIsNotNone(feed(v,9)['selected_candidate']);self.assertFalse(v.risk_latched)
 def test_approaching_competitor(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);a=candidate(5,.5,quality='unknown');b=replace(a,candidate_id='5:1',track_id=2,bbox=(35,10,55,60),embedding=vector(.2));self.assertFalse(feed(v,5,cs=[a,b])['provisional']);self.assertEqual(v.last_risk,'APPROACHING_COMPETITOR')
 def test_confirmed_approach_blocks_later_provisional_after_separation(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);a=candidate(5,.5);b=replace(a,candidate_id='5:1',track_id=2,bbox=(35,10,55,60),embedding=vector(.2))
  r=feed(v,5,cs=[a,b]);self.assertIsNotNone(r['selected_candidate']);self.assertEqual(r['continuity_state'],'CONFIRMED_AT_RISK');self.assertTrue(v.risk_latched)
  self.assertFalse(feed(v,6,cs=[candidate(6,.6,quality='unknown')])['provisional'])
 def test_distant_similar_competitor_cannot_be_ignored(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);a=candidate(5,.5,quality='unknown');b=replace(a,candidate_id='5:1',track_id=2,bbox=(200,10,220,60));r=feed(v,5,cs=[a,b]);self.assertFalse(r['provisional']);self.assertEqual(r['continuation_reason'],'COMPETITOR_APPEARANCE_CONFLICT')
 def test_invalid_competitor_embedding(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);a=candidate(5,.5,quality='unknown');b=replace(a,candidate_id='5:1',track_id=2,embedding=None);self.assertFalse(feed(v,5,cs=[a,b])['provisional'])
 def test_motion_jump(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);r=feed(v,5,cs=[candidate(5,.5,quality='unknown',box=(200,200,220,250))]);self.assertFalse(r['provisional'])
 def test_occlusion_and_absence_no_predicted_identity(self):
  for cs in ([],[candidate(5,.5,quality='unknown',occluded=True)]):
   v=fixture(ContinuityPolicy(True,True));warm(v);r=feed(v,5,cs=cs,absence='occluded');self.assertFalse(r['provisional']);self.assertIsNone(r['localization_bbox']);self.assertTrue(v.risk_latched)
 def test_gallery_refused(self):
  v=fixture();v.manager.policy=replace(v.manager.policy,gallery_enabled=True)
  with self.assertRaises(ValueError):ContinuityVerifier(v.manager)
 def test_reset_latch(self):
  v=fixture(ContinuityPolicy(True,True));warm(v);v.interrupt();self.assertFalse(feed(v,5,cs=[candidate(5,.5,quality='unknown')])['provisional'])

class ReviewTests(unittest.TestCase):
 def setUp(self):
  self.pack={'pack_sha256':'hash','sets':[{'split':'development','frames':[{'frame_id':1,'candidates':[{'candidate_id':'1:0'}]}]}]}
  self.a={'schema':'phase2b_manual_v1','reviewer':'human','pack_sha256':'hash','frames':[{'split':'development','frame_id':1,'reviewed':True,'visibility':'UNKNOWN','candidate_identities':{'1:0':'UNKNOWN'}}]}
 def test_unknown_is_not_negative_gt(self):self.assertEqual(validate(self.pack,self.a)['known'],0)
 def test_requires_reviewer(self):
  self.a['reviewer']=''
  with self.assertRaises(ValueError):validate(self.pack,self.a)
 def test_wrong_pack(self):
  self.a['pack_sha256']='other'
  with self.assertRaises(ValueError):validate(self.pack,self.a)
 def test_partial_candidate_label_rejected(self):
  self.a['frames'][0]['candidate_identities']={}
  with self.assertRaises(ValueError):validate(self.pack,self.a)
 def test_invisible_target_conflict(self):
  self.a['frames'][0].update(visibility='OUT_OF_FRAME',candidate_identities={'1:0':'target'})
  with self.assertRaises(ValueError):validate(self.pack,self.a)
 def test_duplicates_rejected(self):
  self.a['frames']*=2
  with self.assertRaises(ValueError):validate(self.pack,self.a)
 def test_known_labels(self):
  self.a['frames'][0].update(visibility='VISIBLE',candidate_identities={'1:0':'target'});self.assertEqual(validate(self.pack,self.a)['known'],1)

class ReviewedMetricTests(unittest.TestCase):
 def test_unknown_never_becomes_distractor(self):
  from tools.phase2b_evaluate import evaluate_reviewed
  rows=[dict(frame_id=i,timestamp=float(i),state='AMBIGUOUS',selected_candidate=str(i)+':0') for i in (1,2)]
  a={'frames':[dict(split='development',frame_id=i,visibility='VISIBLE',candidate_identities={str(i)+':0':'UNKNOWN'}) for i in (1,2)]}
  r=evaluate_reviewed(rows,a,'development');self.assertIsNone(r['wrong_target_tracking_time_s']);self.assertEqual(r['known_frames'],0)
 def test_provisional_scored_separately(self):
  from tools.phase2b_evaluate import evaluate_reviewed
  rows=[dict(frame_id=i,timestamp=float(i),state='AMBIGUOUS',selected_candidate=None,localization_candidate=str(i)+':0') for i in (1,2)]
  a={'frames':[dict(split='development',frame_id=i,visibility='VISIBLE',candidate_identities={str(i)+':0':'target'}) for i in (1,2)]}
  self.assertEqual(evaluate_reviewed(rows,a,'development')['target_correct_tracking_time_s'],0)
  self.assertEqual(evaluate_reviewed(rows,a,'development',localization=True)['target_correct_tracking_time_s'],1)
 def test_unknown_gap_not_integrated(self):
  from tools.phase2b_evaluate import evaluate_reviewed
  rows=[dict(frame_id=i,timestamp=float(i),state='AMBIGUOUS',selected_candidate=str(i)+':0') for i in (1,2,3)]
  a={'frames':[dict(split='development',frame_id=i,visibility='VISIBLE',candidate_identities={str(i)+':0':'target' if i!=2 else 'UNKNOWN'}) for i in (1,2,3)]}
  r=evaluate_reviewed(rows,a,'development');self.assertEqual(r['annotated_duration_s'],0)
