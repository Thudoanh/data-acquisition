import argparse
from _bootstrap import ROOT
from data_acquisition.config import load_config
from data_acquisition.logging_utils import configure_logging
from data_acquisition.watcher import Watcher

def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or scan one configured channel")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--config", default=str(ROOT / "configs" / "youtube_sources.yaml"))
    parser.add_argument("--limit", type=int, help="Stop after this many items (useful for metadata smoke tests)")
    parser.add_argument("--discover", action="store_true", help="List recent video IDs without queueing downloads")
    args = parser.parse_args()
    configure_logging()
    config = load_config(args.config)
    if args.channel not in {c.id for c in config.channels if c.enabled}:
        parser.error("Channel not found or disabled")
    watcher = Watcher(config)
    try:
        if args.discover:
            channel = next(c for c in config.channels if c.id == args.channel)
            for index, item in enumerate(watcher.client.scan_channel(channel), start=1):
                print(f"{item.video_id}\t{item.live_status.value}\t{item.title or ''}")
                if args.limit and index >= args.limit:
                    break
        else:
            print(f"Scanned {watcher.scan(args.channel, dispatch=False, limit=args.limit)} selected items; no media downloaded")
    finally:
        watcher.scheduler.shutdown()

if __name__ == "__main__":
    main()
