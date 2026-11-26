"""Validation and manual annotation storage for recorded detector-frame datasets."""
import hashlib
import json
import math
from pathlib import Path
import tempfile


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_capture(directory):
    directory = Path(directory)
    meta = json.loads((directory/'manifest.json').read_text())
    summary = json.loads((directory/'summary.json').read_text())
    if meta.get('source') != 'identity_detector_frame_capture' or not summary.get('complete'):
        raise ValueError('Not a finalized identity capture; Stop record first')
    if sha(directory/'frames.jsonl') != summary['frames_sha256']:
        raise ValueError('Recorded metadata checksum mismatch')
    if meta['reference_image'] != 'target_reference.png' or sha(directory/meta['reference_image']) != meta['reference_sha256']:
        raise ValueError('Reference checksum mismatch')
    rows = [json.loads(line) for line in (directory/'frames.jsonl').read_text().splitlines()]
    if not rows or len(rows) != summary['frames']:
        raise ValueError('Empty/incomplete capture')
    previous_id, previous_time = -1, float('-inf')
    for row in rows:
        if (not math.isfinite(row['timestamp']) or row['timestamp'] <= previous_time
                or row['frame_id'] <= previous_id):
            raise ValueError('Nonmonotonic capture')
        image = row['image']
        if Path(image).name != image or not (directory/image).is_file():
            raise ValueError('Invalid frame image path')
        ids = [p['candidate_id'] for p in row['candidates']]
        if len(set(ids)) != len(ids):
            raise ValueError('Duplicate candidate ID')
        previous_id, previous_time = row['frame_id'], row['timestamp']
    return meta, summary, rows


def verified_image(directory, row):
    path = Path(directory)/row['image']
    if sha(path) != row['image_sha256']:
        raise ValueError('Frame image checksum mismatch')
    return path


class AnnotationStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.meta, self.summary, self.rows = load_capture(directory)
        self.path = self.directory/'annotations.json'
        self.labels = {}
        self.loaded_sha = None
        if self.path.exists():
            data = json.loads(self.path.read_text())
            if (data.get('source') != 'manual' or data.get('target_identity') != 'target'
                    or data.get('capture_sha256') != self.summary['frames_sha256']):
                raise ValueError('Existing annotation file belongs to another source/target')
            self.labels = {r['frame_id']:r for r in data['frames']}
            self.loaded_sha = sha(self.path)

    def confirm(self, index, identities, visible, occluded=False):
        row = self.rows[index]
        expected = {c['candidate_id'] for c in row['candidates']}
        if set(identities) != expected or any(not isinstance(v,str) or not v.strip() for v in identities.values()):
            raise ValueError('Gán identity cho từng candidate trước khi xác nhận')
        if type(visible) is not bool:
            raise ValueError('Chọn target có/không xuất hiện')
        if not visible and 'target' in identities.values():
            raise ValueError('Target không xuất hiện nhưng một box đang gán target')
        self.labels[row['frame_id']] = dict(frame_id=row['frame_id'],timestamp=row['timestamp'],
            target_visible=visible,target_occluded=bool(occluded),candidate_identities=dict(identities))
        self.save()

    def save(self):
        current = sha(self.path) if self.path.exists() else None
        if current != self.loaded_sha:
            raise ValueError('Annotation file changed externally; reopen before saving')
        payload = dict(source='manual', annotation_tool='identity_dataset_v1', target_identity='target',
            capture_sha256=self.summary['frames_sha256'],
            frames=[self.labels[k] for k in sorted(self.labels)])
        with tempfile.NamedTemporaryFile(mode='w',dir=self.directory,delete=False,suffix='.json') as f:
            json.dump(payload,f,indent=2,ensure_ascii=False,allow_nan=False)
            temp = Path(f.name)
        temp.replace(self.path)
        self.loaded_sha = sha(self.path)
