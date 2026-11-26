import copy,json,pathlib,tempfile,unittest
from tools.phase3_metrics import evaluate
class MetricsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=pathlib.Path(self.tmp.name)
  (self.root/'summary.json').write_text(json.dumps(dict(source_sha256='abc',coordinate_width=100,variants={'B':{}})))
  rows=[dict(frame_id=i,media_timestamp=i*.04,outputs={'B':dict(bbox=[10,10,30,50],tracking_status='LOCAL_VISUAL_ONLY')}) for i in range(2)]
  (self.root/'observations.jsonl').write_text('\n'.join(map(json.dumps,rows)))
  self.labels=dict(schema='phase3_manual_bbox_v1',reviewer='independent tester',source_sha256='abc',width=100,height=100,rows=[dict(frame_id=0,media_timestamp=0.,reviewed=True,identity='target',visibility='visible',bbox=[10,10,30,50],camera_motion='static')])
 def tearDown(self):self.tmp.cleanup()
 def test_perfect(self):self.assertEqual(evaluate(self.root,self.labels)['variants']['B']['static']['localization_recall'],1.)
 def test_no_gt(self):
  self.labels['reviewer']=''
  with self.assertRaises(ValueError):evaluate(self.root,self.labels)
 def test_unknown_excluded(self):
  self.labels['rows'][0]['identity']='UNKNOWN';self.assertEqual(evaluate(self.root,self.labels)['reviewed_target_frames'],0)
 def test_duplicate(self):
  self.labels['rows']*=2
  with self.assertRaises(ValueError):evaluate(self.root,self.labels)
 def test_invisible_no_guess(self):
  self.labels['rows'][0]['visibility']='full_occlusion'
  with self.assertRaises(ValueError):evaluate(self.root,self.labels)
 def test_source_mismatch(self):
  self.labels['source_sha256']='bad'
  with self.assertRaises(ValueError):evaluate(self.root,self.labels)
 def test_time_mismatch(self):
  self.labels['rows'][0]['media_timestamp']=.2
  with self.assertRaises(ValueError):evaluate(self.root,self.labels)
 def test_sparse_metrics_unknown(self):
  r=evaluate(self.root,self.labels)['variants']['B']['all'];self.assertIsNone(r['takeover_episodes']);self.assertIsNone(r['recovery_accuracy'])
