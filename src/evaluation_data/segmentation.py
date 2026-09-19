"""Deterministic interval generation for metadata-filtered raw videos."""
from __future__ import annotations

import hashlib
import math

from .models import CANDIDATES, read_csv, write_csv


def intervals(duration: float, minimum: float, maximum: float, target: float, overlap: float) -> list[tuple[float, float]]:
    """Cover a video with valid clips and avoid a too-short final tail."""
    values = (duration, minimum, maximum, target, overlap)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError('Segment settings and duration must be finite')
    if not 0 < minimum <= target <= maximum or overlap < 0 or target - overlap < .001:
        raise ValueError('Require 0 < min <= target <= max and 0 <= overlap < target')
    if duration < minimum:
        return []
    duration = math.floor(duration * 1000) / 1000
    if duration <= maximum:
        return [(0.0, round(duration, 3))]

    result = []
    start = 0.0
    while start < duration:
        end = min(duration, start + target)
        next_start = end - overlap
        if end == duration:
            result.append((round(start, 3), round(end, 3)))
            break
        if duration - next_start < minimum and duration - start <= maximum:
            result.append((round(start, 3), round(duration, 3)))
            break
        result.append((round(start, 3), round(end, 3)))
        start = next_start
    return result


def candidate_id(source_video_id: str, start: float, end: float) -> str:
    key = f'{source_video_id}:{start:.3f}:{end:.3f}'
    return 'A' + hashlib.sha256(key.encode()).hexdigest()[:16]


def run(config):
    specs = config.data['clip']
    minimum = float(specs['min_duration_sec'])
    maximum = float(specs['max_duration_sec'])
    target = float(specs.get('target_duration_sec', 120))
    overlap = float(specs.get('overlap_sec', 10))
    # Validate the policy even when the catalog is empty.
    intervals(minimum, minimum, maximum, target, overlap)

    catalog_path = config.output('inventory/video_catalog.csv')
    validation_path = config.output('validation/raw_video_validation.csv')
    review_path = config.output('review/review_candidates.csv')
    for path in (catalog_path, validation_path):
        if not path.is_file():
            raise FileNotFoundError(f'Run inventory and raw validation first: {path}')
    # Refresh metadata flags so an existing review CSV from an older version
    # cannot leave duplicate source encodings eligible. Manual decisions persist.
    from .filtering import run as refresh_filter
    refresh_filter(config)
    videos = {row['video_id']: row for row in read_csv(catalog_path)}
    validation = {row['video_id']: row for row in read_csv(validation_path)}
    review = read_csv(review_path)
    output = config.output('review/clip_candidates.csv')
    existing = {row['clip_candidate_id']: row for row in read_csv(output)}
    rows = []
    eligible_sources = set()
    for entry in review:
        video = videos.get(entry['video_id'])
        if not video:
            raise ValueError(f"Review entry references unknown video: {entry['video_id']}")
        source_id = video['source_video_id']
        if entry['review_reason'] or entry['review_status'] == 'reject':
            continue
        if validation.get(entry['video_id'], {}).get('status') != 'valid':
            continue
        if source_id in eligible_sources:
            raise ValueError(f'Multiple eligible files for source_video_id {source_id}; rerun filter_review_candidates.py')
        duration = float(video['duration_sec'])
        eligible_sources.add(source_id)
        for start, end in intervals(duration, minimum, maximum, target, overlap):
            ident = candidate_id(source_id, start, end)
            old = existing.get(ident, {})
            rows.append(dict(clip_candidate_id=ident, source_video_id=source_id,
                             start_sec=start, end_sec=end, duration_sec=round(end-start, 3),
                             scenario_hint=old.get('scenario_hint', ''),
                             status=old.get('status', 'pending'), note=old.get('note', '')))
    # A rerun must not discard a reviewed clip when the segmentation policy changes.
    generated = {row['clip_candidate_id'] for row in rows}
    rows.extend(row for ident, row in existing.items() if ident not in generated and row['status'] == 'keep' and row['source_video_id'] in eligible_sources)
    rows.sort(key=lambda row: (row['source_video_id'], float(row['start_sec']), float(row['end_sec'])))
    write_csv(output, CANDIDATES, rows)
    return rows
