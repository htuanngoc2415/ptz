"""ByteTrack motion association; native IDs are never treated as identity evidence."""
from types import SimpleNamespace
import time
from .tracking_snapshots import TrackingSnapshots
from telemetry import latency
from ai.reid_target import iou
import numpy as np
from ultralytics.trackers.byte_tracker import BYTETracker, STrack


class Detections:
    def __init__(self, rows):
        self.rows = rows
        self.conf = rows[:, 4]
        self.cls = np.zeros(len(rows))
        self.xyxy = rows[:, :4]
        self.xywh = self.xyxy.copy()
        self.xywh[:, :2] = (self.xyxy[:, :2] + self.xyxy[:, 2:]) / 2
        self.xywh[:, 2:] -= self.xyxy[:, :2]
    def __len__(self):
        return len(self.rows)
    def __getitem__(self, index):
        return Detections(self.rows[index])


class IndexedByteTracker(BYTETracker):
    def init_track(self, results, img=None):
        # Preserve original indexes through ByteTrack's high/low score partitions.
        return [STrack(np.r_[box, row[5]], score, 0)
                for box, row, score in zip(results.xywh, results.rows, results.conf)]


class ByteTrackPersonTracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tracker = IndexedByteTracker(SimpleNamespace(
            track_high_thresh=cfg.PERSON_CONF_THRESHOLD,
            track_low_thresh=cfg.BYTE_TRACK_LOW_THRESHOLD,
            new_track_thresh=cfg.PERSON_CONF_THRESHOLD,
            track_buffer=round(30 * cfg.REID_LOST_GRACE_SEC),
            match_thresh=.8, fuse_score=False), frame_rate=cfg.AI_MAX_FPS)
        self.ids = {}
        self.next_id = 0
        self.last_received = None
        self.last_diagnostic = float("-inf")
        self.snapshots = TrackingSnapshots(cfg)
        self._snapshot_context = None
        self._last_selection = None
        self._last_reacquired = None
        self.motion_observer = None
        if getattr(cfg, 'CAMERA_MOTION_DIAGNOSTICS_ENABLED', False) or getattr(cfg, 'CAMERA_MOTION_RECORD_ENABLED', False):
            from .camera_motion import MotionObserver
            self.motion_observer = MotionObserver(cfg)

    def reset(self):
        if self.motion_observer is not None:
            self.motion_observer.reset()
        self.tracker.reset()
        self.ids.clear()
        self.last_received = None
        self._snapshot_context = None
        self._last_selection = None
        self._last_reacquired = None

    def update(self, persons, received, frame=None, frame_id=None):
        if self.last_received is not None:
            if received <= self.last_received:
                return []
            if received-self.last_received > self.cfg.REID_LOST_GRACE_SEC:
                self.reset()
        self.last_received = received
        # Repeating the SAME dict is provably the same observation, unlike two
        # distinct boxes with high IoU. Never merge distinct detections by geometry.
        unique, seen = [], set()
        for person in persons:
            if id(person) not in seen:
                seen.add(id(person))
                unique.append(person)
            else:
                latency.profiler.count('repeated_detection_aliases')
        persons = unique
        if self.motion_observer is not None:
            try:
                context = latency.context.get()
                metadata = self.motion_observer.observe(frame, received, frame_id,
                    [p['bbox'] for p in persons],
                    (context.get('lock_session_id'), context.get('track_generation')),
                    detections=persons)
                if metadata is not None:
                    latency.profiler.event('CAMERA_MOTION_OBSERVATION', **metadata)
            except Exception as exc:
                # Optional diagnostics must not change association or admission.
                latency.profiler.event('CAMERA_MOTION_DIAGNOSTIC_FAILED', error_type=type(exc).__name__)
        rows = np.asarray([[*p['bbox'], p.get('confidence', 1.), i]
                           for i, p in enumerate(persons)], dtype=np.float32).reshape(-1, 6)
        output = []
        native_rows = self.tracker.update(Detections(rows))
        for track in native_rows:
            native, index = int(track[4]), int(track[-1])
            if native not in self.ids:
                self.next_id += 1
                self.ids[native] = self.next_id
            if not 0 <= index < len(persons):
                raise ValueError('ByteTrack returned invalid source detection index')
            person = dict(persons[index])
            person['track_id'] = self.ids[native]
            person['tracker_source_index'] = index
            output.append(person)
        self._diagnose(persons, output, native_rows, received, frame_id, frame)
        return output

    def _diagnose(self, detections, output, native_rows, received, frame_id, frame):
        pairs = []
        for i, a in enumerate(output):
            for b in output[i+1:]:
                x,y = a['bbox'], b['bbox']
                intersection = max(0,min(x[2],y[2])-max(x[0],y[0]))*max(0,min(x[3],y[3])-max(x[1],y[1]))
                overlap = intersection/max(1,min((x[2]-x[0])*(x[3]-x[1]),(y[2]-y[0])*(y[3]-y[1])))
                if overlap >= self.cfg.REID_CROSSING_OVERLAP:
                    pairs.append(dict(ids=[a['track_id'],b['track_id']], iou=iou(x,y), overlap_min_area=overlap))
        if not getattr(self.cfg,'TRACK_SNAPSHOT_ENABLED',False) and (
                not pairs or received-self.last_diagnostic < self.cfg.TRACK_DIAGNOSTICS_INTERVAL):
            self._snapshot_context = None
            return
        active = {t.track_id:t for t in self.tracker.tracked_stracks}
        tracks = []
        for row in native_rows:
            t = active.get(int(row[4]))
            tracks.append(dict(tracker_id=self.ids[int(row[4])], native_id=int(row[4]),
                detection_index=int(row[-1]), predicted_updated_bbox=row[:4].tolist(),
                observed_bbox=detections[int(row[-1])]['bbox'],
                confidence=float(row[5]), class_id=int(detections[int(row[-1])].get('class_id',0)), state=str(t.state) if t else None,
                age_updates=self.tracker.frame_id-t.start_frame+1 if t else None,
                updates_since_observed=self.tracker.frame_id-t.frame_id if t else None,
                last_observation_timestamp=received, reid_evidence=None))
        self._snapshot_context = dict(frame_id=frame_id, observation_timestamp=received,
            detections=[dict(index=i,bbox=p['bbox'],confidence=p.get('confidence'),class_id=p.get('class_id',0),
                detector_source_index=p.get('source_detection_index')) for i,p in enumerate(detections)],
            tracks=tracks, pairs=pairs)
        if not pairs or received-self.last_diagnostic < self.cfg.TRACK_DIAGNOSTICS_INTERVAL:
            return
        self.last_diagnostic = received
        latency.profiler.event('TRACKER_OVERLAP_DIAGNOSTICS', frame_id=frame_id,
            observation_timestamp=received, detections=[dict(index=i,bbox=p['bbox'],confidence=p.get('confidence')) for i,p in enumerate(detections)],
            tracks=tracks, pairs=pairs,
            lost_native_ids=[t.track_id for t in self.tracker.lost_stracks],
            removed_native_ids=[t.track_id for t in self.tracker.removed_stracks[-20:]], reason='UNRESOLVED_OVERLAP_NOT_PROVEN_DUPLICATE')

    def record_selection(self, frame, selector, person, received, detector_mode=None):
        if not getattr(self.cfg,'TRACK_SNAPSHOT_ENABLED',False):
            return
        context = self._snapshot_context
        if context is None or context['observation_timestamp'] != received:
            return
        prior = self._last_selection
        current = (selector.track_id, selector.lock_session_id, bool(selector.crossing_hold))
        if person is not None and (prior is None or prior[1] != current[1]):
            self._last_reacquired = received
        triggers = []
        if any(p['iou'] > .8 for p in context['pairs']):triggers.append('HIGH_IOU')
        if current[2] and (prior is None or not prior[2]):
            triggers.append('ENTER_HOLD')
            if self._last_reacquired is not None and received-self._last_reacquired < 1.:
                triggers.append('REACQUIRE_THEN_HOLD')
        if prior and current[0] != prior[0]:triggers.append('TRACK_ID_CHANGED')
        self._last_selection = current
        if triggers:
            metadata=dict(context,trigger=triggers,selected_target=person.get('track_id') if person else None,
                bound_target=selector.track_id,session=selector.lock_session_id,tracking_state=selector.stage,
                identity_state=getattr(selector,'identity_status',None),transition_reason=getattr(selector,'transition_reason',None),
                model_postprocess=detector_mode,wall_timestamp=time.time())
            self.snapshots.submit(frame,metadata,received)
