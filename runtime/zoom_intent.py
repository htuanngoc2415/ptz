"""Immutable spatial evidence for one motor START; never refreshed in place."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ZoomIntent:
    command_id: str
    frame_id: int | None
    frame_timestamp: float
    bbox: tuple | None
    ratio: float | None
    session_id: str | None
    track_id: int | None
    lens_timestamp: float | None
    generation: int
    expiry: float
    target_kind: str
    target_zoom: float | None
    command_position: float | None
    planned_at: float
