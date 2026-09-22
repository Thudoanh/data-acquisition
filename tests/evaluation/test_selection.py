import copy
import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from evaluation_data.models import Config
from evaluation_data.models import write_csv
from evaluation_data.clip_metadata import SELECTION_CANDIDATE_FIELDS
from evaluation_data.selection import (
    DEFAULTS, SHORTLIST_FIELDS, SelectionError, _cache_entry_matches,
    _is_redundant, analysis_fingerprint, assign_diversity_and_scores,
    finalize_shortlist, preserve_manual_reviews, sample_timestamps, score_signals,
    run_analysis, select_keyframe_indices, select_shortlist,
    validate_candidates,
)


def settings(**overrides):
    result = copy.deepcopy(DEFAULTS)
    result.update(overrides)
    return result


def synthetic_candidates():
    source_counts = [('small-a', 66), ('small-b', 66), ('large', 340)]
    rows = []
    global_index = 0
    for source, count in source_counts:
        for source_index in range(count):
            level = ('LOW', 'MEDIUM', 'HIGH')[global_index % 3]
            event = {'LOW': 0.20, 'MEDIUM': 0.48, 'HIGH': 0.76}[level]
            value = (global_index % 17) / 17
            rows.append({
                'clip_id': f'{source}-{source_index:03d}',
                'source_id': source,
                'source_start_sec': str(source_index * 110),
                'source_end_sec': str(source_index * 110 + 120),
                'event_likelihood_score': str(event),
                'event_likelihood_level': level,
                'hardcase_score': str(0.7 if global_index % 3 == 0 else 0.2),
                'hardcase_tags': 'BLUR' if global_index % 3 == 0 else '',
                'activity_score': str(value),
                'motion_variation_score': str(1 - value),
                'brightness_score': str(0.2 + value * 0.6),
                'visual_variation_score': str(value * 0.8),
                'foreground_occupancy_score': str((global_index % 11) / 11),
                'visual_signature': f'{global_index * 2654435761 & ((1 << 64)-1):016x}',
                'signal_status': 'COMPUTED',
                'prediction_status': 'COMPUTED_SELECTION_SIGNAL_NOT_GROUND_TRUTH',
            })
            global_index += 1
    return rows


class FrameSamplingTests(unittest.TestCase):
    def test_interval_midpoints_are_deterministic_and_avoid_endpoints(self):
        first = sample_timestamps(120, 12)
        self.assertEqual(first, sample_timestamps(120, 12))
        self.assertEqual(first[0], 5)
        self.assertEqual(first[-1], 115)
        self.assertEqual(len(first), 12)

    def test_invalid_sampling_input_fails(self):
        with self.assertRaises(SelectionError):
            sample_timestamps(0, 12)

    def test_smart_keyframes_are_unique_and_limited_to_five(self):
        frames = []
        for index in range(12):
            frame = np.zeros((60, 80, 3), dtype=np.uint8)
            cv2.rectangle(frame, (index * 3, 10), (index * 3 + 12, 30),
                          (30 + index * 15,) * 3, -1)
            frames.append(frame)
        indices, reasons = select_keyframe_indices(frames, 5)
        self.assertEqual(len(indices), 5)
        self.assertEqual(len(indices), len(set(indices)))
        self.assertEqual(len(reasons), 5)
        self.assertTrue(all(0 <= index < len(frames) for index in indices))


