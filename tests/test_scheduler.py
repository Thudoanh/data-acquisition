import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_acquisition.catalog import Catalog
from data_acquisition.config import Config
from data_acquisition.models import ItemMetadata, LiveStatus, LocalStatus
from data_acquisition.scheduler import Scheduler, desired_job


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = Config(root / "configs" / "test.yaml",180,root / "data",1080,"mp4",True,True,3,0,())
        self.catalog = Catalog(root / "state" / "catalog.db")
        self.scheduler = Scheduler(self.catalog,self.config)
        self.addCleanup(self.scheduler.shutdown)

    def add(self, status):
        self.catalog.upsert_item(ItemMetadata("abc","channel","Channel","Title",
                                 "https://www.youtube.com/watch?v=abc","live",status))

    def test_live_queued_once(self):
        self.add(LiveStatus.LIVE)
        self.assertTrue(self.scheduler.schedule("abc"))
        self.assertFalse(self.scheduler.schedule("abc"))
        self.assertEqual(self.catalog.latest_job("abc")["job_type"],"LIVE_RECORD")
        self.catalog.claim_job()
        self.catalog.update_local_status("abc",LocalStatus.RECORDING)
        self.assertFalse(self.scheduler.schedule("abc"))

    def test_upcoming_waits(self):
        self.add(LiveStatus.UPCOMING)
        self.assertFalse(self.scheduler.schedule("abc"))
        self.assertEqual(self.catalog.get_item("abc")["local_status"],"WAITING")

    def test_replay_recovery(self):
        self.add(LiveStatus.ENDED)
        self.catalog.set_fields("abc",capture_complete=0)
        self.assertEqual(desired_job(self.catalog.get_item("abc"),self.config).value,"REPLAY_RECOVERY")
        self.assertTrue(self.scheduler.schedule("abc"))

    def test_retry_stops_after_limit(self):
        self.add(LiveStatus.VOD)
        self.assertTrue(self.scheduler.schedule("abc"))
        with patch("data_acquisition.scheduler.process_job", side_effect=RuntimeError("network")):
            for _ in range(3):
                job = self.catalog.claim_job()
                self.assertIsNotNone(job)
                self.scheduler._run(job)
        self.assertEqual(self.catalog.get_item("abc")["retry_count"],3)
        self.assertEqual(self.catalog.get_item("abc")["local_status"],"FAILED")
        self.assertIsNone(self.catalog.claim_job())

    def test_manual_run_does_not_claim_other_queued_videos(self):
        self.add(LiveStatus.VOD)
        self.catalog.upsert_item(ItemMetadata("other","channel","Channel","Other",
                                 "https://www.youtube.com/watch?v=other","video",LiveStatus.VOD))
        self.scheduler.schedule("abc")
        self.scheduler.schedule("other")
        def fail_selected(job):
            self.catalog.mark_failed(job["video_id"],"test",retry=False)
            self.catalog.finish_job(job["id"],"test")
        with patch.object(self.scheduler,"_run",side_effect=fail_selected):
            result = self.scheduler.run_selected("abc")
        self.assertEqual(result["local_status"],"FAILED")
        self.assertEqual(self.catalog.latest_job("other")["status"],"QUEUED")
