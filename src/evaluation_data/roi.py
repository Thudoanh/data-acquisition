import math
from .models import read_csv,write_json

ZONE_TYPES={'SIDEWALK','MONITORED','ALLOWED','IGNORE'}
def valid_polygon(points,width,height):
    if len({tuple(p) for p in points})<3: return False
    if any(len(p)!=2 or not (0<=p[0]<width and 0<=p[1]<height) for p in points): return False
    area=abs(sum(points[i][0]*points[(i+1)%len(points)][1]-points[(i+1)%len(points)][0]*points[i][1] for i in range(len(points))))/2
    if area<=0: return False
    def orient(a,b,c): return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def on_segment(a,b,c): return min(a[0],b[0])<=c[0]<=max(a[0],b[0]) and min(a[1],b[1])<=c[1]<=max(a[1],b[1])
    n=len(points)
    for i in range(n):
        for j in range(i+1,n):
            if j in (i,(i+1)%n) or (j+1)%n==i: continue
            a,b=points[i],points[(i+1)%n];c,d=points[j],points[(j+1)%n]
            o1,o2,o3,o4=orient(a,b,c),orient(a,b,d),orient(c,d,a),orient(c,d,b)
            if o1*o2<0 and o3*o4<0: return False
            if (o1==0 and on_segment(a,b,c)) or (o2==0 and on_segment(a,b,d)) or (o3==0 and on_segment(c,d,a)) or (o4==0 and on_segment(c,d,b)): return False
    return True

def zone_path(config,camera_id,view_id):
    if not all(s and s.replace('-','').replace('_','').isalnum() for s in (camera_id,view_id)): raise ValueError('Invalid camera/view ID')
    return config.output(f'zones/{camera_id}_{view_id}.json')

def define(config,clip_id):
    try: import cv2
    except ImportError as exc: raise RuntimeError('OpenCV is required: pip install opencv-python') from exc
    clips=read_csv(config.output('clips/clips_manifest.csv'));clip=next((c for c in clips if c['clip_id']==clip_id),None)
    if not clip or not clip['camera_id'] or not clip['view_id']: raise ValueError('Assign camera/view before ROI')
    import json
    cap=cv2.VideoCapture(str(config.at(clip['local_path'])))
    if not cap.isOpened(): raise ValueError('Unreadable clip')
    fps=cap.get(cv2.CAP_PROP_FPS) or 25; duration=float(clip['duration_sec']);position=duration/2
    width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH));height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    path=zone_path(config,clip['camera_id'],clip['view_id'])
    doc=json.loads(path.read_text()) if path.is_file() else dict(camera_id=clip['camera_id'],view_id=clip['view_id'],resolution=[width,height],zones=[])
    if doc['resolution'] != [width,height]: raise ValueError('Existing ROI resolution differs')
    points=[]; types=list(sorted(ZONE_TYPES));selected=0
    def click(event,x,y,flags,param):
        if event==cv2.EVENT_LBUTTONDOWN: points.append([x,y])
    cv2.namedWindow('ROI');cv2.setMouseCallback('ROI',click)
    while True:
        cap.set(cv2.CAP_PROP_POS_MSEC,position*1000);ok,frame=cap.read()
        if not ok: break
        for z in doc['zones']:
            poly=z['polygon']
            for a,b in zip(poly,poly[1:]+poly[:1]): cv2.line(frame,a,b,(0,255,0),2)
        for a,b in zip(points,points[1:]): cv2.line(frame,a,b,(255,255,0),2)
        cv2.putText(frame,f'{types[selected]} | digits type | S save U undo R reset LEFT/RIGHT seek Q quit',(10,25),cv2.FONT_HERSHEY_SIMPLEX,.55,(0,255,255),2)
        cv2.imshow('ROI',frame);key=cv2.waitKeyEx(0)
        if key in (ord('q'),27): break
        if key in (ord('u'),8) and points: points.pop()
        if key==ord('r'): points=[]
        if ord('1')<=key<=ord('4'): selected=key-ord('1')
        if key in (2424832,81): position=max(0,position-5)
        if key in (2555904,83): position=min(duration-.1,position+5)
        if key==ord('s'):
            if not valid_polygon(points,width,height): raise ValueError('Invalid polygon')
            kind=types[selected];number=1+sum(z['zone_type']==kind for z in doc['zones'])
            doc['zones'].append(dict(zone_id={'SIDEWALK':'SW','MONITORED':'MO','ALLOWED':'AL','IGNORE':'IG'}[kind]+f'{number:02d}',zone_type=kind,polygon=points[:]))
            write_json(path,doc);points=[]
    cap.release();cv2.destroyAllWindows()
