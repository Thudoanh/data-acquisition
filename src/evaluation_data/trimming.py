import re
import subprocess
from .models import read_csv,write_csv,CLIPS
from .checksum import sha256_file
from .validator import probe

def clip_name(source_id, number):
    safe=re.sub(r'[^A-Za-z0-9_-]','_',source_id)
    return f'{safe}_C{number:03d}.mp4'

def valid_interval(start,end,source_duration,minimum,maximum):
    return -1e-6 <= start < end <= source_duration + 1e-6 and minimum - 1e-6 <= end-start <= maximum + 1e-6

def run(config):
    from .filtering import selected_sources
    videos=selected_sources(config)
    existing={r['clip_id']:r for r in read_csv(config.output('clips/clips_manifest.csv'))}
    existing_intervals={(r['source_video_id'],float(r['source_start_sec']),float(r['source_end_sec'])):r for r in existing.values()}
    candidates=sorted(read_csv(config.output('review/clip_candidates.csv')),key=lambda c:(c['source_video_id'],float(c['start_sec'])))
    counts={}; rows=[]; assigned=set()
    for c in candidates:
        source=c['source_video_id']
        counts[source]=counts.get(source,0)+1
        if c['status'] != 'keep': continue
        v=videos.get(source)
        if not v: raise ValueError(f'Unknown source_video_id: {source}')
        start,end=float(c['start_sec']),float(c['end_sec'])
        if not valid_interval(start,end,float(v['duration_sec']),config.data['clip']['min_duration_sec'],config.data['clip']['max_duration_sec']):
            raise ValueError(f'Invalid clip interval: {c["clip_candidate_id"]}')
        prior=existing_intervals.get((source,start,end))
        clip_id=prior['clip_id'] if prior else clip_name(source,counts[source]).removesuffix('.mp4')
        if not prior:
            number=counts[source]
            while clip_id in existing or clip_id in assigned:
                number+=1
                clip_id=clip_name(source,number).removesuffix('.mp4')
        if clip_id in assigned: raise ValueError(f'Duplicate kept interval: {source} {start}-{end}')
        assigned.add(clip_id)
        path=config.output('clips/'+clip_id+'.mp4'); path.parent.mkdir(parents=True,exist_ok=True)
        old=existing.get(clip_id)
        if old and float(old['source_start_sec'])==start and float(old['source_end_sec'])==end and path.is_file() and sha256_file(path)==old['sha256']:
            rows.append(old); continue
        if path.exists(): raise FileExistsError(f'Existing clip differs: {path}')
        cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-ss',str(start),'-i',str(config.at(v['local_path'])),'-t',str(end-start),'-map','0:v:0','-map','0:a?','-c:v','libx264','-preset','fast','-crf','18','-c:a','aac','-movflags','+faststart',str(path)]
        try: result=subprocess.run(cmd,capture_output=True,text=True,timeout=600)
        except FileNotFoundError as exc: raise RuntimeError('ffmpeg is required for trimming') from exc
        if result.returncode: raise RuntimeError(result.stderr[-1000:])
        info,reason=probe(path)
        if reason or abs(info['duration_sec']-(end-start))>1.0:
            path.unlink(missing_ok=True);raise ValueError(f'Trim verification failed: {clip_id}')
        rows.append(dict(clip_id=clip_id,source_video_id=source,source_start_sec=start,source_end_sec=end,duration_sec=info['duration_sec'],local_path=str(path.relative_to(config.root)),sha256=sha256_file(path),trim_status='ready',camera_id=v['camera_id'],view_id='',session_id=v['session_id']))
    write_csv(config.output('clips/clips_manifest.csv'),CLIPS,rows)
    return rows
