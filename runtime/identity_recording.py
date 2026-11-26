"""Opt-in detector-frame dataset capture. Reuses the bounded MotionRecorder lane.

No camera access, model inference, identity authorization or motor commands.
The UI requests start/stop; the inference owner submits observed frames only.
"""
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import cv2

from runtime.motion_recording import MotionRecorder


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _plain(value):
    if hasattr(value, 'detach'):
        return value.detach().cpu().tolist()
    if hasattr(value, 'tolist'):
        return value.tolist()
    return value


def automatic_labels(row, max_age):
    """Record the existing selection as a pseudo-label, never as ground truth.

No new inference or threshold tuning. Unselected people remain unknown because
not being selected is not evidence of a different identity.
"""
    age = row['observation_age_s']
    fresh = math.isfinite(age) and 0 <= age <= max_age
    selected = row['selected_candidate'] if fresh and not row['occlusion_flag'] else None
    labels = []
    for candidate in row['candidates']:
        matched = candidate['candidate_id'] == selected
        score = candidate.get('reid_score')
        evidence = ('current_frame_reid' if score is not None and math.isfinite(score)
                    else 'track_continuity_with_prior_reid') if matched else 'not_confirmed'
        labels.append(dict(candidate_id=candidate['candidate_id'],track_id=candidate['track_id'],
            bbox=candidate['bbox'],label='target' if matched else 'unknown',
            target_uid=row['target_uid'] if matched else None,
            evidence=evidence,current_frame_similarity=score,source='pseudo_label',ground_truth=False))
    return dict(frame_id=row['frame_id'],timestamp=row['timestamp'],source='pseudo_label',
        ground_truth=False,selection_status='STALE' if not fresh else 'TARGET_SELECTED' if selected else 'UNCONFIRMED',
        selected_candidate=selected,labels=labels)


class IdentityDatasetWriter(MotionRecorder):
    def __init__(self, cfg, metadata, reference):
        self.metadata = metadata
        self.include_image_hash = True
        self.reference = reference.copy()
        self.failure = None
        self.finished = threading.Event()
        super().__init__(SimpleNamespace(
            CAMERA_MOTION_RECORD_DIRECTORY=cfg.IDENTITY_RECORD_DIRECTORY,
            CAMERA_MOTION_RECORD_MAX_BYTES=cfg.IDENTITY_RECORD_MAX_BYTES,
            CAMERA_MOTION_RECORD_MAX_FRAMES=cfg.IDENTITY_RECORD_MAX_FRAMES), stop_on_limit=True)

    def _run(self):
        try:
            self.root.mkdir(parents=True, exist_ok=False)
            reference = self.root/'target_reference.png'
            if not cv2.imwrite(str(reference), self.reference):
                raise OSError('Cannot write target reference')
            self.reference = None
            metadata = dict(self.metadata, schema=1, source='identity_detector_frame_capture',
                ground_truth=False, reference_image=reference.name, reference_sha256=_sha(reference),
                timestamp_source='decoded application receive monotonic; sensor timestamp unknown',
                frame_sampling='inference observations; NOT full-rate RTSP video',
                created_wall_time_ns=time.time_ns())
            metadata['model_sha256'] = {p:_sha(p) for p in metadata.pop('model_paths',[]) if Path(p).is_file()}
            (self.root/'manifest.json').write_text(json.dumps(metadata, indent=2, allow_nan=False))
            self.metadata = None
            super()._run()
            frames = self.root/'frames.jsonl'
            if not frames.exists():
                frames.touch()
            # Export only successfully written frames; no label for dropped images.
            with (self.root/'auto_labels.jsonl').open('x') as labels, frames.open() as recorded:
                for line in recorded:
                    row = json.loads(line)
                    labels.write(json.dumps(row['auto_annotation'],allow_nan=False)+'\n')
            summary = dict(complete=True, frames=self.written, dropped=self.dropped,
                errors=self.errors, recorded_bytes=self.bytes, frames_sha256=_sha(frames),
                max_frames=self.max_frames, max_bytes=self.max_bytes, ground_truth=False)
            (self.root/'summary.json').write_text(json.dumps(summary, indent=2))
        except Exception as exc:
            self.failure = type(exc).__name__+': '+str(exc)
            self.errors += 1
            self.stop.set()
        finally:
            self.finished.set()


