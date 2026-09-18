import tempfile
import unittest
from pathlib import Path

from data_acquisition.config import load_config
from data_acquisition.models import video_id_from_reference


BASE = """poll_interval_sec: 180
output: {root_dir: data/raw/youtube}
download: {max_height: 1080, merge_format: mp4}
live: {try_from_start: true, recovery_from_replay: true}
retry: {max_attempts: 3, retry_delay_sec: 60}
channels:
  - {id: one, url: 'https://www.youtube.com/@one', enabled: true}
  - {id: two, url: 'https://www.youtube.com/@two', enabled: false}
"""


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "configs" / "test.yaml"
        path.parent.mkdir()
        self.path = path

    def test_multiple_and_disabled(self):
        self.path.write_text(BASE)
        config = load_config(self.path)
        self.assertEqual(len(config.channels),2)
        self.assertFalse(config.channels[1].enabled)
        self.assertEqual(config.selection_mode,"allowlist")
        self.assertEqual(config.selected_video_ids,frozenset())

    def test_selected_urls_and_ids(self):
        text = BASE.replace("enabled: true}", "enabled: true, video_ids: ['https://www.youtube.com/watch?v=NUGc3nLuGsI', oC8ttZHG50I]}")
        self.path.write_text(text)
        config = load_config(self.path)
        self.assertEqual(config.channels[0].video_ids,("NUGc3nLuGsI","oC8ttZHG50I"))
        self.assertEqual(video_id_from_reference("https://youtu.be/NUGc3nLuGsI?t=5"),"NUGc3nLuGsI")

    def test_bad_video_url(self):
        with self.assertRaises(ValueError):
            video_id_from_reference("https://example.com/watch?v=NUGc3nLuGsI")

    def test_keyword_lists(self):
        self.path.write_text(BASE.replace("enabled: true}",
                         "enabled: true, live_keywords: ['cau rong'], vod_keywords: ['tam ky']}"))
        channel = load_config(self.path).channels[0]
        self.assertEqual(channel.live_keywords,("cau rong",))
        self.assertEqual(channel.vod_keywords,("tam ky",))

    def test_keyword_must_be_list(self):
        self.path.write_text(BASE.replace("enabled: true}", "enabled: true, live_keywords: 'cau rong'}"))
        with self.assertRaises(ValueError):
            load_config(self.path)

    def test_missing_required(self):
        self.path.write_text("channels: []")
        with self.assertRaises(ValueError):
            load_config(self.path)
