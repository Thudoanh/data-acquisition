from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime, timedelta, timezone
import threading
import time

from .catalog import Catalog
from .config import Config
from .downloader import process_job
from .logging_utils import log_for
from .models import JobType, LocalStatus, LiveStatus


def desired_job(row: dict, config: Config) -> JobType | None:
    live = LiveStatus(row["live_status"])
    local = LocalStatus(row["local_status"])
    if local in (LocalStatus.QUEUED, LocalStatus.DOWNLOADING, LocalStatus.RECORDING, LocalStatus.VALIDATING):
        return None
    if live == LiveStatus.UPCOMING:
        return None
    if live == LiveStatus.LIVE:
        return None if local == LocalStatus.COMPLETED else JobType.LIVE_RECORD
    if live == LiveStatus.ENDED:
        if config.recovery_from_replay and row["capture_complete"] == 0:
            return JobType.REPLAY_RECOVERY
        if local == LocalStatus.COMPLETED:
            return None
        return JobType.REPLAY_RECOVERY if config.recovery_from_replay else JobType.VOD_DOWNLOAD
    if live == LiveStatus.VOD and local != LocalStatus.COMPLETED:
        return JobType.VOD_DOWNLOAD
    return None


class Scheduler:
    def __init__(self, catalog: Catalog, config: Config, max_workers: int = 4):
        self.catalog = catalog
        self.config = config
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="acquisition")
        self.futures: set[Future] = set()
        self.max_workers = max_workers
        self.cancel = threading.Event()

    def schedule(self, video_id: str, origin: str = "watcher") -> bool:
        row = self.catalog.get_item(video_id)
        if row is None:
            return False
        if row["live_status"] == LiveStatus.UPCOMING.value and row["local_status"] == LocalStatus.DISCOVERED.value:
            self.catalog.update_local_status(video_id, LocalStatus.WAITING)
            return False
        kind = desired_job(row, self.config)
        if kind is None:
            return False
        # Exhausted jobs remain FAILED until a later status change or manual retry.
        if row["local_status"] == LocalStatus.FAILED.value:
            last = self.catalog.latest_job(video_id)
            if kind != JobType.REPLAY_RECOVERY or (last and last["job_type"] == kind.value):
                return False
        queued = self.catalog.enqueue_job(video_id, kind, origin=origin)
        if queued:
            log_for(video_id,row["channel_id"],"JOB_QUEUED").info(kind.value)
        return queued

    def dispatch(self) -> None:
        self.futures = {f for f in self.futures if not f.done()}
        while len(self.futures) < self.max_workers:
            job = self.catalog.claim_job(origin="watcher")
            if job is None:
                break
            self.futures.add(self.pool.submit(self._run, job))

    def _run(self, job: dict) -> None:
        video_id = job["video_id"]
        row = self.catalog.get_item(video_id)
        channel_id = row["channel_id"] if row else "-"
        log = log_for(video_id,channel_id,job["job_type"])
        log.info("Started")
        try:
            process_job(self.catalog,self.config,job,self.cancel)
        except Exception as exc:
            error = str(exc)
            retry = bool(row and row["retry_count"] + 1 < self.config.max_attempts)
            self.catalog.mark_failed(video_id,error,retry=retry)
            self.catalog.finish_job(job["id"],error)
            if retry:
                at = (datetime.now(timezone.utc) + timedelta(seconds=self.config.retry_delay_sec)).isoformat(timespec="seconds")
                self.catalog.enqueue_job(video_id,JobType(job["job_type"]),at,
                                         origin=job.get("origin", "watcher"))
            log.error("Failed: %s",error)
        else:
            self.catalog.finish_job(job["id"])
            log.info("Completed")

    def run_selected(self, video_id: str) -> dict:
        """Run only the requested item's queued job, including configured retries."""
        while True:
            job = self.catalog.claim_job(video_id)
            if job:
                self._run(job)
            row = self.catalog.get_item(video_id)
            if row is None:
                raise KeyError(video_id)
            if row["local_status"] in {LocalStatus.COMPLETED.value, LocalStatus.FAILED.value,
                                       LocalStatus.WAITING.value}:
                return row
            latest = self.catalog.latest_job(video_id)
            if not latest or latest["status"] != "QUEUED":
                return row
            due = datetime.fromisoformat(latest["available_at"])
            time.sleep(max(0, (due - datetime.now(timezone.utc)).total_seconds()))

    def shutdown(self, cancel: bool = True) -> None:
        if cancel:
            self.cancel.set()
        self.pool.shutdown(wait=True)
