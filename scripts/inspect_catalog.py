import argparse
from _bootstrap import ROOT
from data_acquisition.catalog import Catalog

def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect acquisition catalog")
    parser.add_argument("--db", default=str(ROOT / "state" / "catalog.db"))
    parser.add_argument("--status")
    parser.add_argument("--live-status")
    args = parser.parse_args()
    catalog = Catalog(__import__("pathlib").Path(args.db))
    for row in catalog.list_items(args.status,args.live_status):
        print(f"{row['video_id']}\t{row['channel_id']}\t{row['live_status']}\t{row['local_status']}\t{row['title'] or ''}")

if __name__ == "__main__":
    main()
