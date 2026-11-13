"""ReID acquisition and conservative spatial continuity; owned by the AI worker."""
from event_logging import event_print as print
from pathlib import Path
import math
import time
import threading
from uuid import uuid4
from telemetry import latency
from runtime.scheduling import scheduler

import cv2
import numpy as np
from ai.reid.gallery import TargetEmbeddingGallery, illumination_normalized
from ai.quality.image_quality import ImageQualityAnalyzer
from telemetry.events import emit


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    intersection = max(0, x2-x1) * max(0, y2-y1)
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def inside(face, person):
    f, p = face['bbox'], person['bbox']
    cx, cy = (f[0]+f[2])/2, (f[1]+f[3])/2
    return p[0] <= cx <= p[2] and p[1] <= cy <= p[3]


def face_ready(face, sharpness, cfg):
    """Size/alignment gate only; sharpness remains a diagnostic metric."""
    if face is None:
        return False
    x1, y1, x2, y2 = face['bbox']
    landmarks = np.asarray(face.get('landmarks', []))
    return (min(x2-x1, y2-y1) >= cfg.FACE_RECOGNITION_MIN_SIZE
            and landmarks.shape == (5, 2) and np.isfinite(landmarks).all())


class ReIDTargetSelector:
    def __init__(self, cfg, reid=None):
        self.cfg = cfg
        self.reid = reid
        self.embedding = None
        self.gallery = None
        self.track_id = 0
        self.track_generation = 0
        self.reacquiring = False
        self.observation_time = None
        self.last_seen = None
        self.lost_since = None
        self.frame_id = -1
        self.frame_age_ms = 0.0
        self.backlight_state = "UNKNOWN"
        self.quality = ImageQualityAnalyzer(getattr(cfg, 'IMAGE_DARK_THRESHOLD', 90),
            getattr(cfg, 'IMAGE_BACKGROUND_DELTA',45), getattr(cfg,'IMAGE_SHADOW_RATIO',.35),
            getattr(cfg,'IMAGE_SHADOW_LEVEL',60))
        self.error = 'Target not initialized'
        self.signature = None
        self.next_reload_check = 0.0
        self.last_log = float('-inf')
        self.stage = 'SEARCHING'
        self.target_lock = threading.RLock()
        self.target_data = None
        self.acquisition_id = None
        self.lock_session_id = None
        self.first_observed_ns = None
        self.locked_ns = None
        self.recovery_started_ns = None
        self.verify_frames = 0
        self.reset()

    @property
    def valid(self):
        return self.embedding is not None and not self.error

    def set_stage(self, stage):
        if stage != self.stage:
            latency.profiler.event('REID_STATE_TRANSITION', previous_state=self.stage, state=stage,
                lock_session_id=self.lock_session_id, target_track_id=self.track_id, track_generation=self.track_generation,
                frame_id=self.frame_id, target_uid=getattr(self, 'target_uid', None),
                transition_reason=getattr(self, 'transition_reason', None),
                identity_status=getattr(self, 'identity_status', None),
                similarity=self.top1, margin=self.margin, frame_age_ms=self.frame_age_ms,
                last_seen_age=(self.observation_time-self.last_seen
                    if self.observation_time is not None and self.last_seen is not None else None))
            print(f'[STATE] {self.stage} -> {stage}')
            self.stage = stage

    def reset(self, preserve_reacquire=False):
        if not preserve_reacquire:
            self.reacquiring = False
        self.acquisition_id = None
        self.lock_session_id = None
        self.first_observed_ns = None
        self.locked_ns = None
        if not preserve_reacquire:
            self.recovery_started_ns = None
        self.lost_since = None
        self.crossing_hold = False
        self.crossing_confirm = 0
        self.crossing_box = None
        self.locked = False
        self.person_box = self.face_box = self.candidate_box = None
        self.confirm = self.missing = 0
        self.face_missing = 0
        self.top1 = self.top2 = self.margin = None
        self.last_search = self.last_verify = float('-inf')
        self.just_locked = False
        self.set_stage('SEARCHING')

    def invalidate(self, error):
        self.reset()
        self.embedding = None
        self.gallery = None
        self.error = str(error)
        print(f'[REID] DISABLED: {self.error}')

    def load_target_from_path(self, path, detector):
        """Prepare a replacement target without changing the active target."""
        target_path = Path(path).expanduser()
        if target_path.suffix.lower() not in self.cfg.TARGET_ALLOWED_EXTENSIONS:
            raise ValueError("Invalid image format")
        if not target_path.is_file():
            raise ValueError("Invalid image: file does not exist")
        image = cv2.imread(str(target_path))
        if image is None or image.size == 0:
            raise ValueError("Invalid image")
        persons = [p for p in detector.detect(image)
                           if p.get('confidence', 1.) >= self.cfg.PERSON_CONF_THRESHOLD]
        if not persons:
            raise ValueError("No person detected")
        person = max(persons, key=lambda d: (d["bbox"][2]-d["bbox"][0])*(d["bbox"][3]-d["bbox"][1]))
        crop = self.crop(image, person)
        if self.reid is None:
            from person_search.reid import OSNetReID
            self.reid = OSNetReID(self.cfg.REID_MODEL_PATH, device=self.cfg.REID_DEVICE, batch_size=self.cfg.REID_BATCH_SIZE)
        gallery = TargetEmbeddingGallery.build(self.reid, crop, getattr(self.cfg, "REID_GALLERY_ENABLED", False))
        embedding = gallery.embeddings[0]
        norm = float(embedding.norm())
        if embedding.shape != (512,) or not math.isfinite(norm) or abs(norm-1) > 1e-4:
            raise ValueError("Invalid target embedding")
        return dict(path=str(target_path), embedding=embedding, gallery=gallery, crop=crop.copy(), bbox=tuple(person["bbox"]), persons=len(persons))

    def replace_target(self, target):
        """Atomically publish a fully prepared target; never lock during inference."""
        with self.target_lock:
            self.embedding = target["embedding"]
            self.gallery = target.get("gallery")
            self.target_data = target
            self.error = None
            self.signature = None
            self.next_reload_check = float("inf")
        self.reset()

    def reload_if_changed(self, detector, now=None, before_reload=None, force=False):
        """Cache the target; allow an explicit worker-owned retry after transient errors."""
        now = time.monotonic() if now is None else now
        if not force and now < self.next_reload_check:
            return False
        self.next_reload_check = now + self.cfg.REID_RELOAD_INTERVAL
        def stamp(path):
            p = Path(path)
            try:
                s = p.stat()
                return str(p), s.st_mtime_ns, s.st_size
            except OSError:
                return str(p), None
        signature = (stamp(self.cfg.TARGET_IMAGE_PATH), stamp(self.cfg.REID_MODEL_PATH),
                     self.cfg.TARGET_IMAGE_IS_CROP)
        if not force and signature == self.signature:
            return False
        if before_reload is not None:
            before_reload()
        old_signature = self.signature
        self.signature = signature
        self.reset()
        self.embedding = None
        try:
            image = cv2.imread(str(self.cfg.TARGET_IMAGE_PATH))
            if image is None or image.size == 0:
                raise ValueError(f'Cannot read target image: {self.cfg.TARGET_IMAGE_PATH}')
            if not self.cfg.TARGET_IMAGE_IS_CROP:
                persons = [p for p in detector.detect(image)
                           if p.get('confidence', 1.) >= self.cfg.PERSON_CONF_THRESHOLD]
                if not persons:
                    error = getattr(detector, 'last_error', None)
                    if isinstance(error, str) and error:
                        raise RuntimeError(f'Target person detection failed: {error}')
                    raise ValueError('No person detected in target image; auto zoom disabled')
                # Explicit deterministic startup selection: largest person in target image.
                person = max(persons, key=lambda d: (d['bbox'][2]-d['bbox'][0])*(d['bbox'][3]-d['bbox'][1]))
                image = self.crop(image, person)
                print(f'[REID] Target person bbox={person["bbox"]}; persons={len(persons)} (largest selected)')
            if old_signature and signature[1] != old_signature[1]:
                self.gallery = None
                self.reid = None  # release old model before allocating its replacement
            if self.reid is None:
                from person_search.reid import OSNetReID
                self.reid = OSNetReID(self.cfg.REID_MODEL_PATH, device=self.cfg.REID_DEVICE,
                                     batch_size=self.cfg.REID_BATCH_SIZE)
            self.gallery = TargetEmbeddingGallery.build(self.reid, image, getattr(self.cfg, 'REID_GALLERY_ENABLED', False))
            self.embedding = self.gallery.embeddings[0]
            norm = float(self.embedding.norm())
            if self.embedding.shape != (512,) or not math.isfinite(norm) or abs(norm-1) > 1e-4:
                raise ValueError('Invalid target embedding')
            self.error = None
            self.target_data = dict(path=str(self.cfg.TARGET_IMAGE_PATH), embedding=self.embedding, crop=image.copy(), bbox=None, persons=None)
            print(f'[REID] Target ready: {self.cfg.TARGET_IMAGE_PATH}; device={self.embedding.device}')
        except Exception as exc:
            self.invalidate(exc)
        return True

    @staticmethod
    def crop(frame, person):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = map(int, person['bbox'])
        crop = frame[max(0,y1):min(h,y2), max(0,x1):min(w,x2)]
        if x2 <= x1 or y2 <= y1 or crop.size == 0:
            raise ValueError('Invalid person crop')
        return crop

    @latency.measured('REID_ASSOCIATION', 'reid_association_ms')
    def associate(self, detections, previous, threshold, allow_center=False):
        if previous is None or not detections:
            return None
        ranked = sorted(((iou(d['bbox'], previous), i) for i, d in enumerate(detections)), reverse=True)
        if ranked[0][0] < threshold:
            if not allow_center:
                return None
            px1,py1,px2,py2 = previous
            pw,ph = max(1,px2-px1),max(1,py2-py1)
            eligible = []
            for d in detections:
                x1,y1,x2,y2 = d['bbox']
                ratio = max(0,(x2-x1)*(y2-y1))/(pw*ph)
                shift = math.hypot((x1+x2-px1-px2)/2, (y1+y2-py1-py2)/2)/max(pw,ph)
                if .5 <= ratio <= 2 and shift <= getattr(self.cfg, 'REID_CENTER_MAX_SHIFT', .35):
                    eligible.append(d)
            return eligible[0] if len(eligible) == 1 else None
        if len(ranked) > 1 and ranked[0][0]-ranked[1][0] < self.cfg.REID_ASSOC_MARGIN:
            return None  # crossing/overlapping candidates: hold motor, never pick an arbitrary one
        return detections[ranked[0][1]]

    @latency.measured('REID', 'reid_total_ms')
    def _scores(self, frame, persons):
        phase = getattr(self, '_score_trigger', None) or (
            'REACQUISITION' if self.reacquiring else 'OCCLUDED_HOLD' if self.crossing_hold
            else 'TARGET_LOCKED' if self.locked else 'ACQUIRING' if self.confirm else 'SEARCHING')
        with latency.correlated(reid_phase=phase, candidate_count=len(persons)):
            with latency.span('REID_PHASE', 'reid_'+phase.lower()+'_wall_ms', timing='cpu_wall'):
                return self._score_batch(frame, persons)

    def _score_batch(self, frame, persons):
        with latency.span('REID_CROP', 'reid_crop_ms'):
            crops = [self.crop(frame, p) for p in persons]
        with scheduler.use('reid', valid=getattr(self, '_score_valid', None)) as granted:
            if not granted:
                raise TimeoutError('ReID job expired before GPU admission')
            embeddings = self.reid.extract_embeddings(crops)
        latency.profiler.event('REID_CANDIDATES', num_reid_candidates=len(crops),
                               reid_batch_size=min(len(crops), self.cfg.REID_BATCH_SIZE))
        gallery = self.gallery or TargetEmbeddingGallery(self.embedding[None, :])
        with latency.span('REID_MATCH', 'reid_match_ms'):
            raw = gallery.score(embeddings).detach().cpu().tolist()
        normalized = [None] * len(crops)
        qualities = [self.quality.analyze(frame, p) for p in persons]
        indices = [i for i,q in enumerate(qualities) if q and
                   (q['person_mean_luma'] < getattr(self.cfg, 'REID_NORMALIZE_LUMA', 90)
                    or q['local_contrast'] < getattr(self.cfg, 'REID_NORMALIZE_CONTRAST', 35))]
        if getattr(self.cfg, 'REID_NORMALIZATION_ENABLED', False) and indices:
            with latency.span('REID_NORMALIZATION', 'reid_normalization_ms', timing='cpu_wall'):
                variants = [illumination_normalized(crops[i], getattr(self.cfg, 'REID_NORMALIZE_GAMMA', .75),
                            getattr(self.cfg, 'REID_NORMALIZE_CLAHE', 1.5)) for i in indices]
            with scheduler.use('reid', valid=getattr(self, '_score_valid', None)) as granted:
                if not granted:
                    raise TimeoutError('ReID normalization expired before GPU admission')
                scores = gallery.score(self.reid.extract_embeddings(variants)).detach().cpu().tolist()
            for i,score in zip(indices,scores):
                normalized[i] = score
        latency.profiler.event('REID_WORKLOAD', crop_count=len(crops),
            normalization_count=sum(v is not None for v in normalized),
            dark_crop_count=sum(bool(q and q['person_mean_luma'] < getattr(self.cfg,'REID_NORMALIZE_LUMA',90)) for q in qualities),
            low_contrast_crop_count=sum(bool(q and q['local_contrast'] < getattr(self.cfg,'REID_NORMALIZE_CONTRAST',35)) for q in qualities),
            batch_size=min(len(crops),self.cfg.REID_BATCH_SIZE), batch_limit=self.cfg.REID_BATCH_SIZE, timing='cpu_wall',
            gpu_timing='only_explicit_cuda_profiler_spans_are_gpu_time')
        final = []
        for i,(r,n) in enumerate(zip(raw,normalized)):
            mode = getattr(self.cfg, 'REID_FUSION', 'max')
            weight = getattr(self.cfg, 'REID_RAW_WEIGHT', .6)
            if mode not in ('max', 'weighted') or not 0 <= weight <= 1:
                raise ValueError('Invalid ReID fusion configuration')
            score = r if n is None else max(r,n) if mode == 'max' else weight*r+(1-weight)*n
            persons[i].update(raw_similarity=r, normalized_similarity=n, reid_score=score, quality=qualities[i])
            final.append(score)
        return final

    def _crossing_evidence(self, persons, box=None):
        """Overlap relative to the smaller box catches foreground occluders."""
        box = self.person_box if box is None else box
        if box is None:
            return False
        def overlap(a, b):
            x1,y1,x2,y2 = a
            u1,v1,u2,v2 = b
            area = max(0,min(x2,u2)-max(x1,u1))*max(0,min(y2,v2)-max(y1,v1))
            return area/max(1,min((x2-x1)*(y2-y1),(u2-u1)*(v2-v1)))
        relevant = [p for p in persons if overlap(p['bbox'], box)
                    >= getattr(self.cfg, 'REID_CROSSING_OVERLAP', .15)]
        return len(relevant) > 1

    @latency.measured('REID_VERIFICATION', 'reid_verification_ms')
    def _confirm_after_crossing(self, frame, persons, person):
        if person is None:
            self.crossing_confirm = 0
            return None
        scores = self._scores(frame, persons)
        index = next(i for i,p in enumerate(persons) if p is person)
        score = scores[index]
        others = [v for i,v in enumerate(scores) if i != index]
        threshold = getattr(self.cfg, 'REID_CROSSING_THRESHOLD', self.cfg.REID_SIM_THRESHOLD)
        if (not math.isfinite(score) or score < threshold or
                (others and score-max(others) < self.cfg.REID_MARGIN_THRESHOLD)):
            self.crossing_confirm = 0
            self.crossing_box = None
            return None
        continuous = self.crossing_box is not None and iou(person['bbox'], self.crossing_box) >= self.cfg.REID_CONFIRM_IOU
        self.crossing_confirm = self.crossing_confirm+1 if continuous else 1
        self.crossing_box = person['bbox']
        if self.crossing_confirm < getattr(self.cfg, 'REID_CROSSING_CONFIRM_FRAMES', 3):
            return None
        self.crossing_hold = False
        self.crossing_confirm = 0
        emit('TARGET_CONTINUITY_RESTORED', track_id=self.track_id, similarity=score)
        return person

    def _continuous_person(self, frame, persons, now):
        """Fresh appearance evidence on every frame; IDs identify the lock session only."""
        scores = self._scores(frame, persons) if persons else []
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        self.top1 = scores[order[0]] if order else None
        self.top2 = scores[order[1]] if len(order) > 1 else None
        self.margin = self.top1-self.top2 if self.top2 is not None else None
        threshold = getattr(self.cfg, 'REID_ACQUIRE_THRESHOLD', self.cfg.REID_SIM_THRESHOLD)
        accepted = (self.top1 is not None and all(math.isfinite(v) for v in scores) and
                    self.top1 >= threshold and
                    (self.margin is None or self.margin >= self.cfg.REID_MARGIN_THRESHOLD))
        latency.profiler.event('CONTINUOUS_REID_RESULT', frame_id=self.frame_id,
            target_track_id=self.track_id, best_similarity=self.top1,
            second_similarity=self.top2, accepted=accepted)
        if not accepted:
            self.crossing_hold = True
            self.crossing_confirm = 0
            self.set_stage('OCCLUDED_HOLD')
            return None
        if self.crossing_hold:
            self.crossing_confirm += 1
            if self.crossing_confirm < self.cfg.REID_CONFIRM_FRAMES:
                return None
            self.crossing_hold = False
            self.crossing_confirm = 0
            # Old face geometry is not evidence for a newly observed face.
            self.face_box = None
            self.face_missing = 0
            emit('TARGET_CONTINUITY_RESTORED', track_id=self.track_id,
                 similarity=self.top1, evidence='continuous_reid')
        person = persons[order[0]]
        person['track_id'] = self.track_id
        person['appearance_supported'] = True
        self.person_box = person['bbox']
        return person

    def acquisition_allowed(self, candidate, persons):
        return True

    @latency.measured('REID_SELECTION', 'reid_selection_ms')
    def select_person(self, frame, persons, now):
        if self.observation_time is not None and now <= self.observation_time:
            return None
        self.observation_time = now
        self.just_locked = False
        if not self.valid:
            return None
        continuous = getattr(self.cfg, 'TARGET_CONTINUOUS_REID', False)
        if self.locked and continuous:
            return self._continuous_person(frame, persons, now)
        if self.locked:
            if self._crossing_evidence(persons):
                if not self.crossing_hold:
                    emit('TARGET_OCCLUDED', track_id=self.track_id, bbox=self.person_box)
                self.crossing_hold = True
                self.crossing_confirm = 0
                self.crossing_box = None
                self.set_stage('OCCLUDED_HOLD')
                return None
            person = self.associate(persons, self.person_box, self.cfg.REID_TRACK_IOU, allow_center=True)
            if person is not None and self.face_box is not None and self.face_missing == 0:
                # A new body must remain spatially associated with the tracked face.
                if not inside({'bbox': self.face_box}, person):
                    person = None
            if self.crossing_hold:
                person = self._confirm_after_crossing(frame, persons, person)
            if person is not None:
                self.person_box = person['bbox']
                # ReID is supporting evidence only after lock, and only on full-body-like crops.
                x1, y1, x2, y2 = person['bbox']
                h, w = frame.shape[:2]
                full = (x1 > 0 and y1 > 0 and x2 < w and y2 < h
                        and (y2-y1)/max(1,x2-x1) >= self.cfg.REID_FULL_BODY_ASPECT)
                self.verify_frames += 1
                pipeline = getattr(self.cfg, 'PIPELINE', {})
                due = (pipeline.get('reid_on_every_frame', False) or
                       (self.verify_frames >= pipeline.get('reid_verification_interval_frames', 10)
                        and now-self.last_verify >= self.cfg.REID_VERIFY_INTERVAL))
                if full and due:
                    self.last_verify = now
                    self.verify_frames = 0
                    with latency.span('REID_VERIFICATION', 'reid_verification_ms'):
                        score = self._scores(frame, [person])[0]
                    if not math.isfinite(score):
                        raise ValueError('Invalid supporting ReID score')
                    person['reid_score'] = score
                    person['appearance_supported'] = score >= getattr(self.cfg, 'REID_KEEP_THRESHOLD', .5)
                    self.top1 = score
                    self.top2 = self.margin = None
            return person
        if not persons:
            self.confirm = 0
            self.candidate_box = None
            self.top1 = self.top2 = self.margin = None
            return None
        if not continuous and now-self.last_search < self.cfg.REID_SEARCH_INTERVAL:
            if self.associate(persons, self.candidate_box, self.cfg.REID_CONFIRM_IOU) is None:
                self.confirm = 0
                self.candidate_box = None
            latency.profiler.gate('reid_acquire', 'SEARCH_INTERVAL')
            return None
        latency.profiler.gate('reid_acquire', None)
        self.last_search = now
        scores = self._scores(frame, persons)
        if len(scores) != len(persons) or not all(math.isfinite(s) for s in scores):
            raise ValueError('Invalid candidate scores')
        for person, score in zip(persons, scores):
            person['reid_score'] = score
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        candidate = persons[order[0]]
        self.top1 = scores[order[0]]
        self.top2 = scores[order[1]] if len(order) > 1 else None
        self.margin = self.top1-self.top2 if self.top2 is not None else None
        threshold = getattr(self.cfg, 'REID_REACQUIRE_THRESHOLD', self.cfg.REID_SIM_THRESHOLD) if self.reacquiring else getattr(self.cfg, 'REID_ACQUIRE_THRESHOLD', self.cfg.REID_SIM_THRESHOLD)
        accepted = (self.top1 >= threshold
                    and (self.margin is None or self.margin >= self.cfg.REID_MARGIN_THRESHOLD)
                    and self.acquisition_allowed(candidate, persons))
        if accepted:
            previous = self.associate(persons, self.candidate_box, self.cfg.REID_CONFIRM_IOU) if not continuous else None
            self.confirm = self.confirm+1 if continuous or previous is candidate else 1
            self.candidate_box = candidate['bbox']
            candidate['reid_candidate'] = True
            if self.confirm == 1:
                self.acquisition_id = 'acq_'+uuid4().hex
                self.first_observed_ns = latency.context.get().get('received_ns', time.perf_counter_ns())
                latency.profiler.event('TARGET_CANDIDATE_FOUND', acquisition_id=self.acquisition_id, frame_id=self.frame_id)
                latency.profiler.event('TARGET_FIRST_OBSERVED', acquisition_id=self.acquisition_id,
                                       frame_id=self.frame_id, observed_ns=self.first_observed_ns)
                emit('TARGET_CANDIDATE', frame_id=self.frame_id, bbox=candidate['bbox'], similarity=self.top1)
        else:
            self.confirm = 0
            self.candidate_box = None
        if now-self.last_log >= self.cfg.REID_LOG_INTERVAL:
            print(f'[REID] top1={self.top1:.3f} top2={self.top2} margin={self.margin} confirm={self.confirm}/{self.cfg.REID_CONFIRM_FRAMES}')
            if getattr(self.cfg, 'REID_DIAGNOSTICS', False):
                for p in persons:
                    q = p.get('quality') or {}
                    emit('REID_SCORE', frame_id=self.frame_id, track_id=self.track_id or None, bbox=p['bbox'],
                         raw_similarity=p.get('raw_similarity'), normalized_similarity=p.get('normalized_similarity'),
                         final_similarity=p['reid_score'], best_similarity=self.top1, second_best_similarity=self.top2,
                         threshold=threshold, person_width_px=p['bbox'][2]-p['bbox'][0],
                         person_height_px=p['bbox'][3]-p['bbox'][1],
                         local_contrast=q.get('local_contrast'), roi_laplacian=q.get('laplacian_variance'),
                         person_luminance=q.get('person_mean_luma'),
                         background_luminance=q.get('background_mean_luma'), backlight_state=self.backlight_state,
                         frame_age_ms=self.frame_age_ms)
            self.last_log = now
        if self.confirm >= self.cfg.REID_CONFIRM_FRAMES:
            self.track_id = self.track_id+1 if continuous else candidate.get("track_id", self.track_id+1)
            if continuous:
                candidate["track_id"] = self.track_id
            self.track_generation += 1
            self.lock_session_id = 'lock_'+uuid4().hex
            self.locked_ns = time.perf_counter_ns()
            fields = dict(acquisition_id=self.acquisition_id, lock_session_id=self.lock_session_id,
                          target_track_id=self.track_id, frame_id=self.frame_id,
                          track_generation=self.track_generation,
                          best_similarity=self.top1, confirmation_count=self.confirm)
            latency.profiler.event('REID_CONFIRMED', **fields)
            latency.profiler.elapsed('TARGET_LOCKED', self.first_observed_ns, 'target_lock_latency_ms', **fields)
            latency.profiler.count('target_lock_success_count')
            if self.reacquiring:
                latency.profiler.elapsed('TARGET_REACQUIRED', self.recovery_started_ns, 'tracking_recovery_ms', **fields)
                latency.profiler.count('tracking_recovery_count')
            emit('TARGET_REACQUIRED' if self.reacquiring else 'TARGET_CONFIRMED', track_id=self.track_id)
            self.reacquiring = False
            self.last_seen = now
            self.locked = self.just_locked = True
            self.person_box = candidate['bbox']
            self.set_stage('TARGET_LOCKED')
            print('[TARGET] LOCKED')
            return candidate
        return None

    def select_face(self, faces, person):
        if not self.locked or self.crossing_hold:
            return None
        eligible = [f for f in faces if inside(f, person)] if person is not None else faces
        if self.face_box is not None:
            return self.associate(eligible, self.face_box, self.cfg.REID_FACE_IOU)
        # Never acquire an arbitrary full-frame face without a current locked body.
        return eligible[0] if person is not None and len(eligible) == 1 else None

    def finish_frame(self, person, face, face_observed=True):
        if not self.locked:
            return False
        if face is not None:
            if self.face_box is None:
                print('[FACE] DETECTED (locked target)')
            self.face_box = face['bbox']
            self.face_missing = 0
        elif face_observed:
            self.face_missing += 1
            if self.face_missing >= self.cfg.TARGET_LOST_FRAMES:
                self.face_box = None
        self.missing = self.missing+1 if person is None and face is None else 0
        now = self.observation_time if self.observation_time is not None else time.monotonic()
        if person is not None or face is not None:
            self.last_seen, self.lost_since = now, None
        elif self.lost_since is None:
            self.lost_since = now
        grace = getattr(self.cfg, 'REID_LOST_GRACE_SEC', 0.)
        if self.missing >= self.cfg.TARGET_LOST_FRAMES and now-self.lost_since >= grace:
            print('[TARGET] LOST -> SEARCHING')
            emit('TARGET_LOST', track_id=self.track_id)
            self.mark_lost()
            self.reset(preserve_reacquire=True)
            return True
        return False

    def mark_lost(self):
        """Preserve same-reference recovery correlation across all loss paths."""
        if not self.locked:
            return
        latency.profiler.event('TARGET_LOST', acquisition_id=self.acquisition_id,
                               lock_session_id=self.lock_session_id, target_track_id=self.track_id)
        latency.profiler.count('tracking_lost_count')
        self.recovery_started_ns = time.perf_counter_ns()
        self.reacquiring = True

    def snapshot(self):
        return dict(state=self.stage, locked=self.locked, valid=self.valid, error=self.error,
                    top1=self.top1, top2=self.top2, margin=self.margin, confirm=self.confirm,
                    required=self.cfg.REID_CONFIRM_FRAMES, missing=self.missing,
                    track_id=self.track_id, reacquiring=self.reacquiring, crossing_hold=self.crossing_hold,
                    last_seen=self.last_seen, lost_since=self.lost_since)
