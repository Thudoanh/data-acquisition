import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.events import interactive
p=argparse.ArgumentParser(description='Mark event times and add ground truth events')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
p.add_argument('--clip-id',required=True)
if __name__=='__main__':
 a=p.parse_args();interactive(Config(a.config),a.clip_id)
