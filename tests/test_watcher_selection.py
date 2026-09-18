import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_acquisition.catalog import Catalog
from data_acquisition.config import Config
from data_acquisition.models import Channel, ItemMetadata, LiveStatus, JobType
from data_acquisition.watcher import Watcher


class WatcherSelectionTest(unittest.TestCase):
    def test_empty_allowlist_cancels_old_queue_without_scanning_channel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel = Channel("c","https://www.youtube.com/@c",True,())
            config = Config(root / "configs" / "test.yaml",180,root / "data",1080,"mp4",True,True,3,0,(channel,))
            catalog = Catalog(config.db_path)
            catalog.upsert_item(ItemMetadata("video","c","Channel","Title",
                                "https://www.youtube.com/watch?v=video","video",LiveStatus.VOD))
            catalog.enqueue_job("video",JobType.VOD_DOWNLOAD)
            watcher = Watcher(config)
            try:
                with patch.object(watcher.client,"scan_channel",side_effect=AssertionError("bulk scan")):
                    self.assertEqual(watcher.scan(dispatch=False),0)
            finally:
                watcher.scheduler.shutdown()
            self.assertEqual(catalog.get_item("video")["local_status"],"DISCOVERED")
            self.assertEqual(catalog.latest_job("video")["status"],"CANCELLED")

    def test_only_listed_video_is_queued(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel = Channel("c","https://www.youtube.com/@c",True,("NUGc3nLuGsI",))
            config = Config(root / "configs" / "test.yaml",180,root / "data",1080,"mp4",True,True,3,0,(channel,))
            watcher = Watcher(config)
            item = ItemMetadata("NUGc3nLuGsI","c","Channel","Live",
                                "https://www.youtube.com/watch?v=NUGc3nLuGsI","live",LiveStatus.LIVE)
            try:
                with patch.object(watcher.client,"scan_channel",side_effect=AssertionError("bulk scan")), \
                     patch.object(watcher.client,"get_video",return_value=item) as fetch:
                    self.assertEqual(watcher.scan(dispatch=False),1)
                fetch.assert_called_once_with("NUGc3nLuGsI",channel,attempts=1)
            finally:
                watcher.scheduler.shutdown()
            self.assertEqual(watcher.catalog.latest_job("NUGc3nLuGsI")["job_type"],"LIVE_RECORD")

    def test_keyword_queues_matching_live_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            channel = Channel("c","https://www.youtube.com/@c",True,(),(),("cau rong",),())
            config = Config(root / "configs" / "test.yaml",180,root / "data",1080,"mp4",True,True,3,0,(channel,))
            watcher = Watcher(config)
            live = ItemMetadata("NUGc3nLuGsI","c","Channel","Camera Cầu Rồng",
                                "https://www.youtube.com/watch?v=NUGc3nLuGsI","live",LiveStatus.LIVE)
            vod = ItemMetadata("oC8ttZHG50I","c","Channel","Camera Cầu Rồng",
                               "https://www.youtube.com/watch?v=oC8ttZHG50I","video",LiveStatus.VOD)
            try:
                with patch.object(watcher.client,"scan_channel",return_value=iter([live,vod])) as scan:
                    self.assertEqual(watcher.scan(dispatch=False),1)
                scan.assert_called_once_with(channel,keywords_only=True)
            finally:
                watcher.scheduler.shutdown()
            self.assertEqual(watcher.catalog.latest_job("NUGc3nLuGsI")["job_type"],"LIVE_RECORD")
            self.assertIsNone(watcher.catalog.get_item("oC8ttZHG50I"))
