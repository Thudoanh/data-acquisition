import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from evaluation_data.review import FrameStream


class FakeProcess:
    def __init__(self, payload):
        self.stdout = io.BytesIO(payload)
        self.terminated = False
    def poll(self): return None if not self.terminated else 0
    def terminate(self): self.terminated = True
    def wait(self, timeout=None): return 0


class FrameStreamTests(unittest.TestCase):
    def test_ffmpeg_frame_decode_and_cleanup(self):
        stream = FrameStream(Path('video.mp4'), 4, 4, 30, fps=8)
        payload = bytes([7]) * (stream.width * stream.height * 3)
        fake = FakeProcess(payload)
        with patch('evaluation_data.review.subprocess.Popen', return_value=fake) as popen:
            stream.seek(2)
            frame = stream.read()
            self.assertEqual(frame.shape, (4, 4, 3))
            self.assertEqual(int(frame[0, 0, 0]), 7)
            self.assertIn('rawvideo', popen.call_args.args[0])
            self.assertIsNone(stream.read())
            stream.close()
            self.assertTrue(fake.terminated)
