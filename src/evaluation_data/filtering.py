"""Metadata-only source selection and review candidate preparation."""
from .models import read_csv, write_csv, REVIEW


def _rank(video, validation):
    """Prefer a valid acquisition original, then a stable path as tie-breaker."""
    return (validation.get(video['video_id'], {}).get('status') != 'valid',
            video['filename'] != 'source.mp4', video['local_path'])


def run(config):
    videos = read_csv(config.output('inventory/video_catalog.csv'))
    validation = {r['video_id']: r for r in read_csv(config.output('validation/raw_video_validation.csv'))}
    preferred_source = {}
    for video in videos:
        source_id = video['source_video_id']
        current = preferred_source.get(source_id)
        if current is None or _rank(video, validation) < _rank(current, validation):
            preferred_source[source_id] = video
    preferred_checksum = {}
    for video in preferred_source.values():
        digest = video['sha256']
        current = preferred_checksum.get(digest)
        if digest and (current is None or _rank(video, validation) < _rank(current, validation)):
            preferred_checksum[digest] = video

    rows = []
    for video in videos:
        reasons = []
        if validation.get(video['video_id'], {}).get('status') != 'valid':
            reasons.append('invalid_video')
        try:
            if float(video['duration_sec']) < config.data['clip']['min_duration_sec']:
                reasons.append('too_short')
            if (int(video['width']) < config.data['validation']['min_width'] or
                    int(video['height']) < config.data['validation']['min_height']):
                reasons.append('unsupported_resolution')
        except (ValueError, TypeError):
            pass
        if preferred_source[video['source_video_id']]['video_id'] != video['video_id']:
            reasons.append('duplicate_source_video_id')
        digest = video['sha256']
        if digest and digest in preferred_checksum and preferred_checksum[digest]['video_id'] != video['video_id']:
            reasons.append('duplicate_checksum')
        rows.append(dict(candidate_id=video['video_id'], video_id=video['video_id'],
                         source_video_id=video['source_video_id'], local_path=video['local_path'],
                         review_status='pending', review_reason=';'.join(reasons), review_note=''))
    path = config.output('review/review_candidates.csv')
    previous = {r['candidate_id']: r for r in read_csv(path)}
    for row in rows:
        old = previous.get(row['candidate_id'])
        if old:
            row['review_status'] = old['review_status']
            row['review_note'] = old['review_note']
    write_csv(path, REVIEW, rows)
    return rows


def selected_sources(config):
    """Return the one eligible inventory row for each source video ID."""
    videos = {r['video_id']: r for r in read_csv(config.output('inventory/video_catalog.csv'))}
    validation = {r['video_id']: r for r in read_csv(config.output('validation/raw_video_validation.csv'))}
    selected = {}
    for entry in read_csv(config.output('review/review_candidates.csv')):
        if entry['review_reason'] or entry['review_status'] == 'reject':
            continue
        video = videos.get(entry['video_id'])
        if video is None or validation.get(entry['video_id'], {}).get('status') != 'valid':
            continue
        source_id = video['source_video_id']
        if source_id in selected:
            raise ValueError(f'Multiple eligible files for source_video_id {source_id}; rerun filter_review_candidates.py')
        selected[source_id] = video
    return selected
