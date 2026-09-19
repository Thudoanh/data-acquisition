import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.roi import define
p=argparse.ArgumentParser(description='Draw and save polygon zones')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
p.add_argument('--clip-id',required=True)
if __name__=='__main__':
 a=p.parse_args();define(Config(a.config),a.clip_id)
