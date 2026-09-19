from pathlib import Path
import csv
import json
import os
import tempfile
import yaml

INVENTORY = 'video_id source source_video_id source_url channel_id camera_id session_id filename local_path duration_sec fps width height codec file_size_bytes sha256 recorded_at inventory_status'.split()
VALIDATION = 'video_id status reason'.split()
REVIEW = 'candidate_id video_id source_video_id local_path review_status review_reason review_note'.split()
CANDIDATES = 'clip_candidate_id source_video_id start_sec end_sec duration_sec scenario_hint status note'.split()
CLIPS = 'clip_id source_video_id source_start_sec source_end_sec duration_sec local_path sha256 trim_status camera_id view_id session_id'.split()
SCENARIOS = 'clip_id scenario scenario_group reviewer review_status note'.split()
CAMERAS = 'camera_id view_id source source_camera_name session_id width height notes'.split()
EVENTS = 'event_id clip_id camera_id zone_id zone_type object_class start_time_sec violation_time_sec end_time_sec label track_hint bbox_keyframes reviewer review_status notes'.split()
SPLITS = 'clip_id source_video_id camera_id session_id split'.split()

class Config:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.root = self.path.parents[2]
        self.data = yaml.safe_load(self.path.read_text(encoding='utf-8'))
        if not isinstance(self.data, dict):
            raise ValueError('Configuration must be a mapping')
    def at(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f'Path escapes project root: {relative}')
        return path
    def output(self, relative):
        return self.at(str(self.data.get('paths', {}).get('work_root', 'data')) + '/' + relative)
    @property
    def dataset_id(self):
        d = self.data['dataset']
        return f"{d['name']}-{d['version']}"

def read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))

def write_csv(path, fields, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', newline='', encoding='utf-8', dir=path.parent, delete=False) as f:
        w = csv.DictWriter(f, fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
        temp = f.name
    os.replace(temp, path)

def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        temp = f.name
    os.replace(temp, path)
