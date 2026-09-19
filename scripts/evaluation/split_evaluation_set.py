import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.split import run
p=argparse.ArgumentParser(description='Group-safe deterministic dev/test split')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
if __name__=='__main__': print(f'{len(run(Config(p.parse_args().config)))} clips split')
