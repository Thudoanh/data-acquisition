import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.trimming import reconcile, run
p = argparse.ArgumentParser(description='Trim kept intervals with ffmpeg; safely resumes completed clips')
p.add_argument('--config', default='configs/evaluation/evaluation_dataset.yaml')
p.add_argument('--reconcile-only', action='store_true', help='Rebuild manifest from valid existing clips without trimming')
if __name__ == '__main__':
    args = p.parse_args()
    config = Config(args.config)
    if args.reconcile_only:
        rows, incomplete, total = reconcile(config)
        print(f'{len(rows)}/{total} complete; {len(incomplete)} incomplete existing files')
        for clip_id in incomplete:
            print(f'INCOMPLETE {clip_id}')
    else:
        def progress(index, total, action, clip_id):
            print(f'[{index}/{total}] {action}: {clip_id}', flush=True)
        print(f'{len(run(config, progress))} clips')
