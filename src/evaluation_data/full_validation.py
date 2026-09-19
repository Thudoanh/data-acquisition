import json
from collections import Counter
from .models import read_csv,write_csv,write_json
from .validator import validate_file
from .roi import valid_polygon,ZONE_TYPES,zone_path
from .events import validate_event

def run(config):
    issues=[]
    def issue(severity,code,entity='',detail=''): issues.append(dict(severity=severity,code=code,entity=entity,detail=detail))
    videos=read_csv(config.output('inventory/video_catalog.csv'))
    clips=read_csv(config.output('clips/clips_manifest.csv'))
    cameras=read_csv(config.output('zones/camera_registry.csv'))
    scenarios=read_csv(config.output('annotations/scenarios.csv'))
    events=read_csv(config.output('annotations/events.csv'))
    splits=read_csv(config.output('annotations/split_manifest.csv'))
    if not clips: issue('ERROR','no_clips')
    for name,rows,key in [('video',videos,'video_id'),('clip',clips,'clip_id'),('event',events,'event_id'),('split',splits,'clip_id')]:
        for value,count in Counter(r[key] for r in rows).items():
            if count>1: issue('ERROR','duplicate_'+name,value)
    sources={v['source_video_id'] for v in videos};clip_by_id={c['clip_id']:c for c in clips};camera_by_id={(r['camera_id'],r['view_id']):r for r in cameras}
    used_sources={c['source_video_id'] for c in clips}
    selected_ids=None
    if config.output('review/review_candidates.csv').is_file():
        from .filtering import selected_sources
        selected_ids={v['video_id'] for v in selected_sources(config).values()}
    for v in videos:
        if v['source_video_id'] not in used_sources or (selected_ids is not None and v['video_id'] not in selected_ids): continue
        status,reason,_=validate_file(config.at(v['local_path']),config.data['validation'],v['sha256'])
        if status=='invalid': issue('ERROR','source_'+reason,v['video_id'])
        elif status=='warning': issue('WARNING','source_'+reason,v['video_id'])
    zones={}
    selected_sources_ids={v['source_video_id'] for v in videos if selected_ids is None or v['video_id'] in selected_ids}
    for c in clips:
        ident=c['clip_id']
        if c['source_video_id'] not in sources: issue('ERROR','missing_source',ident)
        elif c['source_video_id'] not in selected_sources_ids: issue('ERROR','source_not_selected',ident)
        status,reason,info=validate_file(config.at(c['local_path']),config.data['validation'],c['sha256'])
        if status!='valid': issue('ERROR','clip_'+reason,ident)
        camera=camera_by_id.get((c['camera_id'],c['view_id']))
        if not camera: issue('ERROR','missing_camera_view',ident);continue
        if info and (int(camera['width']),int(camera['height']))!=(info['width'],info['height']): issue('ERROR','camera_resolution_mismatch',ident)
        try: path=zone_path(config,c['camera_id'],c['view_id'])
        except ValueError:
            issue('ERROR','invalid_camera_view_id',ident);continue
        if not path.is_file(): issue('ERROR','missing_roi',ident);continue
        try:
            doc=json.loads(path.read_text(encoding='utf-8'))
            width,height=map(int,doc['resolution'])
            if (width,height)!=(int(camera['width']),int(camera['height'])): issue('ERROR','roi_resolution_mismatch',ident)
            ids=set()
            for z in doc['zones']:
                if z['zone_id'] in ids: issue('ERROR','duplicate_zone',ident,z['zone_id'])
                ids.add(z['zone_id'])
                if z['zone_type'] not in ZONE_TYPES or not valid_polygon(z['polygon'],width,height): issue('ERROR','invalid_polygon',ident,z['zone_id'])
            if not doc['zones']: issue('ERROR','empty_roi',ident)
            zones[ident]={z['zone_id']:z for z in doc['zones']}
        except (ValueError,KeyError,TypeError,json.JSONDecodeError): issue('ERROR','invalid_roi_file',ident)
    scenario_by_clip={}
    for r in scenarios:
        if r['clip_id'] not in clip_by_id: issue('ERROR','scenario_missing_clip',r['clip_id'])
        expected=[g for g,labels in config.data['scenario_labels'].items() if r['scenario'] in labels]
        if expected != [r['scenario_group']]: issue('ERROR','invalid_scenario',r['clip_id'],r['scenario'])
        if r['review_status']!='approved': issue('ERROR','scenario_not_approved',r['clip_id'])
        scenario_by_clip.setdefault(r['clip_id'],set()).add(r['scenario_group'])
    events_by_clip=Counter()
    for e in events:
        clip=clip_by_id.get(e['clip_id'])
        if not clip: issue('ERROR','event_missing_clip',e['event_id']);continue
        events_by_clip[e['clip_id']]+=1
        zone=zones.get(e['clip_id'],{}).get(e['zone_id'])
        if not zone or zone['zone_type']!=e['zone_type']: issue('ERROR','event_missing_zone',e['event_id'])
        if e['camera_id']!=clip['camera_id']: issue('ERROR','event_camera_mismatch',e['event_id'])
        for error in validate_event(e,float(clip['duration_sec']),config.data['violation']['target_classes']): issue('ERROR',error,e['event_id'])
        if e['review_status']!='approved': issue('ERROR','event_not_approved',e['event_id'])
    for c in clips:
        ident=c['clip_id'];groups=scenario_by_clip.get(ident,set())
        if not groups: issue('ERROR','missing_scenario',ident)
        if groups == {'hard'}: issue('ERROR','missing_outcome_scenario',ident)
        if 'negative' in groups and 'positive' in groups: issue('ERROR','conflicting_scenarios',ident)
        if 'negative' in groups and events_by_clip[ident]: issue('ERROR','negative_has_events',ident)
        if 'positive' in groups and not events_by_clip[ident]: issue('ERROR','positive_without_event',ident)
    split_by_clip={r['clip_id']:r for r in splits}
    groups={}
    from .split import group_key
    for r in splits:
        c=clip_by_id.get(r['clip_id'])
        if not c: issue('ERROR','split_missing_clip',r['clip_id']);continue
        if r['split'] not in ('dev','test'): issue('ERROR','invalid_split',r['clip_id'])
        if any(r[k]!=c[k] for k in ('source_video_id','camera_id','session_id')): issue('ERROR','split_metadata_mismatch',r['clip_id'])
        try: key=group_key(c,config.data['split']['grouping_key'])
        except ValueError as exc:
            issue('ERROR','invalid_split_group',r['clip_id'],str(exc));continue
        groups.setdefault(key,set()).add(r['split'])
    for c in clips:
        if c['clip_id'] not in split_by_clip: issue('ERROR','missing_split',c['clip_id'])
    for key,values in groups.items():
        if len(values)>1: issue('ERROR','split_leakage',key)
    report=dict(dataset_id=config.dataset_id,status='ERROR' if any(i['severity']=='ERROR' for i in issues) else 'PASS',issues=issues)
    write_json(config.output('validation/evaluation_validation.json'),report)
    write_csv(config.output('validation/evaluation_validation.csv'),['severity','code','entity','detail'],issues)
    return report
