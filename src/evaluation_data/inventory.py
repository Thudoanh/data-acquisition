import hashlib
from pathlib import Path
from .models import INVENTORY, write_csv
from .metadata import acquisition_metadata, for_file
from .validator import probe
from .checksum import sha256_file

EXTENSIONS = {'.mp4', '.mkv', '.mov', '.avi', '.webm', '.ts'}

def stable_id(source, source_video_id, relative):
    key = f'{source}:{source_video_id}:{relative.as_posix()}'
    return 'V' + hashlib.sha256(key.encode()).hexdigest()[:16]

def run(config):
    catalog = acquisition_metadata(config)
    rows, seen = [], set()
    for root_name in config.data['input']['raw_video_roots']:
        root = config.at(root_name)
        source = root.name
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in EXTENSIONS):
            meta = for_file(path, catalog)
            source_id = str(meta.get('video_id') or meta.get('source_video_id') or (path.parent.name if source == 'youtube' and path.name.startswith('source.') else path.stem))
            relative = path.relative_to(root)
            video_id = stable_id(source, source_id, relative)
            if video_id in seen:
                raise ValueError(f'Duplicate inventory ID: {video_id}')
            seen.add(video_id)
            info, reason = probe(path)
            rows.append(dict(video_id=video_id, source=source, source_video_id=source_id,
                source_url=meta.get('source_url') or '', channel_id=meta.get('channel_id') or '',
                camera_id='', session_id='', filename=path.name, local_path=str(path.relative_to(config.root)),
                duration_sec=info.get('duration_sec', meta.get('duration_sec') or ''), fps=info.get('fps',''),
                width=info.get('width',''), height=info.get('height',''), codec=info.get('codec',''),
                file_size_bytes=path.stat().st_size, sha256=sha256_file(path),
                recorded_at=meta.get('actual_start') or meta.get('record_started_at') or meta.get('collected_at') or '',
                inventory_status='probed' if not reason else reason))
    write_csv(config.output('inventory/video_catalog.csv'), INVENTORY, rows)
    return rows
