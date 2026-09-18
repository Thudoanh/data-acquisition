from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import yaml

from .models import Channel, video_id_from_reference


@dataclass(frozen=True)
class Config:
    path: Path
    poll_interval_sec: int
    output_root: Path
    max_height: int
    merge_format: str
    try_from_start: bool
    recovery_from_replay: bool
    max_attempts: int
    retry_delay_sec: int
    channels: tuple[Channel, ...]
    selection_mode: str = "allowlist"

    @property
    def selected_video_ids(self) -> frozenset[str]:
        return frozenset(video_id for channel in self.channels if channel.enabled
                         for video_id in channel.video_ids)

    @property
    def project_root(self) -> Path:
        return self.path.parent.parent

    @property
    def db_path(self) -> Path:
        return self.project_root / "state" / "catalog.db"


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, dict):
        raise ValueError("Config must be a YAML mapping")
    def keywords(value: object, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(not isinstance(term, str) or not term.strip() for term in value):
            raise ValueError(f"{field} must be a list of nonempty strings")
        return tuple(term.strip() for term in value)
    try:
        channels_raw = raw["channels"]
        if not isinstance(channels_raw, list) or not channels_raw:
            raise ValueError("channels must be a nonempty list")
        channels = tuple(Channel(str(c["id"]), str(c["url"]), c.get("enabled", True),
                                 tuple(video_id_from_reference(str(ref)) for ref in c.get("video_ids", [])),
                                 keywords(c.get("title_keywords", []), "title_keywords"),
                                 keywords(c.get("live_keywords", []), "live_keywords"),
                                 keywords(c.get("vod_keywords", []), "vod_keywords"))
                         for c in channels_raw)
        if len({c.id for c in channels}) != len(channels):
            raise ValueError("Duplicate channel id")
        selected = [video_id for c in channels for video_id in c.video_ids]
        if len(selected) != len(set(selected)):
            raise ValueError("Duplicate video ID in channel selections")
        for c in channels:
            if not c.id or not c.url.startswith("https://www.youtube.com/") or not c.id.replace("-", "").replace("_", "").isalnum():
                raise ValueError(f"Invalid channel: {c.id}")
        output = Path(raw["output"]["root_dir"])
        if output.is_absolute():
            raise ValueError("output.root_dir must be relative to project root")
        config = Config(path, int(raw["poll_interval_sec"]), (path.parent.parent / output).resolve(),
                        int(raw["download"]["max_height"]), str(raw["download"]["merge_format"]),
                        bool(raw["live"]["try_from_start"]), bool(raw["live"]["recovery_from_replay"]),
                        int(raw["retry"]["max_attempts"]), int(raw["retry"]["retry_delay_sec"]),
                        channels, str(raw.get("selection", {}).get("mode", "allowlist")))
        if config.selection_mode not in {"allowlist", "all"}:
            raise ValueError("selection.mode must be allowlist or all")
        if min(config.poll_interval_sec, config.max_height, config.max_attempts) < 1 or config.retry_delay_sec < 0:
            raise ValueError("Intervals, height and attempts must be positive")
        if config.merge_format != "mp4":
            raise ValueError("MVP supports merge_format: mp4")
        if not config.output_root.is_relative_to(config.project_root):
            raise ValueError("output.root_dir escapes project root")
        return config
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Missing or invalid config field: {exc}") from exc
