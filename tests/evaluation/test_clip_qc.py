import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from evaluation_data.clip_qc import (
    DEFAULT_QC, apply_metadata_rules, assign_duplicate_groups,
    assign_overlap_groups, calculate_overlap, finalize_status, probe_video, run,
)
from evaluation_data.models import CLIPS, Config, write_csv


def sample_clip(clip_id, **updates):
    row = {
        'clip_id': clip_id, 'source_id': 'source', 'session_id': '',
        'file_path': f'data/clips/{clip_id}.mp4', 'file_name': f'{clip_id}.mp4',
        'file_size_mb': 1, 'sha256': clip_id, 'source_start_sec': '',
        'source_end_sec': '', 'duration_sec': 120.0, 'fps': 25.0,
        'frame_count': 3000, 'width': 1920, 'height': 1080, 'codec': 'h264',
        'metadata_source': 'ffprobe', 'decode_ok': True, 'decode_error_count': 0,
        'first_frame_ok': True, 'middle_frame_ok': True, 'last_frame_ok': True,
        'full_decode_ok': '', 'duplicate_group': '', 'overlap_group': '',
        'overlap_status': 'NOT_CHECKED', 'overlap_max_sec': '',
        'overlap_max_ratio': '', 'qc_status': 'PASS', 'qc_notes': '', '_issues': [],
    }
    row.update(updates)
    return row


class MetadataTests(unittest.TestCase):
    def test_valid_ffprobe_metadata(self):
        payload = ('{"streams":[{"codec_type":"video","avg_frame_rate":"30000/1001",'
                   '"nb_frames":"3596","width":1280,"height":720,"codec_name":"h264"}],'
                   '"format":{"duration":"120.0"}}')
        result = unittest.mock.Mock(returncode=0, stdout=payload)
        with patch('evaluation_data.clip_qc.subprocess.run', return_value=result):
            info, reason, source = probe_video(Path('clip.mp4'), '/usr/bin/ffprobe')
        self.assertEqual(reason, '')
        self.assertEqual(source, 'ffprobe')
        self.assertEqual(info['frame_count'], 3596)
        self.assertAlmostEqual(info['fps'], 29.97003, places=4)

    def test_pass_warning_fail_decision_logic(self):
        good = sample_clip('good')
        warning = sample_clip('warning', duration_sec=10)
        failure = sample_clip('failure')
        failure['_issues'].append({
            'issue_id': 'x', 'clip_id': 'failure', 'source_id': 'source',
            'issue_type': 'DECODE_ERROR', 'severity': 'FAIL', 'details': 'bad',
            'related_clip_id': '', 'action': 'review',
        })
        apply_metadata_rules([good, warning, failure], DEFAULT_QC)
        finalize_status([good, warning, failure])
        self.assertEqual([good['qc_status'], warning['qc_status'], failure['qc_status']],
                         ['PASS', 'WARNING', 'FAIL'])


class GroupingTests(unittest.TestCase):
    def test_duplicate_grouping(self):
        clips = [sample_clip('a', sha256='same'), sample_clip('b', sha256='same'),
                 sample_clip('c', sha256='different')]
        self.assertEqual(assign_duplicate_groups(clips), 1)
        self.assertEqual(clips[0]['duplicate_group'], clips[1]['duplicate_group'])
        self.assertEqual(clips[2]['duplicate_group'], '')

    def test_temporal_overlap_calculation_and_boundary_contact(self):
        self.assertEqual(calculate_overlap(0, 10, 10, 20), (0.0, 0.0))
        overlap, ratio = calculate_overlap(0, 12, 10, 20)
        self.assertEqual(overlap, 2)
        self.assertAlmostEqual(ratio, 0.2)
        clips = [
            sample_clip('a', source_start_sec=0, source_end_sec=120),
            sample_clip('b', source_start_sec=110, source_end_sec=230),
            sample_clip('c', source_start_sec=230, source_end_sec=350),
        ]
        groups, metrics = assign_overlap_groups(clips, expected_overlap=10, tolerance=.5)
        self.assertEqual(groups, 1)
        self.assertEqual(metrics[('a', 'b')][0], 10)
        self.assertTrue(all(issue['severity'] == 'INFO'
                            for clip in clips for issue in clip['_issues']))
        self.assertEqual(clips[2]['overlap_status'], 'CHECKED_NO_OVERLAP')


class PipelineResilienceTests(unittest.TestCase):
    def _config(self, root):
        config_path = root / 'configs/evaluation/evaluation_dataset.yaml'
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            'paths: {work_root: data}\nclip: {overlap_sec: 10}\n'
            'qc: {input_dir: data/clips, output_dir: data/processed/qc}\n')
        return Config(config_path)

    def test_corrupt_file_does_not_stop_remaining_files(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = self._config(root)
            clips_dir = root / 'data/clips'
            clips_dir.mkdir(parents=True)
            bad = clips_dir / 'source_C001.mp4'
            good = clips_dir / 'source_C002.mp4'
            bad.write_bytes(b'bad')
            good.write_bytes(b'good')
            write_csv(clips_dir / 'clips_manifest.csv', CLIPS, [
                {'clip_id': 'source_C001', 'source_video_id': 'source',
                 'source_start_sec': 0, 'source_end_sec': 120,
                 'local_path': 'data/clips/source_C001.mp4'},
                {'clip_id': 'source_C002', 'source_video_id': 'source',
                 'source_start_sec': 120, 'source_end_sec': 240,
                 'local_path': 'data/clips/source_C002.mp4'},
            ])

            def fake_probe(path, _ffprobe):
                if path.name == bad.name:
                    return {}, 'ffprobe_failed; opencv_probe_failed', 'unavailable'
                return ({'duration_sec': 120.0, 'fps': 25.0, 'frame_count': 3000,
                         'width': 1920, 'height': 1080, 'codec': 'h264'}, '', 'ffprobe')

            decoded = {'first_frame_ok': True, 'middle_frame_ok': True,
                       'last_frame_ok': True, 'full_decode_ok': '',
                       'decode_error_count': 0, 'details': [], 'fallback': False}
            with patch('evaluation_data.clip_qc.find_media_tool', return_value='/tool'), \
                 patch('evaluation_data.clip_qc.probe_video', side_effect=fake_probe), \
                 patch('evaluation_data.clip_qc.decode_video', return_value=decoded):
                rows, issues, summary = run(config)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['qc_status'], 'FAIL')
            self.assertEqual(rows[1]['qc_status'], 'PASS')
            self.assertEqual(summary['fail'], 1)
            self.assertTrue(any(issue['issue_type'] == 'FILE_CORRUPTED' for issue in issues))
            self.assertTrue((root / 'data/processed/qc/clips_inventory.csv').is_file())
            self.assertTrue((root / 'data/processed/qc/trimming_qc_report.csv').is_file())


if __name__ == '__main__':
    unittest.main()
