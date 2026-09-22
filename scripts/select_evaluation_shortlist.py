import argparse

import _bootstrap

from evaluation_data.models import Config
from evaluation_data.selection import (
    SelectionError, format_summary, run_selection,
)


def main():
    parser = argparse.ArgumentParser(
        description='Build the B1-04 review pool and smart keyframe contact sheets')
    parser.add_argument(
        '--config', default='configs/evaluation/evaluation_dataset.yaml')
    parser.add_argument(
        '--skip-file-checksums', action='store_true',
        help='Skip filesystem SHA-256 verification (development only)')
    parser.add_argument(
        '--no-previews', action='store_true',
        help='Do not render contact-sheet previews')
    args = parser.parse_args()

    def progress(done, total, failures, stage):
        verb = 'verified' if not args.skip_file_checksums else 'validated'
        print(f'{verb} {done}/{total} clips; issues={failures}', flush=True)

    try:
        _rows, _selected, summary = run_selection(
            Config(args.config),
            verify_checksums=not args.skip_file_checksums,
            create_previews=not args.no_previews,
            progress=progress)
    except SelectionError as error:
        print(f'B1-04 validation failed: {error}')
        return 1
    print(format_summary(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
