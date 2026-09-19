from .models import CAMERAS,CLIPS,read_csv,write_csv

def assign(config,clip_id,camera_id,view_id,session_id='',name='',notes=''):
    path=config.output('clips/clips_manifest.csv'); clips=read_csv(path)
    clip=next((c for c in clips if c['clip_id']==clip_id),None)
    if not clip: raise ValueError(f'Unknown clip: {clip_id}')
    from .validator import probe
    info,reason=probe(config.at(clip['local_path']))
    if reason: raise ValueError(f'Unreadable clip: {reason}')
    registry_path=config.output('zones/camera_registry.csv'); registry=read_csv(registry_path)
    existing=next((r for r in registry if (r['camera_id'],r['view_id'])==(camera_id,view_id)),None)
    if existing and (int(existing['width']),int(existing['height']))!=(info['width'],info['height']):
        raise ValueError('View resolution changed; use a new view_id')
    source=next((v['source'] for v in read_csv(config.output('inventory/video_catalog.csv')) if v['source_video_id']==clip['source_video_id']),'')
    if not existing: registry.append(dict(camera_id=camera_id,view_id=view_id,source=source,source_camera_name=name,session_id=session_id,width=info['width'],height=info['height'],notes=notes))
    clip.update(camera_id=camera_id,view_id=view_id,session_id=session_id)
    write_csv(registry_path,CAMERAS,registry);write_csv(path,CLIPS,clips)
