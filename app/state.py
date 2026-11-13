from dataclasses import dataclass, asdict


@dataclass
class TargetSnapshot:
    target_uid: str | None = None
    tracking_status: str | None = None
    identity_status: str | None = None
    acquisition_id: str | None = None
    lock_session_id: str | None = None
    identity_frame_id: int | None = None
    state: str = 'SEARCHING'
    frame_id: int = -1
    track_id: int | None = None
    bbox: tuple | None = None
    face_bbox: tuple | None = None
    person_confirmed: bool = False
    person_similarity: float | None = None
    raw_similarity: float | None = None
    normalized_similarity: float | None = None
    identity_similarity: float | None = None
    face_detected: bool = False
    identity: str | None = None
    last_seen_ts: float | None = None
    lost_since: float | None = None
    illumination_quality: dict | None = None
    focus_quality: dict | None = None

    def to_dict(self):
        return asdict(self)
