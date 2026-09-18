from __future__ import annotations

import json
import subprocess
from pathlib import Path


def validate_video(path: Path) -> float | None:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError(f"Missing or empty media: {path}")
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0:
        raise ValueError(f"ffprobe failed: {result.stderr[-500:]}")
    info = json.loads(result.stdout)
    if not any(s.get("codec_type") == "video" for s in info.get("streams", [])):
        raise ValueError("No video stream")
    raw = info.get("format", {}).get("duration")
    if raw not in (None, "N/A"):
        duration = float(raw)
        if duration <= 0:
            raise ValueError("Invalid duration")
        return duration
    return None