class IncrementalCacheTests(unittest.TestCase):
    def test_cache_key_includes_checksum_and_analysis_fingerprint(self):
        current = settings()
        fingerprint = analysis_fingerprint(current)
        row = {'sha256': 'abc'}
        cached = {
            'signal_status': 'COMPUTED', 'analysis_sha256': 'abc',
            'analysis_fingerprint': fingerprint, 'visual_signature': '0' * 16,
            'event_likelihood_score': '0.2', 'hardcase_score': '0.3',
            'activity_score': '0.1', 'keyframe_timestamps': '[5, 15, 25]',
            'keyframe_reasons': '["OVERVIEW", "HARD_CASE", "SCENE_DIVERSITY"]',
        }
        self.assertTrue(_cache_entry_matches(row, cached, fingerprint))
        self.assertFalse(_cache_entry_matches(
            {'sha256': 'changed'}, cached, fingerprint))
        changed = settings(sample_frames=10)
        self.assertNotEqual(fingerprint, analysis_fingerprint(changed))
        self.assertFalse(_cache_entry_matches(
            row, cached, analysis_fingerprint(changed)))

    def test_second_analysis_run_reuses_cache_after_metadata_reset(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / 'configs/evaluation/evaluation_dataset.yaml'
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                'dataset: {name: test, version: v1}\n'
                'selection:\n'
                '  expected_candidates: 1\n'
                '  expected_sources: 1\n',
                encoding='utf-8')
            config = Config(config_path)
            clip = root / 'clip.mp4'
            clip.write_bytes(b'clip')
            digest = hashlib.sha256(b'clip').hexdigest()
            row = {
                'clip_id': 'clip-1', 'clip_path': 'clip.mp4',
                'source_id': 'source', 'source_video_id': 'source',
                'camera_id': 'UNRESOLVED', 'source_start_sec': '0',
                'source_end_sec': '120', 'duration_sec': '120', 'fps': '30',
                'width': '1920', 'height': '1080', 'codec': 'h264',
                'sha256': digest, 'qc_status': 'PASS',
                'metadata_status': 'WARNING', 'signal_status': 'NOT_COMPUTED',
            }
            canonical = root / 'data/processed/metadata/clips_metadata.csv'
            candidate = root / 'data/processed/metadata/clip_selection_candidates.csv'
            identity_fields = [
                'clip_id', 'clip_path', 'source_id', 'source_video_id',
                'camera_id', 'source_start_sec', 'source_end_sec',
                'duration_sec', 'fps', 'width', 'height', 'codec', 'sha256',
                'qc_status', 'metadata_status',
            ]
            write_csv(canonical, identity_fields, [row])
            write_csv(candidate, SELECTION_CANDIDATE_FIELDS, [row])

            def fake_analysis(_config, source, _settings, fingerprint):
                result = dict(source)
                result.update({
                    'analysis_sha256': source['sha256'],
                    'analysis_fingerprint': fingerprint,
                    'visual_signature': '0' * 16,
                    'keyframe_timestamps': '[5, 55, 115]',
                    'keyframe_reasons': '["OVERVIEW", "HARD_CASE", "SCENE_DIVERSITY"]',
                    'sampled_frame_count': '12',
                    'event_likelihood_score': '0.2',
                    'event_likelihood_level': 'LOW',
                    'hardcase_score': '0.3', 'activity_score': '0.1',
                    'signal_status': 'COMPUTED',
                    'prediction_status': 'COMPUTED_SELECTION_SIGNAL_NOT_GROUND_TRUTH',
                })
                return result

            with patch('evaluation_data.selection._analyze_one',
                       side_effect=fake_analysis) as analyzer:
                _rows, first = run_analysis(config, verify_checksums=False)
                self.assertEqual(analyzer.call_count, 1)
                self.assertEqual(first['reused_feature_count'], 0)

            # Simulate build_clip_metadata.py recreating a blank candidate table.
            write_csv(candidate, SELECTION_CANDIDATE_FIELDS, [row])
            with patch('evaluation_data.selection._analyze_one') as analyzer:
                _rows, second = run_analysis(config, verify_checksums=False)
                analyzer.assert_not_called()
                self.assertEqual(second['reused_feature_count'], 1)
                self.assertEqual(second['globally_reranked_candidates'], 1)


class ScoringTests(unittest.TestCase):
    def test_score_is_reproducible_and_selection_only(self):
        signal = {
            'foreground_occupancy_score': 0.4, 'activity_score': 0.3,
            'persistence_score': 0.8, 'visual_variation_score': 0.2,
            'small_far_score': 0.1, 'occlusion_score': 0.2,
            'boundary_edge_score': 0.3, 'blur_score': 0.4,
            'crowded_score': 0.5, 'fast_motion_score': 0.6,
            'low_light_score': 0.7,
        }
        first = score_signals(signal, settings())
        self.assertEqual(first, score_signals(signal, settings()))
        self.assertIn(first[1], {'LOW', 'MEDIUM', 'HIGH'})
        forbidden = {'ground_truth', 'is_violation', 'true_positive', 'positive_label'}
        self.assertTrue(forbidden.isdisjoint(SHORTLIST_FIELDS))

    def test_rerun_preserves_human_review_fields(self):
        selected = [{'clip_id': 'clip-1', 'manual_review_status': 'PENDING',
                     'manual_review_label': '', 'manual_review_notes': ''}]
        existing = [{'clip_id': 'clip-1', 'manual_review_status': 'REVIEWED',
                     'manual_review_label': 'KEEP',
                     'manual_review_notes': 'Reviewed full video'}]
        preserve_manual_reviews(selected, existing)
        self.assertEqual(selected[0]['manual_review_status'], 'REVIEWED')
        self.assertEqual(selected[0]['manual_review_label'], 'KEEP')
        self.assertEqual(selected[0]['manual_review_notes'], 'Reviewed full video')

    def test_finalize_writes_only_reviewed_keep_rows(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / 'configs/evaluation/evaluation_dataset.yaml'
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                'dataset: {name: test, version: v1}\n'
                'selection: {final_shortlist_min: 2, final_shortlist_max: 2}\n',
                encoding='utf-8')
            pool = [
                {'clip_id': 'a', 'candidate_score': '0.9',
                 'manual_review_status': 'REVIEWED', 'manual_review_label': 'KEEP'},
                {'clip_id': 'b', 'candidate_score': '0.8',
                 'manual_review_status': 'REVIEWED', 'manual_review_label': 'REJECT'},
                {'clip_id': 'c', 'candidate_score': '0.7',
                 'manual_review_status': 'REVIEWED', 'manual_review_label': 'KEEP'},
            ]
            output = root / 'data/processed/selection'
            write_csv(output / 'candidate_review_pool.csv', SHORTLIST_FIELDS, pool)
            selected, summary = finalize_shortlist(Config(config_path))
            self.assertEqual([row['clip_id'] for row in selected], ['a', 'c'])
            self.assertEqual(summary['selected_shortlist_size'], 2)
            self.assertTrue((output / 'selected_shortlist.csv').is_file())


