from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import ItemMetadata, LocalStatus, LiveStatus, JobType, can_transition, utc_now


class Catalog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS youtube_items (
                video_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, channel_name TEXT,
                title TEXT, source_url TEXT NOT NULL, item_type TEXT NOT NULL,
                live_status TEXT NOT NULL, local_status TEXT NOT NULL,
                discovered_at TEXT NOT NULL, scheduled_start TEXT, actual_start TEXT,
                record_started_at TEXT, record_ended_at TEXT,
                duration_sec REAL, local_path TEXT, file_size_bytes INTEGER, sha256 TEXT,
                capture_complete INTEGER, capture_source TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT NOT NULL REFERENCES youtube_items(video_id),
                job_type TEXT NOT NULL, status TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'watcher',
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(video_id) WHERE status IN ('QUEUED','RUNNING');
            CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, available_at);
            CREATE INDEX IF NOT EXISTS items_status ON youtube_items(local_status, live_status);
            """)
            # Existing catalogs created before selective downloads need this column.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "origin" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN origin TEXT NOT NULL DEFAULT 'watcher'")

    def upsert_item(self, item: ItemMetadata) -> dict[str, Any]:
        now = utc_now()
        with self._connect() as db:
            db.execute("""INSERT INTO youtube_items
                (video_id,channel_id,channel_name,title,source_url,item_type,live_status,local_status,
                 discovered_at,scheduled_start,actual_start,duration_sec,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(video_id) DO UPDATE SET
                  channel_name=COALESCE(excluded.channel_name,youtube_items.channel_name),
                  title=COALESCE(excluded.title,youtube_items.title),
                  live_status=CASE WHEN excluded.live_status='unknown' THEN youtube_items.live_status ELSE excluded.live_status END,
                  scheduled_start=COALESCE(excluded.scheduled_start,youtube_items.scheduled_start),
                  actual_start=COALESCE(excluded.actual_start,youtube_items.actual_start),
                  duration_sec=COALESCE(excluded.duration_sec,youtube_items.duration_sec),
                  updated_at=excluded.updated_at""",
                (item.video_id,item.channel_id,item.channel_name,item.title,item.source_url,item.item_type,
                 item.live_status.value,LocalStatus.DISCOVERED.value,now,item.scheduled_start,item.actual_start,
                 item.duration_sec,now,now))
        return self.get_item(item.video_id)  # type: ignore[return-value]

    def get_item(self, video_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM youtube_items WHERE video_id=?", (video_id,)).fetchone()
            return dict(row) if row else None

    def list_items(self, status: str | None = None, live_status: str | None = None) -> list[dict[str, Any]]:
        where, args = [], []
        if status:
            where.append("local_status=?"); args.append(status)
        if live_status:
            where.append("live_status=?"); args.append(live_status)
        sql = "SELECT * FROM youtube_items" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY discovered_at DESC"
        with self._connect() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def update_live_status(self, video_id: str, status: LiveStatus) -> None:
        with self._connect() as db:
            db.execute("UPDATE youtube_items SET live_status=?,updated_at=? WHERE video_id=?", (status.value, utc_now(), video_id))

    def update_local_status(self, video_id: str, status: LocalStatus) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT local_status FROM youtube_items WHERE video_id=?", (video_id,)).fetchone()
            if not row:
                raise KeyError(video_id)
            if not can_transition(LocalStatus(row[0]), status):
                raise ValueError(f"Invalid transition {row[0]} -> {status.value}")
            db.execute("UPDATE youtube_items SET local_status=?,updated_at=? WHERE video_id=?", (status.value,utc_now(),video_id))

    def set_fields(self, video_id: str, **fields: Any) -> None:
        allowed = {"actual_start", "record_started_at", "record_ended_at", "duration_sec", "capture_complete", "capture_source", "last_error", "retry_count"}
        if not fields.keys() <= allowed:
            raise ValueError("Unsupported catalog field")
        if not fields:
            return
        names = list(fields)
        with self._connect() as db:
            db.execute(f"UPDATE youtube_items SET {','.join(n+'=?' for n in names)},updated_at=? WHERE video_id=?",
                       [*(fields[n] for n in names), utc_now(), video_id])

    def mark_failed(self, video_id: str, error: str, retry: bool = False) -> None:
        status = LocalStatus.RETRY if retry else LocalStatus.FAILED
        with self._connect() as db:
            db.execute("UPDATE youtube_items SET local_status=?,retry_count=retry_count+1,last_error=?,updated_at=? WHERE video_id=?",
                       (status.value,error[:2000],utc_now(),video_id))

    def mark_completed(self, video_id: str, path: Path, size: int, sha256: str,
                       duration: float | None, source: str, complete: bool) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT local_status FROM youtube_items WHERE video_id=?", (video_id,)).fetchone()
            if not row or LocalStatus(row[0]) != LocalStatus.VALIDATING:
                raise ValueError("Item must be VALIDATING before completion")
            db.execute("""UPDATE youtube_items SET local_status='COMPLETED',local_path=?,file_size_bytes=?,sha256=?,
                duration_sec=COALESCE(?,duration_sec),capture_source=?,capture_complete=?,last_error=NULL,updated_at=?
                WHERE video_id=?""", (str(path),size,sha256,duration,source,int(complete),utc_now(),video_id))

    def enqueue_job(self, video_id: str, kind: JobType, available_at: str | None = None,
                    origin: str = "watcher") -> bool:
        if origin not in {"watcher", "manual"}:
            raise ValueError("Invalid job origin")
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT local_status FROM youtube_items WHERE video_id=?", (video_id,)).fetchone()
            if not row:
                raise KeyError(video_id)
            active = db.execute("SELECT 1 FROM jobs WHERE video_id=? AND status IN ('QUEUED','RUNNING')", (video_id,)).fetchone()
            if active:
                return False
            old = LocalStatus(row[0])
            if old == LocalStatus.COMPLETED and kind != JobType.REPLAY_RECOVERY:
                return False
            if not can_transition(old, LocalStatus.QUEUED):
                return False
            last = db.execute("SELECT job_type FROM jobs WHERE video_id=? ORDER BY id DESC LIMIT 1", (video_id,)).fetchone()
            if last and last[0] != kind.value:
                db.execute("UPDATE youtube_items SET retry_count=0 WHERE video_id=?", (video_id,))
            db.execute("INSERT INTO jobs(video_id,job_type,status,origin,available_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                       (video_id,kind.value,"QUEUED",origin,available_at or now,now,now))
            db.execute("UPDATE youtube_items SET local_status='QUEUED',updated_at=? WHERE video_id=?", (now,video_id))
            return True

    def claim_job(self, video_id: str | None = None, origin: str | None = None) -> dict[str, Any] | None:
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            query = "SELECT * FROM jobs WHERE status='QUEUED' AND available_at<=?"
            args: list[Any] = [now]
            if video_id is not None:
                query += " AND video_id=?"
                args.append(video_id)
            if origin is not None:
                query += " AND origin=?"
                args.append(origin)
            row = db.execute(query + " ORDER BY id LIMIT 1", args).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='RUNNING',attempts=attempts+1,updated_at=? WHERE id=?", (now,row["id"]))
            return dict(row)

    def cancel_queued_except(self, video_ids: frozenset[str]) -> int:
        """Cancel old queued work outside the current allowlist, retaining item history."""
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id,video_id FROM jobs WHERE status='QUEUED' AND origin='watcher'").fetchall()
            excluded = [(row["id"], row["video_id"]) for row in rows if row["video_id"] not in video_ids]
            for job_id, video_id in excluded:
                db.execute("UPDATE jobs SET status='CANCELLED',updated_at=? WHERE id=?", (now, job_id))
                db.execute("UPDATE youtube_items SET local_status='DISCOVERED',updated_at=? WHERE video_id=? AND local_status='QUEUED'",
                           (now, video_id))
            return len(excluded)

    def finish_job(self, job_id: int, error: str | None = None) -> None:
        with self._connect() as db:
            db.execute("UPDATE jobs SET status=?,last_error=?,updated_at=? WHERE id=?", ("FAILED" if error else "DONE",error,utc_now(),job_id))

    def latest_job(self, video_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE video_id=? ORDER BY id DESC LIMIT 1", (video_id,)).fetchone()
            return dict(row) if row else None

    def recover_interrupted(self, video_id: str | None = None) -> int:
        """Requeue interrupted work globally or for one explicitly selected item."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            suffix = " AND video_id=?" if video_id is not None else ""
            args = (video_id,) if video_id is not None else ()
            rows = db.execute("SELECT DISTINCT video_id FROM jobs WHERE status='RUNNING'" + suffix, args).fetchall()
            now = utc_now()
            db.execute("UPDATE jobs SET status='QUEUED',updated_at=? WHERE status='RUNNING'" + suffix,
                       (now, *args))
            item_suffix = " AND video_id=?" if video_id is not None else ""
            db.execute("""UPDATE youtube_items SET local_status='QUEUED',updated_at=?
                WHERE video_id IN (SELECT video_id FROM jobs WHERE status='QUEUED')
                AND local_status IN ('DOWNLOADING','RECORDING','VALIDATING')""" + item_suffix,
                       (now, *args))
            # A process may have died outside a tracked job.
            db.execute("""UPDATE youtube_items SET local_status='RETRY',updated_at=?
                WHERE local_status IN ('DOWNLOADING','RECORDING','VALIDATING')
                AND video_id NOT IN (SELECT video_id FROM jobs WHERE status IN ('QUEUED','RUNNING'))""" + item_suffix,
                       (now, *args))
            return len(rows)
