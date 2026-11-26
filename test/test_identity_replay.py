import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
import config
from tools.identity_metrics import evaluate
from tools.identity_replay import digest, offline_guard, replay_cache, write_json, ROOT
from test_target_identity import vector


class ReplayTests(unittest.TestCase):
    def make_cache(self, path):
        path.mkdir()
        rows = []
        for f in range(8):
            p = dict(candidate_id=f'{f}:0',track_id=1,bbox=[10,10,60,150],confidence=.9,
                     embedding=vector(),reid_score=1.,occluded=False,quality_approved=False)
            rows.append(dict(frame_id=f,timestamp=f*.1,frame_shape=[200,400,3],candidates=[p],
                             detections=[dict(bbox=p['bbox'],confidence=.9)], detection_ms=10.,
                             reid_ms=20.,observation_age_s=.03))
        (path/'observations.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        write_json(path/'manifest.json',dict(config={}, reference_embeddings=[vector()],
            observations_sha256=digest(path/'observations.jsonl'), video='synthetic_fixture',reference='synthetic_reference',
            baseline_source_sha256={p:digest(ROOT/p) for p in ('ai/hybrid_reid.py','ai/reid_target.py','ai/overlap_policy.py','runtime/byte_tracking.py')}))

    def test_off_default_and_shared_evidence_integration(self):
        self.assertFalse(config.IDENTITY_MEMORY_ENABLED)
        self.assertFalse(config.IDENTITY_MEMORY_GALLERY_ENABLED)
        with tempfile.TemporaryDirectory() as td, offline_guard():
            p = Path(td)
            self.make_cache(p/'cache')
            with patch('ai.target_identity.TargetIdentityManager',side_effect=AssertionError('OFF created manager')):
                off = replay_cache(p/'cache',p/'off')
            on = replay_cache(p/'cache',p/'on',identity_enabled=True)
            self.assertNotIn('identity',off['metrics'])
            self.assertEqual(off['metrics']['baseline'],on['metrics']['baseline'])
            self.assertEqual(on['metrics']['identity']['selected_observations'],6)
            self.assertIsNone(on['metrics']['baseline']['id_switches'])
            # Legacy output parity, apart from intentionally random session identifiers.
            a = [json.loads(l) for l in (p/'off/baseline.jsonl').read_text().splitlines()]
            b = [json.loads(l) for l in (p/'on/baseline.jsonl').read_text().splitlines()]
            for x,y in zip(a,b):
                x.pop('target_uid'); y.pop('target_uid')
                self.assertEqual(x,y)
                self.assertEqual(x['ptz_decision']['physical_commands'],0)

    def test_network_guard(self):
        with offline_guard(), self.assertRaises(RuntimeError):
            socket.create_connection(('127.0.0.1',80))

    def test_cache_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            self.make_cache(p/'cache')
            with (p/'cache/observations.jsonl').open('a') as f:
                f.write('\n')
            with self.assertRaises(ValueError):
                replay_cache(p/'cache',p/'out')

    def test_manual_metrics_have_known_durations_and_episodes(self):
        selected = ['a','b',None,'a','a']
        rows = [dict(frame_id=i,timestamp=float(i),state='AMBIGUOUS' if s is None else 'CONFIRMED_VISIBLE',
                     selected_candidate=s) for i,s in enumerate(selected)]
        labels = dict(source='manual',target_identity='target',frames=[dict(frame_id=i,target_visible=True,
                      candidate_identities={'a':'target','b':'other'}) for i in range(5)])
        r = evaluate(rows,labels)
        self.assertEqual(r['target_correct_tracking_time_s'],2.)
        self.assertEqual(r['wrong_target_tracking_time_s'],1.)
        self.assertEqual(r['id_switches'],2)
        self.assertEqual(r['fragmentation'],1)
        self.assertEqual(r['target_loss'],1)
        self.assertEqual(r['recovery_latency_s'],[2.])

    def test_sparse_labels_do_not_extrapolate_unknown_intervals(self):
        rows = [dict(frame_id=i,timestamp=float(i),state='CONFIRMED_VISIBLE',selected_candidate='a') for i in range(4)]
        ann = dict(source='manual',target_identity='T',frames=[dict(frame_id=i,target_visible=True,
                   candidate_identities={'a':'T'}) for i in (0,2,3)])
        self.assertEqual(evaluate(rows,ann)['annotated_duration_s'],1.)

    def test_unreviewed_annotations_rejected(self):
        with self.assertRaises(ValueError):
            evaluate([],dict(source='UNREVIEWED_TEMPLATE',target_identity=None,frames=[]))


if __name__ == '__main__':
    unittest.main()
