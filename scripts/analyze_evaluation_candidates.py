import argparse

import _bootstrap

from evaluation_data.models import Config
from evaluation_data.selection import SelectionError, format_summary, run_analysis


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Incrementally compute B1-03 candidate signals, merge the cache, '
            'and rerank the complete candidate table'))
    parser.add_argument(
        '--config', default='configs/evaluation/evaluation_dataset.yaml')
    parser.add_argument(
        '--skip-file-checksums', action='store_true',
        help='Skip filesystem SHA-256 verification (development only)')
    args = parser.parse_args()

    def progress(done, total, failures, stage):
        if stage == 'checksum':
            verb = 'validated' if args.skip_file_checksums else 'verified'
            print(f'{verb} {done}/{total} clips; issues={failures}', flush=True)
        else:
            print(f'analyzed {done}/{total} cache misses; failures={failures}',
                  flush=True)

    try:
        _rows, summary = run_analysis(
            Config(args.config),
            verify_checksums=not args.skip_file_checksums,
            progress=progress)
    except SelectionError as error:
        print(f'B1-03 validation failed: {error}')
        return 1
    print(format_summary(summary))
    return 1 if summary['failed_analyses'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
