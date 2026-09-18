from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from .catalog import Catalog
from .config import Config
from .models import JobType, LocalStatus, utc_now
from .validator import validate_video
from .checksum import sha256_file
from .recorder import capture_is_complete


def run_ytdlp(url: str, directory: Path, config: Config, live: bool,
              cancel: threading.Event | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "source.%(ext)s"
    height = config.max_height
    # Prefer H.264/AAC so the MP4 also opens in QuickTime. Keep the existing
    # formats as fallbacks for streams that do not offer H.264.
    fmt = (f"bestvideo[height<={height}][vcodec^=avc1][ext=mp4]+"
           f"bestaudio[acodec^=mp4a][ext=m4a]/"
           f"best[height<={height}][vcodec^=avc1][acodec^=mp4a][ext=mp4]/"
           f"bestvideo[height<={height}][ext=mp4]+bestaudio[ext=m4a]/"
           f"best[height<={height}][ext=mp4]/best[height<={height}]")
    cmd = ["yt-dlp", "--no-config", "--no-warnings", "--no-progress", "--no-playlist",
           "--format", fmt, "--merge-output-format", "mp4", "--remux-video", "mp4",
           "--output", str(output), "--retries", "3", "--fragment-retries", "3"]
    if live and config.try_from_start:
        cmd.append("--live-from-start")
    cmd.append(url)
    log_path = directory / "yt-dlp.log"
    def invoke(args: list[str]) -> int:
        with log_path.open("ab") as log:
            proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
            while proc.poll() is None:
                if cancel and cancel.wait(1):
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    raise RuntimeError("Worker interrupted; retry on next startup")
                if not cancel:
                    import time
                    time.sleep(1)
            return proc.returncode

    code = invoke(cmd)
    if code and live and config.try_from_start and not (cancel and cancel.is_set()):
        # Some live streams do not expose a from-start format. Retain fragments for audit.
        partial = directory.parent / "partial"
        partial.mkdir(exist_ok=True)
        for candidate in directory.iterdir():
            if candidate != log_path and candidate.is_file():
                os.replace(candidate, partial / f"from-start-{candidate.name}")
        code = invoke([arg for arg in cmd if arg != "--live-from-start"])
    if code:
        tail = log_path.read_bytes()[-2000:].decode("utf-8", "replace")
        raise RuntimeError(f"yt-dlp exited {code}: {tail}")
    media = directory / "source.mp4"
    if not media.is_file():
        raise RuntimeError("yt-dlp finished without source.mp4")
    return media


def _write_metadata(path: Path, row: dict) -> None:
    metadata = {key: row.get(key) for key in (
        "video_id", "channel_id", "channel_name", "title", "source_url", "item_type",
        "live_status", "local_status", "discovered_at", "scheduled_start",
        "capture_source", "actual_start", "record_started_at", "record_ended_at",
        "capture_complete", "duration_sec", "file_size_bytes", "sha256")}
    metadata["capture_complete"] = bool(metadata["capture_complete"])
    metadata["collected_at"] = utc_now()
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def process_job(catalog: Catalog, config: Config, job: dict,
                cancel: threading.Event | None = None) -> None:
    video_id = job["video_id"]
    row = catalog.get_item(video_id)
    if row is None:
        raise KeyError(video_id)
    kind = JobType(job["job_type"])
    live = kind == JobType.LIVE_RECORD
    catalog.update_local_status(video_id, LocalStatus.RECORDING if live else LocalStatus.DOWNLOADING)
    start = utc_now()
    if live:
        catalog.set_fields(video_id, record_started_at=start)
    directory = config.output_root / row["channel_id"] / video_id
    staging = directory / "staging"
    if staging.exists():
        if live:
            partial = directory / "partial"
            partial.mkdir(exist_ok=True)
            for candidate in staging.iterdir():
                if candidate.is_file():
                    os.replace(candidate, partial / f"interrupted-{utc_now().replace(':','')}-{candidate.name}")
        import shutil
        shutil.rmtree(staging)
    media = run_ytdlp(row["source_url"], staging, config, live, cancel)
    if live:
        catalog.set_fields(video_id, record_ended_at=utc_now())
    catalog.update_local_status(video_id, LocalStatus.VALIDATING)
    duration = validate_video(media)
    digest = sha256_file(media)
    size = media.stat().st_size
    final = directory / "source.mp4"
    if final.exists():
        partial = directory / "partial"
        partial.mkdir(exist_ok=True)
        os.replace(final, partial / f"source-{utc_now().replace(':','')}.mp4")
    os.replace(media, final)
    complete = capture_is_complete(start, row.get("actual_start")) if live else True
    source = "live_record" if live else ("replay_recovery" if kind == JobType.REPLAY_RECOVERY else "vod_download")
    final_row = dict(row, local_status=LocalStatus.COMPLETED.value, local_path=str(final),
                     file_size_bytes=size, sha256=digest, duration_sec=duration,
                     capture_source=source, capture_complete=int(complete))
    _write_metadata(directory / "metadata.json", final_row)
    catalog.mark_completed(video_id, final, size, digest, duration, source, complete)
    if kind == JobType.REPLAY_RECOVERY:
        import shutil
        shutil.rmtree(directory / "partial", ignore_errors=True)
