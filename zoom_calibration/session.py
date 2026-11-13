"""Single writer for bounded asynchronous CSV logging."""
import csv
import json
import queue
import threading
import time
from pathlib import Path
from uuid import uuid4
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import public_config

SCHEMAS = {
 'frames': 'frame_id timestamp wall_timestamp frame_pts capture_timestamp receive_timestamp inference_start_timestamp inference_end_timestamp measurement_timestamp frame_width frame_height target_id bbox_x1 bbox_y1 bbox_x2 bbox_y2 bbox_height bbox_width bbox_filtered person_ratio_raw person_ratio_filtered confidence tracking_valid bbox_clipped zoom_position zoom_position_source zoom_value zoom_value_source telemetry_timestamp current_command trial_id command_id measurement_valid invalid_reason frame_age_s recording_index recording_pts_s'.split(),
 'commands': 'command_id trial_id command_type requested_duration_ms actual_duration_ms send_timestamp send_wall_timestamp response_timestamp response_wall_timestamp stop_timestamp stop_wall_timestamp stop_response_timestamp stop_response_wall_timestamp status error_message zoom_position_before zoom_position_after motor_start_timestamp motor_stop_timestamp timing_source'.split(),
 'measurements': 'trial_id command_id region pulse_ms zoom_direction ratio_before ratio_after delta_ratio relative_gain log_ratio_gain effective_gain_per_ms estimated_response_latency_ms sample_count measurement_valid invalid_reason zoom_position_before zoom_position_after baseline_group'.split(),
 'trials': 'trial_id mode strategy region target_ratio initial_ratio final_ratio time_to_target_s command_count overshoot settling_time_s tracking_failures motor_active_ms measurement_valid invalid_reason baseline_group'.split(),
 'recording': 'frame_id receive_timestamp recording_index recording_pts_s dropped error'.split(),
 'events': 'timestamp wall_timestamp kind message'.split(),
}

class Session:
    def __init__(self, root, cfg):
        stamp = datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%Y%m%d_%H%M%S')
        self.id = stamp+'_'+uuid4().hex[:8]
        self.path = Path(root)/self.id
        for folder in ['data','video','plots','reports','calibration']:
            (self.path/folder).mkdir(parents=True, exist_ok=True)
        self.meta = dict(session_id=self.id, config=public_config(cfg),
                         created_wall=time.time(), created_monotonic=time.monotonic(), clock='monotonic seconds; wall unix seconds',
                         camera_capture_timestamp='unknown', focus_policy='unchanged',
                         frame_drops=0, log_drops=0, errors=[])
        self.queue = queue.Queue(maxsize=4096)
        self.failed = threading.Event()
        self.closed = False
        self.thread = threading.Thread(target=self._run, name='calibration-log', daemon=True)
        self.thread.start()
        self.save()

    def save(self):
        p = self.path/'data/session.json'
        temp = p.with_suffix('.tmp')
        temp.write_text(json.dumps(self.meta, indent=2, allow_nan=False))
        temp.replace(p)

    def log(self, table, row):
        if self.closed:
            return False
        try:
            self.queue.put_nowait((table, dict(row, session_id=self.id)))
            return True
        except queue.Full:
            self.meta['log_drops'] += 1
            self.failed.set()
            return False

    def event(self, kind, message):
        self.log('events', dict(timestamp=time.monotonic(), wall_timestamp=time.time(), kind=kind, message=message))

    def _run(self):
        files = {}
        try:
            writers = {}
            for table, fields in SCHEMAS.items():
                f = (self.path/f'data/{table}.csv').open('w', newline='')
                files[table] = f
                writer = csv.DictWriter(f, ['session_id']+fields, extrasaction='ignore')
                writer.writeheader()
                writers[table] = writer
            while True:
                item = self.queue.get()
                if item is None:
                    break
                table, row = item
                writers[table].writerow({k: json.dumps(v) if isinstance(v,(list,tuple,dict)) else v for k,v in row.items()})
                files[table].flush()
        except Exception as exc:
            self.failed.set()
            self.meta['errors'].append('CSV writer: '+type(exc).__name__)
        finally:
            for f in files.values():
                f.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        while self.thread.is_alive():
            try:
                self.queue.put(None, timeout=.2)
                break
            except queue.Full:
                continue
        self.thread.join(5)
        if self.thread.is_alive():
            self.meta['errors'].append('CSV shutdown timeout')
            self.failed.set()
        self.meta['ended_wall'] = time.time()
        self.save()
