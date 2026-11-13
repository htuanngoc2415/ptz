"""Measurement correctness tests. Synthetic fixtures are never hardware benchmarks."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
import contextlib
import io
from unittest.mock import patch

from telemetry import latency
from telemetry.pipeline_report import build_report, read_traces
from runtime.face_worker import FaceJob


class PipelineProfilingTests(unittest.TestCase):
    def test_gui_frames_do_not_hide_model_initialization_failure(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[self.row('FRAME_RECEIVED',10,None,frame_id=1),
                  self.row('STRUCTURED_MODEL_INIT_ERROR',20,None,error='CUBLAS_STATUS_ALLOC_FAILED'),
                  self.row('GUI_SUBMITTED',30,None,frame_id=1)]
            report=build_report([self.write(d,rows)],Path(d)/'out')
            self.assertEqual(report['event_count'],3)
            self.assertEqual(report['runs']['r']['pipeline_health']['status'],'MODEL_INIT_FAILED')
            self.assertIsNone(report['runs']['r']['end_to_end_ms'])

    def test_launcher_reports_ai_failure_even_after_normal_gui_exit(self):
        from tools.profile_pipeline import main
        with tempfile.TemporaryDirectory() as d:
            with patch('tools.profile_pipeline.subprocess.Popen') as process, \
                 patch('telemetry.pipeline_report.build_report') as report:
                process.return_value.wait.return_value=0
                report.return_value=dict(event_count=3,runs={'r':dict(pipeline_health=dict(status='MODEL_INIT_FAILED'))})
                with contextlib.redirect_stdout(io.StringIO()):
                    code=main(['run','--scenario','P1','--output',d])
            manifest=json.loads(next(Path(d).glob('*/manifest.json')).read_text())
            self.assertEqual(code,3)
            self.assertEqual(manifest['child_exit_code'],0)
            self.assertEqual(manifest['status'],'AI_PIPELINE_FAILED')

    def test_launcher_startup_failure_is_not_a_successful_profile(self):
        from tools.profile_pipeline import main
        with tempfile.TemporaryDirectory() as d:
            with patch('tools.profile_pipeline.subprocess.Popen') as process:
                process.return_value.wait.return_value = 1
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main(['run', '--scenario', 'P1', '--output', d])
            manifest = json.loads(next(Path(d).glob('*/manifest.json')).read_text())
            report = json.loads(next(Path(d).glob('*/report.json')).read_text())
            self.assertEqual(code, 1)
            self.assertEqual(manifest['status'], 'FAILED_NO_TRACE')
            self.assertEqual(manifest['events'], 0)
            self.assertEqual(report['evidence'], 'no_trace')
            self.assertIn('Không có số đo pipeline/scenario', output.getvalue())

    def write(self, directory, rows, name='trace.jsonl'):
        path=Path(directory)/name
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return path

    def row(self, event, ms, session='a', **fields):
        return dict(event=event, monotonic_ns=int(ms*1e6), run_id='r',
                    lock_session_id=session, thread='worker', **fields)

    def test_parallel_e2e_is_timestamp_difference(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[self.row('TARGET_FIRST_OBSERVED',100,observed_ns=0),
                  self.row('TARGET_LOCKED',200),self.row('IDENTITY_CONFIRMED',1000),
                  self.row('ZOOM_END',900,metric='zoom_ms',duration_ms=700),
                  self.row('FACE_END',900,metric='face_ms',duration_ms=700)]
            report=build_report([self.write(d,rows)],Path(d)/'out')
            s=report['sessions'][0]
            self.assertEqual(s['journey']['end_to_end_ms'],1000)
            self.assertIsNone(s['journey']['milestones']['focus_quality_validated']['monotonic_ns'])
            self.assertIsNone(s['sensor_to_display_ms'])

    def test_missing_and_reversed_origin_remain_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[self.row('IDENTITY_CONFIRMED',100),
                  self.row('TARGET_FIRST_OBSERVED',300,observed_ns=200_000_000)]
            report=build_report([self.write(d,rows)],Path(d)/'out')
            self.assertIsNone(report['sessions'][0]['journey']['end_to_end_ms'])

    def test_explicit_appearance_annotation_uses_receipt_not_sensor(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[self.row('FRAME_RECEIVED',10,None,frame_id=1),
                  self.row('TARGET_FIRST_OBSERVED',200,observed_ns=100_000_000),
                  self.row('IDENTITY_CONFIRMED',500)]
            path=self.write(d,rows)
            annotations=Path(d)/'annotations.json'
            annotations.write_text(json.dumps([dict(run_id='r',session_id='a',appearance_frame_id=1)]))
            report=build_report([path],Path(d)/'out',annotations=annotations)
            session=next(s for s in report['sessions'] if s['session_id']=='a')
            self.assertEqual(session['journey']['end_to_end_ms'],490)
            self.assertIn('operator-annotated',session['journey']['origin'])
            self.assertIsNone(session['sensor_to_display_ms'])

    def test_never_join_recovery_motor_to_future_lock_by_frame(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[self.row('ZOOM_COMMAND_SENT',90,None,frame_id=1,rpc_method='ptz.start',rpc_code='ZoomWide'),
                  self.row('YOLO_INFERENCE_END',100,None,frame_id=1),
                  self.row('TARGET_LOCKED',200,frame_id=1)]
            events,_=read_traces([self.write(d,rows)])
            self.assertIsNone(events[0]['session_id'])
            self.assertEqual(events[1]['session_id'],'a')
            self.assertIsNone(events[1]['track_generation'])

    def test_runs_and_generations_are_not_invented(self):
        with tempfile.TemporaryDirectory() as d:
            rows=[self.row('TARGET_FIRST_OBSERVED',10,observed_ns=0),
                  dict(self.row('IDENTITY_CONFIRMED',100),run_id='other')]
            report=build_report([self.write(d,rows)],Path(d)/'out')
            self.assertEqual(len(report['sessions']),2)
            self.assertTrue(all(s['journey']['end_to_end_ms'] is None for s in report['sessions']))

    def test_rotation_is_loaded_once_and_sorted(self):
        with tempfile.TemporaryDirectory() as d:
            base=self.write(d,[self.row('B',200)],'trace.jsonl')
            old=self.write(d,[self.row('A',100)],'trace.jsonl.1')
            rows,info=read_traces([base,old])
            self.assertEqual([r['event'] for r in rows],['A','B'])
            self.assertEqual(len(info['sources']),2)

    def test_bad_rows_reported_without_wall_clock_substitution(self):
        with tempfile.TemporaryDirectory() as d:
            path=self.write(d,[{'event':'x','timestamp':'2026-01-01'}])
            with path.open('a') as stream:stream.write('{')
            report=build_report([path],Path(d)/'out')
            self.assertEqual(report['event_count'],0)
            self.assertEqual(len(report['provenance']['invalid_rows']),2)

    def test_correlation_alias_precedence_and_face_generation(self):
        p=latency.LatencyProfiler();p.enabled=True
        with latency.correlated(lock_session_id='old',target_track_id=1,track_generation=7):
            p.event('X',lock_session_id='new',target_track_id=2)
        row=p.queue.get_nowait()
        self.assertEqual((row['session_id'],row['track_id'],row['track_generation']),('new',2,7))
        self.assertEqual(row['timestamp_monotonic'],row['monotonic_ns']/1e9)
        job=FaceJob(1,0,None,None,None,(),'acq','new',2,99,0,False,7)
        self.assertEqual(job.correlation()['track_generation'],7)
        self.assertEqual(job.correlation()['face_generation'],99)
        p.close()

    def test_gate_closes_with_old_context_and_open_gate_has_no_duration(self):
        p=latency.LatencyProfiler();p.enabled=True
        with latency.correlated(lock_session_id='old'):
            p.gate('test','STALE_TRACK')
        with latency.correlated(lock_session_id='new'):
            p.gate('test',None)
            p.gate('test','WATCHDOG')
        rows=[]
        while not p.queue.empty():rows.append(p.queue.get_nowait())
        self.assertEqual([r['event'] for r in rows],['GATE_START','GATE_END','GATE_START'])
        self.assertEqual(rows[1]['session_id'],'old')
        self.assertNotIn('duration_ms',rows[-1])
        p.close()

    def test_html_script_injection_is_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            row=self.row('X',100,reason='</script><img src=x onerror=alert(1)>')
            build_report([self.write(d,[row])],Path(d)/'out')
            html=(Path(d)/'out/report.html').read_text()
            self.assertNotIn('</script><img',html)

    def test_guard_tuple_uses_reason_and_preserves_return(self):
        p=latency.LatencyProfiler();p.enabled=True
        @latency.observed_result('guard', reason_index=0)
        def guard(reason):return reason, {'flag':reason is not None}
        with patch.object(latency,'profiler',p):
            self.assertEqual(guard('STALE_TRACK'),('STALE_TRACK',{'flag':True}))
            self.assertEqual(guard(None),(None,{'flag':False}))
        rows=[]
        while not p.queue.empty():rows.append(p.queue.get_nowait())
        self.assertEqual([r['reason'] for r in rows],['STALE_TRACK','STALE_TRACK'])
        self.assertEqual([r['event'] for r in rows],['GATE_START','GATE_END'])
        p.close()

    def test_resources_written_and_emit_overhead_is_separate(self):
        with tempfile.TemporaryDirectory() as d:
            p=latency.LatencyProfiler(d,enabled=True)
            for i in range(256):p.event('X',metric='stage_ms',duration_ms=3)
            p._resource_sample()
            self.assertTrue(p.close())
            self.assertIsNone(p.error)
            self.assertEqual(p.totals['profiling_emit_sample_ms'][0],2)
            rows=[json.loads(x) for x in next((Path(d)/'latency').glob('*.jsonl')).read_text().splitlines()]
            sample=next(r for r in rows if r['event']=='RESOURCE_SAMPLE')
            self.assertIn('gpu_percent',sample)
            self.assertEqual(p.totals['stage_ms'][0],256)


if __name__=='__main__':unittest.main()
