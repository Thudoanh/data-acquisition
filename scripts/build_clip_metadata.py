import argparse

import _bootstrap

from evaluation_data.clip_metadata import format_summary, run
from evaluation_data.models import Config


def main():
    parser = argparse.ArgumentParser(
        description='Build and validate canonical B1-03 clip metadata')
    parser.add_argument(
        '--config', default='configs/evaluation/evaluation_dataset.yaml')
    parser.add_argument(
        '--skip-file-checksums', action='store_true',
        help='Skip filesystem SHA-256 recomputation (intended only for fast local diagnostics)')
    args = parser.parse_args()
    _rows, _issues, summary = run(
        Config(args.config), verify_checksums=not args.skip_file_checksums)
    print(format_summary(summary))
    return 1 if summary['metadata_status_counts']['FAIL'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
