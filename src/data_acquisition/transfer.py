"""Portable backups for completed downloads and the SQLite catalog."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from .config import Config


class TransferError(ValueError):
    pass


def _completed(db: sqlite3.Connection) -> list[tuple[str, str, int, str]]:
    rows = db.execute("""SELECT channel_id, video_id, file_size_bytes, sha256
                         FROM youtube_items WHERE local_status='COMPLETED'""").fetchall()
    result = []
    for channel_id, video_id, size, digest in rows:
        if (not isinstance(channel_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", channel_id)
                or not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", video_id)
                or size is None or not digest):
            raise TransferError(f"Invalid completed item in catalog: {video_id}")
        result.append((channel_id, video_id, size, digest))
    return result


def _copy_checked(source: Path, target: Path, size: int, digest: str) -> None:
    if not source.is_file():
        raise TransferError(f"Missing video: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    hasher = hashlib.sha256()
    count = 0
    try:
        with source.open("rb") as src, temporary.open("wb") as dst:
            while chunk := src.read(1024 * 1024):
                dst.write(chunk)
                hasher.update(chunk)
                count += len(chunk)
        if count != size or hasher.hexdigest() != digest:
            raise TransferError(f"Video differs from catalog: {source}")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def backup(config: Config, destination: Path) -> int:
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise TransferError(f"Backup destination already exists: {destination}")
    if destination.is_relative_to(config.project_root):
        raise TransferError("Choose a backup destination outside the project")
    with closing(sqlite3.connect(config.db_path)) as source_db:
        running = source_db.execute("SELECT COUNT(*) FROM jobs WHERE status='RUNNING'").fetchone()[0]
        if running:
            raise TransferError("Downloads are running; wait for them to finish before backing up")
        items = _completed(source_db)
        destination.mkdir(parents=True)
        try:
            with closing(sqlite3.connect(destination / "catalog.db")) as backup_db:
                source_db.backup(backup_db)
                backup_db.execute("PRAGMA journal_mode=DELETE")
            for channel_id, video_id, size, digest in items:
                source_dir = config.output_root / channel_id / video_id
                target_dir = destination / "youtube" / channel_id / video_id
                _copy_checked(source_dir / "source.mp4", target_dir / "source.mp4", size, digest)
                metadata = source_dir / "metadata.json"
                if not metadata.is_file():
                    raise TransferError(f"Missing metadata: {metadata}")
                shutil.copy2(metadata, target_dir / "metadata.json")
            (destination / "backup.json").write_text(
                json.dumps({"format": 1, "completed_videos": len(items)}, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(destination)
            raise
    return len(items)


def restore(config: Config, source: Path) -> int:
    source = source.expanduser().resolve()
    manifest = source / "backup.json"
    if not manifest.is_file() or not (source / "catalog.db").is_file():
        raise TransferError(f"Not a transfer backup: {source}")
    try:
        version = json.loads(manifest.read_text(encoding="utf-8"))["format"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise TransferError("Invalid backup manifest") from exc
    if version != 1:
        raise TransferError(f"Unsupported backup format: {version}")
    if config.db_path.exists():
        raise TransferError(f"Catalog already exists: {config.db_path}; restore into a fresh checkout")
    if config.output_root.exists() and any(p.name != ".gitkeep" for p in config.output_root.iterdir()):
        raise TransferError(f"Video directory is not empty: {config.output_root}; restore into a fresh checkout")
    with closing(sqlite3.connect(source / "catalog.db")) as source_db:
        items = _completed(source_db)
    if source.is_relative_to(config.project_root):
        raise TransferError("Backup must be outside the project")
    config.output_root.mkdir(parents=True, exist_ok=True)
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_db = config.db_path.with_name("catalog.restore.tmp")
    copied_dirs: list[Path] = []
    try:
        shutil.copy2(source / "catalog.db", temp_db)
        with closing(sqlite3.connect(temp_db)) as db:
            db.execute("PRAGMA journal_mode=DELETE")
            with db:
                for channel_id, video_id, size, digest in items:
                    source_dir = source / "youtube" / channel_id / video_id
                    target_dir = config.output_root / channel_id / video_id
                    copied_dirs.append(target_dir)
                    _copy_checked(source_dir / "source.mp4", target_dir / "source.mp4", size, digest)
                    metadata = source_dir / "metadata.json"
                    if not metadata.is_file():
                        raise TransferError(f"Missing metadata: {metadata}")
                    shutil.copy2(metadata, target_dir / "metadata.json")
                    db.execute("UPDATE youtube_items SET local_path=? WHERE video_id=?",
                               (str(target_dir / "source.mp4"), video_id))
        os.replace(temp_db, config.db_path)
    except Exception:
        temp_db.unlink(missing_ok=True)
        for directory in copied_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    return len(items)
