"""Manual-label target tracking metrics, never prediction-as-ground-truth.

Labels identify the person in each cached candidate, not the ByteTrack ID.
Duration is left-interval integration between consecutive annotated PTS samples.
Gaps in labels break episodes. The last sample has zero extrapolated duration.
"""
import math
import statistics


def summary(values):
    values = sorted(float(v) for v in values if v is not None and math.isfinite(v))
    if not values:
        return dict(count=0, mean=None, p95=None)
    return dict(count=len(values), mean=statistics.mean(values),
                p95=values[min(len(values)-1, math.ceil(.95*len(values))-1)])


def evaluate(rows, annotations=None):
    timestamps = [r['timestamp'] for r in rows]
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError('Metrics require strictly ordered timestamps')
    durations = [b-a for a, b in zip(timestamps, timestamps[1:])] + ([0.] if rows else [])
    result = dict(samples=len(rows), duration_s=sum(durations),
                  ambiguous_state_duration_s=sum(dt for r, dt in zip(rows, durations)
                      if r['state'] in ('AMBIGUOUS', 'OCCLUDED_HOLD', 'VISIBLE_UNVERIFIED')),
                  target_correct_tracking_time_s=None, wrong_target_tracking_time_s=None,
                  id_switches=None, fragmentation=None, target_loss=None, recovery_latency_s=None,
                  annotated_duration_s=0., annotation_coverage=0.,
                  detection_latency_ms=summary(r.get('detection_ms') for r in rows),
                  reid_latency_ms=summary(r.get('reid_ms') for r in rows),
                  frame_age_ms=summary(r.get('observation_age_s', 0)*1000 for r in rows))
    ds = sum(r.get('detection_ms', 0) or 0 for r in rows)
    result['detection_service_fps'] = 1000*len(rows)/ds if ds else None
    result['detection_sample_fps'] = (len(rows)-1)/sum(durations) if sum(durations) else None
    if annotations is None:
        return result
    if annotations.get('source') != 'manual' or not annotations.get('target_identity'):
        raise ValueError('Require source=manual and explicit target_identity')
    labels = {}
    for label in annotations['frames']:
        if label['frame_id'] in labels:
            raise ValueError('Duplicate annotation frame')
        if label.get('target_visible') not in (True, False) or not isinstance(label.get('candidate_identities'), dict):
            raise ValueError('Incomplete manual annotation')
        labels[label['frame_id']] = label
    if set(labels)-{r['frame_id'] for r in rows}:
        raise ValueError('Annotations reference frames outside this replay')
    correct = wrong = covered = 0.
    switches = fragments = losses = 0
    recoveries = []
    last_identity = None
    prior_correct = False
    ever_correct = False
    loss_at = None
    for index, (r, dt) in enumerate(zip(rows, durations)):
        label = labels.get(r['frame_id'])
        if label is None:
            last_identity = None
            prior_correct = ever_correct = False
            loss_at = None
            continue
        # No duration crosses an unlabeled interval boundary.
        dt = dt if index+1 < len(rows) and rows[index+1]['frame_id'] in labels else 0.
        covered += dt
        selected = r['selected_candidate']
        if selected is not None and not isinstance(label['candidate_identities'].get(selected), str):
            raise ValueError('Selected candidate lacks a manual identity label')
        identity = label['candidate_identities'].get(selected) if selected is not None else None
        is_correct = identity == annotations['target_identity']
        if identity is not None:
            if last_identity is not None and identity != last_identity:
                switches += 1
            last_identity = identity
        correct += dt if is_correct else 0.
        wrong += dt if identity is not None and not is_correct else 0.
        if is_correct:
            if ever_correct and not prior_correct:
                fragments += 1
            if loss_at is not None:
                recoveries.append(r['timestamp']-loss_at)
                loss_at = None
            ever_correct = True
        elif prior_correct and label['target_visible']:
            losses += 1
            loss_at = r['timestamp']
        if not label['target_visible']:
            loss_at = None  # out-of-view time is not visible-target recovery latency
        prior_correct = is_correct
    result.update(target_correct_tracking_time_s=correct, wrong_target_tracking_time_s=wrong,
                  id_switches=switches, fragmentation=fragments, target_loss=losses,
                  recovery_latency_s=recoveries, annotated_duration_s=covered,
                  annotation_coverage=covered/sum(durations) if sum(durations) else 0.)
    result['unrecovered_visible_loss_at_end_s'] = timestamps[-1]-loss_at if loss_at is not None else None
    return result
