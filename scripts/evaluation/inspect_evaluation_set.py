import argparse
import json
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.inspect import summary
p=argparse.ArgumentParser(description='Inspect frozen evaluation set')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
if __name__=='__main__': print(json.dumps(summary(Config(p.parse_args().config)),indent=2))
