import json
from pathlib import Path
import tempfile
import unittest
from tools.analyze_identity_live import analyze


class LiveAnalysisTests(unittest.TestCase):
    def fixture(self, batched=True, age=100.):
        return [dict(event='SHADOW_START',batched=batched,max_age_s=.8,monotonic_ts=10.),
            dict(event='SHADOW_POLICY',source_sha256={'module':'hash'}),
            *[dict(event='SHADOW_COMPARISON',timestamp=t,frame_id=i,age_ms=age,
                   physical_commands=0,decision=dict(ptz_authorized=False)) for i,t in enumerate((10.1,10.4,12.))],
            dict(event='SHADOW_STOP',saved=3,monotonic_ts=12.2)]

    def run_audit(self,rows,traces=()):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'shadow.jsonl';p.write_text('\n'.join(json.dumps(r) for r in rows))
            paths=[]
            if traces:
                tr=Path(folder)/'trace.jsonl';tr.write_text('\n'.join(json.dumps(r) for r in traces));paths=[tr]
            return analyze(p,paths)

    def test_v4_operational_status_and_gaps(self):
        r=self.run_audit(self.fixture())
        self.assertEqual(r['status'],'VERIFIED');self.assertEqual(r['gaps_above_1s'],1)
        self.assertEqual(r['windows']['ON']['receive_to_gui_submit_ms']['n'],0)

    def test_legacy_is_not_v4_and_age_rejected(self):
        rows=self.fixture(False)
        for r in rows:r.pop('monotonic_ts',None)
        self.assertEqual(self.run_audit(rows)['status'],'AWAITING_LIVE_EVIDENCE')
        r=self.run_audit(self.fixture(age=900.))
        self.assertIn('INVALID_AGE',r['errors'])

    def test_foreground_wait_without_resource_and_cpu_exclusion(self):
        rows=[dict(event='INFERENCE_QUEUE_EXIT',timestamp_monotonic=11.,duration_ms=2.,branch='detection'),
              dict(event='INFERENCE_QUEUE_EXIT',timestamp_monotonic=11.1,duration_ms=10.,branch='face',resource='cpu'),
              dict(event='GUI_SUBMITTED',timestamp_monotonic=11.2,duration_ms=60.)]
        r=self.run_audit(self.fixture(),rows)
        self.assertEqual(r['windows']['ON']['foreground_wait_ms']['n'],1)
        self.assertEqual(r['windows']['ON']['foreground_wait_ms']['p50'],2.)

    def test_incomplete_log_never_verified(self):
        self.assertEqual(self.run_audit(self.fixture()[:-1])['status'],'AWAITING_LIVE_EVIDENCE')
