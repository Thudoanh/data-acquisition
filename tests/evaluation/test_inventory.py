import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from evaluation_data.inventory import stable_id
from evaluation_data.metadata import for_file

class InventoryTests(unittest.TestCase):
    def test_stable_unique_portable(self):
        a=stable_id('youtube','abc',Path('channel/abc/source.mp4'))
        self.assertEqual(a,stable_id('youtube','abc',Path('channel/abc/source.mp4')))
        self.assertNotEqual(a,stable_id('youtube','abc',Path('other/abc/source.mp4')))
    def test_metadata_reuse(self):
        import tempfile,json
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.mp4';path.touch()
            (Path(tmp)/'metadata.json').write_text(json.dumps({'video_id':'yt123','source_url':'https://example.test'}))
            self.assertEqual(for_file(path,{})['video_id'],'yt123')
