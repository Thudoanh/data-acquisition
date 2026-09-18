from __future__ import annotations

import time
import re
from datetime import datetime, timezone
from typing import Any, Iterator

from .models import Channel, ItemMetadata, LiveStatus
from .logging_utils import log_for


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, (float, int)):
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds")


def normalize(info: dict[str, Any], channel: Channel) -> ItemMetadata:
    video_id = str(info.get("id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", video_id):
        raise ValueError("Metadata has invalid video id")
    raw_status = info.get("live_status") or "unknown"
    status = LiveStatus(raw_status) if raw_status in LiveStatus._value2member_map_ else LiveStatus.UNKNOWN
    return ItemMetadata(video_id, channel.id, info.get("channel") or info.get("uploader"),
                        info.get("title"), f"https://www.youtube.com/watch?v={video_id}",
                        "live" if status in (LiveStatus.LIVE, LiveStatus.UPCOMING, LiveStatus.ENDED) else "video",
                        status, _timestamp(info.get("release_timestamp")),
                        _timestamp(info.get("start_time") or info.get("release_timestamp") if status == LiveStatus.LIVE else info.get("start_time")),
                        info.get("duration"))


class _QuietLogger:
    def debug(self, msg: str) -> None: pass
    def warning(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass


class YouTubeClient:
    def __init__(self, max_attempts: int, delay_sec: int):
        self.max_attempts = max_attempts
        self.delay_sec = delay_sec

    def _extract(self, url: str, flat: bool, attempts: int | None = None) -> dict[str, Any]:
        import yt_dlp
        error: Exception | None = None
        total = attempts or self.max_attempts
        for attempt in range(total):
            try:
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "logger": _QuietLogger(),
                                       "skip_download": True, "extract_flat": flat, "ignoreerrors": False,
                                       "noplaylist": not flat, "socket_timeout": 15,
                                       "playlistend": 50 if flat else None}) as ydl:
                    info = ydl.extract_info(url, download=False)
                if not isinstance(info, dict):
                    raise ValueError("No metadata returned")
                return info
            except Exception as exc:
                error = exc
                if attempt + 1 < total:
                    time.sleep(self.delay_sec)
        raise RuntimeError(f"Metadata fetch failed for {url}: {error}") from error

    def scan_channel(self, channel: Channel, keywords_only: bool = False) -> Iterator[ItemMetadata]:
        # YouTube separates uploads and live/upcoming streams into channel tabs.
        seen: set[str] = set()
        for tab in ("streams", "videos"):
            try:
                info = self._extract(channel.url.rstrip("/") + "/" + tab, flat=True)
            except Exception as exc:
                log_for(channel_id=channel.id,action="CHANNEL_TAB_FAILED").error("%s: %s",tab,exc)
                continue
            for entry in info.get("entries") or []:
                if not isinstance(entry, dict) or not entry.get("id"):
                    continue
                if keywords_only and not channel.title_may_match(entry.get("title")):
                    continue
                video_id = str(entry["id"])
                if video_id in seen:
                    continue
                seen.add(video_id)
                try:
                    # One attempt per poll; repeated polls provide bounded retries without
                    # one inaccessible item stalling the entire channel for minutes.
                    yield normalize(self._extract(f"https://www.youtube.com/watch?v={video_id}", flat=False,
                                                  attempts=1), channel)
                except Exception as exc:
                    log_for(video_id,channel.id,"METADATA_FETCH_FAILED").warning("%s",exc)
                    # Flat entries may include private/member-only videos. Do not queue them.
                    continue

    def get_video(self, video_id: str, channel: Channel, attempts: int | None = None) -> ItemMetadata:
        return normalize(self._extract(f"https://www.youtube.com/watch?v={video_id}", flat=False,
                                       attempts=attempts), channel)
