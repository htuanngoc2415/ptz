"""Map inference coordinates to display pixels without mutating AI results."""


def scale_detection(detection, source_shape, display_shape):
    if detection is None:
        return None
    sy = display_shape[0] / source_shape[0]
    sx = display_shape[1] / source_shape[1]
    result = dict(detection)
    x1, y1, x2, y2 = detection['bbox']
    result['bbox'] = (round(x1*sx), round(y1*sy), round(x2*sx), round(y2*sy))
    if detection.get('landmarks') is not None:
        result['landmarks'] = [(x*sx, y*sy) for x, y in detection['landmarks']]
    return result


def display_result(result, display_shape, now, max_age, *, display_received=None):
    """Hide expired overlays; streams have independent receive clocks/sequence IDs."""
    result = dict(result)
    shape = result.get('frame_shape')
    received = result.get('frame_received')
    valid = shape is not None and received is not None and 0 <= now-received <= max_age
    # A healthy sub stream must not keep moving boxes on a frozen main image.
    # Receive freshness does not establish sensor-time synchronization.
    if display_received is not None:
        valid = valid and 0 <= now-display_received <= max_age
    for key in ('persons', 'faces'):
        result[key] = [scale_detection(d, shape, display_shape)
                       for d in result.get(key, [])] if valid else []
    for key in ('person_target', 'face_target'):
        result[key] = scale_detection(result.get(key), shape, display_shape) if valid else None
    if not valid:
        for key in ('recognition', 'display_recognition', 'face_panel'):
            result[key] = None
    return result
