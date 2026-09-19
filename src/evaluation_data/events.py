import json
import uuid
from .models import EVENTS,read_csv,write_csv

LABEL='SUSPECTED_AREA_OCCUPATION'
def validate_event(event,duration,classes):
    errors=[]
    if event.get('label') != LABEL: errors.append('invalid_label')
    if event.get('object_class') not in classes: errors.append('invalid_class')
    try:
        start=float(event['start_time_sec']); violation=float(event['violation_time_sec'])
        end=duration if event.get('end_time_sec') in ('',None) else float(event['end_time_sec'])
        if not 0<=start<=violation<=end<=duration: errors.append('invalid_timing')
        frames=event.get('bbox_keyframes') or '[]';frames=json.loads(frames) if isinstance(frames,str) else frames
        for f in frames:
            x1,y1,x2,y2=map(float,f['bbox'])
            if not start<=float(f['time_sec'])<=end or not x1<x2 or not y1<y2: errors.append('invalid_bbox')
    except (ValueError,TypeError,KeyError,json.JSONDecodeError): errors.append('invalid_timing_or_bbox')
    return errors

def add(config,clip_id,zone_id,object_class,start,violation,end,reviewer,notes='',bbox_keyframes=None):
    clips={c['clip_id']:c for c in read_csv(config.output('clips/clips_manifest.csv'))}
    clip=clips.get(clip_id)
    if not clip: raise ValueError('Unknown clip')
    from .roi import zone_path
    roi=json.loads(zone_path(config,clip['camera_id'],clip['view_id']).read_text())
    zone=next((z for z in roi['zones'] if z['zone_id']==zone_id),None)
    if not zone: raise ValueError('Unknown zone')
    event=dict(event_id='E'+uuid.uuid4().hex[:16],clip_id=clip_id,camera_id=clip['camera_id'],zone_id=zone_id,zone_type=zone['zone_type'],object_class=object_class,start_time_sec=start,violation_time_sec=violation,end_time_sec=end,label=LABEL,track_hint='',bbox_keyframes=json.dumps(bbox_keyframes or []),reviewer=reviewer,review_status='approved',notes=notes)
    errors=validate_event(event,float(clip['duration_sec']),config.data['violation']['target_classes'])
    if errors: raise ValueError(', '.join(errors))
    path=config.output('annotations/events.csv');rows=read_csv(path);rows.append(event);write_csv(path,EVENTS,rows)
    return event

def interactive(config,clip_id):
    try: import cv2
    except ImportError as exc: raise RuntimeError('OpenCV is required: pip install opencv-python') from exc
    clip=next((c for c in read_csv(config.output('clips/clips_manifest.csv')) if c['clip_id']==clip_id),None)
    if not clip: raise ValueError('Unknown clip')
    cap=cv2.VideoCapture(str(config.at(clip['local_path'])))
    if not cap.isOpened(): raise ValueError('Unreadable clip')
    pos=0.;duration=float(clip['duration_sec']); marks=[]
    while True:
        cap.set(cv2.CAP_PROP_POS_MSEC,pos*1000);ok,frame=cap.read()
        if not ok: break
        cv2.putText(frame,f'{pos:.1f}s LEFT/RIGHT seek S start V violation E end A add Q quit',(10,25),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,255,255),2)
        cv2.imshow('Event annotation',frame);key=cv2.waitKeyEx(0)
        if key in (ord('q'),27): break
        if key in (2424832,81): pos=max(0,pos-1)
        if key in (2555904,83): pos=min(duration-.1,pos+1)
        if key in (ord('s'),ord('v'),ord('e')): marks.append((chr(key),round(pos,3)))
        if key==ord('a'):
            stamps={k:v for k,v in marks}
            if 's' not in stamps or 'v' not in stamps: print('Mark S and V first');continue
            print('Zones:',[(z['zone_id'],z['zone_type']) for z in json.loads(__import__('evaluation_data.roi',fromlist=['zone_path']).zone_path(config,clip['camera_id'],clip['view_id']).read_text())['zones']])
            zone=input('zone_id: ').strip();object_class=input('object_class: ').strip();reviewer=input('reviewer: ').strip()
            add(config,clip_id,zone,object_class,stamps['s'],stamps['v'],stamps.get('e',''),reviewer)
            marks=[];print('Event saved')
    cap.release();cv2.destroyAllWindows()
