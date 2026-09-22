import argparse
import _bootstrap

from evaluation_data.clip_qc import format_summary, run
from evaluation_data.models import Config


def main():
    parser = argparse.ArgumentParser(
        description='Run technical QC and build an inventory for trimmed clips')
    parser.add_argument('--config', default='configs/evaluation/evaluation_dataset.yaml')
    parser.add_argument('--input', help='Trimmed clip directory (defaults to qc.input_dir)')
    parser.add_argument('--output-dir', help='Output directory (defaults to qc.output_dir)')
    parser.add_argument('--full-decode', action='store_true',
                        help='Sequentially decode every frame in addition to three sample frames')
    args = parser.parse_args()
    config = Config(args.config)

    def progress(index, total, clip):
        print(f'[{index}/{total}] {clip["file_name"]}: inspected', flush=True)

    _clips, _issues, summary = run(
        config, input_dir=args.input, output_dir=args.output_dir,
        full_decode=args.full_decode, progress=progress)
    print(format_summary(summary))


if __name__ == '__main__':
    main()
