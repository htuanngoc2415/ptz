"""Explicit zoom coordinate conversions and validated monotonic calibration.

ZoomValue/10 is a vendor-reported scale, not guaranteed optical magnification.
PositionABS normalized commands are NOT the normalized encoder in status.Postion.
"""
from bisect import bisect_right
from dataclasses import dataclass, asdict
import json
import math
from pathlib import Path


def finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return float(value)


def interpolate(value, xs, ys):
    value = min(xs[-1], max(xs[0], value))
    index = min(len(xs)-2, max(0, bisect_right(xs, value)-1))
    weight = (value-xs[index])/(xs[index+1]-xs[index])
    return ys[index]+weight*(ys[index+1]-ys[index])


class PositionABSMapping:
    """Historical command measurements in loi/zoom_kem_net.py (2026-08-27).

    This maps a requested ZoomValue-scale endpoint to PositionABS command units.
    It does not claim that the camera exposes actual position in those units.
    """
    source = 'repository_20260827_PositionABS_measurements'
    positions = (.15, .30, .45, .60, .75, .90, 1.)
    zoom_values = (6.4, 11.7, 17.1, 22.5, 27.9, 33.3, 37.)

    def zoom_to_command(self, zoom):
        zoom = finite(zoom, 'ZoomValue scale')
        if not self.zoom_values[0] <= zoom <= self.zoom_values[-1]:
            raise ValueError('OUTSIDE_VERIFIED_POSITIONABS_RANGE')
        return interpolate(zoom, self.zoom_values, self.positions)

    def command_to_zoom(self, position):
        position = finite(position, 'PositionABS command')
        if not self.positions[0] <= position <= self.positions[-1]:
            raise ValueError('OUTSIDE_VERIFIED_POSITIONABS_RANGE')
        return interpolate(position, self.positions, self.zoom_values)

    @staticmethod
    def gui_value(position):
        position = finite(position, 'normalized command')
        if not .15 <= position <= 1.:
            raise ValueError('OUTSIDE_VERIFIED_POSITIONABS_RANGE')
        return round(position*127)+1


class ZoomCalibration:
    def __init__(self, data, camera_id=None):
        if data.get('schema_version') != 1 or data.get('coordinate') != 'zoom_value_x':
            raise ValueError('Calibration requires explicit zoom_value_x coordinate and schema_version=1')
        if not data.get('reference_target_id') or data.get('fixed_distance') is not True:
            raise ValueError('Calibration requires one reference target at a fixed distance')
        if not data.get('camera_id') or (camera_id is not None and data['camera_id'] != camera_id):
            raise ValueError('Calibration camera binding missing or mismatched')
        if not data.get('timestamp'):
            raise ValueError('Calibration provenance timestamp missing')
        samples = data.get('samples', [])
        if len(samples) < 3:
            raise ValueError('At least three measured calibration samples required')
        self.positions, self.magnifications = [], []
        for row in samples:
            if row.get('stable') is not True or row.get('reference_target_id') != data['reference_target_id']:
                raise ValueError('Unstable or mixed-reference calibration samples')
            position = finite(row.get('zoom_value_x'), 'zoom_value_x')
            magnification = finite(row.get('magnification'), 'magnification')
            if not 1 <= position <= 37 or magnification <= 0:
                raise ValueError('Calibration sample out of bounds')
            if self.positions and (position <= self.positions[-1] or magnification <= self.magnifications[-1]):
                raise ValueError('Calibration must be strictly monotonic without duplicate positions')
            self.positions.append(position)
            self.magnifications.append(magnification)
        self.camera_id = data['camera_id']
        self.data = data

    @classmethod
    def load(cls, path, camera_id=None):
        data = json.loads(Path(path).read_text())
        model = cls(data, camera_id)
        validation = model.validate_holdout(data.get('holdout_samples', []))
        threshold = finite(data.get('validation_max_relative_error', .10), 'validation threshold')
        if not 0 < threshold <= .25 or not validation['count'] or validation['max_relative_error'] > threshold:
            raise ValueError('Calibration requires passing independent held-out measurements')
        return model

    def zoom_to_magnification(self, position):
        position = finite(position, 'position')
        if not self.positions[0] <= position <= self.positions[-1]:
            raise ValueError('CURRENT_ZOOM_OUTSIDE_CALIBRATION')
        return interpolate(position, self.positions, self.magnifications)

    def magnification_to_zoom(self, magnification):
        # Explicit saturation, never extrapolation.
        return interpolate(finite(magnification, 'magnification'), self.magnifications, self.positions)

    def estimate_target_zoom(self, current_zoom, current_ratio, desired_ratio):
        current_ratio, desired_ratio = finite(current_ratio, 'ratio'), finite(desired_ratio, 'desired ratio')
        if min(current_ratio, desired_ratio) <= 0:
            raise ValueError('Ratios must be positive')
        required = self.zoom_to_magnification(current_zoom)*desired_ratio/current_ratio
        return self.magnification_to_zoom(required)

    def validate_holdout(self, rows):
        errors = []
        for row in rows:
            if (row.get('reference_target_id') != self.data['reference_target_id'] or
                    row.get('stable') is not True):
                raise ValueError('Invalid holdout reference/stability')
            position = finite(row.get('zoom_value_x'), 'holdout position')
            if position in self.positions:
                raise ValueError('Holdout points must not be training positions')
            actual = finite(row.get('magnification'), 'holdout magnification')
            if actual <= 0:
                raise ValueError('Invalid holdout magnification')
            errors.append(abs(self.zoom_to_magnification(position)/actual-1))
        return dict(count=len(errors), mean_relative_error=sum(errors)/len(errors) if errors else None,
                    max_relative_error=max(errors) if errors else None)


