from .models import SCENARIOS,read_csv,write_csv

def add(config,clip_id,scenario,reviewer,note=''):
    groups=config.data['scenario_labels']; matches=[g for g,labels in groups.items() if scenario in labels]
    if len(matches)!=1: raise ValueError(f'Unknown scenario: {scenario}')
    clips={c['clip_id'] for c in read_csv(config.output('clips/clips_manifest.csv'))}
    if clip_id not in clips: raise ValueError(f'Unknown clip: {clip_id}')
    path=config.output('annotations/scenarios.csv'); rows=read_csv(path)
    rows=[r for r in rows if (r['clip_id'],r['scenario'])!=(clip_id,scenario)]
    rows.append(dict(clip_id=clip_id,scenario=scenario,scenario_group=matches[0],reviewer=reviewer,review_status='approved',note=note))
    write_csv(path,SCENARIOS,rows)
