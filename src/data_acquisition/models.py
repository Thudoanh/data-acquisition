from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
import re
import unicodedata
from urllib.parse import parse_qs, urlparse


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def video_id_from_reference(reference: str) -> str:
    """Accept a YouTube video ID or canonical/share/live/shorts URL."""
    value = reference.strip()
    if value.startswith("https://"):
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower()
        if host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            if parsed.path == "/watch":
                value = parse_qs(parsed.query).get("v", [""])[0]
            elif parsed.path.startswith(("/live/", "/shorts/")):
                value = parsed.path.split("/")[2]
            else:
                raise ValueError("URL must point to a YouTube video")
        elif host == "youtu.be":
            value = parsed.path.strip("/")
        else:
            raise ValueError("URL must be from YouTube")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise ValueError("Expected a YouTube video ID (11 characters) or video URL")
    return value


def normalize_title(text: str) -> str:
    value = unicodedata.normalize("NFKD", text.casefold()).replace("đ", "d")
    return "".join(char for char in value if not unicodedata.combining(char))


class LiveStatus(str, Enum):
    UPCOMING = "is_upcoming"
    LIVE = "is_live"
    ENDED = "was_live"
    VOD = "not_live"
    UNKNOWN = "unknown"


class LocalStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    WAITING = "WAITING"
    QUEUED = "QUEUED"
    DOWNLOADING = "DOWNLOADING"
    RECORDING = "RECORDING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY = "RETRY"


class JobType(str, Enum):
    VOD_DOWNLOAD = "VOD_DOWNLOAD"
    LIVE_RECORD = "LIVE_RECORD"
    REPLAY_RECOVERY = "REPLAY_RECOVERY"


ALLOWED_TRANSITIONS: dict[LocalStatus, set[LocalStatus]] = {
    LocalStatus.DISCOVERED: {LocalStatus.WAITING, LocalStatus.QUEUED, LocalStatus.FAILED},
    LocalStatus.WAITING: {LocalStatus.QUEUED, LocalStatus.RECORDING, LocalStatus.RETRY, LocalStatus.FAILED},
    LocalStatus.QUEUED: {LocalStatus.DOWNLOADING, LocalStatus.RECORDING, LocalStatus.RETRY, LocalStatus.FAILED},
    LocalStatus.DOWNLOADING: {LocalStatus.VALIDATING, LocalStatus.RETRY, LocalStatus.FAILED},
    LocalStatus.RECORDING: {LocalStatus.VALIDATING, LocalStatus.RETRY, LocalStatus.FAILED},
    LocalStatus.VALIDATING: {LocalStatus.COMPLETED, LocalStatus.RETRY, LocalStatus.FAILED},
    LocalStatus.COMPLETED: {LocalStatus.QUEUED},  # verified replay replacement
    LocalStatus.FAILED: {LocalStatus.RETRY, LocalStatus.QUEUED},
    LocalStatus.RETRY: {LocalStatus.QUEUED, LocalStatus.WAITING, LocalStatus.FAILED},
}


def can_transition(old: LocalStatus, new: LocalStatus) -> bool:
    return old == new or new in ALLOWED_TRANSITIONS[old]


@dataclass(frozen=True)
class Channel:
    id: str
    url: str
    enabled: bool = True
    video_ids: tuple[str, ...] = ()
    title_keywords: tuple[str, ...] = ()
    live_keywords: tuple[str, ...] = ()
    vod_keywords: tuple[str, ...] = ()

    @property
    def search_keywords(self) -> tuple[str, ...]:
        return self.title_keywords + self.live_keywords + self.vod_keywords

    def title_may_match(self, title: str | None) -> bool:
        if not title:
            return False
        folded = normalize_title(title)
        return any(normalize_title(keyword) in folded for keyword in self.search_keywords)

    def selects(self, video_id: str, title: str | None, live_status: LiveStatus) -> bool:
        if video_id in self.video_ids:
            return True
        if not title:
            return False
        folded = normalize_title(title)
        if any(normalize_title(keyword) in folded for keyword in self.title_keywords):
            return True
        if live_status in {LiveStatus.UPCOMING, LiveStatus.LIVE, LiveStatus.ENDED}:
            return any(normalize_title(keyword) in folded for keyword in self.live_keywords)
        if live_status == LiveStatus.VOD:
            return any(normalize_title(keyword) in folded for keyword in self.vod_keywords)
        return False


@dataclass(frozen=True)
class ItemMetadata:
    video_id: str
    channel_id: str
    channel_name: str | None
    title: str | None
    source_url: str
    item_type: str
    live_status: LiveStatus
    scheduled_start: str | None = None
    actual_start: str | None = None
    duration_sec: float | None = None
