"""Local-video/cache Phase 0/1 replay. No camera import, network or motor output.

extract caches real detector/ByteTrack/OSNet observations once for both policies.
replay runs the existing baseline and optionally Phase 1 over identical evidence.
Baseline verification completion is deterministic at the next sampled frame;
this is a selection-policy replay, not a real-time scheduler/PTZ benchmark.
"""
import argparse
from concurrent.futures import Future
from contextlib import contextmanager
import copy
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@contextmanager
def offline_guard():
    from unittest.mock import patch
    def blocked(*args, **kwargs):
        raise RuntimeError('Network prohibited in identity offline replay')
    with patch.object(socket.socket, 'connect', blocked), patch.object(socket.socket, 'connect_ex', blocked), \
         patch.object(socket, 'create_connection', blocked), patch.object(socket.socket, 'sendto', blocked):
        yield


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2, allow_nan=False)


def dry_pan(box, shape, age, cfg):
    from control.pan_policy import plan_pan
    plan = plan_pan(box, shape, cfg, age=age, verified=box is not None)
    return dict(mode='dry_run_existing_plan_pan', plan=asdict(plan), physical_commands=0,
                assumptions=dict(enabled=True, manual=False, busy=False, settling=False, active=False),
                production_controller_evaluated=False)


def extract(args):
    import cv2
    import numpy as np
    import torch
    import config
    from ai.detector import PersonDetector
    from ai.hybrid_reid import HybridReIDTargetSelector
    from person_search.reid import OSNetReID
    from runtime.byte_tracking import ByteTrackPersonTracker
    if not args.video.is_file() or not args.reference.is_file():
        raise ValueError('Local video and explicit reference image required')
    if args.stride < 1 or args.frames < 1 or args.start_frame < 0:
        raise ValueError('Invalid sampling range')
    args.output.mkdir(parents=True, exist_ok=False)
    cv2.setNumThreads(1)
    torch.set_num_threads(1)
    cfg = SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    cfg.CAMERA_MOTION_DIAGNOSTICS_ENABLED = cfg.CAMERA_MOTION_RECORD_ENABLED = cfg.TRACK_SNAPSHOT_ENABLED = False
    detector = PersonDetector(cfg.PERSON_MODEL_PATH, conf_threshold=cfg.BYTE_TRACK_LOW_THRESHOLD,
                              iou_threshold=cfg.PERSON_IOU_THRESHOLD, imgsz=cfg.PERSON_IMGSZ,
                              device=args.device)
    class RecordingReID:
        def __init__(self):
            self.model = OSNetReID(cfg.REID_MODEL_PATH, device=args.device, batch_size=cfg.REID_BATCH_SIZE)
            self.calls = []
        def extract_embeddings(self, crops):
            emb = self.model.extract_embeddings(crops)
            self.calls.append(emb.detach().cpu().numpy().copy())
            return emb
    reid = RecordingReID()
    scorer = HybridReIDTargetSelector(cfg, reid)
    scorer.replace_target(scorer.load_target_from_path(args.reference, detector))
    tracker = ByteTrackPersonTracker(cfg)
    policy_keys = [k for k in vars(cfg) if k.startswith(('REID_', 'BYTE_', 'PERSON_', 'TARGET_', 'IDENTITY_', 'AUTO_PAN', 'AUTO_TILT'))
                   or k in ('MAX_CONTROL_OBSERVATION_AGE', 'AI_MAX_FPS')]
    settings = {k: getattr(cfg, k) for k in policy_keys}
    meta = dict(schema=1, evidence='real_video_model_inference', video=str(args.video.resolve()),
        video_sha256=digest(args.video), reference=str(args.reference.resolve()), reference_sha256=digest(args.reference),
        model_sha256={p: digest(p) for p in (cfg.PERSON_MODEL_PATH, cfg.REID_MODEL_PATH)},
        config=settings, config_sha256=digest(ROOT/'config.py'), device=args.device,
        reference_embeddings=scorer.gallery.embeddings.detach().cpu().tolist(),
        start_frame=args.start_frame, requested_frames=args.frames, stride=args.stride,
        timestamp_source='OpenCV CAP_PROP_POS_MSEC media PTS; not sensor exposure time',
        baseline_source_sha256={p: digest(ROOT/p) for p in ('ai/hybrid_reid.py','ai/reid_target.py','ai/overlap_policy.py','runtime/byte_tracking.py','control/pan_policy.py')},
        gallery_quality='unreviewed; no automatic gallery admission', ground_truth=False)
    cap = cv2.VideoCapture(str(args.video.resolve()))
    previous = None
    count = 0
    started = time.perf_counter()
    try:
        if not cap.isOpened():
            raise ValueError('Cannot decode video')
        with (args.output/'observations.jsonl').open('x') as out:
            for fid in range(args.start_frame+args.frames):
                ok, frame = cap.read()
                if not ok:
                    break
                if fid < args.start_frame or (fid-args.start_frame) % args.stride:
                    continue
                pts = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
                if not math.isfinite(pts) or (previous is not None and pts <= previous):
                    raise ValueError('Missing/nonmonotonic video PTS; refusing inferred default FPS')
                previous = pts
                received = time.perf_counter()
                detections = detector.detect(frame)
                detection_ms = (time.perf_counter()-received)*1000
                if detector.last_error:
                    raise RuntimeError(detector.last_error)
                persons = tracker.update(detections, pts, frame_id=fid)
                reid.calls.clear()
                begin = time.perf_counter()
                if persons:
                    scorer.frame_id = fid
                    scorer._scores(frame, persons)
                reid_ms = (time.perf_counter()-begin)*1000
                normalized_index = 0
                candidates = []
                for i, p in enumerate(persons):
                    emb = reid.calls[0][i]
                    if p.get('normalized_similarity') is not None:
                        if p['normalized_similarity'] > p['raw_similarity']:
                            emb = reid.calls[1][normalized_index]
                        normalized_index += 1
                    candidates.append(dict(p, candidate_id=f'{fid}:{i}', embedding=emb.tolist(),
                                           occluded=False, quality_approved=False))
                row = dict(frame_id=fid, timestamp=pts, frame_shape=list(frame.shape),
                           detections=detections, candidates=candidates, detection_ms=detection_ms,
                           reid_ms=reid_ms, observation_age_s=time.perf_counter()-received)
                out.write(json.dumps(row, allow_nan=False)+'\n')
                if count == 0:
                    cv2.imwrite(str(args.output/'first_frame.jpg'), frame)
                count += 1
    finally:
        cap.release()
    meta.update(samples=count, extraction_wall_s=time.perf_counter()-started,
                observations_sha256=digest(args.output/'observations.jsonl'))
    write_json(args.output/'manifest.json', meta)
    print(json.dumps(dict(samples=count, output=str(args.output), extraction_wall_s=meta['extraction_wall_s'])))


