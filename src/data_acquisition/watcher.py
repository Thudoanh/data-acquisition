from __future__ import annotations

import time
from itertools import chain
from typing import Iterator

from .catalog import Catalog
from .config import Config
from .logging_utils import log_for
from .scheduler import Scheduler
from .youtube_client import YouTubeClient
from .models import ItemMetadata, LiveStatus


class Watcher:
    def __init__(self, config: Config):
        self.config = config
        self.catalog = Catalog(config.db_path)
        self.client = YouTubeClient(config.max_attempts, config.retry_delay_sec)
        self.scheduler = Scheduler(self.catalog,config)

    def apply_selection(self) -> int:
        if self.config.selection_mode != "allowlist":
            return 0
        allowed = set(self.config.selected_video_ids)
        channels = {channel.id: channel for channel in self.config.channels if channel.enabled}
        for row in self.catalog.list_items():
            channel = channels.get(row["channel_id"])
            if channel and channel.selects(row["video_id"], row["title"], LiveStatus(row["live_status"])):
                allowed.add(row["video_id"])
        cancelled = self.catalog.cancel_queued_except(frozenset(allowed))
        if cancelled:
            log_for(action="SELECTION_UPDATED").info("Cancelled %d unselected queued jobs", cancelled)
        return cancelled

    def _selected_items(self, channel) -> Iterator[ItemMetadata]:
        for video_id in channel.video_ids:
            try:
                yield self.client.get_video(video_id, channel, attempts=1)
            except Exception as exc:
                log_for(video_id,channel.id,"METADATA_FETCH_FAILED").warning("%s",exc)

    def scan(self, channel_id: str | None = None, dispatch: bool = True,
             limit: int | None = None) -> int:
        self.apply_selection()
        count = 0
        for channel in self.config.channels:
            if not channel.enabled or (channel_id and channel.id != channel_id):
                continue
            log = log_for(channel_id=channel.id,action="CHANNEL_SCAN")
            try:
                if self.config.selection_mode == "all":
                    source = self.client.scan_channel(channel)
                else:
                    source = chain(self._selected_items(channel),
                                   self.client.scan_channel(channel, keywords_only=True)
                                   if channel.search_keywords else ())
                seen: set[str] = set()
                for item in source:
                    try:
                        if item.video_id in seen:
                            continue
                        seen.add(item.video_id)
                        if self.config.selection_mode == "allowlist" and not channel.selects(
                                item.video_id, item.title, item.live_status):
                            continue
                        self.catalog.upsert_item(item)
                        self.scheduler.schedule(item.video_id)
                        if dispatch:
                            self.scheduler.dispatch()
                        count += 1
                        if limit is not None and count >= limit:
                            break
                    except Exception as exc:
                        log_for(item.video_id,channel.id,"ITEM_ERROR").error("%s",exc)
            except Exception as exc:
                log.error("%s",exc)
            else:
                log.info("Scanned %d items",count)
        if dispatch:
            self.scheduler.dispatch()
        return count

    def run(self) -> None:
        self.catalog.recover_interrupted()
        self.apply_selection()
        # Explicit IDs can resume immediately. Keyword items enter through the fresh scan;
        # already queued keyword jobs are retained by apply_selection and dispatched below.
        channels = {channel.id: channel for channel in self.config.channels if channel.enabled}
        for row in self.catalog.list_items():
            channel = channels.get(row["channel_id"])
            if channel and (self.config.selection_mode == "all" or row["video_id"] in channel.video_ids):
                self.scheduler.schedule(row["video_id"])
        self.scheduler.dispatch()
        try:
            while True:
                self.scan()
                time.sleep(self.config.poll_interval_sec)
        except KeyboardInterrupt:
            log_for(action="SHUTDOWN").info("Waiting for active workers")
        finally:
            self.scheduler.shutdown()
