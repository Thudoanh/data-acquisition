"""Backward-compatible CLI name for automatic candidate segmentation."""
import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.segmentation import run
p = argparse.ArgumentParser(description='Automatically generate 30–180 second clip candidates from validated videos')
p.add_argument('--config', default='configs/evaluation/evaluation_dataset.yaml')
if __name__ == '__main__':
    print(f'{len(run(Config(p.parse_args().config)))} clip candidates')
