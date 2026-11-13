"""P0 behavioral tests: labelled scripted trajectories, real tracker, no camera/model I/O."""
import json,tempfile,time,threading,unittest
from pathlib import Path
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
import config
from ai.hybrid_reid import HybridReIDTargetSelector
from runtime.byte_tracking import ByteTrackPersonTracker
from runtime.tracking_snapshots import TrackingSnapshots
from telemetry import latency

def cfgcopy():return SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
def person(ident=1,box=(324,95,412,391),label='target'):
    return dict(track_id=ident,bbox=box,confidence=.9,class_id=0,label=label)
def other():return person(2,(282,79,335,231),'other')

class OverlapBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.cfg=cfgcopy();self.cfg.REID_SEARCH_INTERVAL=0;self.cfg.REID_VERIFY_INTERVAL=100
        self.s=HybridReIDTargetSelector(self.cfg,Mock());self.s.embedding=np.ones(512);self.s.error=None
        self.calls=[]
        def scores(frame,people):
            self.calls.append([p['track_id'] for p in people]);return [.95 if p['label']=='target' else .1 for p in people]
        self.s._scores=Mock(side_effect=scores)
        self.s._start_verification=Mock()
        self.frame=np.zeros((500,700,3),np.uint8)
        for t in (0.,.1,.2):self.feed(t,[person()])
        self.assertTrue(self.s.locked)
    def feed(self,t,people):
        chosen=self.s.select_person(self.frame,people,t);self.s.finish_frame(chosen,None)
        if chosen is not None:self.assertEqual(chosen['label'],'target')
        return chosen
    def result(self,captured,scores,people,token=None):
        f=Future();f.set_result(scores)
        token=token or (self.s.verification_epoch,self.s.lock_session_id,self.s.track_id)
        self.s._verification=(f,token,captured,1,people)
    def test_rollback_flag_restores_immediate_geometry_hold(self):
        self.cfg.REID_TEMPORAL_OVERLAP_ENABLED=False
        self.assertIsNone(self.feed(.3,[person(),other()]))
        self.assertTrue(self.s.crossing_hold)

    def test_single_weak_overlap_keeps_observed_target(self):
        self.assertIsNotNone(self.feed(.3,[person(),other()]))
        self.assertFalse(self.s.crossing_hold);self.s._start_verification.assert_called()
        self.assertIsNotNone(self.feed(.4,[person()]))
    def test_low_confidence_observed_track_can_continue_weak_contact(self):
        current=person();current['confidence']=.2
        self.assertIsNotNone(self.feed(.3,[current,other()]))

    def test_persistent_weak_overlap_without_evidence_holds(self):
        self.feed(.3,[person(),other()]);self.assertIsNone(self.feed(.7,[person(),other()]))
        self.assertTrue(self.s.crossing_hold)
    def test_persistent_weak_contact_with_fresh_conflict_verification(self):
        self.feed(.3,[person(),other()]);self.result(.31,[.95,.1],[person(),other()])
        self.assertIsNotNone(self.feed(.7,[person(),other()]))
        self.assertIsNotNone(self.feed(1.0,[person(),other()]))
        self.assertIsNone(self.feed(2.0,[person(),other()]))
    def test_strong_occlusion_holds_immediately(self):
        self.assertIsNone(self.feed(.3,[person(),person(2,(325,96,413,392),'other')]))
        self.assertTrue(self.s.crossing_hold)
    def test_conflicting_appearance_cannot_use_weak_grace(self):
        self.result(.25,[.9,.89],[person(),other()])
        self.assertIsNone(self.feed(.3,[person(),other()]))
        self.assertTrue(self.s.crossing_hold)
    def test_new_conflicting_track_during_weak_contact_holds(self):
        self.feed(.3,[person(),other()]);p=other();p['track_id']=3
        self.assertIsNone(self.feed(.4,[person(),p]))
    def test_id_change_is_not_authorized_by_geometry(self):
        chosen=self.feed(.3,[person(3,label='other')]);self.assertIsNone(chosen)
        for t in (.4,.5):self.assertIsNone(self.feed(t,[person(3,label='other')]))
    def test_reacquire_then_weak_contact_retains_checks(self):
        self.feed(.3,[])
        for t in (.4,.5,.6):chosen=self.feed(t,[person()])
        self.assertIsNotNone(chosen)
        self.assertIsNotNone(self.feed(.7,[person(),other()]))
        self.assertIsNone(self.feed(.8,[person(),person(2,(325,96,413,392),'other')]))
    def test_loss_grace_and_distant_reappearance(self):
        for t in np.arange(.3,4.,.1):self.assertIsNone(self.feed(float(t),[]))
        self.assertFalse(self.s.locked)
        for t in (4.,4.1,4.2,4.3):chosen=self.feed(t,[person(8,(50,30,120,200))])
        self.assertIsNotNone(chosen)
    def test_delayed_result_never_renews_new_session(self):
        token=(self.s.verification_epoch,self.s.lock_session_id,self.s.track_id)
        self.feed(.3,[])
        for t in (.4,.5,.6):self.feed(t,[person()])
        self.result(.2,[.99,.0],[person(),other()],token)
        self.feed(.7,[person(),other()]);self.assertIsNone(self.s._overlap_memory.verified_at)
    def test_out_of_order_does_not_extend_overlap_grace(self):
        self.feed(.3,[person(),other()]);before=self.s._overlap_memory.since
        self.assertIsNone(self.feed(.25,[person()]));self.assertEqual(before,self.s._overlap_memory.since)
    def test_future_or_old_identity_evidence_blocks_weak_contact(self):
        for stamp in (-5,10):
            self.s.last_positive_verify=stamp
            self.assertTrue(self.s._candidate_overlap(person(),[person(),other()]))
    def test_global_hold_retry_includes_distant_true_target(self):
        # Current box belongs to an impostor; distant target must remain searchable.
        self.s._scores.reset_mock();self.calls.clear()
        scene=[person(label='other'),person(2,(325,96,413,392),'other'),person(3,(20,50,90,240))]
        for t in (.3,.4,.5,.8,1.3):self.feed(t,scene)
        self.assertTrue(any(3 in c for c in self.calls))
        self.assertEqual(self.s.track_id,3)
    def test_geometry_hold_does_not_score_every_observation(self):
        scene=[person(),person(2,(325,96,413,392),'other'),person(3,(20,50,90,240),'other')]
        self.calls.clear()
        for t in np.arange(.3,1.3,.1):self.feed(float(t),scene)
        self.assertLessEqual(len(self.calls),2)
    def test_gallery_unchanged_through_overlap(self):
        before=self.s.embedding.copy()
        self.feed(.3,[person(),other()]);self.result(.31,[.95,.1],[person(),other()]);self.feed(.7,[person(),other()])
        np.testing.assert_array_equal(self.s.embedding,before)
    def test_turn_back_no_face_and_gradual_distance(self):
        for t,box in ((.3,(326,98,410,380)),(.4,(328,102,408,360)),(.5,(330,105,406,340))):
            self.assertIsNotNone(self.feed(t,[person(box=box)]))

