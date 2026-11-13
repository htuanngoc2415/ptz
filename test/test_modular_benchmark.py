"""Framework evidence semantics and safety. No camera, network or pretrained model."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from telemetry.benchmark import GroundTruth,distribution,public_value,summarize_trace,write_report
from tools.modular_benchmark import acceptance,camera_lease,comparison,online_run,parse_stages,replay_run,setup_imports,timeline


class MetricsTests(unittest.TestCase):
    def test_percentiles_filter_unknown_nonfinite(self):
        values=distribution([0,10,20,30,None,float('nan'),-1,True])
        self.assertEqual(values['count'],4)
        self.assertEqual(values['p50'],15)
        self.assertAlmostEqual(values['p90'],27)
        self.assertAlmostEqual(values['p99'],29.7)
        self.assertIsNone(distribution([]))

    def test_missing_annotation_is_not_negative(self):
        gt=GroundTruth({'frames':[{'frame_id':1,'objects':[]}]})
        gt.observe(2,[{'bbox':[0,0,10,10]}])
        self.assertEqual(gt.summary()['evaluated_frames'],0)
        self.assertIsNone(gt.summary()['precision'])
        gt.observe(1,[{'bbox':[0,0,10,10]}])
        self.assertEqual(gt.summary()['fp'],1)
        self.assertEqual(gt.summary()['precision'],0)

    def test_one_to_one_and_identity_switches(self):
        g={'bbox':[0,0,10,10],'identity':'a'}
        gt=GroundTruth({'frames':[{'frame_id':i,'objects':[g]} for i in range(1,4)]})
        gt.observe(1,[dict(bbox=g['bbox'],track_id=2),dict(bbox=g['bbox'],track_id=2)])
        gt.observe(2,[])
        gt.observe(3,[dict(bbox=g['bbox'],track_id=4)])
        out=gt.summary()
        self.assertEqual((out['tp'],out['fp'],out['fn']),(2,1,1))
        self.assertEqual((out['id_switches'],out['track_fragmentation']),(1,1))
        with self.assertRaises(ValueError): gt.observe(3,[])

    def test_invalid_annotations_fail_closed(self):
        for frames in ([{'frame_id':1,'objects':[{'bbox':[0,0,float('nan'),1]}]}],
                       [{'frame_id':1,'objects':[]},{'frame_id':1,'objects':[]}],
                       [{'frame_id':True,'objects':[]}]):
            with self.assertRaises(ValueError): GroundTruth({'frames':frames})

    def test_trace_never_pairs_different_generation_or_session(self):
        rows=[dict(event='TARGET_LOCKED',session_id='a',track_id=1,track_generation=0,monotonic_ns=1000000),
              dict(event='ZOOM_COMMAND_SENT',session_id='a',track_id=1,track_generation=1,monotonic_ns=2000000,rpc_method='ptz.start',rpc_code='PositionABS'),
              dict(event='ZOOM_COMMAND_SENT',session_id='b',track_id=1,track_generation=0,monotonic_ns=2000000,rpc_method='ptz.start',rpc_code='PositionABS')]
        self.assertNotIn('lock_to_first_zoom_ms',summarize_trace(rows)['latency_ms'])
        rows.append(dict(rows[-1],session_id='a',monotonic_ns=3000000))
        rows.append(dict(rows[-1],monotonic_ns=9000000))
        stat=summarize_trace(rows)['latency_ms']['lock_to_first_zoom_ms']
        self.assertEqual(stat['count'],1);self.assertEqual(stat['p50'],2)

    def test_stop_rpc_and_missing_generation_not_zoom_evidence(self):
        common=dict(session_id='s',track_id=1,track_generation=0)
        rows=[dict(common,event='TARGET_LOCKED',monotonic_ns=1),
              dict(common,event='ZOOM_COMMAND_SENT',monotonic_ns=100,rpc_method='ptz.stop',rpc_code='PositionABS')]
        self.assertFalse(summarize_trace(rows)['latency_ms'])
        for row in rows: row.pop('track_generation')
        self.assertIsNone(summarize_trace(rows)['accuracy'])

    def test_unavailable_instrumentation_is_not_a_rejected_job(self):
        out=summarize_trace([dict(event='DECODE_START',reason='timestamp unavailable'),
            dict(event='ZOOM_COMMAND_REJECTED',command_rejection_reason='STALE_TRACK'),
            dict(event='INFERENCE_JOB_SKIPPED',reason='STALE_BEFORE_INFERENCE')])
        self.assertEqual(out['reject_reasons'],{'STALE_TRACK':1,'STALE_BEFORE_INFERENCE':1})
        self.assertEqual(out['event_reasons']['timestamp unavailable'],1)

    def test_acceptance_requires_all_evidence(self):
        self.assertEqual(acceptance({},{} )[0],'NOT_RUN')
        self.assertEqual(acceptance({'fps':10},{'fps':{'min':5},'accuracy':{'min':.9}})[0],'NOT_RUN')
        self.assertEqual(acceptance({'fps':4},{'fps':{'min':5}})[0],'FAIL')
        self.assertEqual(acceptance({'latency':{'p99':3}},{'latency.p99':{'max':5}})[0],'PASS')
        for rule in ({'max':float('nan')},{'min':2,'max':1},{'x':1},{}):
            with self.assertRaises(ValueError): acceptance({}, {'fps':rule})

    def test_credentials_redacted_recursively(self):
        result=public_value({'nested':{'ptz_password':'secret','uri':'rtsp://a:b@host'},'message':'http://a:b@host'})
        self.assertNotIn('secret',json.dumps(result));self.assertNotIn('a:b',json.dumps(result))

    def test_report_escapes_html_and_preserves_status(self):
        with tempfile.TemporaryDirectory() as d:
            write_report(d,dict(results=[dict(stage=0,case='manual',mode='offline',repetition=1,
                status='NOT_RUN',reason='<script>alert(1)</script>',artifact='',metrics={})]))
            page=(Path(d)/'report.html').read_text()
            self.assertNotIn('<script>',page);self.assertIn('&lt;script&gt;',page)
            self.assertEqual(json.loads((Path(d)/'report.json').read_text())['results'][0]['status'],'NOT_RUN')


class SafetyTests(unittest.TestCase):
    def test_motor_needs_confirmation_before_camera_lease(self):
        with patch('tools.modular_benchmark.camera_lease',side_effect=AssertionError('lease touched')):
            result=online_run(1,SimpleNamespace(case='motor',confirm_motor=False),Path('/tmp'))
        self.assertEqual(result['status'],'NOT_RUN')

    def test_online_needs_exclusive_attestation(self):
        with self.assertRaisesRegex(ValueError,'exclusive'):
            with camera_lease(SimpleNamespace(exclusive=False)): pass

    def test_offline_cli_rejects_motor_options(self):
        result=subprocess.run([sys.executable,str(ROOT/'tools/modular_benchmark.py'),'run',
            '--stage','1','--confirm-motor'],capture_output=True,text=True)
        self.assertEqual(result.returncode,2)
        self.assertIn('Offline mode forbids',result.stderr)

    def test_offline_socket_connections_denied(self):
        proc=subprocess.run([sys.executable,'-c',
            'from tools.modular_benchmark import deny_network; import socket; deny_network(); socket.create_connection(("127.0.0.1",9))'],
            cwd=ROOT,capture_output=True,text=True)
        self.assertNotEqual(proc.returncode,0)
        self.assertIn('OFFLINE_NETWORK_FORBIDDEN',proc.stderr)

    def test_replay_missing_dependencies_are_explicit(self):
        args=SimpleNamespace(modules=[3],video=__file__,detections=None,target=None)
        self.assertEqual(replay_run(3,args,Path('/tmp'))['status'],'NOT_RUN')
        args.modules=[11]
        self.assertEqual(replay_run(11,args,Path('/tmp'))['status'],'NOT_IMPLEMENTED')

    def test_unknown_stage_rejected(self):
        self.assertEqual(parse_stages('0,5,0'),[0,5])
        with self.assertRaises(Exception): parse_stages('12')

    def test_comparison_flags_different_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=[]
            for i in range(2):
                p=Path(directory)/str(i);p.mkdir();paths.append(p)
                (p/'manifest.json').write_text(json.dumps({'inputs':{'video':str(i)},'arguments':{}}))
                (p/'report.json').write_text(json.dumps({'results':[]}))
            comparison(paths,Path(directory)/'out')
            data=json.loads((Path(directory)/'out/comparison.json').read_text())
            self.assertFalse(data['comparable'])

    def test_production_camera_package_precedes_legacy_script(self):
        setup_imports()
        import importlib.util
        self.assertEqual(Path(importlib.util.find_spec('camera').origin).parent,ROOT/'camera')

    def test_exclusive_lease_rejects_second_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'camera.json'
            config.write_text(json.dumps({'ptz_ip':'unit-test-no-camera-'+Path(directory).name}))
            args=SimpleNamespace(exclusive=True,config=config)
            with camera_lease(args):
                with self.assertRaisesRegex(RuntimeError,'leased'):
                    with camera_lease(args): pass

    def test_timeline_preserves_missing_generation_and_frame_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/'events.jsonl').write_text(json.dumps(dict(event='FRAME',frame_id=5,video_index=0,
                monotonic_ns=10,source_pts_s=.4,receive_timestamp=1,lock_session_id='s',target_track_id=4))+'\n')
            rows=timeline(p)
            self.assertIsNone(rows[0].get('track_generation'))
            self.assertEqual(rows[0]['track_id'],4)
            self.assertIn('5,0,0.4,1,10',(p/'recording.csv').read_text())


class ReplayIntegrationTests(unittest.TestCase):
    def test_cli_recording_and_repeat_are_synchronized(self):
        try:
            import cv2
            import numpy as np
        except ImportError: self.skipTest('OpenCV/numpy unavailable')
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);video=p/'fixture.avi'
            writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'MJPG'),10,(80,60))
            if not writer.isOpened(): self.skipTest('MJPG unavailable')
            for i in range(4): writer.write(np.full((60,80,3),i*30,np.uint8))
            writer.release()
            limits=p/'limits.json';limits.write_text('{"frames":{"min":4,"max":4}}')
            proc=subprocess.run([sys.executable,str(ROOT/'tools/modular_benchmark.py'),'run',
                '--stage','0','--case','replay','--video',str(video),'--frames','4','--repeat','2',
                '--record','--limits',str(limits),'--output',str(p/'runs')],
                cwd=ROOT,capture_output=True,text=True,timeout=60)
            self.assertEqual(proc.returncode,0,proc.stdout+proc.stderr)
            report_path=next((p/'runs').glob('*/report.json'))
            report=json.loads(report_path.read_text())
            self.assertEqual(len(report['results']),2)
            for result in report['results']:
                self.assertEqual(result['status'],'PASS')
                self.assertIsNone(result['metrics']['accuracy'])
                folder=report_path.parent/result['artifact']
                frames=[json.loads(l) for l in (folder/'events.jsonl').read_text().splitlines() if json.loads(l)['event']=='FRAME']
                self.assertEqual([r['frame_id'] for r in frames],[1,2,3,4])
                self.assertEqual([r['video_index'] for r in frames],[0,1,2,3])
                for name in ('original','overlay'):
                    cap=cv2.VideoCapture(str(folder/(name+'.mp4')))
                    self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),4)
                    cap.release()


if __name__=='__main__': unittest.main()
