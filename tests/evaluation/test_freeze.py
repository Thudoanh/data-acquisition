import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from evaluation_data.models import Config,write_csv,write_json,INVENTORY,CLIPS,SCENARIOS,SPLITS,CAMERAS
from evaluation_data.freeze import freeze
from evaluation_data.checksum import sha256_file
class FreezeTests(unittest.TestCase):
    def make_config(self,root):
        (root/'configs/evaluation').mkdir(parents=True)
        config_path=root/'configs/evaluation/evaluation_dataset.yaml'
        config_path.write_text('dataset: {name: X, version: v1}\nfreeze: {output_root: data/evaluation}\n')
        return Config(config_path)
    def test_blocks_error_and_existing_version(self):
        with TemporaryDirectory() as tmp:
            cfg=self.make_config(Path(tmp))
            with patch('evaluation_data.freeze.validate',return_value={'status':'ERROR'}):
                with self.assertRaises(ValueError): freeze(cfg)
            target=cfg.at('data/evaluation/X-v1');target.mkdir(parents=True)
            with self.assertRaises(FileExistsError): freeze(cfg)
    def test_structure_and_checksums(self):
        with TemporaryDirectory() as tmp:
            cfg=self.make_config(Path(tmp));video=cfg.at('data/clips/c_C001.mp4');video.parent.mkdir(parents=True);video.write_bytes(b'tiny fixture')
            source=cfg.at('data/raw/local/source.mp4');source.parent.mkdir(parents=True);source.write_bytes(b'source')
            write_csv(cfg.output('inventory/video_catalog.csv'),INVENTORY,[dict(video_id='v',source='local',source_video_id='s',local_path='data/raw/local/source.mp4',sha256=sha256_file(source))])
            write_csv(cfg.output('clips/clips_manifest.csv'),CLIPS,[dict(clip_id='c_C001',source_video_id='s',source_start_sec=0,source_end_sec=30,duration_sec=30,local_path='data/clips/c_C001.mp4',sha256=sha256_file(video),trim_status='ready',camera_id='cam',view_id='v1',session_id='session')])
            write_csv(cfg.output('annotations/scenarios.csv'),SCENARIOS,[dict(clip_id='c_C001',scenario='pass_through',scenario_group='negative',reviewer='tester',review_status='approved')])
            write_csv(cfg.output('annotations/split_manifest.csv'),SPLITS,[dict(clip_id='c_C001',source_video_id='s',camera_id='cam',session_id='session',split='test')])
            write_csv(cfg.output('zones/camera_registry.csv'),CAMERAS,[dict(camera_id='cam',view_id='v1',source='local',session_id='session',width=640,height=480)])
            write_json(cfg.output('zones/cam_v1.json'),dict(camera_id='cam',view_id='v1',resolution=[640,480],zones=[dict(zone_id='SW01',zone_type='SIDEWALK',polygon=[[0,0],[100,0],[100,100]])]))
            write_json(cfg.output('validation/evaluation_validation.json'),{'status':'PASS'})
            with patch('evaluation_data.freeze.validate',return_value={'status':'PASS'}): target=freeze(cfg)
            self.assertTrue((target/'videos/test/c_C001.mp4').is_file())
            self.assertTrue((target/'annotations/events.jsonl').is_file())
            from evaluation_data.models import read_csv
            self.assertEqual(read_csv(target/'manifests/clips.csv')[0]['local_path'],'videos/test/c_C001.mp4')
            checks=read_csv(target/'manifests/checksums.csv')
            self.assertTrue(all(sha256_file(target/r['path'])==r['sha256'] for r in checks))
            self.assertEqual((target/'videos/test/c_C001.mp4').read_bytes(),b'tiny fixture')
            with self.assertRaises(FileExistsError): freeze(cfg)