class CandidateVerificationTests(unittest.TestCase):
    def test_only_target_and_possible_association_conflicts(self):
        c=cfgcopy();s=HybridReIDTargetSelector(c,Mock());s.track_id=1;s.person_box=person()['bbox']
        s.lock_session_id='s';s.identity_status='VERIFIED'
        people=[person(),other(),person(3,(20,10,60,100),'other'),person(4,(500,10,540,100),'other')]
        calls=[];s._scores=lambda frame,ps:calls.append([p['track_id'] for p in ps]) or [.95 if p['track_id']==1 else .1 for p in ps]
        now=time.monotonic();s._start_verification(np.zeros((500,700,3),np.uint8),people,now)
        s._verification[0].result(timeout=2)
        self.assertEqual(calls,[[1,2]])
        self.assertEqual(s._verification[4][0]['track_id'],1)
    def test_candidate_rollback_scores_all_observations(self):
        c=cfgcopy();c.REID_CANDIDATE_AWARE=False
        s=HybridReIDTargetSelector(c,Mock());s.track_id=1;s.person_box=person()['bbox'];s.lock_session_id='s'
        calls=[];s._scores=lambda f,ps:calls.append(len(ps)) or [.95,.1]
        s._start_verification(np.zeros((500,700,3),np.uint8),[person(),person(3,(20,10,60,100),'other')],time.monotonic())
        s._verification[0].result(timeout=2);self.assertEqual(calls,[2])

    def test_isolated_locked_target_only(self):
        c=cfgcopy();s=HybridReIDTargetSelector(c,Mock());s.track_id=1;s.person_box=person()['bbox'];s.lock_session_id='s'
        calls=[];s._scores=lambda f,ps:calls.append(len(ps)) or [.95]*len(ps)
        s._start_verification(np.zeros((500,700,3),np.uint8),[person(),person(3,(20,10,60,100),'other')],time.monotonic())
        s._verification[0].result(timeout=2);self.assertEqual(calls,[1])
    def test_low_quality_normalization_kept_and_gallery_not_written(self):
        import torch
        c=cfgcopy();model=Mock();model.extract_embeddings.return_value=torch.ones((1,512))
        s=HybridReIDTargetSelector(c,model);s.embedding=np.ones(512);s.error=None
        gallery=Mock();gallery.score.side_effect=[torch.tensor([.6]),torch.tensor([.8])];s.gallery=gallery
        s.quality=Mock();s.quality.analyze.return_value={'person_mean_luma':40,'local_contrast':100}
        scores=s._scores(np.zeros((500,700,3),np.uint8),[person()])
        self.assertAlmostEqual(scores[0],.8,places=5);self.assertEqual(model.extract_embeddings.call_count,2)
        self.assertEqual(gallery.method_calls[0][0],'score');self.assertEqual(len(gallery.method_calls),2)

