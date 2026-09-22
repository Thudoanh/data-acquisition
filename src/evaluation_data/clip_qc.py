"""Technical QC and inventory for trimmed evaluation clips.

This module deliberately performs no semantic video analysis.  It only reads
container metadata, decodes frames, hashes files, and compares P0 timestamps.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .checksum import sha256_file
from .inventory import EXTENSIONS
from .models import read_csv, write_csv


INVENTORY_FIELDS = [
    'clip_id', 'source_id', 'session_id', 'file_path', 'file_name',
    'file_size_mb', 'sha256', 'source_start_sec', 'source_end_sec',
    'duration_sec', 'fps', 'frame_count', 'width', 'height', 'codec',
    'metadata_source', 'decode_ok', 'decode_error_count', 'first_frame_ok',
    'middle_frame_ok', 'last_frame_ok', 'full_decode_ok', 'duplicate_group',
    'overlap_group', 'overlap_status', 'overlap_max_sec',
    'overlap_max_ratio', 'qc_status', 'qc_notes',
]

REPORT_FIELDS = [
    'issue_id', 'clip_id', 'source_id', 'issue_type', 'severity', 'details',
    'related_clip_id', 'action',
]

DEFAULT_QC = {
    'duration_warning_min_sec': 30.0,
    'duration_warning_max_sec': 180.0,
    'fps_relative_tolerance': 0.02,
    'frame_duration_tolerance_sec': 1.0,
    'frame_duration_tolerance_ratio': 0.02,
    'overlap_tolerance_sec': 0.5,
}

_SEVERITY_RANK = {'INFO': 0, 'WARNING': 1, 'FAIL': 2}


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _rate(value):
    """Parse an ffprobe rational such as ``30000/1001``."""
    if value in (None, '', 'N/A'):
        return 0.0
    text = str(value)
    try:
        if '/' in text:
            numerator, denominator = text.split('/', 1)
            return float(numerator) / float(denominator) if float(denominator) else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def find_media_tool(name):
    """Find a media tool on PATH or beside the active virtualenv Python."""
    found = shutil.which(name)
    if found:
        return found
    # Do not resolve sys.executable: venv Python is commonly a symlink to a
    # system interpreter, while ffmpeg/ffprobe are installed beside the
    # symlink inside .venv/bin.
    beside_python = Path(sys.executable).parent / name
    return str(beside_python) if beside_python.is_file() else None


def _opencv_probe(path):
    try:
        import cv2
    except ImportError:
        return {}, 'opencv_unavailable'
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return {}, 'opencv_probe_failed'
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        return {
            'duration_sec': duration,
            'fps': fps,
            'frame_count': frame_count if frame_count > 0 else '',
            'width': int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            'height': int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
            'codec': '',
        }, ''
    finally:
        cap.release()


def probe_video(path, ffprobe_path=None):
    """Return ``(metadata, fallback_reason, source)`` without raising.

    ffprobe is authoritative. OpenCV is an explicit fallback so a missing
    ffprobe produces useful CSV output and a visible warning instead of a
    cryptic process-wide failure.
    """
    fallback_reason = ''
    if ffprobe_path:
        command = [
            ffprobe_path, '-v', 'error', '-show_streams', '-show_format',
            '-of', 'json', str(path),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
                if streams:
                    stream = streams[0]
                    duration = _number(data.get('format', {}).get('duration'))
                    if duration is None:
                        duration = _number(stream.get('duration')) or 0.0
                    count = stream.get('nb_frames')
                    count = int(count) if str(count).isdigit() else ''
                    return {
                        'duration_sec': duration,
                        'fps': _rate(stream.get('avg_frame_rate') or stream.get('r_frame_rate')),
                        'frame_count': count,
                        'width': int(stream.get('width') or 0),
                        'height': int(stream.get('height') or 0),
                        'codec': stream.get('codec_name') or '',
                    }, '', 'ffprobe'
                fallback_reason = 'ffprobe_no_video_stream'
            else:
                fallback_reason = 'ffprobe_failed'
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
            fallback_reason = 'ffprobe_failed'
    else:
        fallback_reason = 'ffprobe_unavailable'

    info, opencv_reason = _opencv_probe(path)
    if opencv_reason:
        reason = '; '.join(value for value in (fallback_reason, opencv_reason) if value)
        return {}, reason, 'unavailable'
    return info, fallback_reason, 'opencv'


def _opencv_decode(path, info, full_decode):
    try:
        import cv2
    except ImportError:
        return {
            'first_frame_ok': False, 'middle_frame_ok': False,
            'last_frame_ok': False, 'full_decode_ok': False if full_decode else '',
            'decode_error_count': 3 + int(full_decode),
            'details': ['ffmpeg and OpenCV are unavailable'], 'fallback': True,
        }
    cap = cv2.VideoCapture(str(path))
    results = []
    details = ['ffmpeg unavailable; OpenCV decode fallback used']
    duration = float(info.get('duration_sec') or 0)
    # Container duration may include audio/timestamp padding beyond the final
    # video frame. One second from the end is still the last portion and avoids
    # false failures caused by seeking into that padding.
    positions = (0.0, duration / 2.0, max(0.0, duration - 1.0))
    try:
        if not cap.isOpened():
            results = [False, False, False]
        else:
            for position in positions:
                cap.set(cv2.CAP_PROP_POS_MSEC, position * 1000.0)
                ok, _frame = cap.read()
                results.append(bool(ok))
    finally:
        cap.release()

    full_ok = ''
    full_errors = 0
    if full_decode:
        cap = cv2.VideoCapture(str(path))
        decoded = 0
        try:
            if cap.isOpened():
                while True:
                    ok, _frame = cap.read()
                    if not ok:
                        break
                    decoded += 1
            expected = _number(info.get('frame_count'))
            full_ok = decoded > 0 and (expected is None or decoded >= max(1, int(expected) - 1))
            full_errors = 0 if full_ok else 1
        finally:
            cap.release()
    return {
        'first_frame_ok': results[0], 'middle_frame_ok': results[1],
        'last_frame_ok': results[2], 'full_decode_ok': full_ok,
        'decode_error_count': sum(not item for item in results) + full_errors,
        'details': details, 'fallback': True,
    }


def decode_video(path, info, ffmpeg_path=None, full_decode=False):
    """Decode three samples and optionally the complete video sequentially."""
    if not ffmpeg_path:
        return _opencv_decode(path, info, full_decode)

    duration = float(info.get('duration_sec') or 0)
    positions = (
        ('first', 0.0),
        ('middle', duration / 2.0),
        ('last', max(0.0, duration - 1.0)),
    )
    flags = {}
    details = []
    errors = 0
    for label, position in positions:
        command = [
            ffmpeg_path, '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-ss', f'{position:.6f}', '-i', str(path), '-map', '0:v:0',
            '-frames:v', '1', '-f', 'framehash', '-',
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            frame_lines = [line for line in result.stdout.splitlines()
                           if line.strip() and not line.startswith('#')]
            ok = result.returncode == 0 and bool(frame_lines)
            if not ok:
                details.append(
                    f'{label}: {(result.stderr or "no decoded frame produced").strip()[-500:]}')
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok = False
            details.append(f'{label}: {type(exc).__name__}')
        flags[f'{label}_frame_ok'] = ok
        errors += int(not ok)

    full_ok = ''
    if full_decode:
        command = [
            ffmpeg_path, '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-i', str(path), '-map', '0:v:0', '-f', 'null', '-',
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
            error_lines = [line for line in result.stderr.splitlines() if line.strip()]
            full_ok = result.returncode == 0 and not error_lines
            errors += len(error_lines) or int(result.returncode != 0)
            if not full_ok:
                details.append('full decode: ' + (' | '.join(error_lines[-3:])[-1000:] or 'failed'))
        except (OSError, subprocess.TimeoutExpired) as exc:
            full_ok = False
            errors += 1
            details.append(f'full decode: {type(exc).__name__}')
    flags.update(full_decode_ok=full_ok, decode_error_count=errors,
                 details=details, fallback=False)
    return flags


def stable_clip_id(path, input_dir):
    """Use the P0 name when recognizable, otherwise hash the relative path."""
    stem = path.stem
    if '_C' in stem and stem.rsplit('_C', 1)[-1].isdigit():
        return stem
    relative = path.relative_to(input_dir).as_posix()
    return 'QC' + hashlib.sha256(relative.encode()).hexdigest()[:16]


def _display_path(path, project_root, input_dir):
    try:
        return path.relative_to(project_root).as_posix()
    except ValueError:
        return path.relative_to(input_dir).as_posix()


def _manifest_indexes(config):
    rows = read_csv(config.output('clips/clips_manifest.csv'))
    by_path = {}
    by_name = defaultdict(list)
    for row in rows:
        local_path = row.get('local_path')
        if local_path:
            try:
                by_path[config.at(local_path)] = row
            except ValueError:
                pass
            by_name[Path(local_path).name].append(row)
    return by_path, by_name


def discover_clips(input_dir):
    return sorted(
        path for path in input_dir.rglob('*')
        if path.is_file() and path.suffix.lower() in EXTENSIONS
    )


def _issue(clip, issue_type, severity, details, action, related=''):
    key = '\0'.join((clip['clip_id'], issue_type, str(related), details))
    return {
        'issue_id': 'I' + hashlib.sha256(key.encode()).hexdigest()[:16],
        'clip_id': clip['clip_id'], 'source_id': clip['source_id'],
        'issue_type': issue_type, 'severity': severity, 'details': details,
        'related_clip_id': related, 'action': action,
    }


def _add_issue(clip, issue_type, severity, details, action, related=''):
    clip['_issues'].append(_issue(clip, issue_type, severity, details, action, related))


def _inspect_one(path, input_dir, config, manifest, ffprobe_path, ffmpeg_path,
                 full_decode, clip_id):
    source_id = (manifest or {}).get('source_video_id', '')
    if not source_id and '_C' in path.stem:
        source_id = path.stem.rsplit('_C', 1)[0]
    clip = {
        'clip_id': (manifest or {}).get('clip_id') or clip_id,
        'source_id': source_id,
        'session_id': (manifest or {}).get('session_id', ''),
        'file_path': _display_path(path, config.root, input_dir),
        'file_name': path.name,
        'file_size_mb': '',
        'sha256': '',
        'source_start_sec': (manifest or {}).get('source_start_sec', ''),
        'source_end_sec': (manifest or {}).get('source_end_sec', ''),
        'duration_sec': '', 'fps': '', 'frame_count': '', 'width': '',
        'height': '', 'codec': '', 'metadata_source': '', 'decode_ok': False,
        'decode_error_count': 0, 'first_frame_ok': False,
        'middle_frame_ok': False, 'last_frame_ok': False,
        'full_decode_ok': '', 'duplicate_group': '', 'overlap_group': '',
        'overlap_status': 'NOT_CHECKED', 'overlap_max_sec': '',
        'overlap_max_ratio': '', 'qc_status': 'PASS', 'qc_notes': '',
        '_issues': [],
    }
    try:
        clip['file_size_mb'] = round(path.stat().st_size / (1024 * 1024), 3)
        clip['sha256'] = sha256_file(path)
        expected_sha = (manifest or {}).get('sha256', '')
        if expected_sha and clip['sha256'] != expected_sha:
            _add_issue(clip, 'CHECKSUM_MISMATCH', 'FAIL',
                       f'current SHA-256 differs from P0 manifest ({expected_sha})',
                       'Keep both the file and manifest unchanged; investigate before any regeneration.')
        info, probe_reason, metadata_source = probe_video(path, ffprobe_path)
        clip['metadata_source'] = metadata_source
        for key in ('duration_sec', 'fps', 'frame_count', 'width', 'height', 'codec'):
            clip[key] = info.get(key, '')
        if not info:
            clip['decode_error_count'] = 3 + int(full_decode)
            clip['full_decode_ok'] = False if full_decode else ''
            _add_issue(clip, 'FILE_CORRUPTED', 'FAIL', probe_reason or 'metadata probe failed',
                       'Keep the file unchanged and inspect or regenerate it from P0.')
            _add_issue(clip, 'DECODE_ERROR', 'FAIL',
                       'sample decoding could not be positioned because video metadata is unavailable',
                       'Keep the file unchanged and review it manually; regenerate from P0 if corrupt.')
            return clip
        if metadata_source != 'ffprobe':
            _add_issue(clip, 'METADATA_FALLBACK', 'WARNING', probe_reason,
                       'Install ffprobe and rerun P1 for authoritative metadata.')

        duration = _number(clip['duration_sec'])
        fps = _number(clip['fps'])
        width = _number(clip['width'])
        height = _number(clip['height'])
        frame_count = _number(clip['frame_count'])
        if duration is None or duration <= 0:
            _add_issue(clip, 'INVALID_DURATION', 'FAIL', f'duration_sec={clip["duration_sec"]!r}',
                       'Inspect the source and regenerate the clip in P0 if necessary.')
        if fps is None or fps <= 0:
            _add_issue(clip, 'INVALID_FPS', 'FAIL', f'fps={clip["fps"]!r}',
                       'Inspect the video stream metadata.')
        if width is None or height is None or width <= 0 or height <= 0:
            _add_issue(clip, 'INVALID_RESOLUTION', 'FAIL',
                       f'width={clip["width"]!r}, height={clip["height"]!r}',
                       'Inspect the video stream metadata.')
        if frame_count is not None and frame_count <= 0:
            _add_issue(clip, 'INVALID_FRAME_COUNT', 'FAIL',
                       f'frame_count={clip["frame_count"]!r}', 'Inspect the video stream metadata.')

        decoded = decode_video(path, info, ffmpeg_path, full_decode)
        for key in ('first_frame_ok', 'middle_frame_ok', 'last_frame_ok',
                    'full_decode_ok', 'decode_error_count'):
            clip[key] = decoded[key]
        clip['decode_ok'] = all(decoded[key] for key in
                                ('first_frame_ok', 'middle_frame_ok', 'last_frame_ok'))
        if full_decode:
            clip['decode_ok'] = clip['decode_ok'] and bool(decoded['full_decode_ok'])
        if decoded['fallback']:
            _add_issue(clip, 'DECODE_FALLBACK', 'WARNING', decoded['details'][0],
                       'Install ffmpeg and rerun P1 for authoritative decode QC.')
        if not clip['decode_ok']:
            details = '; '.join(decoded['details']) or 'one or more decode checks failed'
            _add_issue(clip, 'DECODE_ERROR', 'FAIL', details,
                       'Keep the file unchanged and review it manually; regenerate from P0 if corrupt.')
    except Exception as exc:  # A single damaged or inaccessible file must not stop P1.
        clip['decode_error_count'] = max(int(clip['decode_error_count'] or 0), 3 + int(full_decode))
        clip['full_decode_ok'] = False if full_decode else clip['full_decode_ok']
        _add_issue(clip, 'FILE_CORRUPTED', 'FAIL', f'{type(exc).__name__}: {exc}',
                   'Keep the file unchanged and inspect its filesystem and source.')
    return clip


def _qc_settings(config):
    settings = dict(DEFAULT_QC)
    configured = config.data.get('qc', {})
    settings.update(configured)
    clip = config.data.get('clip', {})
    if 'duration_warning_min_sec' not in configured:
        settings['duration_warning_min_sec'] = clip.get('min_duration_sec', 30)
    if 'duration_warning_max_sec' not in configured:
        settings['duration_warning_max_sec'] = clip.get('max_duration_sec', 180)
    return settings


def apply_metadata_rules(clips, settings):
    """Apply duration, count, FPS, and resolution checks in-place."""
    for clip in clips:
        duration = _number(clip['duration_sec'])
        fps = _number(clip['fps'])
        frame_count = _number(clip['frame_count'])
        if duration and (duration < float(settings['duration_warning_min_sec']) or
                         duration > float(settings['duration_warning_max_sec'])):
            _add_issue(clip, 'DURATION_ANOMALY', 'WARNING',
                       f'duration_sec={duration:.3f} is outside '
                       f'[{settings["duration_warning_min_sec"]}, '
                       f'{settings["duration_warning_max_sec"]}]',
                       'Review the clip boundaries against the P0 manifest.')
        if duration and fps and frame_count is not None and frame_count > 0:
            implied = frame_count / fps
            difference = abs(duration - implied)
            allowed = max(float(settings['frame_duration_tolerance_sec']),
                          duration * float(settings['frame_duration_tolerance_ratio']))
            if difference > allowed:
                _add_issue(clip, 'FRAME_COUNT_MISMATCH', 'WARNING',
                           f'duration={duration:.3f}s, frame_count/fps={implied:.3f}s, '
                           f'difference={difference:.3f}s',
                           'Review container timestamps and frame count with ffprobe.')

    by_source = defaultdict(list)
    for clip in clips:
        if clip['source_id'] and _number(clip['fps']) and _number(clip['width']) and _number(clip['height']):
            by_source[clip['source_id']].append(clip)
    for source_clips in by_source.values():
        fps_counts = Counter(round(float(clip['fps']), 3) for clip in source_clips)
        resolution_counts = Counter((int(clip['width']), int(clip['height'])) for clip in source_clips)
        modal_fps, fps_count = fps_counts.most_common(1)[0]
        modal_resolution, resolution_count = resolution_counts.most_common(1)[0]
        for clip in source_clips:
            fps = float(clip['fps'])
            if fps_count >= 2 and abs(fps - modal_fps) > max(
                    0.1, modal_fps * float(settings['fps_relative_tolerance'])):
                _add_issue(clip, 'FPS_ANOMALY', 'WARNING',
                           f'fps={fps:.3f}; source mode={modal_fps:.3f}',
                           'Verify that this clip belongs to the source and inspect its encoding.')
            resolution = (int(clip['width']), int(clip['height']))
            if resolution_count >= 2 and resolution != modal_resolution:
                _add_issue(clip, 'RESOLUTION_ANOMALY', 'WARNING',
                           f'resolution={resolution[0]}x{resolution[1]}; source mode='
                           f'{modal_resolution[0]}x{modal_resolution[1]}',
                           'Verify that this clip belongs to the source and inspect its encoding.')


def assign_duplicate_groups(clips):
    groups = defaultdict(list)
    for clip in clips:
        if clip.get('sha256'):
            groups[clip['sha256']].append(clip)
    duplicate_sets = [group for group in groups.values() if len(group) > 1]
    duplicate_sets.sort(key=lambda group: group[0]['sha256'])
    for index, group in enumerate(duplicate_sets, 1):
        group_id = f'DUP{index:03d}'
        ordered = sorted(group, key=lambda clip: clip['clip_id'])
        for clip in ordered:
            clip['duplicate_group'] = group_id
            related = next(other['clip_id'] for other in ordered if other is not clip)
            _add_issue(clip, 'EXACT_DUPLICATE', 'WARNING',
                       f'exact SHA-256 duplicate in {group_id}',
                       'Retain the evidence and review whether both manifest entries are intended.', related)
    return len(duplicate_sets)


def calculate_overlap(start_a, end_a, start_b, end_b):
    overlap = max(0.0, min(end_a, end_b) - max(start_a, start_b))
    shorter = min(end_a - start_a, end_b - start_b)
    ratio = overlap / shorter if overlap > 0 and shorter > 0 else 0.0
    return overlap, ratio


def assign_overlap_groups(clips, expected_overlap=0.0, tolerance=0.5):
    """Group timestamp-connected clips and flag only excess/suspicious overlap."""
    checked = []
    by_source = defaultdict(list)
    for clip in clips:
        start, end = _number(clip['source_start_sec']), _number(clip['source_end_sec'])
        if clip['source_id'] and start is not None and end is not None and end > start:
            clip['overlap_status'] = 'CHECKED_NO_OVERLAP'
            checked.append(clip)
            by_source[clip['source_id']].append((clip, start, end))
        else:
            clip['overlap_status'] = 'NOT_CHECKED'

    adjacency = defaultdict(set)
    pair_metrics = {}
    for source_rows in by_source.values():
        source_rows.sort(key=lambda item: (item[1], item[2], item[0]['clip_id']))
        for index, (left, left_start, left_end) in enumerate(source_rows):
            for right, right_start, right_end in source_rows[index + 1:]:
                if right_start >= left_end:  # Boundary contact is not overlap.
                    break
                overlap, ratio = calculate_overlap(left_start, left_end, right_start, right_end)
                if overlap <= 1e-9:
                    continue
                adjacency[left['clip_id']].add(right['clip_id'])
                adjacency[right['clip_id']].add(left['clip_id'])
                pair_metrics[(left['clip_id'], right['clip_id'])] = (overlap, ratio)
                for clip in (left, right):
                    clip['overlap_status'] = 'CHECKED_OVERLAP'
                    clip['overlap_max_sec'] = max(_number(clip['overlap_max_sec']) or 0.0, overlap)
                    clip['overlap_max_ratio'] = max(_number(clip['overlap_max_ratio']) or 0.0, ratio)
                if overlap > float(expected_overlap) + float(tolerance):
                    details = (f'overlap_sec={overlap:.3f}, overlap_ratio={ratio:.6f}; '
                               f'configured expected overlap={float(expected_overlap):.3f}s')
                    _add_issue(left, 'TEMPORAL_OVERLAP', 'WARNING', details,
                               'Review P0 boundaries; do not retrim automatically.', right['clip_id'])
                    _add_issue(right, 'TEMPORAL_OVERLAP', 'WARNING', details,
                               'Review P0 boundaries; do not retrim automatically.', left['clip_id'])
                else:
                    details = (f'overlap_sec={overlap:.3f}, overlap_ratio={ratio:.6f}; '
                               f'within configured expected overlap={float(expected_overlap):.3f}s')
                    _add_issue(left, 'TEMPORAL_OVERLAP', 'INFO', details,
                               'No automatic action; retain for inventory evidence.', right['clip_id'])
                    _add_issue(right, 'TEMPORAL_OVERLAP', 'INFO', details,
                               'No automatic action; retain for inventory evidence.', left['clip_id'])

    by_id = {clip['clip_id']: clip for clip in checked}
    components = []
    unseen = set(adjacency)
    while unseen:
        pending = [min(unseen)]
        component = set()
        while pending:
            clip_id = pending.pop()
            if clip_id in component:
                continue
            component.add(clip_id)
            pending.extend(adjacency[clip_id] - component)
        unseen -= component
        components.append(sorted(component))
    components.sort(key=lambda component: component[0])
    for index, component in enumerate(components, 1):
        group_id = f'OVL{index:03d}'
        for clip_id in component:
            by_id[clip_id]['overlap_group'] = group_id
    return len(components), pair_metrics


def finalize_status(clips):
    issues = []
    for clip in clips:
        issues.extend(clip['_issues'])
        highest = max((_SEVERITY_RANK.get(issue['severity'], 0) for issue in clip['_issues']), default=0)
        clip['qc_status'] = 'FAIL' if highest >= 2 else ('WARNING' if highest == 1 else 'PASS')
        clip['qc_notes'] = '; '.join(
            f'{issue["issue_type"]}: {issue["details"]}'
            for issue in clip['_issues'] if issue['severity'] != 'INFO'
        )
    return sorted(issues, key=lambda issue: (issue['clip_id'], issue['issue_type'],
                                              issue['related_clip_id'], issue['issue_id']))


def _summary(clips, duplicate_groups, overlap_groups):
    counts = Counter(clip['qc_status'] for clip in clips)
    issues = [issue for clip in clips for issue in clip['_issues']]
    issue_counts = Counter(issue['issue_type'] for issue in issues)
    return {
        'total_clips': len(clips), 'pass': counts['PASS'], 'warning': counts['WARNING'],
        'fail': counts['FAIL'], 'decode_failures': issue_counts['DECODE_ERROR'],
        'duration_anomalies': issue_counts['DURATION_ANOMALY'],
        'fps_anomalies': issue_counts['FPS_ANOMALY'],
        'resolution_anomalies': issue_counts['RESOLUTION_ANOMALY'],
        'exact_duplicate_groups': duplicate_groups,
        'temporal_overlap_groups': overlap_groups,
        'total_duration_hours': sum(_number(clip['duration_sec']) or 0 for clip in clips) / 3600,
    }


def run(config, input_dir=None, output_dir=None, full_decode=False, progress=None):
    settings = _qc_settings(config)
    input_dir = Path(input_dir or config.at(
        config.data.get('qc', {}).get('input_dir', 'data/clips'))).resolve()
    output_dir = Path(output_dir or config.at(
        config.data.get('qc', {}).get('output_dir', 'data/processed/qc'))).resolve()
    if not input_dir.is_dir():
        raise ValueError(f'Clip input directory does not exist: {input_dir}')

    paths = discover_clips(input_dir)
    by_path, by_name = _manifest_indexes(config)
    ffprobe_path = find_media_tool('ffprobe')
    ffmpeg_path = find_media_tool('ffmpeg')
    ids = set()
    clips = []
    for index, path in enumerate(paths, 1):
        manifest = by_path.get(path)
        if manifest is None and len(by_name[path.name]) == 1:
            manifest = by_name[path.name][0]
        clip_id = (manifest or {}).get('clip_id') or stable_clip_id(path, input_dir)
        if clip_id in ids:
            relative = path.relative_to(input_dir).as_posix()
            clip_id += '-' + hashlib.sha256(relative.encode()).hexdigest()[:8]
        ids.add(clip_id)
        clip = _inspect_one(path, input_dir, config, manifest, ffprobe_path, ffmpeg_path,
                            full_decode, clip_id)
        clips.append(clip)
        if progress:
            progress(index, len(paths), clip)

    apply_metadata_rules(clips, settings)
    duplicate_groups = assign_duplicate_groups(clips)
    expected_overlap = config.data.get('clip', {}).get('overlap_sec', 0.0)
    overlap_groups, _pair_metrics = assign_overlap_groups(
        clips, expected_overlap, settings['overlap_tolerance_sec'])
    issues = finalize_status(clips)
    write_csv(output_dir / 'clips_inventory.csv', INVENTORY_FIELDS, clips)
    write_csv(output_dir / 'trimming_qc_report.csv', REPORT_FIELDS, issues)
    return clips, issues, _summary(clips, duplicate_groups, overlap_groups)


def format_summary(summary):
    labels = [
        ('Total clips', 'total_clips'), ('PASS', 'pass'), ('WARNING', 'warning'),
        ('FAIL', 'fail'), ('Decode failures', 'decode_failures'),
        ('Duration anomalies', 'duration_anomalies'), ('FPS anomalies', 'fps_anomalies'),
        ('Resolution anomalies', 'resolution_anomalies'),
        ('Exact duplicate groups', 'exact_duplicate_groups'),
        ('Temporal overlap groups', 'temporal_overlap_groups'),
    ]
    lines = [f'{label}: {summary[key]}' for label, key in labels]
    lines.append(f'Total duration hours: {summary["total_duration_hours"]:.3f}')
    return '\n'.join(lines)