@dataclass(frozen=True)
class ZoomPlan:
    current_zoom_value: float
    target_zoom_value: float
    requested_zoom_position: float | None
    prediction_method: str
    calibration_valid: bool
    prediction_confidence_or_status: str
    desired_ratio: float
    current_ratio: float
    target_kind: str
    saturated: bool
    correction: bool

    def fields(self):
        return asdict(self)


class ZoomEstimator:
    def __init__(self, calibration=None, correction_fraction=.15):
        self.calibration = calibration
        self.mapping = PositionABSMapping()
        self.correction_fraction = finite(correction_fraction, 'correction fraction')
        if not 0 < self.correction_fraction <= .25:
            raise ValueError('Correction fraction must be within (0,.25]')

    def estimate_target_zoom(self, current_zoom, current_ratio, desired_ratio,
                             target_kind='PERSON', correction=False):
        current = finite(current_zoom, 'current zoom')
        ratio, desired = finite(current_ratio, 'current ratio'), finite(desired_ratio, 'desired ratio')
        if not 1 <= current <= 37 or not 0 < ratio <= 1 or not 0 < desired <= 1:
            raise ValueError('Invalid zoom/ratio observation')
        if target_kind not in ('PERSON', 'FACE', 'TARGET'):
            raise ValueError('Unknown target ratio domain')
        if self.calibration is not None:
            magnification = self.calibration.zoom_to_magnification(current)*desired/ratio
            target = self.calibration.magnification_to_zoom(magnification)
            saturated = not self.calibration.magnifications[0] <= magnification <= self.calibration.magnifications[-1]
            method, status, valid = 'CALIBRATION_LUT', 'MEASURED_CAMERA_BOUND', True
        else:
            raw_target = current*desired/ratio
            target = min(37., max(1., raw_target))
            saturated = target != raw_target
            method, status, valid = 'GEOMETRIC_ZOOMVALUE_APPROXIMATION', 'UNCALIBRATED_OPTICAL_SCALE', False
        if correction:
            target = min(current*(1+self.correction_fraction), max(current/(1+self.correction_fraction), target))
        position = self.mapping.zoom_to_command(target) if target >= self.mapping.zoom_values[0] else None
        return ZoomPlan(current, target, position, method, valid, status, desired, ratio,
                        target_kind, saturated, correction)
