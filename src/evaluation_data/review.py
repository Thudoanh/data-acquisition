"""Review candidate intervals with ffmpeg decoding and an OpenCV display window."""
import shutil
import subprocess

from .models import CANDIDATES, read_csv, write_csv
from .filtering import selected_sources


class FrameStream:
    """Decode a bounded interval to BGR frames without VideoCapture codec support."""
    def __init__(self, path, width, height, end, fps=8):
        self.path = path
        self.width = min(int(width), 960)
        self.height = max(2, round(int(height) * self.width / int(width) / 2) * 2)
        self.end = end
        self.fps = fps
        self.process = None

    def seek(self, position):
        self.close()
        args = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                '-ss', str(position), '-i', str(self.path), '-t', str(self.end-position),
                '-an', '-sn', '-dn', '-vf', f'fps={self.fps},scale={self.width}:{self.height}',
                '-pix_fmt', 'bgr24', '-f', 'rawvideo', 'pipe:1']
        try:
            self.process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except FileNotFoundError as exc:
            raise RuntimeError('ffmpeg is required for clip review') from exc

    def read(self):
        import numpy as np
        size = self.width * self.height * 3
        chunks = []
        remaining = size
        while remaining:
            data = self.process.stdout.read(remaining)
            if not data:
                return None
            chunks.append(data)
            remaining -= len(data)
        return np.frombuffer(b''.join(chunks), dtype=np.uint8).reshape(self.height, self.width, 3)

    def close(self):
        if self.process is not None:
            if self.process.stdout:
                self.process.stdout.close()
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = None


def run(config):
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError('OpenCV is required: pip install opencv-python') from exc
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg is required for clip review')
    path = config.output('review/clip_candidates.csv')
    rows = read_csv(path)
    if not rows:
        return
    videos = selected_sources(config)
    index = 0
    try:
        while 0 <= index < len(rows):
            row = rows[index]
            video = videos.get(row['source_video_id'])
            if not video:
                raise ValueError(f"No eligible source video for {row['source_video_id']}; rerun filter and segmentation")
            start, end = float(row['start_sec']), float(row['end_sec'])
            stream = FrameStream(config.at(video['local_path']), video['width'], video['height'], end)
            position, playing = start, False
            try:
                stream.seek(position)
                frame = stream.read()
                if frame is None:
                    raise ValueError(f"ffmpeg could not decode: {video['local_path']} at {start:.3f}s")
                while True:
                    display = frame.copy()
                    label = (f"{index+1}/{len(rows)} {position-start:.1f}/{end-start:.1f}s "
                             f"{row['status']} SPACE play LEFT/RIGHT seek K/R N/P Q")
                    cv2.putText(display, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                                .55, (0, 255, 255), 2)
                    cv2.imshow('Clip candidate review', display)
                    key = cv2.waitKeyEx(max(1, round(1000/stream.fps)) if playing else 0)
                    if key == 32:
                        playing = not playing
                    elif key in (2424832, 81, 2555904, 83):
                        delta = -5 if key in (2424832, 81) else 5
                        position = max(start, min(end-1/stream.fps, position+delta))
                        playing = False
                        stream.seek(position)
                        frame = stream.read()
                        if frame is None:
                            raise ValueError(f"ffmpeg could not decode: {video['local_path']} at {position:.3f}s")
                    elif key in (ord('k'), ord('K'), ord('r'), ord('R')):
                        row['status'] = 'keep' if chr(key).lower() == 'k' else 'reject'
                        write_csv(path, CANDIDATES, rows)
                    elif key in (ord('n'), ord('N')):
                        index += 1
                        break
                    elif key in (ord('p'), ord('P')):
                        index = max(0, index-1)
                        break
                    elif key in (ord('q'), ord('Q'), 27):
                        return
                    if playing:
                        next_frame = stream.read()
                        if next_frame is None or position + 1/stream.fps >= end:
                            position, playing = start, False
                            stream.seek(position)
                            frame = stream.read()
                            if frame is None:
                                raise ValueError(f"ffmpeg could not decode: {video['local_path']} at {start:.3f}s")
                        else:
                            position += 1/stream.fps
                            frame = next_frame
            finally:
                stream.close()
    finally:
        cv2.destroyAllWindows()
