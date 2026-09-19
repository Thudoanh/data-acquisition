import json
import subprocess
from pathlib import Path
from .models import read_csv, write_csv, VALIDATION
from .checksum import sha256_file

def probe(path):
    try:
        result = subprocess.run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)], capture_output=True,text=True,timeout=120)
        if result.returncode:
            return {}, 'ffprobe_failed'
        data = json.loads(result.stdout)
        streams = [s for s in data.get('streams',[]) if s.get('codec_type') == 'video']
        if not streams:
            return {}, 'no_video_stream'
        s = streams[0]
        fps_raw = s.get('avg_frame_rate') or s.get('r_frame_rate') or '0/1'
        num, den = (fps_raw.split('/') + ['1'])[:2]
        fps = float(num)/float(den) if float(den) else 0
        duration = float(data.get('format',{}).get('duration') or s.get('duration') or 0)
        return dict(duration_sec=duration, fps=fps, width=int(s.get('width') or 0), height=int(s.get('height') or 0),codec=s.get('codec_name') or ''), ''
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}, 'ffprobe_failed'

def validate_file(path, cfg, expected_sha=None):
    if not path.is_file(): return 'invalid','missing_file',{}
    info, reason = probe(path)
    if reason: return 'invalid',reason,info
    if info['duration_sec'] <= 0: return 'invalid','invalid_duration',info
    if info['fps'] <= 0 or info['fps'] < cfg['min_fps']: return 'invalid','invalid_fps',info
    if expected_sha and sha256_file(path) != expected_sha: return 'invalid','checksum_mismatch',info
    if info['width'] < cfg['min_width'] or info['height'] < cfg['min_height']: return 'warning','resolution_too_small',info
    return 'valid','',info

def run(config):
    rows=[]
    for video in read_csv(config.output('inventory/video_catalog.csv')):
        status, reason, _ = validate_file(config.at(video['local_path']),config.data['validation'],video['sha256'])
        rows.append(dict(video_id=video['video_id'],status=status,reason=reason))
    write_csv(config.output('validation/raw_video_validation.csv'),VALIDATION,rows)
    return rows
