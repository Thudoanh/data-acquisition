import argparse
from pathlib import Path
import sys

from _bootstrap import ROOT
from data_acquisition.catalog import Catalog
from data_acquisition.config import Config, load_config
from data_acquisition.logging_utils import configure_logging
from data_acquisition.models import LocalStatus, video_id_from_reference
from data_acquisition.scheduler import Scheduler
from data_acquisition.youtube_client import YouTubeClient


def read_list(path: Path) -> list[str]:
    """Read one video ID/URL per line; comments and empty lines are ignored."""
    return [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def download_one(video_id: str, channel_hint: str | None, config: Config,
                 catalog: Catalog, client: YouTubeClient, scheduler: Scheduler) -> tuple[str, str]:
    existing = catalog.get_item(video_id)
    channel_id = channel_hint or (existing["channel_id"] if existing else None)
    if channel_id is None:
        enabled = [c for c in config.channels if c.enabled]
        if len(enabled) == 1:
            channel_id = enabled[0].id
        else:
            raise ValueError("Use --channel for a new video when multiple channels are configured")
    channel = next((c for c in config.channels if c.id == channel_id), None)
    if channel is None or (existing and existing["channel_id"] != channel.id):
        raise ValueError("Channel is not configured or does not match the catalog item")
    if existing and existing["local_status"] == "COMPLETED" and existing["capture_complete"]:
        return "SKIPPED", f"Already completed: {existing['local_path']}"

    catalog.upsert_item(client.get_video(video_id, channel))
    if catalog.get_item(video_id)["local_status"] == LocalStatus.FAILED.value:
        catalog.update_local_status(video_id, LocalStatus.RETRY)
        catalog.set_fields(video_id, retry_count=0)
    scheduler.schedule(video_id, origin="manual")
    row = scheduler.run_selected(video_id)
    if row["local_status"] == "COMPLETED":
        return "COMPLETED", f"Completed: {row['local_path']}"
    if row["local_status"] == "WAITING":
        return "WAITING", "Livestream is upcoming; add its ID to video_ids and run run_watcher.py"
    raise RuntimeError(f"Video not completed: {row['local_status']} ({row['last_error'] or 'another worker may own it'})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download one video or a text list of YouTube URLs/IDs")
    parser.add_argument("video", nargs="?", help="YouTube watch/live/shorts URL or 11-character video ID")
    parser.add_argument("--file", type=Path, help="UTF-8 text file with one YouTube URL or ID per line")
    parser.add_argument("--channel", help="Configured channel ID; needed for new videos if multiple channels exist")
    parser.add_argument("--recover-interrupted", action="store_true",
                        help="Requeue a stale RUNNING job for only the requested video(s)")
    parser.add_argument("--config", default=str(ROOT / "configs" / "youtube_sources.yaml"))
    args = parser.parse_args()
    if (args.video is None) == (args.file is None):
        parser.error("Provide one video or --file, but not both")
    try:
        references = read_list(args.file) if args.file else [args.video]
    except OSError as exc:
        parser.error(f"Cannot read list: {exc}")
    if not references:
        parser.error("The video list is empty")

    configure_logging()
    config = load_config(args.config)
    catalog = Catalog(config.db_path)
    client = YouTubeClient(config.max_attempts, config.retry_delay_sec)
    scheduler = Scheduler(catalog, config, max_workers=1)
    seen: set[str] = set()
    completed = skipped = waiting = failed = 0
    try:
        for reference in references:
            try:
                video_id = video_id_from_reference(reference)
                if video_id in seen:
                    skipped += 1
                    print(f"SKIPPED {video_id}: duplicate in list")
                    continue
                seen.add(video_id)
                if args.recover_interrupted:
                    recovered = catalog.recover_interrupted(video_id)
                    if recovered:
                        print(f"RECOVERED {video_id}: requeued {recovered} interrupted job")
                result, message = download_one(video_id, args.channel, config, catalog, client, scheduler)
                print(f"{result} {video_id}: {message}")
                if result == "COMPLETED":
                    completed += 1
                elif result == "WAITING":
                    waiting += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                print(f"FAILED {reference}: {exc}", file=sys.stderr)
    finally:
        scheduler.shutdown(cancel=False)
    print(f"Summary: completed={completed}, already/duplicate={skipped}, waiting={waiting}, failed={failed}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
