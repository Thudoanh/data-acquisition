import argparse
from _bootstrap import ROOT
from data_acquisition.catalog import Catalog

def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize SQLite catalog")
    parser.add_argument("--db", default=str(ROOT / "state" / "catalog.db"))
    args = parser.parse_args()
    catalog = Catalog(__import__("pathlib").Path(args.db))
    print(catalog.path)

if __name__ == "__main__":
    main()