class DiagnosticsTests(unittest.TestCase):
    def test_real_tracker_preserves_distinct_sources_and_observed_geometry(self):
        c=cfgcopy();tracker=ByteTrackPersonTracker(c)
        a=person();b=person(2,(325,96,413,392),'other');a.pop('track_id');b.pop('track_id')
        tracks=tracker.update([a,b],1.)
        self.assertEqual(len(tracks),2);self.assertEqual({t['tracker_source_index'] for t in tracks},{0,1})
        self.assertEqual(tracks[0]['bbox'],a['bbox']);self.assertNotIn('track_id',a)
        self.assertIn('predicted_updated_bbox',tracker._snapshot_context['tracks'][0])
    def test_same_object_alias_is_one_observation(self):
        t=ByteTrackPersonTracker(cfgcopy());p=person();self.assertEqual(len(t.update([p,p],1.)),1)
    def test_end_to_end_postprocess_retains_high_iou_observations(self):
        import torch
        from ultralytics.utils.nms import non_max_suppression
        x=torch.tensor([[[159.,91.,219.,197.,.39,0.],[159.,90.,219.,216.,.67,0.]]])
        out=non_max_suppression(x,conf_thres=.1,iou_thres=.6,end2end=True)
        self.assertEqual(len(out[0]),2)  # no unsupported assumption that .6 performs NMS
    def test_snapshot_off_and_async_writer_bounded(self):
        cfg=cfgcopy();frame=np.zeros((40,40,3),np.uint8)
        recorder=TrackingSnapshots(cfg);self.assertFalse(recorder.submit(frame,{},1));self.assertIsNone(recorder.thread)
        with tempfile.TemporaryDirectory() as folder:
            cfg.TRACK_SNAPSHOT_ENABLED=True;cfg.TRACK_SNAPSHOT_DIRECTORY=folder;cfg.TRACK_SNAPSHOT_MAX_FILES=1
            recorder=TrackingSnapshots(cfg);self.addCleanup(recorder.close)
            entered=threading.Event();release=threading.Event();original=recorder._write
            def slow(f,m):entered.set();release.wait(2);original(f,m)
            recorder._write=slow
            self.assertTrue(recorder.submit(frame,{'trigger':['HIGH_IOU'],'frame_id':1},1))
            self.assertTrue(entered.wait(1));self.assertTrue(recorder.thread.is_alive())
            self.assertFalse(recorder.submit(frame,{},1.1))
            self.assertTrue(recorder.submit(frame,{'trigger':['ENTER_HOLD'],'frame_id':2},2.1))
            self.assertFalse(recorder.submit(frame,{},3.2))
            release.set();recorder.pending.join();recorder.close()
            self.assertEqual(len(list(Path(folder).glob('*.json'))),1)
            self.assertEqual(len(list(Path(folder).glob('*.jpg'))),2)
    def test_stale_control_stop_not_inference(self):
        with patch.object(latency.profiler,'count') as count:
            latency.record_stale('ZOOM_SESSION_MISMATCH',category='control_stop_session_mismatch')
            latency.record_stale('TRACKING_RESET')
        self.assertNotIn(('stale_inference_results',),[c.args for c in count.call_args_list])
        with patch.object(latency.profiler,'count') as count:
            latency.record_stale('DETECTION_FRAME_TOO_OLD')
        self.assertIn(('stale_inference_results',),[c.args for c in count.call_args_list])

class RecordedReplayTests(unittest.TestCase):
    def test_recorded_failures_have_only_past_evidence(self):
        path=Path(__file__).resolve().parents[1]/'docs/target_tracking_p0_20261112/failure_cases.json'
        cases=json.loads(path.read_text());self.assertEqual(len(cases),5)
        for case in cases:
            with self.subTest(marker=case['marker']):
                self.assertLess(case['previous_observation'][0],case['observation_time'])
                stamp=case['last_positive_evidence_timestamp']
                if stamp is not None:self.assertLessEqual(stamp,case['observation_time'])
                self.assertIsNone(case['ground_truth_identity'])
    def test_recorded_strong_case_still_holds(self):
        from ai.overlap_policy import OverlapMemory
        path=Path(__file__).resolve().parents[1]/'docs/target_tracking_p0_20261112/failure_cases.json'
        case=next(c for c in json.loads(path.read_text()) if c['marker']=='09:44:08.234')
        s=SimpleNamespace(cfg=cfgcopy(),lock_session_id=case['session'],track_id=case['target_id'],
            person_box=case['previous_observation'][1],last_positive_verify=case['last_positive_evidence_timestamp'],
            locked=True,crossing_hold=False,negative_verifications=0,identity_status='VERIFIED')
        candidate=next(p for p in case['persons'] if p['track_id']==s.track_id)
        blocked,_=OverlapMemory().assess(candidate,case['persons'],s,case['observation_time'],case['previous_observation'][0])
        self.assertTrue(blocked)

if __name__=='__main__':unittest.main()
