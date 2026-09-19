import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.review import run
p = argparse.ArgumentParser(description='Review generated clip candidates with OpenCV')
p.add_argument('--config', default='configs/evaluation/evaluation_dataset.yaml')
if __name__ == '__main__':
    run(Config(p.parse_args().config))
