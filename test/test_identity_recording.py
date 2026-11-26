"""Hardware-free dataset lifecycle, manual label and export integration tests."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
import config
from runtime.identity_recording import IdentityCapture, IdentityDatasetWriter, automatic_labels
from tools.identity_dataset import AnnotationStore, load_capture, verified_image
from tools.identity_replay import offline_guard, replay_cache


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = SimpleNamespace(**{k:getattr(config,k) for k in dir(config) if k.isupper()})
        self.cfg.IDENTITY_RECORD_DIRECTORY = self.temp.name
        self.cfg.IDENTITY_RECORD_MAX_BYTES = 1000000
        self.cfg.IDENTITY_RECORD_MAX_FRAMES = 10
        self.cfg.PERSON_MODEL_PATH = self.cfg.REID_MODEL_PATH = '/nonexistent/test_model'
        emb = np.zeros(512,dtype=np.float32);emb[0]=1.
        self.selector = SimpleNamespace(target_uid='t',embedding=emb,gallery=None,valid=True,
            target_data={'crop':np.zeros((128,64,3),np.uint8)}, stage='TARGET_LOCKED',
            lock_session_id='s',top1=.9,top2=None,margin=None,crossing_hold=False,
            transition_reason='test')
        self.frame = np.zeros((180,320,3),np.uint8)
        self.person = dict(bbox=(10,10,70,160),track_id=1,confidence=.9,reid_score=.9)
        self.capture = IdentityCapture(self.cfg)

    def tearDown(self):
        self.capture.close()
        self.temp.cleanup()

    def record(self,fid=1):
        return self.capture.record(self.frame,[self.person],[self.person],self.selector,
                                   self.person,fid,time.monotonic(),12.)

    def dataset(self):
        self.capture.start()
        self.assertTrue(self.record())
        self.assertTrue(self.capture.close())
        return self.capture.writer.root

    def test_default_off_has_no_worker_or_files(self):
        self.assertFalse(self.record())
        self.assertIsNone(self.capture.writer)
        self.assertEqual(list(Path(self.temp.name).iterdir()),[])

    def test_pixels_timestamps_and_automatic_labels_are_not_ground_truth(self):
        self.capture.start()
        original = self.frame.copy()
        self.assertTrue(self.record())
        self.frame[:]=255
        self.capture.close()
        path = self.capture.writer.root
        meta,summary,rows = load_capture(path)
        import cv2
        np.testing.assert_array_equal(cv2.imread(str(verified_image(path,rows[0]))),original)
        self.assertEqual(rows[0]['frame_id'],1)
        self.assertEqual(rows[0]['selected_candidate'],'1:0')
        self.assertEqual(rows[0]['annotation_status'],'AUTO_LABELED_UNREVIEWED')
        auto = json.loads((path/'auto_labels.jsonl').read_text())
        self.assertEqual(auto['labels'][0]['label'],'target')
        self.assertEqual(auto['labels'][0]['evidence'],'current_frame_reid')
        self.assertFalse(auto['ground_truth'])
        self.assertFalse(meta['ground_truth'])
        self.assertFalse((path/'annotations.json').exists())
        self.assertEqual(summary['frames'],1)

    def test_continuity_label_does_not_claim_fresh_osnet(self):
        self.person.pop('reid_score')
        path=self.dataset()
        auto=json.loads((path/'auto_labels.jsonl').read_text())
        self.assertEqual(auto['labels'][0]['label'],'target')
        self.assertEqual(auto['labels'][0]['evidence'],'track_continuity_with_prior_reid')

    def test_unselected_or_stale_is_unknown_not_false_identity(self):
        row=dict(frame_id=1,timestamp=1.,observation_age_s=0.,selected_candidate='a',occlusion_flag=False,
                 target_uid='t',candidates=[dict(candidate_id='a',track_id=1,bbox=[0,0,10,20],reid_score=.9),
                                           dict(candidate_id='b',track_id=2,bbox=[20,0,30,20],reid_score=.2)])
        self.assertEqual([r['label'] for r in automatic_labels(row,.8)['labels']],['target','unknown'])
        for changes in ({'observation_age_s':1.},{'selected_candidate':None},{'occlusion_flag':True}):
            self.assertTrue(all(r['label']=='unknown' for r in automatic_labels(dict(row,**changes),.8)['labels']))

    def test_automatic_labels_cannot_be_used_as_manual_ground_truth(self):
        from tools.identity_metrics import evaluate
        with self.assertRaises(ValueError):
            evaluate([],dict(source='pseudo_label',target_identity='target',frames=[]))

    def test_duplicate_frame_never_written_as_new(self):
        self.capture.start()
        self.assertTrue(self.record())
        self.assertFalse(self.record())
        self.capture.close()
        self.assertEqual(self.capture.writer.written,1)

    def test_future_or_nonfinite_timestamp_is_not_observation(self):
        self.capture.start()
        for stamp in (float('nan'),float('inf'),time.monotonic()+100):
            self.assertFalse(self.capture.record(self.frame,[self.person],[self.person],self.selector,
                                                self.person,1,stamp,12.))
        self.assertIsNone(self.capture.writer)

    def test_stop_status_reports_finalized_dataset(self):
        self.dataset()
        self.assertIn('Đã hoàn tất',self.capture.status())

    def test_reference_change_stops_before_mixing_targets(self):
        self.capture.start();self.record()
        self.selector.embedding = self.selector.embedding.copy()
        self.assertFalse(self.record(2))
        self.assertFalse(self.capture.enabled)
        self.capture.close()
        self.assertEqual(self.capture.writer.written,1)

    def test_missing_reference_waits_without_files(self):
        self.selector.target_data = None
        self.capture.start()
        self.assertFalse(self.record())
        self.assertIsNone(self.capture.writer)

    def test_invalid_disk_path_does_not_raise_into_control(self):
        blocked = Path(self.temp.name)/'file';blocked.write_text('x')
        self.cfg.IDENTITY_RECORD_DIRECTORY = str(blocked)
        self.capture.start();self.record();self.capture.close()
        self.assertIsNotNone(self.capture.writer.failure)

    def test_byte_limit_stops_writer(self):
        self.cfg.IDENTITY_RECORD_MAX_BYTES = 10
        self.capture.start();self.record();self.capture.close()
        self.assertEqual(self.capture.writer.written,0)
        self.assertGreater(self.capture.writer.dropped,0)

    def test_submit_failure_is_contained(self):
        self.capture.start()
        with patch.object(IdentityDatasetWriter,'submit',side_effect=OSError('disk failed')):
            self.assertFalse(self.record())
        self.assertFalse(self.capture.enabled)

    def test_manual_labels_require_explicit_complete_review(self):
        path = self.dataset()
        store = AnnotationStore(path)
        self.assertEqual(store.labels,{})
        with self.assertRaises(ValueError):store.confirm(0,{},True)
        with self.assertRaises(ValueError):store.confirm(0,{'1:0':'target'},None)
        with self.assertRaises(ValueError):store.confirm(0,{'1:0':'target'},False)
        store.confirm(0,{'1:0':'target'},True)
        saved = AnnotationStore(path)
        self.assertEqual(saved.labels[1]['candidate_identities'],{'1:0':'target'})
        self.assertEqual(json.loads((path/'annotations.json').read_text())['source'],'manual')

    def test_external_label_edit_is_not_overwritten(self):
        path = self.dataset();store = AnnotationStore(path)
        store.confirm(0,{'1:0':'target'},True)
        (path/'annotations.json').write_text('external edit')
        with self.assertRaises(ValueError):store.confirm(0,{'1:0':'person_2'},True)
        self.assertEqual((path/'annotations.json').read_text(),'external edit')

    def test_image_tampering_rejected(self):
        path = self.dataset();_,_,rows = load_capture(path)
        (path/rows[0]['image']).write_bytes(b'changed')
        with self.assertRaises(ValueError):verified_image(path,rows[0])

    def test_incomplete_capture_cannot_be_labeled(self):
        path = self.dataset();(path/'summary.json').unlink()
        with self.assertRaises(FileNotFoundError):load_capture(path)

    def test_record_to_labeled_cache_to_replay(self):
        import torch
        from tools.prepare_identity_capture import prepare
        path = self.dataset()
        AnnotationStore(path).confirm(0,{'1:0':'target'},True)
        class FakeReID:
            def __init__(self,*a,**kw):pass
            def extract_embeddings(self,crops):
                result = torch.zeros((len(crops),512));result[:,0]=1.
                return result
        cache = Path(self.temp.name)/'cache'
        with offline_guard(),patch('person_search.reid.OSNetReID',FakeReID):
            prepare(path,cache)
            labels=json.loads((cache/'annotations.json').read_text())
            report=replay_cache(cache,Path(self.temp.name)/'comparison',identity_enabled=True,annotations=labels)
        self.assertTrue(report['ground_truth'])
        self.assertEqual(report['metrics']['identity']['samples'],1)
        self.assertEqual(labels['frames'][0]['frame_id'],1)


if __name__ == '__main__':
    unittest.main()
