import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from evaluation_data.validator import validate_file
from evaluation_data.roi import valid_polygon
from evaluation_data.events import validate_event,LABEL
from unittest.mock import patch

class ValidationTests(unittest.TestCase):
    def setUp(self): self.cfg=dict(min_fps=5,min_width=320,min_height=240)
    def test_missing_file(self):
        self.assertEqual(validate_file(Path('/nonexistent/video.mp4'),self.cfg)[1],'missing_file')
    def test_bad_duration(self):
        with patch('evaluation_data.validator.probe',return_value=(dict(duration_sec=0,fps=25,width=640,height=480),'')):
            with patch.object(Path,'is_file',return_value=True):
                self.assertEqual(validate_file(Path('x'),self.cfg)[1],'invalid_duration')
    def test_invalid_polygon(self):
        self.assertFalse(valid_polygon([[0,0],[10,10],[0,10],[10,0]],100,100))
        self.assertFalse(valid_polygon([[0,0],[1,1]],100,100))
    def test_invalid_class(self):
        event=dict(label=LABEL,object_class='person',start_time_sec=0,violation_time_sec=1,end_time_sec=2,bbox_keyframes='[]')
        self.assertIn('invalid_class',validate_event(event,3,['motorcycle']))
