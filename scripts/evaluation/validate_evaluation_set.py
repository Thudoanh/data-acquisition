import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.full_validation import run
p=argparse.ArgumentParser(description='Validate cross-file evaluation data')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
if __name__=='__main__':
 report=run(Config(p.parse_args().config));print(report['status'],len(report['issues']),'issues');raise SystemExit(1 if report['status']=='ERROR' else 0)
