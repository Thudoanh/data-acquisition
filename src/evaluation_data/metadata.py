import json
import sqlite3
from pathlib import Path

def acquisition_metadata(config):
    """Read acquisition catalog without modifying it; sidecar metadata works without SQLite."""
    by_path = {}
    db = config.at(config.data['input'].get('acquisition_catalog', 'state/catalog.db'))
    if db.is_file():
        with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute('SELECT * FROM youtube_items WHERE local_path IS NOT NULL')
                by_path = {str(Path(row['local_path']).resolve()): dict(row) for row in rows}
            except sqlite3.DatabaseError:
                pass
    return by_path

def for_file(path, catalog):
    result = {}
    sidecar = path.parent / 'metadata.json'
    if sidecar.is_file():
        try:
            result.update(json.loads(sidecar.read_text(encoding='utf-8')))
        except (ValueError, OSError):
            pass
    result.update({k:v for k,v in catalog.get(str(path.resolve()), {}).items() if v is not None})
    return result
