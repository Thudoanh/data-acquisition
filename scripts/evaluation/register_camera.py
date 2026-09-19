import argparse
import _bootstrap
from evaluation_data.models import Config
from evaluation_data.camera_registry import assign
p=argparse.ArgumentParser(description='Assign a clip to a camera/view/session')
p.add_argument('--config',default='configs/evaluation/evaluation_dataset.yaml')
p.add_argument('--clip-id',required=True);p.add_argument('--camera-id',required=True);p.add_argument('--view-id',required=True);p.add_argument('--session-id',default='');p.add_argument('--name',default='');p.add_argument('--notes',default='')
if __name__=='__main__':
 a=p.parse_args();assign(Config(a.config),a.clip_id,a.camera_id,a.view_id,a.session_id,a.name,a.notes)
