"""Build and validate canonical metadata for trimmed evaluation clips.

The P0 trimming manifest is treated as immutable input.  This module joins it
with the P1 technical inventory and the selected raw-video catalog, validates
referential integrity, and writes separate B1-03 artifacts.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path

from .checksum import sha256_file
from .inventory import EXTENSIONS
from .models import read_csv, write_csv, write_json


METADATA_FIELDS = [
    'clip_id', 'source_id', 'source_video_id', 'camera_id', 'clip_path',
    'source_start_sec', 'source_end_sec', 'duration_sec', 'fps', 'width',
    'height', 'codec', 'sha256', 'qc_status', 'metadata_status', 'notes',
]

SOURCE_FIELDS = [
    'source_id', 'source_video_id', 'source_video', 'source_path', 'num_clips',
    'total_duration_sec', 'first_clip_start_sec', 'last_clip_end_sec',
    'camera_id', 'camera_status', 'notes',
]

VALIDATION_FIELDS = [
    'severity', 'clip_id', 'source_id', 'field', 'issue_code', 'message',
]

CAMERA_REPORT_FIELDS = [
    'source_id', 'source_video_id', 'source_path', 'camera_id', 'status',
    'evidence', 'notes',
]

SELECTION_CANDIDATE_FIELDS = [
    # Stable identity and inherited B1-02/B1-03 metadata.
    'clip_id', 'clip_path', 'source_id', 'source_video_id', 'camera_id',
    'source_start_sec', 'source_end_sec', 'duration_sec', 'fps', 'width',
    'height', 'codec', 'sha256', 'qc_status', 'metadata_status',
    # Incremental-analysis provenance.  The cache is reusable only when both
    # the clip checksum and analysis fingerprint still match.
    'analysis_sha256', 'analysis_fingerprint', 'visual_signature',
    'keyframe_timestamps', 'keyframe_reasons',
    # Scene/diversity signals. Counts/scores are intentionally nullable until
    # a documented visual-analysis step computes them.
    'sampled_frame_count', 'vehicle_density_score', 'object_count_estimate',
    'observed_classes', 'activity_score', 'motion_variation_score',
    'brightness_score', 'low_light_score', 'visual_variation_score',
    'foreground_occupancy_score', 'diversity_score',
    # Hard-case signals.
    'small_far_score', 'occlusion_score', 'boundary_edge_score', 'blur_score',
    'crowded_score', 'fast_motion_score', 'hardcase_tags', 'hardcase_score',
    # Prediction-only event/selection fields; never ground truth.
    'event_likelihood_score', 'event_likelihood_level', 'candidate_score',
    'source_rank', 'global_rank', 'selection_rank',
    'recommended_for_shortlist', 'selection_status', 'selection_reason',
    'manual_review_status', 'manual_review_label', 'manual_review_notes',
    'signal_status', 'prediction_status', 'notes',
]

_RANK = {'INFO': 0, 'WARNING': 1, 'FAIL': 2}
_UNRESOLVED = {'', 'UNRESOLVED', 'UNKNOWN', 'NULL', 'NONE'}


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _same_number(left, right, tolerance=1e-6):
    left_number, right_number = _number(left), _number(right)
    return (left_number is not None and right_number is not None and
            abs(left_number - right_number) <= tolerance)


def _issue(issues, severity, clip_id, source_id, field, code, message):
    issues.append({
        'severity': severity, 'clip_id': clip_id, 'source_id': source_id,
        'field': field, 'issue_code': code, 'message': message,
    })


def _settings(config):
    configured = config.data.get('metadata', {})
    return {
        'manifest_path': configured.get('manifest_path', 'data/clips/clips_manifest.csv'),
        'inventory_path': configured.get(
            'inventory_path', 'data/processed/qc/clips_inventory.csv'),
        'source_catalog_path': configured.get(
            'source_catalog_path', 'data/inventory/video_catalog.csv'),
        'source_review_path': configured.get(
            'source_review_path', 'data/review/review_candidates.csv'),
        'clip_dir': configured.get('clip_dir', 'data/clips'),
        'output_dir': configured.get('output_dir', 'data/processed/metadata'),
        'unresolved_camera_value': str(configured.get(
            'unresolved_camera_value', 'UNRESOLVED')),
        'source_camera_mapping': configured.get('source_camera_mapping', {}) or {},
        'timestamp_tolerance_sec': float(configured.get(
            'timestamp_tolerance_sec', 0.001)),
        'media_tolerance': float(configured.get('media_tolerance', 0.001)),
    }


def _origin_video_id(config, source_path, fallback):
    """Resolve a YouTube ID from the documented raw directory layout.

    For recovered recordings the P0 source alias came from the filename, while
    the enclosing acquisition directory still supplies the original video ID.
    This is path evidence, not an ordering-based inference.
    """
    try:
        relative = config.at(source_path).relative_to(config.root).parts
    except (ValueError, TypeError):
        return fallback
    try:
        index = relative.index('youtube')
        candidate = relative[index + 2]
    except (ValueError, IndexError):
        return fallback
    return candidate or fallback


def _selected_catalog(manifest_sources, catalog_rows, review_rows):
    catalog_by_video = {row.get('video_id', ''): row for row in catalog_rows}
    selected = {}
    for review in review_rows:
        if review.get('review_reason') or review.get('review_status') == 'reject':
            continue
        row = catalog_by_video.get(review.get('video_id', ''))
        if not row or row.get('source_video_id') not in manifest_sources:
            continue
        source_id = row['source_video_id']
        if source_id in selected:
            selected[source_id] = None
        else:
            selected[source_id] = row
    # A unique catalog match is a safe fallback for older runs without review CSV.
    by_source = defaultdict(list)
    for row in catalog_rows:
        if row.get('source_video_id') in manifest_sources:
            by_source[row['source_video_id']].append(row)
    for source_id, rows in by_source.items():
        if source_id not in selected and len(rows) == 1:
            selected[source_id] = rows[0]
    return selected


def build_source_registry(config, manifest_rows, catalog_rows, review_rows, issues):
    """Build one source record per distinct P0 source alias."""
    source_rows = defaultdict(list)
    for row in manifest_rows:
        source_rows[row.get('source_video_id', '')].append(row)
    manifest_sources = set(source_rows)
    selected = _selected_catalog(manifest_sources, catalog_rows, review_rows)
    settings = _settings(config)
    explicit_cameras = settings['source_camera_mapping']
    registry = []
    for source_id in sorted(manifest_sources):
        clips = source_rows[source_id]
        catalog = selected.get(source_id)
        source_path = catalog.get('local_path', '') if catalog else ''
        source_video_id = _origin_video_id(config, source_path, source_id)
        source_video = Path(source_path).name if source_path else ''
        catalog_camera = catalog.get('camera_id', '') if catalog else ''
        camera_id = str(explicit_cameras.get(source_id) or catalog_camera or
                        settings['unresolved_camera_value'])
        resolved = camera_id.upper() not in _UNRESOLVED

        starts = [_number(row.get('source_start_sec')) for row in clips]
        ends = [_number(row.get('source_end_sec')) for row in clips]
        durations = [_number(row.get('duration_sec')) for row in clips]
        valid_starts = [value for value in starts if value is not None]
        valid_ends = [value for value in ends if value is not None]
        valid_durations = [value for value in durations if value is not None]
        notes = []
        expected_overlap = float(config.data.get('clip', {}).get('overlap_sec', 0.0))
        tolerance = settings['timestamp_tolerance_sec']
        intervals = sorted(
            (start, end) for start, end in zip(starts, ends)
            if start is not None and end is not None and end > start)
        expected_pairs = 0
        irregular_pairs = 0
        for (_left_start, left_end), (right_start, _right_end) in zip(
                intervals, intervals[1:]):
            overlap = left_end - right_start
            if abs(overlap - expected_overlap) <= tolerance:
                expected_pairs += 1
            elif overlap > expected_overlap + tolerance or overlap < -tolerance:
                irregular_pairs += 1
        notes.append(
            f'Timeline has {expected_pairs} adjacent pair(s) at expected '
            f'{expected_overlap:g}s overlap and {irregular_pairs} irregular pair(s)')
        if source_video_id != source_id:
            notes.append(
                f'P0 source alias {source_id!r} came from the selected filename; '
                f'origin video ID {source_video_id!r} is evidenced by source_path')
        if not catalog:
            notes.append('No unique selected raw-video catalog mapping')
            _issue(issues, 'FAIL', '', source_id, 'source_id',
                   'UNKNOWN_SOURCE_MAPPING',
                   'P0 source has no unique selected raw-video catalog mapping')
        elif not source_path:
            notes.append('Selected raw-video catalog mapping has no source path')
            _issue(issues, 'FAIL', '', source_id, 'source_path',
                   'MISSING_SOURCE_PATH',
                   'Selected raw-video catalog mapping has an empty local_path')
        else:
            try:
                source_exists = config.at(source_path).is_file()
            except ValueError:
                source_exists = False
            if not source_exists:
                notes.append('Selected source file is missing or outside the project root')
                _issue(issues, 'FAIL', '', source_id, 'source_path',
                       'MISSING_SOURCE_FILE',
                       f'Selected source file does not exist: {source_path!r}')
        if not resolved:
            notes.append('No verified structured camera mapping is available')

        registry.append({
            'source_id': source_id,
            'source_video_id': source_video_id,
            'source_video': source_video,
            'source_path': source_path,
            'num_clips': len(clips),
            'total_duration_sec': round(sum(valid_durations), 6),
            'first_clip_start_sec': min(valid_starts) if valid_starts else '',
            'last_clip_end_sec': max(valid_ends) if valid_ends else '',
            'camera_id': camera_id,
            'camera_status': 'RESOLVED' if resolved else 'UNRESOLVED',
            'notes': '; '.join(notes),
        })
    return registry


def _index_rows(rows, key, issues, issue_code, field):
    index = {}
    duplicates = set()
    for row in rows:
        value = row.get(key, '')
        if value in index:
            duplicates.add(value)
        else:
            index[value] = row
    for value in sorted(duplicates):
        source_id = next((row.get('source_video_id', '') for row in rows
                          if row.get(key, '') == value), '')
        _issue(issues, 'FAIL', value if key == 'clip_id' else '', source_id,
               field, issue_code, f'Duplicate {field}: {value!r}')
    return index


def _validate_timeline(rows, expected_overlap, tolerance, issues):
    by_source = defaultdict(list)
    for row in rows:
        start, end = _number(row.get('source_start_sec')), _number(row.get('source_end_sec'))
        if row.get('source_id') and start is not None and end is not None and end > start:
            by_source[row['source_id']].append((start, end, row))
    for source_id, source_rows in by_source.items():
        source_rows.sort(key=lambda item: (item[0], item[1], item[2].get('clip_id', '')))
        for (_left_start, left_end, left), (right_start, _right_end, right) in zip(
                source_rows, source_rows[1:]):
            overlap = left_end - right_start
            if overlap > expected_overlap + tolerance:
                _issue(issues, 'WARNING', right['clip_id'], source_id,
                       'source_start_sec', 'EXCESS_TEMPORAL_OVERLAP',
                       f'Overlap with {left["clip_id"]} is {overlap:.6f}s; '
                       f'expected at most {expected_overlap:.6f}s')
            elif overlap < -tolerance:
                _issue(issues, 'WARNING', right['clip_id'], source_id,
                       'source_start_sec', 'TIMELINE_GAP',
                       f'Gap after {left["clip_id"]} is {-overlap:.6f}s')


def _validate_source_consistency(rows, issues, tolerance):
    by_source = defaultdict(list)
    for row in rows:
        if row.get('source_id'):
            by_source[row['source_id']].append(row)
    for source_id, source_rows in by_source.items():
        for field in ('fps', 'width', 'height', 'codec'):
            values = [row.get(field, '') for row in source_rows if row.get(field, '') != '']
            if not values:
                continue
            baseline = values[0]
            for row in source_rows:
                value = row.get(field, '')
                same = (_same_number(value, baseline, tolerance)
                        if field != 'codec' else value == baseline)
                if value != '' and not same:
                    _issue(issues, 'WARNING', row['clip_id'], source_id, field,
                           'SOURCE_MEDIA_INCONSISTENCY',
                           f'{field}={value!r} differs from source baseline {baseline!r}')


def build_metadata(config, manifest_rows, inventory_rows, registry,
                   verify_checksums=True):
    """Join inputs and return ``(canonical rows, validation issues)``."""
    issues = []
    settings = _settings(config)
    inventory_by_id = _index_rows(
        inventory_rows, 'clip_id', issues, 'DUPLICATE_INVENTORY_CLIP_ID', 'clip_id')
    registry_by_id = {row['source_id']: row for row in registry}
    manifest_ids = Counter(row.get('clip_id', '') for row in manifest_rows)
    manifest_paths = Counter(row.get('local_path', '') for row in manifest_rows)

    for clip_id, count in sorted(manifest_ids.items()):
        if not clip_id:
            _issue(issues, 'FAIL', '', '', 'clip_id', 'MISSING_CLIP_ID',
                   'Manifest row has an empty clip_id')
        elif count > 1:
            _issue(issues, 'FAIL', clip_id, '', 'clip_id', 'DUPLICATE_CLIP_ID',
                   f'clip_id appears {count} times in the P0 manifest')
    for clip_path, count in sorted(manifest_paths.items()):
        if not clip_path:
            _issue(issues, 'FAIL', '', '', 'clip_path', 'MISSING_CLIP_PATH',
                   'Manifest row has an empty local_path')
        elif count > 1:
            _issue(issues, 'FAIL', '', '', 'clip_path', 'DUPLICATE_CLIP_PATH',
                   f'clip path appears {count} times in the P0 manifest: {clip_path}')

    rows = []
    for manifest in manifest_rows:
        clip_id = manifest.get('clip_id', '')
        source_id = manifest.get('source_video_id', '')
        inventory = inventory_by_id.get(clip_id)
        source = registry_by_id.get(source_id)
        clip_path = manifest.get('local_path', '')
        if not source_id:
            _issue(issues, 'FAIL', clip_id, '', 'source_id', 'MISSING_SOURCE_ID',
                   'P0 manifest source_video_id is empty')
        elif not source:
            _issue(issues, 'FAIL', clip_id, source_id, 'source_id',
                   'UNKNOWN_SOURCE_MAPPING',
                   'No source_registry row exists for this source_id')
        elif (not clip_id.startswith(f'{source_id}_C') or
              not clip_id.removeprefix(f'{source_id}_C').isdigit()):
            _issue(issues, 'WARNING', clip_id, source_id, 'clip_id',
                   'CLIP_NAMING_INCONSISTENCY',
                   'clip_id does not follow the P0 <source_id>_Cnnn convention')
        if not inventory:
            _issue(issues, 'FAIL', clip_id, source_id, 'clip_id',
                   'MISSING_INVENTORY_RECORD',
                   'Clip is present in P0 manifest but absent from P1 inventory')

        start = _number(manifest.get('source_start_sec'))
        end = _number(manifest.get('source_end_sec'))
        if start is None or end is None:
            _issue(issues, 'FAIL', clip_id, source_id, 'source_start_sec/source_end_sec',
                   'MALFORMED_TIMESTAMPS', 'Start/end timestamps must be finite numbers')
        elif end <= start:
            _issue(issues, 'FAIL', clip_id, source_id, 'source_end_sec',
                   'INVALID_TIMESTAMP_ORDER',
                   f'source_end_sec={end} must be greater than source_start_sec={start}')

        manifest_duration = _number(manifest.get('duration_sec'))
        if (start is not None and end is not None and end > start and
                (manifest_duration is None or
                 abs(manifest_duration - (end - start)) >
                 settings['timestamp_tolerance_sec'])):
            _issue(issues, 'FAIL', clip_id, source_id, 'duration_sec',
                   'MANIFEST_DURATION_MISMATCH',
                   'P0 duration does not match source_end_sec - source_start_sec')

        if inventory:
            comparisons = (
                ('source_id', source_id, inventory.get('source_id', ''), 'text'),
                ('clip_path', clip_path, inventory.get('file_path', ''), 'text'),
                ('source_start_sec', manifest.get('source_start_sec', ''),
                 inventory.get('source_start_sec', ''), 'number'),
                ('source_end_sec', manifest.get('source_end_sec', ''),
                 inventory.get('source_end_sec', ''), 'number'),
                ('duration_sec', manifest.get('duration_sec', ''),
                 inventory.get('duration_sec', ''), 'number'),
                ('sha256', manifest.get('sha256', ''), inventory.get('sha256', ''), 'text'),
            )
            for field, expected, actual, kind in comparisons:
                same = (_same_number(expected, actual, settings['timestamp_tolerance_sec'])
                        if kind == 'number' else expected == actual)
                if not same:
                    code = ('CHECKSUM_MISMATCH' if field == 'sha256'
                            else 'METADATA_INVENTORY_MISMATCH')
                    _issue(issues, 'FAIL', clip_id, source_id, field, code,
                           f'P0 value {expected!r} differs from P1 inventory {actual!r}')

        path = None
        try:
            path = config.at(clip_path) if clip_path else None
        except ValueError:
            _issue(issues, 'FAIL', clip_id, source_id, 'clip_path',
                   'INVALID_CLIP_PATH', f'Clip path escapes project root: {clip_path!r}')
        if path is None or not path.is_file():
            _issue(issues, 'FAIL', clip_id, source_id, 'clip_path', 'MISSING_FILE',
                   f'Clip file does not exist: {clip_path!r}')
        elif verify_checksums:
            actual_sha = sha256_file(path)
            expected_sha = manifest.get('sha256', '')
            if not expected_sha or actual_sha != expected_sha:
                _issue(issues, 'FAIL', clip_id, source_id, 'sha256',
                       'CHECKSUM_MISMATCH',
                       f'Filesystem SHA-256 {actual_sha!r} differs from P0 {expected_sha!r}')
        if clip_path and Path(clip_path).stem != clip_id:
            _issue(issues, 'WARNING', clip_id, source_id, 'clip_path',
                   'CLIP_PATH_NAMING_INCONSISTENCY',
                   f'Clip filename stem {Path(clip_path).stem!r} differs from clip_id')

        camera_id = source.get('camera_id', settings['unresolved_camera_value']) if source else settings['unresolved_camera_value']
        if str(camera_id).upper() in _UNRESOLVED:
            _issue(issues, 'WARNING', clip_id, source_id, 'camera_id',
                   'UNRESOLVED_CAMERA',
                   'No verified camera identity exists; camera_id is intentionally UNRESOLVED')

        qc_status = inventory.get('qc_status', '') if inventory else ''
        if qc_status == 'FAIL':
            _issue(issues, 'FAIL', clip_id, source_id, 'qc_status', 'QC_FAILED',
                   'P1 technical QC status is FAIL')
        elif qc_status == 'WARNING':
            _issue(issues, 'WARNING', clip_id, source_id, 'qc_status', 'QC_WARNING',
                   'P1 technical QC status is WARNING')
        elif qc_status != 'PASS':
            _issue(issues, 'FAIL', clip_id, source_id, 'qc_status',
                   'INVALID_QC_STATUS', f'Expected P1 qc_status PASS, got {qc_status!r}')

        rows.append({
            'clip_id': clip_id,
            'source_id': source_id,
            'source_video_id': source.get('source_video_id', '') if source else '',
            'camera_id': camera_id,
            'clip_path': clip_path,
            'source_start_sec': manifest.get('source_start_sec', ''),
            'source_end_sec': manifest.get('source_end_sec', ''),
            'duration_sec': inventory.get('duration_sec', '') if inventory else manifest.get('duration_sec', ''),
            'fps': inventory.get('fps', '') if inventory else '',
            'width': inventory.get('width', '') if inventory else '',
            'height': inventory.get('height', '') if inventory else '',
            'codec': inventory.get('codec', '') if inventory else '',
            'sha256': manifest.get('sha256', ''),
            'qc_status': qc_status,
            'metadata_status': 'PASS',
            'notes': '',
        })

    manifest_id_set = set(manifest_ids)
    for inventory in inventory_rows:
        if inventory.get('clip_id', '') not in manifest_id_set:
            _issue(issues, 'FAIL', inventory.get('clip_id', ''),
                   inventory.get('source_id', ''), 'clip_id',
                   'ORPHAN_INVENTORY_RECORD',
                   'P1 inventory row has no matching P0 manifest row')

    clip_dir = config.at(settings['clip_dir'])
    manifest_resolved = set()
    for path_text in manifest_paths:
        try:
            manifest_resolved.add(config.at(path_text).resolve())
        except ValueError:
            pass
    if clip_dir.is_dir():
        for path in sorted(candidate for candidate in clip_dir.rglob('*')
                           if candidate.is_file() and candidate.suffix.lower() in EXTENSIONS):
            if path.resolve() not in manifest_resolved:
                _issue(issues, 'FAIL', '', '', 'clip_path', 'ORPHAN_CLIP_FILE',
                       f'Media file has no P0 manifest row: {path.relative_to(config.root)}')

    expected_overlap = float(config.data.get('clip', {}).get('overlap_sec', 0.0))
    _validate_timeline(rows, expected_overlap,
                       settings['timestamp_tolerance_sec'], issues)
    _validate_source_consistency(rows, issues, settings['media_tolerance'])

    by_clip = defaultdict(list)
    for issue in issues:
        if issue['clip_id']:
            by_clip[issue['clip_id']].append(issue)
    for row in rows:
        row_issues = by_clip[row['clip_id']]
        highest = max((_RANK[issue['severity']] for issue in row_issues), default=0)
        row['metadata_status'] = 'FAIL' if highest == 2 else ('WARNING' if highest == 1 else 'PASS')
        row['notes'] = '; '.join(
            f'{issue["issue_code"]}: {issue["message"]}'
            for issue in row_issues if issue['severity'] != 'INFO')
    issues.sort(key=lambda row: (
        -_RANK[row['severity']], row['source_id'], row['clip_id'],
        row['field'], row['issue_code'], row['message']))
    return rows, issues


def _camera_report(registry):
    rows = []
    for source in registry:
        if source['camera_status'] == 'UNRESOLVED':
            rows.append({
                'source_id': source['source_id'],
                'source_video_id': source['source_video_id'],
                'source_path': source['source_path'],
                'camera_id': source['camera_id'],
                'status': 'UNRESOLVED',
                'evidence': 'P0 manifest, source catalog, and explicit config mapping contain no camera_id',
                'notes': 'Resolve only from verified camera metadata; do not infer camera_id from source_id or file order.',
            })
    return rows


def build_selection_candidates(metadata_rows):
    """Prepare the B1-03 candidate table without fabricating visual signals.

    This metadata step owns identity and technical metadata only. Scene,
    hard-case, event, ranking, and recommendation values remain nullable until
    incremental candidate analysis records how they were computed.
    """
    inherited = (
        'clip_id', 'clip_path', 'source_id', 'source_video_id', 'camera_id',
        'source_start_sec', 'source_end_sec', 'duration_sec', 'fps', 'width',
        'height', 'codec', 'sha256', 'qc_status', 'metadata_status',
    )
    rows = []
    for metadata in metadata_rows:
        row = {field: metadata.get(field, '') for field in inherited}
        for field in SELECTION_CANDIDATE_FIELDS:
            row.setdefault(field, '')
        row.update(
            signal_status='NOT_COMPUTED',
            prediction_status='NOT_COMPUTED_NOT_GROUND_TRUTH',
            notes='Visual selection signals are reserved for B1-03 analysis; no score, rank, or recommendation was inferred by metadata construction.',
        )
        rows.append(row)
    return rows


def _summary(config, rows, registry, issues, verify_checksums):
    status_counts = Counter(row['metadata_status'] for row in rows)
    severity_counts = Counter(issue['severity'] for issue in issues)
    clips_per_source = Counter(row['source_id'] for row in rows)
    duration_per_source = defaultdict(float)
    for row in rows:
        duration_per_source[row['source_id']] += _number(row['duration_sec']) or 0.0
    issue_counts = Counter(issue['issue_code'] for issue in issues)
    resolved_cameras = {
        row['camera_id'] for row in rows
        if str(row['camera_id']).upper() not in _UNRESOLVED
    }
    return {
        'source_of_truth': _settings(config)['manifest_path'],
        'filesystem_checksum_verification': bool(verify_checksums),
        'total_clips': len(rows),
        'distinct_source_count': len({row['source_id'] for row in rows if row['source_id']}),
        'distinct_resolved_camera_count': len(resolved_cameras),
        'unresolved_camera_count': sum(
            str(row['camera_id']).upper() in _UNRESOLVED for row in rows),
        'unresolved_camera_source_count': sum(
            source['camera_status'] == 'UNRESOLVED' for source in registry),
        'clips_per_source': dict(sorted(clips_per_source.items())),
        'total_duration_sec_per_source': {
            key: round(value, 6) for key, value in sorted(duration_per_source.items())
        },
        'total_dataset_duration_sec': round(sum(duration_per_source.values()), 6),
        'metadata_status_counts': {
            status: status_counts[status] for status in ('PASS', 'WARNING', 'FAIL')
        },
        'validation_severity_counts': {
            severity: severity_counts[severity]
            for severity in ('INFO', 'WARNING', 'FAIL')
        },
        'checksum_mismatch_count': len({
            issue['clip_id'] for issue in issues
            if issue['issue_code'] == 'CHECKSUM_MISMATCH'
        }),
        'orphan_count': (issue_counts['ORPHAN_INVENTORY_RECORD'] +
                         issue_counts['ORPHAN_CLIP_FILE']),
        'validation_issue_counts': dict(sorted(issue_counts.items())),
        'source_registry_count': len(registry),
        'clip_selection_candidate_count': len(rows),
        'selection_signals_computed_count': 0,
    }


def run(config, verify_checksums=True):
    """Generate all B1-03 artifacts and return rows, issues, and summary."""
    settings = _settings(config)
    manifest_path = config.at(settings['manifest_path'])
    inventory_path = config.at(settings['inventory_path'])
    catalog_path = config.at(settings['source_catalog_path'])
    review_path = config.at(settings['source_review_path'])
    for label, path in (
            ('P0 manifest', manifest_path), ('P1 inventory', inventory_path),
            ('source catalog', catalog_path), ('source review', review_path)):
        if not path.is_file():
            raise FileNotFoundError(f'{label} does not exist: {path}')

    manifest_rows = read_csv(manifest_path)
    inventory_rows = read_csv(inventory_path)
    catalog_rows = read_csv(catalog_path)
    review_rows = read_csv(review_path)
    registry_issues = []
    registry = build_source_registry(
        config, manifest_rows, catalog_rows, review_rows, registry_issues)
    rows, issues = build_metadata(
        config, manifest_rows, inventory_rows, registry, verify_checksums)
    issues.extend(registry_issues)
    # Source-level failures must fail every affected canonical row, not merely
    # appear in a global report that the CLI could accidentally overlook.
    for issue in registry_issues:
        for row in rows:
            if row['source_id'] != issue['source_id']:
                continue
            if _RANK[issue['severity']] > _RANK.get(row['metadata_status'], 0):
                row['metadata_status'] = issue['severity']
            if issue['severity'] != 'INFO':
                note = f'{issue["issue_code"]}: {issue["message"]}'
                row['notes'] = '; '.join(value for value in (row['notes'], note) if value)
    issues.sort(key=lambda row: (
        -_RANK[row['severity']], row['source_id'], row['clip_id'],
        row['field'], row['issue_code'], row['message']))
    summary = _summary(config, rows, registry, issues, verify_checksums)

    output_dir = config.at(settings['output_dir'])
    write_csv(output_dir / 'clips_metadata.csv', METADATA_FIELDS, rows)
    write_csv(output_dir / 'source_registry.csv', SOURCE_FIELDS, registry)
    write_csv(output_dir / 'metadata_validation_report.csv', VALIDATION_FIELDS, issues)
    write_csv(output_dir / 'camera_resolution_report.csv', CAMERA_REPORT_FIELDS,
              _camera_report(registry))
    write_csv(output_dir / 'clip_selection_candidates.csv',
              SELECTION_CANDIDATE_FIELDS, build_selection_candidates(rows))
    write_json(output_dir / 'metadata_summary.json', summary)
    return rows, issues, summary


def format_summary(summary):
    counts = summary['metadata_status_counts']
    return '\n'.join([
        f"Total clips: {summary['total_clips']}",
        f"Distinct sources: {summary['distinct_source_count']}",
        f"Resolved cameras: {summary['distinct_resolved_camera_count']}",
        f"Unresolved camera clips: {summary['unresolved_camera_count']}",
        f"Metadata PASS: {counts['PASS']}",
        f"Metadata WARNING: {counts['WARNING']}",
        f"Metadata FAIL: {counts['FAIL']}",
        f"Validation FAIL issues: {summary['validation_severity_counts']['FAIL']}",
        f"Checksum mismatches: {summary['checksum_mismatch_count']}",
        f"Orphans: {summary['orphan_count']}",
    ])
