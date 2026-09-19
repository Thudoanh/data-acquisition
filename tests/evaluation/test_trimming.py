import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from evaluation_data.trimming import clip_name,valid_interval
class TrimmingTests(unittest.TestCase):
    def test_naming_and_times(self):
        self.assertEqual(clip_name('abc',1),'abc_C001.mp4')
        self.assertTrue(valid_interval(5,35,100,30,180))
        self.assertFalse(valid_interval(5,36,30,30,180))

class TrimSelectionTests(unittest.TestCase):
    def test_rejecting_first_candidate_does_not_renumber_second(self):
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from types import SimpleNamespace
        from evaluation_data.models import Config, INVENTORY, CANDIDATES, REVIEW, VALIDATION, write_csv
        from evaluation_data.trimming import run
        with TemporaryDirectory() as tmp:
            root=Path(tmp);cfg_path=root/'configs/evaluation/evaluation_dataset.yaml'
            cfg_path.parent.mkdir(parents=True)
            cfg_path.write_text('clip: {min_duration_sec: 30, max_duration_sec: 180}\n')
            cfg=Config(cfg_path)
            source=cfg.at('data/raw/local/s.mp4');source.parent.mkdir(parents=True);source.write_bytes(b'source')
            write_csv(cfg.output('inventory/video_catalog.csv'),INVENTORY,[dict(video_id='v',source_video_id='s',duration_sec=240,local_path='data/raw/local/s.mp4',camera_id='',session_id='')])
            write_csv(cfg.output('validation/raw_video_validation.csv'),VALIDATION,[dict(video_id='v',status='valid')])
            write_csv(cfg.output('review/review_candidates.csv'),REVIEW,[dict(candidate_id='v',video_id='v',source_video_id='s',review_reason='',review_status='pending')])
            write_csv(cfg.output('review/clip_candidates.csv'),CANDIDATES,[
                dict(clip_candidate_id='a',source_video_id='s',start_sec=0,end_sec=120,status='reject'),
                dict(clip_candidate_id='b',source_video_id='s',start_sec=110,end_sec=240,status='keep')])
            def fake_ffmpeg(args,**kwargs):
                Path(args[-1]).write_bytes(b'clip')
                return SimpleNamespace(returncode=0,stderr='')
            with patch('evaluation_data.trimming.subprocess.run',side_effect=fake_ffmpeg), \
                 patch('evaluation_data.trimming.probe',return_value=({'duration_sec':130},'')):
                rows=run(cfg)
            self.assertEqual(rows[0]['clip_id'],'s_C002')
            self.assertEqual(rows[0]['source_start_sec'],110.0)