class IdentityCapture:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = False
        self.writer = None
        self.binding = None
        self.last_frame = -1
        self.last_timestamp = None
        self.message = 'Record OFF'
        self._lock = threading.Lock()

    def start(self):
        with self._lock:
            if self.writer is not None and self.writer.thread.is_alive():
                if not self.enabled:
                    self.message = 'Đang hoàn tất bản ghi; chờ rồi Start lại'
                return False
            self.writer = None
            self.binding = None
            self.last_frame = -1
            self.last_timestamp = None
            self.enabled = True
            self.message = 'Đang chờ target reference và frame AI'
            return True

    def stop(self):
        with self._lock:
            self.enabled = False
            if self.writer is not None:
                self.writer.stop.set()  # drain existing queue; never join on Tk/inference
            self.message = 'Record stopped'

    def close(self):
        self.stop()
        return self.writer.close() if self.writer is not None else True

    def status(self):
        with self._lock:
            w = self.writer
            if w is None:
                return self.message
            if w.failure:
                return 'Record ERROR: '+w.failure
            state = ('Đã hoàn tất — đã lưu nhãn tự động' if w.finished.is_set() else
                     'Đang hoàn tất bản ghi…' if not self.enabled else self.message)
            return f'{state} | saved={w.written} drop={w.dropped} error={w.errors}\n{w.root}'

    def record(self, frame, detections, persons, selector, selected, frame_id, timestamp, detection_ms):
        # Every failure stays in the recorder; it must never become a control exception.
        if not self.enabled:
            return False
        try:
            with self._lock:
                if not self.enabled:
                    return False
                if (not math.isfinite(timestamp) or timestamp > time.monotonic()
                        or frame_id <= self.last_frame
                        or (self.last_timestamp is not None and timestamp <= self.last_timestamp)):
                    return False
                if self.writer is None:
                    target = getattr(selector, 'target_data', None)
                    if not target or target.get('crop') is None or not selector.valid:
                        self.message = 'Chờ target reference hợp lệ; chưa ghi'
                        return False
                    self.binding = (getattr(selector, 'target_uid', None), id(selector.embedding))
                    names = [k for k in dir(self.cfg) if k.startswith(('REID_', 'BYTE_', 'PERSON_', 'TARGET_',
                             'IDENTITY_', 'AUTO_PAN', 'AUTO_TILT')) or k in ('AI_MAX_FPS', 'MAX_CONTROL_OBSERVATION_AGE')]
                    config = {k: getattr(self.cfg, k) for k in names}
                    root = Path(__file__).resolve().parents[1]
                    sources = ('ai/hybrid_reid.py','ai/reid_target.py','ai/overlap_policy.py',
                               'runtime/byte_tracking.py','control/pan_policy.py')
                    gallery = getattr(selector, 'gallery', None)
                    refs = _plain(gallery.embeddings) if gallery is not None else [_plain(selector.embedding)]
                    metadata = dict(target_uid=self.binding[0], reference_embeddings=refs, config=config,
                        model_paths=[self.cfg.PERSON_MODEL_PATH,self.cfg.REID_MODEL_PATH],
                        baseline_source_sha256={p:_sha(root/p) for p in sources},
                        selection_kind='baseline proposal before final motor freshness/session gates')
                    self.writer = IdentityDatasetWriter(self.cfg, metadata, target['crop'])
                if self.binding != (getattr(selector, 'target_uid', None), id(selector.embedding)):
                    self.enabled = False
                    self.writer.stop.set()
                    self.message = 'Target session đổi: đã dừng. Start để ghi session mới'
                    return False
                w = self.writer
                if w.failure or w.stop.is_set() or w.written >= w.max_frames or w.bytes >= w.max_bytes:
                    self.enabled = False
                    w.stop.set()
                    self.message = 'Đã đạt giới hạn hoặc lỗi ghi; xem thống kê'
                    return False
                candidates = []
                selected_id = None
                fields = ('track_id','bbox','confidence','reid_score','raw_similarity','normalized_similarity')
                for i, person in enumerate(persons):
                    cid = f'{frame_id}:{i}'
                    candidates.append(dict(candidate_id=cid, **{k:_plain(person.get(k)) for k in fields}))
                    if person is selected:
                        selected_id = cid
                now = time.monotonic()
                metadata = dict(frame_id=frame_id, timestamp=timestamp, frame_shape=list(frame.shape),
                    candidates=candidates, selected_candidate=selected_id,
                    state=selector.stage, target_uid=getattr(selector,'target_uid',None),
                    lock_session_id=selector.lock_session_id, top1=selector.top1, top2=selector.top2,
                    margin=selector.margin, reason=getattr(selector,'transition_reason',None),
                    occlusion_flag=selector.crossing_hold, observation_age_s=now-timestamp,
                    detection_ms=detection_ms, annotation_status='AUTO_LABELED_UNREVIEWED', ground_truth=False)
                metadata['auto_annotation'] = automatic_labels(metadata,self.cfg.MAX_CONTROL_OBSERVATION_AGE)
                accepted = w.submit(frame, detections, metadata)
                self.last_frame, self.last_timestamp = frame_id, timestamp
                self.message = 'Recording AI frames'
                return accepted
        except Exception as exc:
            self.enabled = False
            if self.writer is not None:
                self.writer.stop.set()
            self.message = 'Record ERROR: '+type(exc).__name__+': '+str(exc)
            return False
