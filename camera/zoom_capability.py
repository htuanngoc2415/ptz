"""No movement probes; method advertisement alone is not a verified coordinate map."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ZoomCapability:
    supported: bool | None
    source: str
    error: object = None

    @classmethod
    def from_response(cls, reply):
        if not isinstance(reply, dict) or reply.get('result') is not True or reply.get('error'):
            return cls(None, 'CAPABILITY_UNAVAILABLE', reply.get('error') if isinstance(reply, dict) else None)
        caps = (reply.get('params') or {}).get('caps')
        if not isinstance(caps, dict):
            return cls(None, 'CAPABILITY_SCHEMA_UNKNOWN')
        absolute = caps.get('MoveAbsolutely')
        if isinstance(absolute, dict) and isinstance(absolute.get('PTZ'), list):
            return cls('Zoom' in absolute['PTZ'], 'ptz.getCurrentProtocolCaps.MoveAbsolutely.PTZ')
        if isinstance(caps.get('Move'), list) and 'Absolutely' not in caps['Move']:
            return cls(False, 'ptz.getCurrentProtocolCaps.Move')
        return cls(None, 'ABSOLUTE_ZOOM_CAPABILITY_UNKNOWN')
