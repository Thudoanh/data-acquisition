import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_acquisition.catalog import Catalog
from data_acquisition.config import Config
from data_acquisition.downloader import process_job
from data_acquisition.models import ItemMetadata, LiveStatus, LocalStatus, JobType


class ReplayRecoveryTest(unittest.TestCase):
    def test_replay_replaces_validated_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = Config(root / "configs" / "test.yaml",180,root / "data",1080,"mp4",True,True,3,0,())
            catalog = Catalog(root / "state" / "catalog.db")
            catalog.upsert_item(ItemMetadata("abc","channel","Channel","Live",
                              "https://www.youtube.com/watch?v=abc","live",LiveStatus.ENDED))
            old = config.output_root / "channel" / "abc" / "source.mp4"
            old.parent.mkdir(parents=True)
            old.write_bytes(b"incomplete")
            for status in (LocalStatus.QUEUED,LocalStatus.RECORDING,LocalStatus.VALIDATING):
                catalog.update_local_status("abc",status)
            catalog.mark_completed("abc",old,old.stat().st_size,"old",1,"live_record",False)
            self.assertTrue(catalog.enqueue_job("abc",JobType.REPLAY_RECOVERY))
            job = catalog.claim_job()

            def fake_download(url, staging, cfg, live, cancel):
                staging.mkdir(parents=True)
                path = staging / "source.mp4"
                path.write_bytes(b"full replay")
                return path

            with patch("data_acquisition.downloader.run_ytdlp",side_effect=fake_download), \
                 patch("data_acquisition.downloader.validate_video",return_value=2):
                process_job(catalog,config,job)
            row = catalog.get_item("abc")
            self.assertEqual(row["local_status"],"COMPLETED")
            self.assertEqual(row["capture_source"],"replay_recovery")
            self.assertEqual(row["capture_complete"],1)
            self.assertEqual(old.read_bytes(),b"full replay")
            self.assertFalse((old.parent / "partial").exists())
            metadata = json.loads((old.parent / "metadata.json").read_text())
            self.assertEqual(metadata["source_url"],"https://www.youtube.com/watch?v=abc")
