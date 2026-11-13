"""Pure pan/tilt planning. Positions and errors use image coordinates, not degrees."""
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class PanPlan:
    action: str
    reason: str
    direction: str = None
    speed: int = 0
    duration: float = 0.
    axis: str = None


def plan_pan(box, shape, cfg, *, previous_axis=None, state='TARGET_LOCKED',
             verified=True, age=0., enabled=True, manual=False, busy=False,
             settling=False, active=False):
    def gate(reason):
        return PanPlan('STOP' if active else 'HOLD', reason)
    if not enabled: return gate('AUTO_PAN_DISABLED')
    if manual: return gate('MANUAL_OVERRIDE')
    if state != 'TARGET_LOCKED' or not verified: return gate('TARGET_UNCERTAIN')
    if not math.isfinite(age) or not 0 <= age <= cfg.MAX_CONTROL_OBSERVATION_AGE:
        return gate('STALE_OBSERVATION')
    h,w = shape[:2]
    if (box is None or len(box) != 4 or h <= 0 or w <= 0
            or not all(math.isfinite(v) for v in box)):
        return gate('NO_VALID_TARGET')
    x1,y1,x2,y2 = box
    if x2 <= x1 or y2 <= y1 or x2 <= 0 or y2 <= 0 or x1 >= w or y1 >= h:
        return gate('NO_VALID_TARGET')
    errors = {'pan': (x1+x2)/2/w-.5, 'tilt': (y1+y2)/2/h-.5}
    eligible = {axis:error for axis,error in errors.items() if abs(error) > (
        cfg.AUTO_PAN_TILT_RELEASE if axis == previous_axis else cfg.AUTO_PAN_TILT_DEADZONE)}
    if not eligible: return gate('DEADZONE')
    if busy: return gate('MOTOR_BUSY')
    if settling: return gate('COOLDOWN')
    axis = max(eligible, key=lambda a: abs(eligible[a]))
    error = eligible[axis]
    sign = cfg.AUTO_PAN_SIGN if axis == 'pan' else cfg.AUTO_TILT_SIGN
    direction = ('pan_right' if error*sign > 0 else 'pan_left') if axis == 'pan' else (
        'tilt_down' if error*sign > 0 else 'tilt_up')
    speed = min(cfg.AUTO_PAN_TILT_MAX_SPEED, max(1,1+int(abs(error)*6)))
    duration = cfg.AUTO_PAN_TILT_PULSE
    if getattr(cfg,'AUTO_PAN_TILT_ADAPTIVE',False):
        fraction = min(1.,max(0.,(max(map(abs,errors.values()))-cfg.AUTO_PAN_TILT_DEADZONE)/
            max(.001,cfg.AUTO_PAN_TILT_FAR_ERROR-cfg.AUTO_PAN_TILT_DEADZONE)))
        duration += fraction*(cfg.AUTO_PAN_TILT_MAX_PULSE-duration)
    return PanPlan('MOVE','TARGET_LOCKED',direction,speed,duration,axis)
