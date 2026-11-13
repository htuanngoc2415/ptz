from pathlib import Path
import json
import math

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path(__file__).parent / 'configs/calibration.yaml'

def load(path=DEFAULT):
    import yaml
    cfg = yaml.safe_load(Path(path).read_text())
    validate(cfg)
    return cfg

def validate(c):
    numeric = ['max_pulse_ms', 'pulse_ms', 'command_timeout_s', 'sample_timeout_s',
               'max_frame_age_s', 'stream_watchdog_s', 'recording_fps', 'wait_ms',
               'settle_ms', 'samples_per_step', 'repetitions', 'max_steps',
               'max_return_steps', 'queue_size', 'filter_window', 'speed',
               'min_person_ratio', 'target_ratio', 'max_person_ratio', 'confidence',
               'outlier_fraction', 'max_center_motion', 'stability_fraction',
               'baseline_tolerance', 'target_tolerance']
    for key in numeric + ['zoom_position_min', 'zoom_position_max']:
        value = c.get(key)
        if key.startswith('zoom_position_') and value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key} must be a finite number')
    integer = ['max_pulse_ms', 'pulse_ms', 'samples_per_step', 'repetitions',
               'max_steps', 'max_return_steps', 'queue_size', 'filter_window', 'speed']
    for key in integer:
        if not isinstance(c[key], int):
            raise ValueError(f'{key} must be an integer')
    for key in ['allow_baseline_return', 'auto_return_to_start']:
        if not isinstance(c[key], bool):
            raise ValueError(f'{key} must be a boolean')
    if not isinstance(c['pulse_options'], list) or not c['pulse_options']:
        raise ValueError('pulse_options must be a nonempty list')
    if any(isinstance(p, bool) or not isinstance(p, int) for p in c['pulse_options']):
        raise ValueError('pulse_options must contain integer durations')
    positive = ['max_pulse_ms', 'command_timeout_s', 'sample_timeout_s',
                'max_frame_age_s', 'stream_watchdog_s', 'recording_fps']
    for k in positive:
        if not 0 < float(c[k]) < 10000:
            raise ValueError(f'Invalid {k}')
    if not 1 <= int(c['pulse_ms']) <= int(c['max_pulse_ms']) <= 1000:
        raise ValueError('Pulse must be 1..max_pulse_ms; limit <=1000 ms')
    if any(not 1 <= int(p) <= c['max_pulse_ms'] for p in c['pulse_options']):
        raise ValueError('Invalid pulse_options')
    for k in ['samples_per_step', 'repetitions', 'max_steps', 'max_return_steps', 'queue_size', 'filter_window']:
        if not 1 <= int(c[k]) <= 10000:
            raise ValueError(f'Invalid {k}')
    if not 0 < c['min_person_ratio'] < c['target_ratio'] < c['max_person_ratio'] < 1:
        raise ValueError('Require min < target < max ratio < 1')
    if c['settle_ms'] < 0 or c['wait_ms'] < 0:
        raise ValueError('Wait and settle must be nonnegative')
    if not 1 <= int(c['speed']) <= 8:
        raise ValueError('RPC speed must be 1..8')
    if c['strategy'] not in ('fixed','coarse_to_fine','adaptive'):
        raise ValueError('Unknown controller strategy')
    if not isinstance(c['region'],str) or not c['region'].strip():
        raise ValueError('Region must be a nonempty experimental label')
    if c['focus_policy'] != 'unchanged' or c['auto_return_to_start']:
        raise ValueError('Focus changes and automatic end-of-run return are unsupported')
    lo,hi = c.get('zoom_position_min'),c.get('zoom_position_max')
    if lo is not None and hi is not None and float(lo)>=float(hi):
        raise ValueError('Position minimum must be below maximum')
    for k in ['confidence', 'outlier_fraction', 'max_center_motion', 'stability_fraction', 'baseline_tolerance', 'target_tolerance']:
        if not 0 < float(c[k]) < 1:
            raise ValueError(f'Invalid {k}')

def resolve(path):
    p = Path(path)
    return p if p.is_absolute() else ROOT / p

def camera_settings(cfg):
    c = json.loads(resolve(cfg['camera_config']).read_text())
    return c.get('camera', c)

def public_config(cfg):
    # Connection credentials and RTSP URLs are never copied to session metadata.
    return {k: v for k, v in cfg.items() if k != 'camera_config'}
