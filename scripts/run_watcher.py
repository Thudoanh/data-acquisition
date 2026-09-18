import argparse
from _bootstrap import ROOT
from data_acquisition.config import load_config
from data_acquisition.logging_utils import configure_logging
from data_acquisition.watcher import Watcher

def main() -> None:
    parser = argparse.ArgumentParser(description="Continuously scan configured channels and dispatch jobs")
    parser.add_argument("--config", default=str(ROOT / "configs" / "youtube_sources.yaml"))
    args = parser.parse_args()
    configure_logging()
    Watcher(load_config(args.config)).run()

if __name__ == "__main__":
    main()
