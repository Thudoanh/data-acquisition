import hashlib
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from evaluation_data.clip_metadata import build_metadata, build_selection_candidates
from evaluation_data.models import Config


class ClipMetadataValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        config_path = self.root / 'configs/evaluation/evaluation_dataset.yaml'
        config_path.parent.mkdir(parents=True)
        config_path.write_text(
            'clip: {overlap_sec: 10}\n'
            'metadata:\n'
            '  clip_dir: data/clips\n'
            '  unresolved_camera_value: UNRESOLVED\n',
            encoding='utf-8')
        self.config = Config(config_path)
        self.clips = self.root / 'data/clips'
        self.clips.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def fixture(self, clip_id='source_C001', source_id='source', start='0',
                end='120', duration='120', camera_id='UNRESOLVED',
                create_file=True):
        path = self.clips / f'{clip_id}.mp4'
        if create_file:
            path.write_bytes(clip_id.encode())
        digest = hashlib.sha256(clip_id.encode()).hexdigest()
        manifest = [{
            'clip_id': clip_id, 'source_video_id': source_id,
            'source_start_sec': start, 'source_end_sec': end,
            'duration_sec': duration, 'local_path': f'data/clips/{clip_id}.mp4',
            'sha256': digest,
        }]
        inventory = [{
            'clip_id': clip_id, 'source_id': source_id,
            'file_path': f'data/clips/{clip_id}.mp4',
            'source_start_sec': start, 'source_end_sec': end,
            'duration_sec': duration, 'fps': '30', 'width': '1920',
            'height': '1080', 'codec': 'h264', 'sha256': digest,
            'qc_status': 'PASS',
        }]
        registry = [{
            'source_id': source_id, 'source_video_id': source_id,
            'camera_id': camera_id,
        }]
        return manifest, inventory, registry

    @staticmethod
    def codes(issues):
        return {issue['issue_code'] for issue in issues}

    def test_unresolved_camera_warns_but_does_not_fail(self):
        manifest, inventory, registry = self.fixture()
        rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=True)
        self.assertEqual(rows[0]['metadata_status'], 'WARNING')
        self.assertIn('UNRESOLVED_CAMERA', self.codes(issues))
        self.assertFalse(any(issue['severity'] == 'FAIL' for issue in issues))

    def test_duplicate_clip_id_fails(self):
        manifest, inventory, registry = self.fixture(camera_id='camera-real')
        manifest.append(dict(manifest[0]))
        _rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        self.assertIn('DUPLICATE_CLIP_ID', self.codes(issues))

    def test_missing_source_id_fails(self):
        manifest, inventory, _registry = self.fixture(source_id='')
        _rows, issues = build_metadata(
            self.config, manifest, inventory, [], verify_checksums=False)
        self.assertIn('MISSING_SOURCE_ID', self.codes(issues))

    def test_unknown_source_mapping_fails(self):
        manifest, inventory, _registry = self.fixture()
        _rows, issues = build_metadata(
            self.config, manifest, inventory, [], verify_checksums=False)
        self.assertIn('UNKNOWN_SOURCE_MAPPING', self.codes(issues))

    def test_checksum_mismatch_fails(self):
        manifest, inventory, registry = self.fixture(camera_id='camera-real')
        manifest[0]['sha256'] = '0' * 64
        inventory[0]['sha256'] = manifest[0]['sha256']
        _rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=True)
        self.assertIn('CHECKSUM_MISMATCH', self.codes(issues))

    def test_malformed_timestamps_fail(self):
        manifest, inventory, registry = self.fixture(
            start='not-a-number', camera_id='camera-real')
        _rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        self.assertIn('MALFORMED_TIMESTAMPS', self.codes(issues))

    def test_end_not_after_start_fails(self):
        manifest, inventory, registry = self.fixture(
            start='120', end='120', duration='0', camera_id='camera-real')
        _rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        self.assertIn('INVALID_TIMESTAMP_ORDER', self.codes(issues))

    def test_inventory_mismatch_fails(self):
        manifest, inventory, registry = self.fixture(camera_id='camera-real')
        inventory[0]['source_start_sec'] = '1'
        _rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        self.assertIn('METADATA_INVENTORY_MISMATCH', self.codes(issues))

    def test_missing_file_fails(self):
        manifest, inventory, registry = self.fixture(
            camera_id='camera-real', create_file=False)
        _rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        self.assertIn('MISSING_FILE', self.codes(issues))

    def test_orphan_file_and_inventory_record_fail(self):
        manifest, inventory, registry = self.fixture(camera_id='camera-real')
        orphan = self.clips / 'orphan.mp4'
        orphan.write_bytes(b'orphan')
        inventory.append({
            'clip_id': 'orphan', 'source_id': 'source',
            'file_path': 'data/clips/orphan.mp4', 'qc_status': 'PASS',
        })
        _rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        self.assertIn('ORPHAN_CLIP_FILE', self.codes(issues))
        self.assertIn('ORPHAN_INVENTORY_RECORD', self.codes(issues))

    def test_expected_ten_second_overlap_is_not_an_issue(self):
        manifest, inventory, registry = self.fixture(camera_id='camera-real')
        second_manifest, second_inventory, _ = self.fixture(
            clip_id='source_C002', start='110', end='230',
            camera_id='camera-real')
        manifest += second_manifest
        inventory += second_inventory
        rows, issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        self.assertEqual([row['metadata_status'] for row in rows], ['PASS', 'PASS'])
        self.assertNotIn('EXCESS_TEMPORAL_OVERLAP', self.codes(issues))
        self.assertNotIn('TIMELINE_GAP', self.codes(issues))

    def test_selection_candidates_preserve_metadata_without_fake_predictions(self):
        manifest, inventory, registry = self.fixture()
        metadata, _issues = build_metadata(
            self.config, manifest, inventory, registry, verify_checksums=False)
        candidates = build_selection_candidates(metadata)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]['clip_id'], 'source_C001')
        self.assertEqual(candidates[0]['fps'], '30')
        self.assertEqual(candidates[0]['signal_status'], 'NOT_COMPUTED')
        self.assertEqual(candidates[0]['prediction_status'],
                         'NOT_COMPUTED_NOT_GROUND_TRUTH')
        for field in ('object_count_estimate', 'hardcase_score',
                      'event_likelihood_score', 'candidate_score',
                      'selection_rank', 'recommended_for_shortlist'):
            self.assertEqual(candidates[0][field], '')


if __name__ == '__main__':
    unittest.main()
