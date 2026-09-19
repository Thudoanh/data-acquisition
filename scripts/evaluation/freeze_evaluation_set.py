import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.freeze import freeze
p=argparse.ArgumentParser(description='Validate and create immutable evaluation version')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
if __name__=='__main__': print(freeze(Config(p.parse_args().config)))
