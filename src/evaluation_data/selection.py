"""Incremental candidate analysis (B1-03) and keyframe review pool (B1-04).

The signals in this module are image statistics and motion-region heuristics.
They are useful for choosing clips for review, but are neither object labels nor
violation ground truth.  In particular, no vehicle detector is bundled with
this repository; the legacy ``vehicle_density_score`` column is populated by a
documented moving-foreground occupancy proxy and ``observed_classes`` remains
``NOT_INFERRED_NO_DETECTOR``.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import numpy as np

from .checksum import sha256_file
from .clip_qc import find_media_tool
from .clip_metadata import SELECTION_CANDIDATE_FIELDS
from .models import read_csv, write_csv, write_json


SHORTLIST_FIELDS = [
    'clip_id', 'source_id', 'source_video_id', 'camera_id', 'clip_path',
    'source_start_sec', 'source_end_sec', 'duration_sec', 'sha256',
    'sampled_frame_count', 'event_likelihood_score',
    'event_likelihood_level', 'hardcase_score', 'hardcase_tags',
    'diversity_score', 'candidate_score', 'source_rank', 'global_rank',
    'keyframe_timestamps', 'keyframe_reasons',
    'selection_status', 'selection_reason', 'manual_review_status',
    'manual_review_label', 'manual_review_notes', 'signal_status',
    'prediction_status',
]

REVIEW_FIELDS = [
    'clip_id', 'source_id', 'clip_path', 'preview_path',
    'event_likelihood_score', 'event_likelihood_level', 'hardcase_tags',
    'keyframe_timestamps', 'keyframe_reasons',
    'selection_reason', 'manual_review_status', 'manual_review_label',
    'manual_review_notes',
]

IDENTITY_FIELDS = [
    'clip_id', 'clip_path', 'source_id', 'source_video_id', 'camera_id',
    'source_start_sec', 'source_end_sec', 'duration_sec', 'fps', 'width',
    'height', 'codec', 'sha256', 'qc_status', 'metadata_status',
]

DEFAULTS = {
    'candidate_path': 'data/processed/metadata/clip_selection_candidates.csv',
    'canonical_metadata_path': 'data/processed/metadata/clips_metadata.csv',
    'feature_cache_path': 'data/processed/metadata/clip_feature_cache.csv',
    'analysis_summary_path': 'data/processed/metadata/candidate_analysis_summary.json',
    'output_dir': 'data/processed/selection',
    'expected_candidates': 472,
    'expected_sources': 3,
    'sample_frames': 12,
    'analysis_width': 320,
    'workers': 4,
    'candidate_pool_size': 110,
    'final_shortlist_min': 75,
    'final_shortlist_max': 90,
    'keyframes_per_clip': 5,
    'source_minimum': 20,
    'source_maximum_fraction': 0.50,
    'event_thresholds': {'low_max': 0.24, 'high_min': 0.36},
    'hardcase_tag_threshold': 0.65,
    'score_weights': {
        'event_likelihood': 0.42,
        'hardcase': 0.28,
        'diversity': 0.18,
        'activity': 0.12,
    },
    'event_weights': {
        'foreground_occupancy': 0.34,
        'activity': 0.27,
        'persistence': 0.21,
        'visual_variation': 0.18,
    },
    'hardcase_weights': {
        'small_far': 0.18,
        'occlusion': 0.14,
        'boundary_edge': 0.14,
        'blur': 0.16,
        'crowded': 0.16,
        'fast_motion': 0.12,
        'low_light': 0.10,
    },
    'selection_level_minimums': {'LOW': 18, 'MEDIUM': 30, 'HIGH': 18},
    'selected_hardcase_minimum': 25,
    'selected_normal_minimum': 25,
    'temporal_adjacency_sec': 11.0,
    'adjacent_similarity_threshold': 0.86,
    'content_similarity_threshold': 0.95,
}

SIGNAL_ALGORITHM_VERSION = 'incremental-opencv-v2'

ANALYSIS_RESULT_FIELDS = [
    'analysis_sha256', 'analysis_fingerprint', 'visual_signature',
    'keyframe_timestamps', 'keyframe_reasons', 'sampled_frame_count',
    'vehicle_density_score', 'object_count_estimate', 'observed_classes',
    'activity_score', 'motion_variation_score', 'brightness_score',
    'low_light_score', 'visual_variation_score',
    'foreground_occupancy_score', 'persistence_score', 'small_far_score',
    'occlusion_score', 'boundary_edge_score', 'blur_score', 'crowded_score',
    'fast_motion_score', 'hardcase_tags', 'hardcase_score',
    'event_likelihood_score', 'event_likelihood_level', 'signal_status',
    'prediction_status', 'notes',
]


class SelectionError(ValueError):
    """Raised when B1-03/B1-04 input or output violates an invariant."""


def _settings(config):
    configured = config.data.get('selection', {})
    settings = dict(DEFAULTS)
    settings.update({key: value for key, value in configured.items()
                     if key not in {'score_weights', 'event_weights',
                                    'hardcase_weights', 'event_thresholds',
                                    'selection_level_minimums'}})
    for key in ('score_weights', 'event_weights', 'hardcase_weights',
                'event_thresholds', 'selection_level_minimums'):
        settings[key] = dict(DEFAULTS[key])
        settings[key].update(configured.get(key, {}))
    # Accept the old key for custom configs while keeping the new stage name
    # explicit in current configs.
    if 'candidate_pool_size' not in configured and 'shortlist_size' in configured:
        settings['candidate_pool_size'] = configured['shortlist_size']
    return settings


def analysis_fingerprint(settings):
    """Hash every setting that can change reusable per-clip signals."""
    payload = {
        'algorithm_version': SIGNAL_ALGORITHM_VERSION,
        'sample_frames': int(settings['sample_frames']),
        'analysis_width': int(settings['analysis_width']),
        'keyframes_per_clip': int(settings['keyframes_per_clip']),
        'event_thresholds': settings['event_thresholds'],
        'hardcase_tag_threshold': settings['hardcase_tag_threshold'],
        'event_weights': settings['event_weights'],
        'hardcase_weights': settings['hardcase_weights'],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(encoded).hexdigest()


def _float(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _clip(value):
    return min(1.0, max(0.0, float(value)))


def _fmt(value):
    return f'{_clip(value):.6f}'


def sample_timestamps(duration_sec, frame_count):
    """Return interval-midpoint timestamps, excluding fragile endpoints."""
    duration = float(duration_sec)
    count = int(frame_count)
    if not math.isfinite(duration) or duration <= 0 or count <= 0:
        raise SelectionError('duration and frame_count must be positive')
    return [duration * (index + 0.5) / count for index in range(count)]


def _read_samples(path, duration_sec, count, analysis_width, fps=30.0,
                  source_width=1920, source_height=1080):
    """Decode selected frame numbers in one ffmpeg pass.

    OpenCV remains the image-analysis dependency, but its platform wheel does
    not always include an H.264 backend.  The project already requires ffmpeg,
    so using it here makes decode behavior match B1-02 technical QC.
    """
    ffmpeg = find_media_tool('ffmpeg')
    if not ffmpeg:
        raise SelectionError('ffmpeg is required for deterministic frame sampling')
    timestamps = sample_timestamps(duration_sec, count)
    rate = float(fps)
    if not math.isfinite(rate) or rate <= 0:
        raise SelectionError(f'invalid FPS for sampling: {fps!r}')
    scaled_height = max(2, round(float(source_height) * analysis_width /
                                 float(source_width)))
    # yuv420 output is happier with even dimensions, though raw BGR itself does
    # not require it; preserving this invariant also keeps contact sheets neat.
    scaled_height += scaled_height % 2
    # Decode keyframes only, then let ffmpeg's deterministic fps filter choose
    # the frame nearest each interval midpoint.  This is intentionally an
    # approximate representative sample, not frame-accurate annotation, and is
    # substantially faster than decoding all 3,600 frames in a 120-second clip.
    sample_rate = count / float(duration_sec)
    first_timestamp = timestamps[0]
    command = [
        ffmpeg, '-nostdin', '-v', 'error', '-threads', '1',
        '-skip_frame', 'nokey', '-i', str(path),
        '-vf', (f'fps={sample_rate:.12f}:start_time={first_timestamp:.9f},'
                f'scale={analysis_width}:{scaled_height}'),
        '-frames:v', str(count), '-f', 'rawvideo', '-pix_fmt', 'bgr24',
        'pipe:1',
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    frame_bytes = analysis_width * scaled_height * 3
    actual = len(result.stdout) // frame_bytes
    minimum = min(8, count)
    if result.returncode or actual < minimum or len(result.stdout) % frame_bytes:
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise SelectionError(
            f'ffmpeg decoded {actual}/{count} representative frames; '
            f'minimum={minimum}: {detail}')
    array = np.frombuffer(result.stdout, dtype=np.uint8)
    frames = [frame.copy() for frame in
              array.reshape(actual, scaled_height, analysis_width, 3)]
    return frames, timestamps[:actual]


def _dhash(gray):
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    value = 0
    for bit in bits.flat:
        value = (value << 1) | int(bit)
    return f'{value:016x}'


def _component_stats(mask, minimum_area_ratio=0.0008):
    height, width = mask.shape
    area = height * width
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask)
    objects = []
    for index in range(1, count):
        x, y, w, h, component_area = stats[index]
        ratio = component_area / area
        if ratio < minimum_area_ratio or ratio > 0.35:
            continue
        boundary = x <= 2 or y <= 2 or x + w >= width - 2 or y + h >= height - 2
        objects.append((ratio, boundary))
    return objects


def analyze_frames(frames):
    """Compute deterministic image/motion heuristics from BGR frames."""
    if len(frames) < 2:
        raise SelectionError('at least two decoded frames are required')
    grays = [cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in frames]
    brightness = float(np.mean([np.mean(gray) / 255.0 for gray in grays]))
    laplacian = float(np.mean([cv2.Laplacian(gray, cv2.CV_64F).var()
                               for gray in grays]))
    blur = _clip(1.0 - laplacian / 180.0)

    motion_means, occupancies, counts, small_ratios, boundary_ratios = [], [], [], [], []
    kernel = np.ones((3, 3), np.uint8)
    for left, right in zip(grays, grays[1:]):
        delta = cv2.absdiff(left, right)
        motion_means.append(float(np.mean(delta) / 42.0))
        mask = np.where(delta >= 24, 255, 0).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        occupancies.append(float(np.mean(mask > 0)))
        objects = _component_stats(mask)
        counts.append(len(objects))
        small_ratios.append(
            sum(ratio <= 0.012 for ratio, _ in objects) / max(1, len(objects)))
        boundary_ratios.append(
            sum(boundary for _, boundary in objects) / max(1, len(objects)))

    histograms = []
    for gray in grays:
        hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        histograms.append(hist)
    histogram_distances = [
        cv2.compareHist(left, right, cv2.HISTCMP_BHATTACHARYYA)
        for left, right in zip(histograms, histograms[1:])]
    variation = _clip(float(np.mean(histogram_distances)) * 2.2)
    activity = _clip(float(np.mean(motion_means)))
    motion_variation = _clip(float(np.std(motion_means)) * 2.5)
    occupancy = _clip(float(np.mean(occupancies)) * 2.0)
    persistence = _clip(sum(value >= 0.015 for value in occupancies) /
                        max(1, len(occupancies)))
    mean_count = float(np.mean(counts))
    # Connected motion regions are deliberately not called objects.  A dozen
    # simultaneous regions is a conservative crowded-scene proxy at 320 px.
    crowded = _clip(mean_count / 12.0)
    small_far = _clip(float(np.mean(small_ratios)))
    boundary = _clip(float(np.mean(boundary_ratios)))
    fast_motion = _clip(float(np.percentile(motion_means, 90)))
    low_light = _clip((0.38 - brightness) / 0.30)
    occlusion = _clip(0.55 * crowded + 0.45 * occupancy)
    signature_gray = np.mean(np.stack(grays).astype(np.float32), axis=0).astype(np.uint8)

    return {
        'activity_score': activity,
        'motion_variation_score': motion_variation,
        'brightness_score': brightness,
        'low_light_score': low_light,
        'visual_variation_score': variation,
        'foreground_occupancy_score': occupancy,
        'persistence_score': persistence,
        'object_count_estimate': mean_count,
        'small_far_score': small_far,
        'occlusion_score': occlusion,
        'boundary_edge_score': boundary,
        'blur_score': blur,
        'crowded_score': crowded,
        'fast_motion_score': fast_motion,
        '_visual_signature': _dhash(signature_gray),
    }


def select_keyframe_indices(frames, count):
    """Choose 3-5 review frames for overview, event, hard case and diversity."""
    if not frames:
        raise SelectionError('cannot select keyframes from an empty frame list')
    target = min(len(frames), max(3, min(5, int(count))))
    grays = [cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) for frame in frames]
    signatures = [_dhash(gray) for gray in grays]
    event_scores, hard_scores = [], []
    kernel = np.ones((3, 3), np.uint8)
    for index, gray in enumerate(grays):
        brightness = float(np.mean(gray) / 255.0)
        blur = _clip(1.0 - cv2.Laplacian(gray, cv2.CV_64F).var() / 180.0)
        if index == 0:
            activity = occupancy = boundary = crowded = 0.0
        else:
            delta = cv2.absdiff(grays[index - 1], gray)
            activity = _clip(float(np.mean(delta) / 42.0))
            mask = np.where(delta >= 24, 255, 0).astype(np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
            occupancy = _clip(float(np.mean(mask > 0)) * 2.0)
            objects = _component_stats(mask)
            boundary = _clip(sum(edge for _, edge in objects) /
                             max(1, len(objects)))
            crowded = _clip(len(objects) / 12.0)
        event_scores.append(0.55 * occupancy + 0.45 * activity)
        low_light = _clip((0.38 - brightness) / 0.30)
        hard_scores.append(max(blur, low_light, boundary, crowded))

    chosen = []
    reasons = {}

    def add(index, reason):
        if index not in chosen:
            chosen.append(index)
            reasons[index] = reason
        elif reason not in reasons[index].split('+'):
            reasons[index] += '+' + reason

    add((len(frames) - 1) // 2, 'OVERVIEW')
    add(max(range(len(frames)), key=lambda i: (event_scores[i], -i)),
        'EVENT_LIKELIHOOD')
    add(max(range(len(frames)), key=lambda i: (hard_scores[i], -i)),
        'HARD_CASE')

    while len(chosen) < target:
        remaining = [index for index in range(len(frames)) if index not in chosen]
        if not remaining:
            break

        def novelty(index):
            value = int(signatures[index], 16)
            return min((value ^ int(signatures[other], 16)).bit_count() / 64.0
                       for other in chosen)

        add(max(remaining, key=lambda i: (novelty(i), -i)), 'SCENE_DIVERSITY')

    # Contact sheets are easier to review in timeline order.
    ordered = sorted(chosen)
    return ordered, [reasons[index] for index in ordered]


def score_signals(signals, settings):
    event_values = {
        'foreground_occupancy': signals['foreground_occupancy_score'],
        'activity': signals['activity_score'],
        'persistence': signals['persistence_score'],
        'visual_variation': signals['visual_variation_score'],
    }
    event = sum(settings['event_weights'][name] * value
                for name, value in event_values.items())
    hard_values = {
        'small_far': signals['small_far_score'],
        'occlusion': signals['occlusion_score'],
        'boundary_edge': signals['boundary_edge_score'],
        'blur': signals['blur_score'],
        'crowded': signals['crowded_score'],
        'fast_motion': signals['fast_motion_score'],
        'low_light': signals['low_light_score'],
    }
    hardcase = sum(settings['hardcase_weights'][name] * value
                   for name, value in hard_values.items())
    low_max = settings['event_thresholds']['low_max']
    high_min = settings['event_thresholds']['high_min']
    level = 'LOW' if event < low_max else ('HIGH' if event >= high_min else 'MEDIUM')
    tags = [name.upper() for name, value in hard_values.items()
            if value >= settings['hardcase_tag_threshold']]
    return _clip(event), level, _clip(hardcase), tags


def _analyze_one(config, row, settings, fingerprint):
    path = config.at(row['clip_path'])
    frames, timestamps = _read_samples(
        path, _float(row['duration_sec']), settings['sample_frames'],
        settings['analysis_width'], _float(row.get('fps'), 30.0),
        _float(row.get('width'), 1920), _float(row.get('height'), 1080))
    signals = analyze_frames(frames)
    event, level, hardcase, tags = score_signals(signals, settings)
    keyframe_indices, keyframe_reasons = select_keyframe_indices(
        frames, settings['keyframes_per_clip'])
    result = dict(row)
    result.update({key: _fmt(value) for key, value in signals.items()
                   if not key.startswith('_') and key != 'object_count_estimate'})
    result.update({
        'analysis_sha256': row.get('sha256', ''),
        'analysis_fingerprint': fingerprint,
        'visual_signature': signals['_visual_signature'],
        'keyframe_timestamps': json.dumps(
            [round(timestamps[index], 6) for index in keyframe_indices]),
        'keyframe_reasons': json.dumps(keyframe_reasons),
        'sampled_frame_count': str(len(frames)),
        # Compatibility columns: these are generic motion-region proxies, not
        # detector output.  The notes and observed_classes make that explicit.
        'vehicle_density_score': _fmt(signals['foreground_occupancy_score']),
        'object_count_estimate': f"{signals['object_count_estimate']:.3f}",
        'observed_classes': 'NOT_INFERRED_NO_DETECTOR',
        'event_likelihood_score': _fmt(event),
        'event_likelihood_level': level,
        'hardcase_score': _fmt(hardcase),
        'hardcase_tags': '|'.join(tags),
        'signal_status': 'COMPUTED',
        'prediction_status': 'COMPUTED_SELECTION_SIGNAL_NOT_GROUND_TRUTH',
        'notes': ('OpenCV image/motion heuristics only; vehicle_density_score and '
                  'object_count_estimate are moving-region proxies, not detections; '
                  'event_likelihood is not ground truth violation.'),
    })
    return result


def validate_candidates(config, rows, canonical_rows, settings,
                        verify_checksums=True, progress=None):
    errors = []
    expected = int(settings['expected_candidates'])
    if len(rows) != expected:
        errors.append(f'expected {expected} candidates, found {len(rows)}')
    for field in ('clip_id', 'clip_path'):
        values = [row.get(field, '') for row in rows]
        if '' in values:
            errors.append(f'missing {field}')
        if len(values) != len(set(values)):
            errors.append(f'duplicate {field}')
    sources = {row.get('source_id', '') for row in rows}
    if '' in sources or len(sources) != int(settings['expected_sources']):
        errors.append(
            f'expected {settings["expected_sources"]} non-empty sources, found {sorted(sources)}')
    canonical = {row['clip_id']: row for row in canonical_rows}
    if len(canonical) != len(canonical_rows) or set(canonical) != {
            row.get('clip_id', '') for row in rows}:
        errors.append('candidate/canonical clip identity set differs')
    for index, row in enumerate(rows, 1):
        clip_id = row.get('clip_id', '')
        path = config.at(row.get('clip_path', ''))
        if not path.is_file():
            errors.append(f'{clip_id}: clip does not exist: {row.get("clip_path", "")}')
            continue
        if row.get('qc_status') == 'FAIL':
            errors.append(f'{clip_id}: QC FAIL')
        if row.get('metadata_status') == 'FAIL':
            errors.append(f'{clip_id}: metadata FAIL')
        reference = canonical.get(clip_id)
        if reference:
            for field in IDENTITY_FIELDS:
                if str(row.get(field, '')) != str(reference.get(field, '')):
                    errors.append(f'{clip_id}: canonical {field} changed')
            if verify_checksums:
                digest = sha256_file(path)
                if digest != row.get('sha256'):
                    errors.append(f'{clip_id}: filesystem checksum changed')
        if progress and (index % 25 == 0 or index == len(rows)):
            progress(index, len(rows), len(errors), 'checksum')
    if errors:
        raise SelectionError('; '.join(errors[:20]) +
                             (f'; and {len(errors) - 20} more' if len(errors) > 20 else ''))


def _feature_values(row):
    cached = row.get('_feature_values_cache')
    if cached is not None:
        return cached
    values = tuple([
        _float(row.get('event_likelihood_score')),
        _float(row.get('hardcase_score')),
        _float(row.get('activity_score')),
        _float(row.get('motion_variation_score')),
        _float(row.get('brightness_score')),
        _float(row.get('visual_variation_score')),
        _float(row.get('foreground_occupancy_score')),
    ])
    row['_feature_values_cache'] = values
    return values


def _feature_vector(row):
    return np.asarray(_feature_values(row), dtype=np.float64)


def assign_diversity_and_scores(rows, settings):
    matrix = np.stack([_feature_vector(row) for row in rows])
    median = np.median(matrix, axis=0)
    scale = np.percentile(np.abs(matrix - median), 75, axis=0)
    scale[scale < 0.05] = 0.05
    unusualness = np.mean(np.minimum(np.abs(matrix - median) / (2.5 * scale), 1.0), axis=1)
    weights = settings['score_weights']
    for row, diversity in zip(rows, unusualness):
        row['diversity_score'] = _fmt(diversity)
        score = (
            weights['event_likelihood'] * _float(row['event_likelihood_score']) +
            weights['hardcase'] * _float(row['hardcase_score']) +
            weights['diversity'] * diversity +
            weights['activity'] * _float(row['activity_score']))
        row['candidate_score'] = _fmt(score)
    ordered = sorted(rows, key=lambda row: (-_float(row['candidate_score']), row['clip_id']))
    for rank, row in enumerate(ordered, 1):
        row['global_rank'] = str(rank)
        row['selection_rank'] = str(rank)
    by_source = defaultdict(list)
    for row in rows:
        by_source[row['source_id']].append(row)
    for source_rows in by_source.values():
        source_rows.sort(key=lambda row: (-_float(row['candidate_score']), row['clip_id']))
        for rank, row in enumerate(source_rows, 1):
            row['source_rank'] = str(rank)


def _signature_similarity(left, right):
    try:
        distance = (int(left, 16) ^ int(right, 16)).bit_count()
        return 1.0 - distance / 64.0
    except (TypeError, ValueError):
        return 0.0


def _row_signature(row):
    raw = row.get('visual_signature', '')
    cached = row.get('_signature_int_cache')
    if cached is not None and cached[0] == raw:
        return cached[1]
    try:
        value = int(raw, 16)
    except (TypeError, ValueError):
        value = 0
    row['_signature_int_cache'] = (raw, value)
    return value


def _row_signature_similarity(left, right):
    return 1.0 - (_row_signature(left) ^ _row_signature(right)).bit_count() / 64.0


def _temporal_gap(left, right):
    if left['source_id'] != right['source_id']:
        return math.inf
    left_start, left_end = _float(left['source_start_sec']), _float(left['source_end_sec'])
    right_start, right_end = _float(right['source_start_sec']), _float(right['source_end_sec'])
    return max(0.0, max(left_start, right_start) - min(left_end, right_end))


def _is_redundant(candidate, chosen, settings):
    similarity = _row_signature_similarity(candidate, chosen)
    if candidate['source_id'] != chosen['source_id']:
        return False
    if similarity >= settings['content_similarity_threshold']:
        return True
    return (_temporal_gap(candidate, chosen) <= settings['temporal_adjacency_sec'] and
            similarity >= settings['adjacent_similarity_threshold'])


def _selection_utility(row, selected, settings):
    base = _float(row['candidate_score'])
    if not selected:
        return base
    vector = _feature_values(row)
    distances = [math.sqrt(sum((left - right) ** 2 for left, right in
                               zip(vector, _feature_values(other))) /
                           len(vector)) for other in selected]
    novelty = min(distances)
    similarity = max(_row_signature_similarity(row, other) for other in selected)
    adjacency_penalty = max(
        (0.08 if row['source_id'] == other['source_id'] and
         _temporal_gap(row, other) <= settings['temporal_adjacency_sec'] else 0.0)
        for other in selected)
    return 0.68 * base + 0.24 * novelty + 0.08 * (1.0 - similarity) - adjacency_penalty


def _pick_best(pool, selected, settings, allow_redundant=False):
    candidates = []
    for row in pool:
        if row in selected:
            continue
        redundant = any(_is_redundant(row, other, settings) for other in selected)
        if redundant and not allow_redundant:
            continue
        candidates.append((_selection_utility(row, selected, settings), row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]['clip_id']))
    return candidates[0][1]


def _source_quotas(rows, settings):
    counts = Counter(row['source_id'] for row in rows)
    target = int(settings['candidate_pool_size'])
    minimum = int(settings['source_minimum'])
    maximum = max(minimum, int(target * settings['source_maximum_fraction']))
    quotas = {source: min(count, minimum) for source, count in counts.items()}
    while sum(quotas.values()) < target:
        eligible = [source for source in counts
                    if quotas[source] < min(counts[source], maximum)]
        if not eligible:
            raise SelectionError('source coverage policy cannot fill shortlist')
        source = max(eligible, key=lambda item: (
            math.sqrt(counts[item]) / (quotas[item] + 1), item))
        quotas[source] += 1
    return quotas


def select_shortlist(rows, settings):
    target = int(settings['candidate_pool_size'])
    if not 100 <= target <= 120:
        raise SelectionError('candidate_pool_size must be between 100 and 120')
    complete = [row for row in rows if row.get('signal_status') == 'COMPUTED']
    if len(complete) < target:
        raise SelectionError('not enough successfully analyzed candidates')
    quotas = _source_quotas(complete, settings)
    selected = []

    def reserve(predicate, required):
        required = min(int(required), sum(predicate(row) for row in complete))
        while sum(predicate(row) for row in selected) < required:
            pool = [row for row in complete
                    if predicate(row) and
                    sum(item['source_id'] == row['source_id'] for item in selected) <
                    quotas[row['source_id']]]
            chosen = _pick_best(pool, selected, settings)
            if chosen is None:
                chosen = _pick_best(pool, selected, settings, allow_redundant=True)
            if chosen is None:
                break
            selected.append(chosen)

    # Reserve both challenging and ordinary cases so a high hardcase score
    # cannot crowd normal/easy clips out of the review set.
    reserve(lambda row: bool(row.get('hardcase_tags')),
            settings['selected_hardcase_minimum'])
    reserve(lambda row: not row.get('hardcase_tags'),
            settings['selected_normal_minimum'])

    # Reserve LOW/MEDIUM/HIGH examples before the general score-driven fill.
    for level in ('LOW', 'MEDIUM', 'HIGH'):
        reserve(lambda row, wanted=level:
                row['event_likelihood_level'] == wanted,
                settings['selection_level_minimums'][level])

    # Ensure every source reaches its quota.  A low-score stratum can still be
    # selected because diversity is an explicit acceptance requirement.
    for source in sorted(quotas):
        while sum(row['source_id'] == source for row in selected) < quotas[source]:
            pool = [row for row in complete if row['source_id'] == source]
            chosen = _pick_best(pool, selected, settings)
            if chosen is None:
                chosen = _pick_best(pool, selected, settings, allow_redundant=True)
            if chosen is None:
                raise SelectionError(f'cannot satisfy quota for {source}')
            selected.append(chosen)

    # Normally the exact source quotas fill the target.  Retain a guarded fill
    # for custom configs where level reservations may be larger.
    if len(selected) > target:
        raise SelectionError('configured constraints overfill shortlist')
    while len(selected) < target:
        pool = [row for row in complete
                if sum(item['source_id'] == row['source_id'] for item in selected) <
                quotas[row['source_id']]]
        chosen = _pick_best(pool, selected, settings)
        if chosen is None:
            chosen = _pick_best(pool, selected, settings, allow_redundant=True)
        if chosen is None:
            raise SelectionError('cannot fill shortlist')
        selected.append(chosen)

    selected_ids = {row['clip_id'] for row in selected}
    for row in rows:
        row['recommended_for_shortlist'] = 'YES' if row['clip_id'] in selected_ids else 'NO'
        row['selection_status'] = ('REVIEW_POOL' if row['clip_id'] in selected_ids
                                   else 'NOT_SELECTED')
        row['manual_review_status'] = 'PENDING' if row['clip_id'] in selected_ids else ''
        row['manual_review_label'] = ''
        row['manual_review_notes'] = ''
        if row['clip_id'] in selected_ids:
            tags = row.get('hardcase_tags') or 'NORMAL_CASE'
            row['selection_reason'] = (
                f"source_coverage={row['source_id']}; "
                f"event_likelihood={row['event_likelihood_level']}; "
                f"hardcase={tags}; diversity_aware_rank; "
                'selection_signal_only_not_ground_truth')
        else:
            row['selection_reason'] = ''

    selected.sort(key=lambda row: (-_float(row['candidate_score']), row['clip_id']))
    if len({row['clip_id'] for row in selected}) != len(selected):
        raise SelectionError('duplicate clip selected')
    return selected, quotas


def preserve_manual_reviews(selected, existing_rows):
    """Keep human decisions when an idempotent B1-04 rerun retains a clip."""
    existing = {row.get('clip_id'): row for row in existing_rows
                if row.get('clip_id')}
    for row in selected:
        previous = existing.get(row['clip_id'])
        if not previous:
            continue
        for field in ('manual_review_status', 'manual_review_label',
                      'manual_review_notes'):
            if previous.get(field, ''):
                row[field] = previous[field]
    return selected


def _json_list(value):
    try:
        parsed = json.loads(value or '[]')
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _write_preview(config, row, output_path, settings):
    frames, timestamps = _read_samples(
        config.at(row['clip_path']), _float(row['duration_sec']),
        settings['sample_frames'],
        settings['analysis_width'], _float(row.get('fps'), 30.0),
        _float(row.get('width'), 1920), _float(row.get('height'), 1080))
    wanted_timestamps = [_float(value) for value in
                         _json_list(row.get('keyframe_timestamps'))]
    reasons = [str(value) for value in _json_list(row.get('keyframe_reasons'))]
    if wanted_timestamps:
        indices = []
        for wanted in wanted_timestamps:
            index = min(range(len(timestamps)),
                        key=lambda item: abs(timestamps[item] - wanted))
            if index not in indices:
                indices.append(index)
        reasons = reasons[:len(indices)]
    else:
        indices, reasons = select_keyframe_indices(
            frames, settings['keyframes_per_clip'])
    while len(reasons) < len(indices):
        reasons.append('REPRESENTATIVE')
    tiles = []
    for index, reason in zip(indices, reasons):
        frame, timestamp = frames[index], timestamps[index]
        tile = cv2.copyMakeBorder(frame, 28, 0, 0, 0, cv2.BORDER_CONSTANT,
                                  value=(0, 0, 0))
        cv2.putText(tile, f'{timestamp:.1f}s  {reason}', (8, 19),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1,
                    cv2.LINE_AA)
        tiles.append(tile)
    if tiles:
        blank = np.zeros_like(tiles[0])
        cv2.putText(blank, 'no additional sample', (8, 17),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (160, 160, 160), 1,
                    cv2.LINE_AA)
        while len(tiles) % 3:
            tiles.append(blank.copy())
    sheet = np.vstack([np.hstack(tiles[index:index + 3])
                       for index in range(0, len(tiles), 3)])
    ok, encoded = cv2.imencode('.jpg', sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise SelectionError(f'failed to encode preview for {row["clip_id"]}')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', dir=output_path.parent,
                                     delete=False) as handle:
        handle.write(encoded.tobytes())
        temporary = handle.name
    os.replace(temporary, output_path)


def _candidate_output_fields(rows):
    fields = list(SELECTION_CANDIDATE_FIELDS)
    # persistence is useful for audit but is not part of B1-03's initial schema.
    if any('persistence_score' in row for row in rows):
        insert_at = fields.index('diversity_score')
        fields.insert(insert_at, 'persistence_score')
    return fields


def _cache_entry_matches(row, cached, fingerprint):
    required = ('visual_signature', 'event_likelihood_score', 'hardcase_score',
                'activity_score', 'keyframe_timestamps', 'keyframe_reasons')
    return bool(
        cached and cached.get('signal_status') == 'COMPUTED' and
        cached.get('analysis_sha256') == row.get('sha256') and
        cached.get('analysis_fingerprint') == fingerprint and
        all(cached.get(field, '') != '' for field in required))


def _merge_cached(row, cached):
    merged = dict(row)
    for field in ANALYSIS_RESULT_FIELDS:
        merged[field] = cached.get(field, '')
    return merged


def _clear_selection_state(rows):
    fields = (
        'recommended_for_shortlist', 'selection_status', 'selection_reason',
        'manual_review_status', 'manual_review_label', 'manual_review_notes',
    )
    for row in rows:
        for field in fields:
            row[field] = ''


def run_analysis(config, verify_checksums=True, progress=None):
    """Reuse valid per-clip features, analyze misses, then rerank globally."""
    settings = _settings(config)
    candidate_path = config.at(settings['candidate_path'])
    canonical_path = config.at(settings['canonical_metadata_path'])
    cache_path = config.at(settings['feature_cache_path'])
    rows, canonical_rows = read_csv(candidate_path), read_csv(canonical_path)
    validate_candidates(config, rows, canonical_rows, settings,
                        verify_checksums, progress)

    fingerprint = analysis_fingerprint(settings)
    cache = {row.get('clip_id'): row for row in read_csv(cache_path)
             if row.get('clip_id')}
    analyzed, failures = [], []
    pending = []
    for row in rows:
        cached = cache.get(row['clip_id'])
        if _cache_entry_matches(row, cached, fingerprint):
            analyzed.append(_merge_cached(row, cached))
        else:
            pending.append(row)

    cv2.setNumThreads(1)
    workers = max(1, int(settings['workers']))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_analyze_one, config, row, settings, fingerprint): row
            for row in pending
        }
        for index, future in enumerate(as_completed(futures), 1):
            source = futures[future]
            try:
                analyzed.append(future.result())
            except Exception as error:  # retain an auditable failed row
                failed = dict(source)
                failed.update(
                    signal_status='FAILED',
                    prediction_status='ANALYSIS_FAILED_NO_PREDICTION',
                    notes=f'{type(error).__name__}: {error}')
                analyzed.append(failed)
                failures.append({'clip_id': source['clip_id'], 'error': str(error)})
            if progress and (index % 25 == 0 or index == len(pending)):
                progress(index, len(pending), len(failures), 'analysis')
    analyzed.sort(key=lambda row: row['clip_id'])
    successful = [row for row in analyzed if row['signal_status'] == 'COMPUTED']
    if successful:
        assign_diversity_and_scores(successful, settings)
    _clear_selection_state(analyzed)
    pending_ids = {row['clip_id'] for row in pending}
    fields = _candidate_output_fields(analyzed)
    write_csv(candidate_path, fields, analyzed)
    write_csv(cache_path, fields, analyzed)
    summary = {
        'schema_version': 'B1-03-incremental-v1',
        'selection_signal_disclaimer': 'event_likelihood != ground truth violation',
        'analysis_method': 'OpenCV deterministic image/motion heuristics; no object detector',
        'analysis_fingerprint': fingerprint,
        'total_candidates': len(rows),
        'reused_feature_count': len(rows) - len(pending),
        'new_or_invalidated_feature_count': len(pending),
        'analyzed_this_run': len(pending) - len(failures),
        'successful_candidates': len(successful),
        'failed_analyses': len(failures),
        'analysis_failures': failures,
        'globally_reranked_candidates': len(successful),
        'sample_frames_per_new_candidate': settings['sample_frames'],
        'decoded_frames_this_run': sum(
            int(row.get('sampled_frame_count') or 0)
            for row in analyzed if row['clip_id'] in pending_ids and
            row.get('signal_status') == 'COMPUTED'),
        'cache_path': settings['feature_cache_path'],
        'candidate_path': settings['candidate_path'],
    }
    write_json(config.at(settings['analysis_summary_path']), summary)
    return analyzed, summary


def run_selection(config, verify_checksums=True, create_previews=True,
                  progress=None):
    """Build the B1-04 review pool and render only its smart keyframes."""
    settings = _settings(config)
    candidate_path = config.at(settings['candidate_path'])
    canonical_path = config.at(settings['canonical_metadata_path'])
    output_dir = config.at(settings['output_dir'])
    # The final shortlist is also read as a one-time migration source for runs
    # created before candidate_review_pool.csv existed.
    existing_reviews = (
        read_csv(output_dir / 'selected_shortlist.csv') +
        read_csv(output_dir / 'candidate_review_pool.csv'))
    analyzed, canonical_rows = read_csv(candidate_path), read_csv(canonical_path)
    validate_candidates(config, analyzed, canonical_rows, settings,
                        verify_checksums, progress)
    fingerprint = analysis_fingerprint(settings)
    stale = [row['clip_id'] for row in analyzed
             if row.get('signal_status') == 'COMPUTED' and
             row.get('analysis_fingerprint') != fingerprint]
    if stale:
        raise SelectionError(
            f'{len(stale)} candidates have stale analysis; run B1-03 first')
    successful = [row for row in analyzed if row.get('signal_status') == 'COMPUTED']
    selected, quotas = select_shortlist(analyzed, settings)
    preserve_manual_reviews(selected, existing_reviews)

    selected_ids = {row['clip_id'] for row in selected}
    redundant = set()
    for row in successful:
        if row['clip_id'] in selected_ids:
            continue
        if any(_is_redundant(row, chosen, settings) for chosen in selected):
            redundant.add(row['clip_id'])

    previews = output_dir / 'keyframe_contact_sheets'
    if create_previews:
        candidate_ids = {row['clip_id'] for row in analyzed}
        selected_preview_names = {
            f'{row["clip_id"]}.jpg' for row in selected}
        if previews.is_dir():
            for existing in previews.glob('*.jpg'):
                if (existing.stem in candidate_ids and
                        existing.name not in selected_preview_names):
                    existing.unlink()
        for row in selected:
            _write_preview(config, row, previews / f'{row["clip_id"]}.jpg', settings)

    write_csv(candidate_path, _candidate_output_fields(analyzed), analyzed)
    write_csv(output_dir / 'candidate_review_pool.csv', SHORTLIST_FIELDS, selected)
    review_rows = []
    for row in selected:
        review = {field: row.get(field, '') for field in REVIEW_FIELDS}
        review['preview_path'] = str(
            (Path(settings['output_dir']) / 'keyframe_contact_sheets' /
             f'{row["clip_id"]}.jpg').as_posix()) if create_previews else ''
        review_rows.append(review)
    write_csv(output_dir / 'shortlist_review_index.csv', REVIEW_FIELDS, review_rows)

    selected_levels = Counter(row['event_likelihood_level'] for row in selected)
    selected_sources = Counter(row['source_id'] for row in selected)
    tag_counts = Counter(tag for row in selected
                         for tag in row.get('hardcase_tags', '').split('|') if tag)
    all_levels = Counter(row['event_likelihood_level'] for row in successful)
    summary = {
        'schema_version': 'B1-04-keyframe-review-v2',
        'selection_signal_disclaimer': 'event_likelihood != ground truth violation',
        'analysis_method': 'OpenCV deterministic image/motion heuristics; no object detector',
        'total_candidates': len(analyzed),
        'analyzed_candidates': len(successful),
        'failed_analyses': sum(
            row.get('signal_status') == 'FAILED' for row in analyzed),
        'candidate_pool_size': len(selected),
        'final_shortlist_target': {
            'minimum': int(settings['final_shortlist_min']),
            'maximum': int(settings['final_shortlist_max']),
        },
        'keyframes_per_clip': int(settings['keyframes_per_clip']),
        'full_video_review_required': False,
        'clips_per_source': dict(sorted(selected_sources.items())),
        'source_percentages': {source: round(count * 100 / len(selected), 2)
                               for source, count in sorted(selected_sources.items())},
        'source_quotas': quotas,
        'source_distribution_reason': (
            'sqrt-balanced source quotas with configured minimum and maximum'),
        'candidate_event_likelihood_distribution': {
            level: all_levels.get(level, 0) for level in ('LOW', 'MEDIUM', 'HIGH')},
        'selected_event_likelihood_distribution': {
            level: selected_levels.get(level, 0) for level in ('LOW', 'MEDIUM', 'HIGH')},
        'selected_hardcase_count': sum(bool(row.get('hardcase_tags')) for row in selected),
        'selected_normal_case_count': sum(not row.get('hardcase_tags') for row in selected),
        'selected_hardcase_tag_distribution': dict(sorted(tag_counts.items())),
        'duplicate_redundancy_exclusions': len(redundant),
        'redundant_candidate_ids': sorted(redundant),
        'weights': {
            'candidate_score': settings['score_weights'],
            'event_likelihood': settings['event_weights'],
            'hardcase': settings['hardcase_weights'],
        },
    }
    write_json(output_dir / 'selection_summary.json', summary)
    return analyzed, selected, summary


def finalize_shortlist(config):
    """Validate completed keyframe review and write the 75-90 clip final set."""
    settings = _settings(config)
    output_dir = config.at(settings['output_dir'])
    pool_path = output_dir / 'candidate_review_pool.csv'
    rows = read_csv(pool_path)
    if not rows:
        raise SelectionError(f'candidate review pool does not exist: {pool_path}')

    invalid = []
    kept = []
    for row in rows:
        status = row.get('manual_review_status', '').strip().upper()
        label = row.get('manual_review_label', '').strip().upper()
        if status != 'REVIEWED' or label not in {'KEEP', 'REJECT'}:
            invalid.append(row.get('clip_id', ''))
        elif label == 'KEEP':
            kept.append(row)
    if invalid:
        raise SelectionError(
            f'{len(invalid)} review-pool rows are not REVIEWED with KEEP/REJECT; '
            f'first IDs: {invalid[:10]}')

    minimum = int(settings['final_shortlist_min'])
    maximum = int(settings['final_shortlist_max'])
    if not minimum <= len(kept) <= maximum:
        raise SelectionError(
            f'final KEEP count must be {minimum}-{maximum}, found {len(kept)}')
    kept.sort(key=lambda row: (-_float(row.get('candidate_score')),
                              row.get('clip_id', '')))
    write_csv(output_dir / 'selected_shortlist.csv', SHORTLIST_FIELDS, kept)
    summary = {
        'schema_version': 'B1-04-final-shortlist-v1',
        'review_pool_size': len(rows),
        'selected_shortlist_size': len(kept),
        'rejected_count': len(rows) - len(kept),
        'target_minimum': minimum,
        'target_maximum': maximum,
        'selection_signal_disclaimer': 'review decision != event ground truth',
    }
    write_json(output_dir / 'final_shortlist_summary.json', summary)
    return kept, summary


def run(config, verify_checksums=True, create_previews=True, progress=None):
    """Backward-compatible combined run; new CLIs expose B1-03/B1-04 separately."""
    run_analysis(config, verify_checksums=verify_checksums, progress=progress)
    return run_selection(config, verify_checksums=False,
                         create_previews=create_previews, progress=progress)


def format_summary(summary):
    return json.dumps(summary, ensure_ascii=False, indent=2)
