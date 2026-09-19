import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from evaluation_data.events import validate_event,LABEL
class EventTests(unittest.TestCase):
    def test_positive_and_open_ended(self):
        event=dict(label=LABEL,object_class='motorcycle',start_time_sec=0,violation_time_sec=3,end_time_sec='',bbox_keyframes='[]')
        self.assertEqual(validate_event(event,10,['motorcycle']),[])
    def test_bad_timing(self):
        event=dict(label=LABEL,object_class='motorcycle',start_time_sec=4,violation_time_sec=3,end_time_sec=5,bbox_keyframes='[]')
        self.assertIn('invalid_timing',validate_event(event,10,['motorcycle']))

class NegativeClipTests(unittest.TestCase):
    def test_negative_clip_without_event_passes(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from evaluation_data.models import Config,write_csv,write_json,INVENTORY,CLIPS,SCENARIOS,SPLITS,CAMERAS
        from evaluation_data.full_validation import run
        with TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'configs/evaluation/evaluation_dataset.yaml';path.parent.mkdir(parents=True)
            path.write_text('''dataset: {name: X, version: v1}
validation: {min_fps: 5, min_width: 320, min_height: 240}
scenario_labels: {positive: [occupation], negative: [pass_through], hard: [nighttime]}
violation: {target_classes: [motorcycle]}
split: {grouping_key: source_video_id}
''')
            cfg=Config(path)
            write_csv(cfg.output('inventory/video_catalog.csv'),INVENTORY,[dict(video_id='v',source_video_id='s',local_path='data/raw/local/s.mp4',sha256='abc')])
            write_csv(cfg.output('clips/clips_manifest.csv'),CLIPS,[dict(clip_id='c',source_video_id='s',local_path='data/clips/c.mp4',sha256='def',duration_sec=40,camera_id='cam',view_id='v1',session_id='session')])
            write_csv(cfg.output('zones/camera_registry.csv'),CAMERAS,[dict(camera_id='cam',view_id='v1',width=640,height=480)])
            write_json(cfg.output('zones/cam_v1.json'),dict(resolution=[640,480],zones=[dict(zone_id='SW01',zone_type='SIDEWALK',polygon=[[0,0],[100,0],[100,100]])]))
            write_csv(cfg.output('annotations/scenarios.csv'),SCENARIOS,[dict(clip_id='c',scenario='pass_through',scenario_group='negative',review_status='approved')])
            write_csv(cfg.output('annotations/split_manifest.csv'),SPLITS,[dict(clip_id='c',source_video_id='s',camera_id='cam',session_id='session',split='test')])
            with patch('evaluation_data.full_validation.validate_file',return_value=('valid','',dict(width=640,height=480))):
                self.assertEqual(run(cfg)['status'],'PASS')