class ConstrainedSelectionTests(unittest.TestCase):
    def setUp(self):
        self.rows = synthetic_candidates()
        self.settings = settings()
        assign_diversity_and_scores(self.rows, self.settings)

    def test_imbalanced_sources_cannot_dominate_and_constraints_hold(self):
        selected, _quotas = select_shortlist(self.rows, self.settings)
        self.assertEqual(len(selected), 110)
        self.assertEqual(len({row['clip_id'] for row in selected}), 110)
        counts = {source: sum(row['source_id'] == source for row in selected)
                  for source in ('small-a', 'small-b', 'large')}
        self.assertTrue(all(count >= 20 for count in counts.values()))
        self.assertLessEqual(counts['large'], 55)
        levels = {level: sum(row['event_likelihood_level'] == level
                             for row in selected)
                  for level in ('LOW', 'MEDIUM', 'HIGH')}
        self.assertGreaterEqual(levels['LOW'], 18)
        self.assertGreaterEqual(levels['MEDIUM'], 30)
        self.assertGreaterEqual(levels['HIGH'], 18)
        self.assertGreaterEqual(sum(bool(row['hardcase_tags']) for row in selected), 25)
        self.assertGreaterEqual(
            sum(not row['hardcase_tags'] for row in selected), 25)
        self.assertTrue(all(row['manual_review_status'] == 'PENDING'
                            for row in selected))
        self.assertTrue(all('not_ground_truth' in row['selection_reason']
                            for row in selected))

    def test_generation_is_idempotent(self):
        left = copy.deepcopy(synthetic_candidates())
        right = copy.deepcopy(synthetic_candidates())
        assign_diversity_and_scores(left, self.settings)
        assign_diversity_and_scores(right, self.settings)
        selected_left, _ = select_shortlist(left, self.settings)
        selected_right, _ = select_shortlist(right, self.settings)
        self.assertEqual([row['clip_id'] for row in selected_left],
                         [row['clip_id'] for row in selected_right])

    def test_temporal_redundancy_requires_similarity(self):
        left = self.rows[0]
        adjacent = dict(left, clip_id='adjacent', source_start_sec='110',
                        source_end_sec='230')
        self.assertTrue(_is_redundant(adjacent, left, self.settings))
        adjacent['visual_signature'] = f'{int(left["visual_signature"], 16) ^ ((1 << 64)-1):016x}'
        self.assertFalse(_is_redundant(adjacent, left, self.settings))

    def test_failed_or_empty_signals_do_not_get_values_or_selection(self):
        failed = [dict(self.rows[0], signal_status='FAILED',
                       event_likelihood_score='', candidate_score='')]
        with self.assertRaises(SelectionError):
            select_shortlist(failed, self.settings)
        self.assertEqual(failed[0]['event_likelihood_score'], '')


class InputValidationTests(unittest.TestCase):
    def test_checksum_and_identity_validation(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_path = root / 'configs/evaluation/evaluation_dataset.yaml'
            config_path.parent.mkdir(parents=True)
            config_path.write_text('dataset: {name: test, version: v1}\n', encoding='utf-8')
            clip = root / 'clip.mp4'
            clip.write_bytes(b'not-decoded-in-this-test')
            digest = hashlib.sha256(clip.read_bytes()).hexdigest()
            row = {
                'clip_id': 'clip-1', 'clip_path': 'clip.mp4', 'source_id': 'source',
                'source_video_id': 'source', 'camera_id': 'UNRESOLVED',
                'source_start_sec': '0', 'source_end_sec': '120',
                'duration_sec': '120', 'fps': '30', 'width': '1920',
                'height': '1080', 'codec': 'h264', 'sha256': digest,
                'qc_status': 'PASS', 'metadata_status': 'WARNING',
            }
            validate_candidates(
                Config(config_path), [row], [dict(row)],
                settings(expected_candidates=1, expected_sources=1), True)
            changed = dict(row, sha256='0' * 64)
            with self.assertRaises(SelectionError):
                validate_candidates(
                    Config(config_path), [changed], [dict(row)],
                    settings(expected_candidates=1, expected_sources=1), True)


if __name__ == '__main__':
    unittest.main()
