"""Session-persistent framing authority. Tracking a body is not permission to reframe it."""
import math


class ZoomPolicy:
    def __init__(self, context=False):
        self.context = context
        self.session = None
        self.face_mode = False
        self.state = 'PERSON_APPROACH'
        self.last_face = None
        self.last_face_edge = False
        self.clipped_since = None
        self.clipped_frames = 0
        self.head_steps = 0
        self.last_head_step = float('-inf')
        self.large_since = None
        self.large_frames = 0

    def decide(self, session, person, face, control, shape, now, face_observed=True, face_received=None):
        if session != self.session:
            self.__init__(context=self.context)
            self.session = session
        previous = self.state
        h,w = shape[:2]
        pr = (person['bbox'][3]-person['bbox'][1])/h if person else None
        if face is not None:
            box = face.get('bbox')
            usable = (box is not None and len(box) == 4 and all(math.isfinite(v) for v in box)
                and box[2] > box[0] and box[3] > box[1]
                and box[2] > 0 and box[0] < w and box[3] > 0 and box[1] < h
                and (face_received is None or 0 <= now-face_received <= .8))
            if not usable:
                face = None
        fr = (face['bbox'][3]-face['bbox'][1])/h if face else None
        if not self.context and control.target_type == 'FACE' and face is not None:
            self.face_mode = True
        reason, action = 'PERSON_APPROACH', 'NORMAL'
        if self.face_mode:
            if face is not None:
                self.last_face = now if face_received is None else face_received
                self.last_face_edge = face['bbox'][1] <= .03*h
                self.clipped_since = None
                self.clipped_frames = 0
                self.state = 'FACE_TRACK'
                if fr > .28:
                    self.large_since = now if self.large_since is None else self.large_since
                    self.large_frames += 1
                    if self.large_frames >= 3 and now-self.large_since >= .3:
                        reason = 'ZOOM_OUT_FACE_TOO_LARGE'
                    else:
                        action,reason = 'HOLD','ZOOM_HOLD_FACE_SIZE_CONFIRMATION'
                else:
                    self.large_since = None
                    self.large_frames = 0
                    reason = 'ZOOM_IN_FACE' if fr < .22 else 'ZOOM_HOLD_FACE_IN_BAND'
                    if .22 <= fr <= .28:
                        action = 'HOLD'
                        self.state = 'ZOOM_HOLD'
            elif person is not None:
                self.state = 'FACE_TEMPORARILY_LOST'
                action,reason = 'HOLD','ZOOM_HOLD_FACE_TEMPORARILY_LOST'
                # A clipped body alone is insufficient: require the last associated
                # face at the top edge and persistent head clipping in fresh frames.
                if not face_observed and self.last_face is not None and now-self.last_face < .8:
                    action = 'WAIT_FACE_SAMPLE'
                    self.state = previous
                clipped = self.last_face_edge and person['bbox'][1] <= .01*h
                if clipped:
                    self.clipped_since = now if self.clipped_since is None else self.clipped_since
                    self.clipped_frames += 1
                    if (self.clipped_frames >= 3 and now-self.clipped_since >= 1.
                            and self.head_steps < 2 and now-self.last_head_step >= .8):
                        action,reason = 'HEAD_RECOVERY','ZOOM_OUT_HEAD_CLIPPED'
                        self.head_steps += 1
                        self.last_head_step = now
                else:
                    self.clipped_since = None
                    self.clipped_frames = 0
                if face_observed:
                    self.large_since = None
                    self.large_frames = 0
            else:
                self.state = 'TARGET_RECOVERY'
                action,reason = 'RECOVERY','ZOOM_OUT_TARGET_RECOVERY'
        elif person is None:
            self.state = 'TARGET_RECOVERY'
            action,reason = 'RECOVERY','ZOOM_OUT_TARGET_RECOVERY'
        else:
            self.state = 'PERSON_APPROACH'
            low, high = (.36, .44) if self.context else (.46, .54)
            if pr is not None and low <= pr <= high:
                self.state = 'ZOOM_HOLD'
                reason = 'ZOOM_HOLD_PERSON_IN_BAND'
            elif pr is not None and pr > high:
                reason = 'ZOOM_OUT_PERSON_APPROACH'
        if self.context and person is not None and pr < .36:
            x1,y1,x2,y2 = person['bbox']
            if x1 < .1*w or x2 > .9*w or y1 < .1*h or y2 > .9*h:
                action,reason = 'HOLD','CONTEXT_EDGE_HOLD'
        return dict(action=action, zoom_state=self.state, previous_zoom_state=previous,
                    face_visible=face is not None, face_detected=face is not None, face_latched=self.face_mode,
                    zoom_owner='FACE' if self.face_mode else 'PERSON' if person is not None else 'TARGET_RECOVERY',
                    person_track_valid=person is not None,
                    person_ratio=pr,face_ratio=fr,
                    face_lost_duration_ms=(now-self.last_face)*1000 if face is None and self.last_face is not None else None,
                    decision_reason=reason)
