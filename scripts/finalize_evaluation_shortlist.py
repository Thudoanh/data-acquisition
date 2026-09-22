import argparse

import _bootstrap

from evaluation_data.models import Config
from evaluation_data.selection import (
    SelectionError, finalize_shortlist, format_summary,
)


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Validate completed B1-04 keyframe review and write the final '
            '75-90 clip selected_shortlist.csv'))
    parser.add_argument(
        '--config', default='configs/evaluation/evaluation_dataset.yaml')
    args = parser.parse_args()
    try:
        _rows, summary = finalize_shortlist(Config(args.config))
    except SelectionError as error:
        print(f'B1-04 finalization failed: {error}')
        return 1
    print(format_summary(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
