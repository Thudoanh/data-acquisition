import csv
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from .models import read_csv,write_csv,write_json
from .checksum import sha256_file
from .full_validation import run as validate

def freeze(config):
    target=config.at(config.data['freeze']['output_root'])/config.dataset_id
    if target.exists(): raise FileExistsError(f'Frozen version already exists: {target}')
    report=validate(config)
    if report['status']=='ERROR': raise ValueError('Validation contains ERROR; freeze blocked')
    clips=read_csv(config.output('clips/clips_manifest.csv'))
    source_rows=read_csv(config.output('inventory/video_catalog.csv'))
    events=read_csv(config.output('annotations/events.csv'))
    scenarios=read_csv(config.output('annotations/scenarios.csv'))
    splits=read_csv(config.output('annotations/split_manifest.csv'))
    split_map={r['clip_id']:r['split'] for r in splits}
    used_source_ids={c['source_video_id'] for c in clips}
    selected_ids=None
    if config.output('review/review_candidates.csv').is_file():
        from .filtering import selected_sources
        selected_ids={v['video_id'] for v in selected_sources(config).values()}
    sources=[r for r in source_rows if r['source_video_id'] in used_source_ids and (selected_ids is None or r['video_id'] in selected_ids)]
    frozen_clips=[dict(c,local_path=f"videos/{split_map[c['clip_id']]}/{Path(c['local_path']).name}") for c in clips]
    parent=target.parent;parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.freeze-',dir=parent))
    try:
        for c in clips:
            dest=staging/'videos'/split_map[c['clip_id']]/Path(c['local_path']).name
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(config.at(c['local_path']),dest)
        (staging/'annotations').mkdir(parents=True,exist_ok=True)
        with (staging/'annotations/events.jsonl').open('w',encoding='utf-8') as f:
            for e in events:
                item=dict(e);item['bbox_keyframes']=json.loads(item['bbox_keyframes'] or '[]')
                for k in ('start_time_sec','violation_time_sec','end_time_sec'):
                    item[k]=float(item[k]) if item[k] else None
                f.write(json.dumps(item,ensure_ascii=False)+'\n')
        for src,dest in [('annotations/scenarios.csv','annotations/scenarios.csv'),('annotations/split_manifest.csv','annotations/split_manifest.csv'),('zones/camera_registry.csv','zones/camera_registry.csv'),('validation/evaluation_validation.json','validation/validation_report.json')]:
            out=staging/dest;out.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(config.output(src),out)
        used={(c['camera_id'],c['view_id']) for c in clips}
        for camera,view in used:
            name=f'{camera}_{view}.json';shutil.copy2(config.output('zones/'+name),staging/'zones'/name)
        write_csv(staging/'manifests/clips.csv',list(frozen_clips[0]),frozen_clips)
        write_csv(staging/'manifests/sources.csv',list(sources[0]) if sources else [],sources)
        (staging/'config').mkdir(parents=True,exist_ok=True);shutil.copy2(config.path,staging/'config/evaluation_dataset.yaml')
        try: git_commit=subprocess.run(['git','rev-parse','HEAD'],cwd=config.root,capture_output=True,text=True,timeout=5).stdout.strip()
        except (OSError,subprocess.TimeoutExpired): git_commit=''
        counts=Counter(split_map.values());event_clips={e['clip_id'] for e in events}
        zone_count=sum(len(json.loads((staging/'zones'/f'{cam}_{view}.json').read_text())['zones']) for cam,view in used)
        manifest=dict(dataset_name=config.data['dataset']['name'],dataset_version=config.data['dataset']['version'],created_at=datetime.now(timezone.utc).isoformat(),git_commit=git_commit,num_sources=len(sources),num_clips=len(clips),num_dev=counts['dev'],num_test=counts['test'],num_events=len(events),num_negative_clips=sum(1 for c in clips if c['clip_id'] not in event_clips),num_cameras=len({c['camera_id'] for c in clips}),num_zones=zone_count,total_duration_sec=sum(float(c['duration_sec']) for c in clips),config_sha256=sha256_file(config.path))
        write_json(staging/'manifests/freeze_manifest.json',manifest)
        (staging/'README.md').write_text(f'# {config.dataset_id}\n\nFrozen evaluation data. Test videos must not be used for training, fine-tuning, or threshold tuning. See manifests/freeze_manifest.json and validation/validation_report.json.\n',encoding='utf-8')
        checks=[dict(path=p.relative_to(staging).as_posix(),sha256=sha256_file(p)) for p in sorted(staging.rglob('*')) if p.is_file() and p.name!='checksums.csv']
        write_csv(staging/'manifests/checksums.csv',['path','sha256'],checks)
        if target.exists(): raise FileExistsError(target)
        os.rename(staging,target)
        return target
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        raise
