import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from evaluation_data.split import assign
class SplitTests(unittest.TestCase):
    def test_deterministic_no_leakage(self):
        clips=[dict(clip_id=str(i),source_video_id=str(i//2),camera_id='c',session_id='s') for i in range(8)]
        a=assign(clips,42,.7);self.assertEqual(a,assign(clips,42,.7))
        self.assertEqual(len({r['split'] for r in a if r['source_video_id']=='1'}),1)
