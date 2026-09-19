import json
from collections import Counter
from .models import read_csv

def summary(config):
    root=config.at(config.data['freeze']['output_root'])/config.dataset_id
    if not root.is_dir(): raise FileNotFoundError(root)
    manifest=json.loads((root/'manifests/freeze_manifest.json').read_text())
    scenarios=read_csv(root/'annotations/scenarios.csv')
    report=json.loads((root/'validation/validation_report.json').read_text())
    return dict(dataset_version=config.dataset_id,source_count=manifest['num_sources'],clip_count=manifest['num_clips'],dev_count=manifest['num_dev'],test_count=manifest['num_test'],event_count=manifest['num_events'],positive_count=len({r['clip_id'] for r in scenarios if r['scenario_group']=='positive'}),negative_count=manifest['num_negative_clips'],scenario_distribution=dict(Counter(r['scenario'] for r in scenarios)),camera_count=manifest['num_cameras'],total_duration_sec=manifest['total_duration_sec'],validation_status=report['status'])