def replay_cache(cache, output, *, identity_enabled=False, annotations=None, gallery_enabled=False):
    import numpy as np
    import config
    from ai.hybrid_reid import HybridReIDTargetSelector
    from ai.target_identity import TargetIdentityManager, IdentityPolicy, Candidate
    from tools.identity_metrics import evaluate
    cache, output = Path(cache), Path(output)
    meta = json.loads((cache/'manifest.json').read_text())
    if meta['observations_sha256'] != digest(cache/'observations.jsonl'):
        raise ValueError('Observation cache hash mismatch')
    for p, sha in meta['baseline_source_sha256'].items():
        if digest(ROOT/p) != sha:
            raise ValueError('Baseline source changed since extraction: '+p)
    if annotations is not None and annotations.get('observations_sha256') != meta['observations_sha256']:
        raise ValueError('Annotation/cache provenance mismatch')
    cfg = SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if k.isupper()})
    for k, v in meta['config'].items():
        setattr(cfg, k, v)
    cfg.IDENTITY_MEMORY_ENABLED = identity_enabled
    cfg.IDENTITY_MEMORY_GALLERY_ENABLED = gallery_enabled
    class ReplayBaseline(HybridReIDTargetSelector):
        def _scores(self, frame, persons):
            return [p['reid_score'] for p in persons]
        def _start_verification(self, frame, persons, now):
            # Same snapshot/token/consumer policy, deterministic one-frame completion.
            if getattr(self, '_verification', None) is not None:
                return
            if self.cfg.REID_CANDIDATE_AWARE:
                from ai.overlap_policy import overlap_geometry
                from ai.reid_target import iou
                target = next((p for p in persons if p['track_id'] == self.track_id), None)
                if target is None:
                    return
                persons = [p for p in persons if p is target or overlap_geometry(target['bbox'],p['bbox'])['overlap'] > 0
                           or (self.person_box is not None and iou(self.person_box,p['bbox']) >= self.cfg.REID_TRACK_IOU)]
            future = Future()
            observations = copy.deepcopy(persons)
            future.set_result(self._scores(frame, observations))
            self._verification = (future, (self.verification_epoch, self.lock_session_id, self.track_id), now, self.frame_id, observations)
            self.last_verify = now
            self.identity_status = 'VERIFYING'
            self.transition_reason = 'PERIODIC_VERIFY'
    baseline = ReplayBaseline(cfg)
    baseline.embedding = np.asarray(meta['reference_embeddings'][0])
    baseline.error = None
    manager = TargetIdentityManager(meta['reference_embeddings'][0], IdentityPolicy.from_config(cfg),
        session_id='offline', target_uid='offline_target', reference_views=meta['reference_embeddings'][1:]) if cfg.IDENTITY_MEMORY_ENABLED else None
    rows = [json.loads(l) for l in (cache/'observations.jsonl').read_text().splitlines()]
    results = {'baseline': []}
    if manager:
        results['identity'] = []
    for row in rows:
        fid, pts = row['frame_id'], row['timestamp']
        persons = copy.deepcopy(row['candidates'])
        baseline.frame_id = fid
        baseline.frame_age_ms = row['observation_age_s']*1000
        # Shared offline freshness gate; expired observations never enter either policy.
        fresh = 0 <= row['observation_age_s'] <= cfg.MAX_CONTROL_OBSERVATION_AGE
        person = baseline.select_person(None, persons, pts) if fresh else None
        if fresh:
            baseline.finish_frame(person, None)
        common = {k: row[k] for k in ('frame_id','timestamp','detection_ms','reid_ms','observation_age_s','detections')}
        base = dict(common, target_uid=baseline.target_uid, state=baseline.stage,
                    selected_candidate=person['candidate_id'] if person else None,
                    track_id=person['track_id'] if person else None, bbox=person['bbox'] if person else None,
                    top1=baseline.top1, top2=baseline.top2, margin=baseline.margin,
                    reason=baseline.transition_reason if fresh else 'STALE_OBSERVATION',
                    reason_source='legacy_transition_reason; may predate current frame',
                    reason_rejected=None if person else 'LEGACY_SELECTOR_RETURNED_NONE' if fresh else 'STALE_OBSERVATION',
                    occlusion_flag=baseline.crossing_hold,
                    candidates=[{k:v for k,v in p.items() if k != 'embedding'} for p in persons])
        base['ptz_decision'] = dry_pan(base['bbox'], row['frame_shape'], row['observation_age_s'], cfg)
        results['baseline'].append(base)
        if manager:
            candidates = [Candidate(p['candidate_id'], p['track_id'], tuple(p['bbox']), p['confidence'],
                tuple(p['embedding']), fid, pts, manager.session_id, occluded=p.get('occluded',False),
                policy_rejection=p.get('policy_rejection'), quality_approved=p.get('quality_approved',False)) for p in persons]
            decision = manager.observe(session_id=manager.session_id, frame_id=fid, timestamp=pts,
                now=pts+row['observation_age_s'], candidates=candidates)
            decision.update(common)
            decision['reason_rejected'] = None if decision['selected_candidate'] else decision['reason']
            decision['occlusion_flag'] = any(a['reason'] in ('PERSON_OVERLAP','OCCLUDED_CROP') for a in decision['candidate_audit'])
            decision['ptz_decision'] = dry_pan(decision['bbox'], row['frame_shape'], row['observation_age_s'], cfg)
            decision['candidates'] = base['candidates']
            results['identity'].append(decision)
    output.mkdir(parents=True, exist_ok=False)
    report = dict(kind='same-evidence offline policy replay; not production scheduler benchmark',
        observations_sha256=meta['observations_sha256'], video=meta['video'], reference=meta['reference'],
        identity_enabled=identity_enabled, gallery_enabled=gallery_enabled, ground_truth=annotations is not None,
        physical_commands=0, baseline_completion='cached verification consumed on next sampled frame',
        timing_scope='shared extraction measurements, NOT per-policy end-to-end FPS', metrics={})
    report['dry_pan_config'] = {k:v for k,v in vars(cfg).items() if k.startswith(('AUTO_PAN','AUTO_TILT'))}
    report['candidate_source_sha256'] = {p: digest(ROOT/p) for p in ('ai/target_identity.py','tools/identity_metrics.py','tools/identity_replay.py')}
    for name, events in results.items():
        with (output/(name+'.jsonl')).open('x') as f:
            for event in events:
                f.write(json.dumps(event, allow_nan=False)+'\n')
        report['metrics'][name] = evaluate(events, annotations)
        report['metrics'][name]['selected_observations'] = sum(r['selected_candidate'] is not None for r in events)
    write_json(output/'report.json', report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    ex = sub.add_parser('extract')
    ex.add_argument('--video', type=Path, required=True)
    ex.add_argument('--reference', type=Path, required=True)
    ex.add_argument('--output', type=Path, required=True)
    ex.add_argument('--device', default='cpu')
    ex.add_argument('--frames', type=int, default=300)
    ex.add_argument('--start-frame', type=int, default=0)
    ex.add_argument('--stride', type=int, default=5)
    rep = sub.add_parser('replay')
    rep.add_argument('--cache', type=Path, required=True)
    rep.add_argument('--output', type=Path, required=True)
    rep.add_argument('--identity-memory', action='store_true', default=False)
    rep.add_argument('--gallery', action='store_true', default=False)
    rep.add_argument('--annotations', type=Path)
    ann = sub.add_parser('annotation-template')
    ann.add_argument('--cache', type=Path, required=True)
    ann.add_argument('--output', type=Path, required=True)
    review = sub.add_parser('review-frames')
    review.add_argument('--cache', type=Path, required=True)
    review.add_argument('--output', type=Path, required=True)
    review.add_argument('--every', type=int, default=10)
    args = p.parse_args()
    with offline_guard():
        if args.command == 'extract':
            extract(args)
        elif args.command == 'replay':
            if args.gallery and not args.identity_memory:
                p.error('--gallery requires --identity-memory')
            report = replay_cache(args.cache, args.output, identity_enabled=args.identity_memory,
                gallery_enabled=args.gallery, annotations=json.loads(args.annotations.read_text()) if args.annotations else None)
            print(json.dumps(report, indent=2))
        elif args.command == 'review-frames':
            import cv2
            meta = json.loads((args.cache/'manifest.json').read_text())
            video = Path(meta['video'])
            if args.every < 1 or not video.is_file() or digest(video) != meta['video_sha256']:
                p.error('Local video hash/every validation failed')
            if digest(args.cache/'observations.jsonl') != meta['observations_sha256']:
                p.error('Cache checksum mismatch')
            rows = [json.loads(l) for l in (args.cache/'observations.jsonl').read_text().splitlines()][::args.every]
            args.output.mkdir(parents=True, exist_ok=False)
            cap = cv2.VideoCapture(str(video))
            try:
                for row in rows:
                    cap.set(cv2.CAP_PROP_POS_FRAMES,row['frame_id'])
                    ok, frame = cap.read()
                    if not ok:
                        raise ValueError('Cannot decode annotation frame')
                    pts = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
                    if abs(pts-row['timestamp']) > .001:
                        raise ValueError('Annotation seek PTS mismatch')
                    for c in row['candidates']:
                        x1,y1,x2,y2 = map(int,c['bbox'])
                        cv2.rectangle(frame,(x1,y1),(x2,y2),(0,255,255),2)
                        cv2.putText(frame,c['candidate_id'],(x1,max(20,y1)),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,255,255),2)
                    cv2.imwrite(str(args.output/f"frame_{row['frame_id']:06d}.jpg"),frame)
            finally:
                cap.release()
        else:
            meta = json.loads((args.cache/'manifest.json').read_text())
            rows = [json.loads(l) for l in (args.cache/'observations.jsonl').read_text().splitlines()]
            write_json(args.output, dict(source='UNREVIEWED_TEMPLATE', target_identity=None,
                observations_sha256=meta['observations_sha256'], frames=[dict(frame_id=r['frame_id'],
                timestamp=r['timestamp'], target_visible=None,
                candidate_identities={c['candidate_id']:None for c in r['candidates']}) for r in rows]))


if __name__ == '__main__':
    main()
