"""Copy completed videos and catalog to or from a portable backup directory."""

import argparse
from pathlib import Path
import sqlite3

from _bootstrap import ROOT
from data_acquisition.config import load_config
from data_acquisition.transfer import TransferError, backup, restore


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up or restore downloaded videos and catalog")
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("path", type=Path, help="Backup directory, preferably on an external drive")
    parser.add_argument("--config", default=str(ROOT / "configs" / "youtube_sources.yaml"))
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.action == "backup":
            if not config.db_path.is_file():
                raise TransferError(f"Catalog does not exist: {config.db_path}")
            count = backup(config, args.path)
            print(f"Backup complete: {count} videos in {args.path.expanduser().resolve()}")
        else:
            count = restore(config, args.path)
            print(f"Restore complete: {count} videos; catalog paths updated for this machine")
    except (TransferError, OSError, ValueError, sqlite3.DatabaseError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
