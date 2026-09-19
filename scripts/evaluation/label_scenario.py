import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.scenarios import add
p=argparse.ArgumentParser(description='Add an approved manual scenario label')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
p.add_argument('--clip-id',required=True);p.add_argument('--scenario',required=True);p.add_argument('--reviewer',required=True);p.add_argument('--note',default='')
if __name__=='__main__':
 a=p.parse_args();add(Config(a.config),a.clip_id,a.scenario,a.reviewer,a.note)
