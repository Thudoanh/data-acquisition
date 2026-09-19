import hashlib
from .models import read_csv,write_csv,SPLITS

def group_key(clip,key):
    if key=='source_video_id': return clip['source_video_id']
    if key=='camera_id + session_id':
        if not clip['camera_id'] or not clip['session_id']: raise ValueError('camera_id and session_id required for grouping')
        return clip['camera_id']+'\0'+clip['session_id']
    raise ValueError(f'Unsupported grouping key: {key}')

def assign(clips,seed,ratio,key='source_video_id'):
    groups={group_key(c,key) for c in clips}
    ordered=sorted(groups,key=lambda g:hashlib.sha256(f'{seed}:{g}'.encode()).hexdigest())
    n=round(len(ordered)*ratio)
    if len(ordered)>1: n=max(1,min(len(ordered)-1,n))
    dev=set(ordered[:n])
    return [dict(clip_id=c['clip_id'],source_video_id=c['source_video_id'],camera_id=c['camera_id'],session_id=c['session_id'],split='dev' if group_key(c,key) in dev else 'test') for c in clips]

def run(config):
    clips=read_csv(config.output('clips/clips_manifest.csv'));spec=config.data['split']
    rows=assign(clips,int(spec['seed']),float(spec['dev_ratio']),spec['grouping_key'])
    write_csv(config.output('annotations/split_manifest.csv'),SPLITS,rows)
    scenarios=read_csv(config.output('annotations/scenarios.csv'))
    by_clip={r['clip_id']:r['split'] for r in rows};counts={}
    for r in scenarios:
        if r['review_status']=='approved' and r['clip_id'] in by_clip:
            count=counts.setdefault(r['scenario'],dict(scenario=r['scenario'],count_dev=0,count_test=0,total=0))
            count['count_'+by_clip[r['clip_id']]]+=1;count['total']+=1
    write_csv(config.output('validation/scenario_coverage.csv'),['scenario','count_dev','count_test','total'],[counts[k] for k in sorted(counts)])
    return rows
