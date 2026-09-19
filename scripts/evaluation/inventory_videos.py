import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.inventory import run
p=argparse.ArgumentParser(description='Inventory raw videos')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
if __name__=='__main__': print(f'{len(run(Config(p.parse_args().config)))} videos inventoried')
