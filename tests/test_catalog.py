import tempfile
import unittest
from pathlib import Path

from data_acquisition.catalog import Catalog
from data_acquisition.models import ItemMetadata, LiveStatus, LocalStatus, JobType


def item(video_id="abc", status=LiveStatus.VOD):
    return ItemMetadata(video_id,"channel","Channel","Title",f"https://www.youtube.com/watch?v={video_id}","video",status)


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.catalog = Catalog(Path(self.temp.name) / "catalog.db")

    def test_insert_upsert_unique_and_status(self):
        self.catalog.upsert_item(item())
        self.catalog.upsert_item(ItemMetadata("abc","channel","Channel","Updated",
                                              "https://www.youtube.com/watch?v=abc","video",LiveStatus.VOD))
        self.assertEqual(len(self.catalog.list_items()),1)
        self.assertEqual(self.catalog.get_item("abc")["title"],"Updated")
        self.catalog.update_local_status("abc",LocalStatus.QUEUED)
        self.assertEqual(self.catalog.get_item("abc")["local_status"],"QUEUED")

    def test_job_unique_and_restart(self):
        self.catalog.upsert_item(item())
        self.assertTrue(self.catalog.enqueue_job("abc",JobType.VOD_DOWNLOAD))
        self.assertFalse(self.catalog.enqueue_job("abc",JobType.VOD_DOWNLOAD))
        claimed = self.catalog.claim_job()
        self.catalog.update_local_status("abc",LocalStatus.DOWNLOADING)
        self.assertEqual(self.catalog.recover_interrupted(),1)
        self.assertEqual(self.catalog.get_item("abc")["local_status"],"QUEUED")
        self.assertEqual(self.catalog.claim_job()["id"],claimed["id"])

    def test_recover_only_selected_interrupted_job(self):
        for video_id in ("selected", "other"):
            self.catalog.upsert_item(item(video_id))
            self.catalog.enqueue_job(video_id,JobType.VOD_DOWNLOAD)
            self.catalog.claim_job(video_id)
            self.catalog.update_local_status(video_id,LocalStatus.DOWNLOADING)
        self.assertEqual(self.catalog.recover_interrupted("selected"),1)
        self.assertEqual(self.catalog.get_item("selected")["local_status"],"QUEUED")
        self.assertEqual(self.catalog.get_item("other")["local_status"],"DOWNLOADING")
        self.assertEqual(self.catalog.latest_job("selected")["status"],"QUEUED")
        self.assertEqual(self.catalog.latest_job("other")["status"],"RUNNING")

    def test_completion_requires_validation(self):
        self.catalog.upsert_item(item())
        with self.assertRaises(ValueError):
            self.catalog.mark_completed("abc",Path("source.mp4"),1,"abc",1,"vod_download",True)

    def test_cancel_unselected_jobs_and_claim_only_selected(self):
        self.catalog.upsert_item(item("selected"))
        self.catalog.upsert_item(item("other"))
        self.catalog.enqueue_job("selected",JobType.VOD_DOWNLOAD)
        self.catalog.enqueue_job("other",JobType.VOD_DOWNLOAD)
        self.assertEqual(self.catalog.cancel_queued_except(frozenset({"selected"})),1)
        self.assertEqual(self.catalog.get_item("other")["local_status"],"DISCOVERED")
        self.assertEqual(self.catalog.latest_job("other")["status"],"CANCELLED")
        self.assertIsNone(self.catalog.claim_job("other"))
        self.assertEqual(self.catalog.claim_job("selected")["video_id"],"selected")

    def test_manual_job_is_not_cancelled_by_allowlist(self):
        self.catalog.upsert_item(item("manual"))
        self.catalog.enqueue_job("manual",JobType.VOD_DOWNLOAD,origin="manual")
        self.assertEqual(self.catalog.cancel_queued_except(frozenset()),0)
        self.assertIsNone(self.catalog.claim_job(origin="watcher"))
        self.assertEqual(self.catalog.claim_job("manual")["origin"],"manual")
