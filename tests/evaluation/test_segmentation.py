import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from evaluation_data.models import Config, INVENTORY, REVIEW, VALIDATION, CANDIDATES, read_csv, write_csv
from evaluation_data.segmentation import intervals, run


class IntervalTests(unittest.TestCase):
    def test_limits_coverage_overlap_and_tail(self):
        for duration in (29, 30, 179, 180, 181, 235, 240, 250, 500):
            parts = intervals(duration, 30, 180, 120, 10)
            if duration < 30:
                self.assertEqual(parts, [])
                continue
            self.assertEqual(parts[0][0], 0)
            self.assertAlmostEqual(parts[-1][1], duration)
            for start, end in parts:
                self.assertTrue(30 <= end-start <= 180, (duration, parts))
            for left, right in zip(parts, parts[1:]):
                self.assertLessEqual(right[0], left[1])
                self.assertGreater(right[0], left[0])
        self.assertEqual(intervals(235, 30, 180, 120, 10), [(0.0, 120.0), (110.0, 235)])

    def test_invalid_policy(self):
        with self.assertRaises(ValueError):
            intervals(300, 30, 180, 200, 10)
        with self.assertRaises(ValueError):
            intervals(300, 30, 180, 120, 120)


class GenerationTests(unittest.TestCase):
    def test_skips_flagged_and_preserves_review_decisions(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root/'configs/evaluation/evaluation_dataset.yaml'
            path.parent.mkdir(parents=True)
            path.write_text('clip: {min_duration_sec: 30, max_duration_sec: 180, target_duration_sec: 120, overlap_sec: 10}\nvalidation: {min_width: 320, min_height: 240}\n')
            cfg = Config(path)
            videos = [dict(video_id='v1', source_video_id='s1', duration_sec=240),
                      dict(video_id='v2', source_video_id='s2', duration_sec=240)]
            write_csv(cfg.output('inventory/video_catalog.csv'), INVENTORY, videos)
            write_csv(cfg.output('validation/raw_video_validation.csv'), VALIDATION,
                      [dict(video_id='v1', status='valid'), dict(video_id='v2', status='invalid')])
            write_csv(cfg.output('review/review_candidates.csv'), REVIEW,
                      [dict(video_id='v1', source_video_id='s1', review_reason='', review_status='pending'),
                       dict(video_id='v2', source_video_id='s2', review_reason='invalid_video', review_status='pending')])
            first = run(cfg)
            self.assertEqual(len(first), 2)
            self.assertTrue(all(row['source_video_id'] == 's1' for row in first))
            first[1]['status'] = 'keep'
            first[1]['note'] = 'useful'
            write_csv(cfg.output('review/clip_candidates.csv'), CANDIDATES, first)
            second = run(cfg)
            self.assertEqual(second[1]['status'], 'keep')
            self.assertEqual(second[1]['note'], 'useful')
            self.assertEqual([row['clip_candidate_id'] for row in first],
                             [row['clip_candidate_id'] for row in second])
