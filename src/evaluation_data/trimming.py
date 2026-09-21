"""Create, verify, checkpoint, and safely resume trimmed evaluation clips."""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from pathlib import Path

from .models import CLIPS, read_csv, write_csv
from .checksum import sha256_file
from .validator import probe


def clip_name(source_id, number):
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', source_id)
    return f'{safe}_C{number:03d}.mp4'


def valid_interval(start, end, source_duration, minimum, maximum):
    return (-1e-6 <= start < end <= source_duration + 1e-6 and
            minimum - 1e-6 <= end-start <= maximum + 1e-6)


def _jobs(config):
    from .filtering import selected_sources
    videos = selected_sources(config)
    candidates = sorted(read_csv(config.output('review/clip_candidates.csv')),
                        key=lambda c: (c['source_video_id'], float(c['start_sec']), float(c['end_sec'])))
    counts = {}
    jobs = []
    for candidate in candidates:
        source = candidate['source_video_id']
        counts[source] = counts.get(source, 0) + 1
        if candidate['status'] != 'keep':
            continue
        video = videos.get(source)
        if not video:
            raise ValueError(f'Unknown source_video_id: {source}')
        start, end = float(candidate['start_sec']), float(candidate['end_sec'])
        if not valid_interval(start, end, float(video['duration_sec']),
                              config.data['clip']['min_duration_sec'],
                              config.data['clip']['max_duration_sec']):
            raise ValueError(f'Invalid clip interval: {candidate["clip_candidate_id"]}')
        clip_id = clip_name(source, counts[source]).removesuffix('.mp4')
        jobs.append((clip_id, candidate, video, start, end))
    return jobs


def _matching_info(path, expected_duration):
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    info, reason = probe(path)
    if reason or abs(float(info.get('duration_sec', 0)) - expected_duration) > 1.0:
        return None
    return info


def _row(config, clip_id, video, start, end, path, info, prior=None):
    prior = prior or {}
    return dict(clip_id=clip_id, source_video_id=video['source_video_id'],
                source_start_sec=start, source_end_sec=end,
                duration_sec=info['duration_sec'], local_path=str(path.relative_to(config.root)),
                sha256=sha256_file(path), trim_status='ready',
                camera_id=prior.get('camera_id') or video.get('camera_id', ''),
                view_id=prior.get('view_id', ''),
                session_id=prior.get('session_id') or video.get('session_id', ''))


def reconcile(config):
    """Register complete existing files and report files that need to be regenerated."""
    manifest_path = config.output('clips/clips_manifest.csv')
    existing_rows = read_csv(manifest_path)
    by_id = {row['clip_id']: row for row in existing_rows}
    rows, incomplete = [], []
    jobs = _jobs(config)
    for clip_id, _candidate, video, start, end in jobs:
        path = config.output(f'clips/{clip_id}.mp4')
        prior = by_id.get(clip_id)
        if prior and (float(prior['source_start_sec']) != start or
                      float(prior['source_end_sec']) != end):
            raise ValueError(f'Manifest interval differs for {clip_id}')
        info = _matching_info(path, end-start)
        if info is None:
            if path.exists():
                incomplete.append(clip_id)
            continue
        digest = sha256_file(path)
        if prior and prior.get('sha256') and prior['sha256'] != digest:
            raise ValueError(f'Checksum changed for completed clip: {clip_id}')
        row = _row(config, clip_id, video, start, end, path, info, prior)
        row['sha256'] = digest
        rows.append(row)
    write_csv(manifest_path, CLIPS, rows)
    return rows, incomplete, len(jobs)


def run(config, progress=None):
    manifest_path = config.output('clips/clips_manifest.csv')
    rows, incomplete, total = reconcile(config)
    completed = {row['clip_id']: row for row in rows}
    jobs = _jobs(config)
    for index, (clip_id, _candidate, video, start, end) in enumerate(jobs, 1):
        if clip_id in completed:
            if progress:
                progress(index, total, 'reused', clip_id)
            continue
        path = config.output(f'clips/{clip_id}.mp4')
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f'.{path.stem}.{uuid.uuid4().hex}.tmp.mp4')
        cmd = ['ffmpeg', '-y', '-nostdin', '-hide_banner', '-loglevel', 'error',
               '-ss', str(start), '-i', str(config.at(video['local_path'])),
               '-t', str(end-start), '-map', '0:v:0', '-map', '0:a?',
               '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-c:a', 'aac',
               '-movflags', '+faststart', str(temp)]
        if progress:
            progress(index, total, 'trimming', clip_id)
        try:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            except FileNotFoundError as exc:
                raise RuntimeError('ffmpeg is required for trimming') from exc
            if result.returncode:
                raise RuntimeError(result.stderr[-1000:])
            info = _matching_info(temp, end-start)
            if info is None:
                raise ValueError(f'Trim verification failed: {clip_id}')
            # The prior file remains untouched until the replacement is fully verified.
            os.replace(temp, path)
            row = _row(config, clip_id, video, start, end, path, info)
            completed[clip_id] = row
            ordered = [completed[job[0]] for job in jobs if job[0] in completed]
            write_csv(manifest_path, CLIPS, ordered)
            if progress:
                progress(index, total, 'completed', clip_id)
        finally:
            temp.unlink(missing_ok=True)
    return [completed[job[0]] for job in jobs]
