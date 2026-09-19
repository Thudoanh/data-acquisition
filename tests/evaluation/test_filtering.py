import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from evaluation_data.models import Config, INVENTORY, VALIDATION, REVIEW, read_csv, write_csv
from evaluation_data.filtering import run, selected_sources
from evaluation_data.segmentation import run as generate


class DuplicateSourceTests(unittest.TestCase):
    def test_youtube_reencode_selects_original_and_generates_once(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg_path = root/'configs/evaluation/evaluation_dataset.yaml'
            cfg_path.parent.mkdir(parents=True)
            cfg_path.write_text('clip: {min_duration_sec: 30, max_duration_sec: 180, target_duration_sec: 120, overlap_sec: 10}\nvalidation: {min_width: 320, min_height: 240}\n')
            cfg = Config(cfg_path)
            rows = [dict(video_id='quick', source='youtube', source_video_id='YppQ6BEHAKY', filename='source_quicktime.mp4', local_path='data/raw/youtube/id/source_quicktime.mp4', duration_sec=240, width=1920, height=1080, sha256='bbb'),
                    dict(video_id='original', source='youtube', source_video_id='YppQ6BEHAKY', filename='source.mp4', local_path='data/raw/youtube/id/source.mp4', duration_sec=240, width=1920, height=1080, sha256='aaa')]
            write_csv(cfg.output('inventory/video_catalog.csv'), INVENTORY, rows)
            write_csv(cfg.output('validation/raw_video_validation.csv'), VALIDATION,
                      [dict(video_id=r['video_id'], status='valid') for r in rows])
            # Mimic a review CSV created before duplicate source detection existed.
            write_csv(cfg.output('review/review_candidates.csv'), REVIEW,
                      [dict(candidate_id=r['video_id'], video_id=r['video_id'], source_video_id=r['source_video_id'],
                            review_status='pending', review_reason='') for r in rows])
            candidates = generate(cfg)
            review = read_csv(cfg.output('review/review_candidates.csv'))
            self.assertEqual(review[0]['review_reason'], 'duplicate_source_video_id')
            self.assertEqual(review[1]['review_reason'], '')
            self.assertEqual(selected_sources(cfg)['YppQ6BEHAKY']['video_id'], 'original')
            self.assertEqual(len(candidates), 2)
            self.assertEqual(len({r['clip_candidate_id'] for r in candidates}), 2)
