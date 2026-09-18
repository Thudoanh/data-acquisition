import hashlib
import tempfile
import unittest
from pathlib import Path

from data_acquisition.catalog import Catalog
from data_acquisition.config import Config
from data_acquisition.models import ItemMetadata, LiveStatus, LocalStatus, JobType
from data_acquisition.transfer import TransferError, backup, restore


def config(root: Path) -> Config:
    return Config(root / "configs" / "youtube_sources.yaml", 180,
                  root / "data" / "raw" / "youtube", 1080, "mp4",
                  True, True, 3, 60, ())


class TransferTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.old = config(base / "old_project")
        self.new = config(base / "new_project")
        self.archive = base / "external_backup"
        self.catalog = Catalog(self.old.db_path)
        self.video_id = "abcdefghijk"
        self.catalog.upsert_item(ItemMetadata(
            self.video_id, "channel", "Channel", "Title",
            f"https://www.youtube.com/watch?v={self.video_id}", "video", LiveStatus.VOD))
        self.catalog.update_local_status(self.video_id, LocalStatus.QUEUED)
        self.catalog.update_local_status(self.video_id, LocalStatus.DOWNLOADING)
        self.catalog.update_local_status(self.video_id, LocalStatus.VALIDATING)
        self.media = self.old.output_root / "channel" / self.video_id / "source.mp4"
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b"sample media for transfer")
        (self.media.parent / "metadata.json").write_text('{"video_id":"abcdefghijk"}')
        self.catalog.mark_completed(self.video_id, self.media, self.media.stat().st_size,
                                    hashlib.sha256(self.media.read_bytes()).hexdigest(),
                                    5.0, "vod_download", True)

    def test_backup_restore_rewrites_absolute_path(self):
        self.assertEqual(backup(self.old, self.archive), 1)
        self.new.output_root.mkdir(parents=True)
        (self.new.output_root / ".gitkeep").touch()
        self.assertEqual(restore(self.new, self.archive), 1)
        new_path = self.new.output_root / "channel" / self.video_id / "source.mp4"
        self.assertEqual(new_path.read_bytes(), self.media.read_bytes())
        row = Catalog(self.new.db_path).get_item(self.video_id)
        self.assertEqual(row["local_path"], str(new_path))
        self.assertEqual(row["local_status"], "COMPLETED")
        self.assertFalse((self.archive / "youtube" / "channel" / self.video_id / "staging").exists())

    def test_restore_rejects_corrupt_backup_without_catalog(self):
        backup(self.old, self.archive)
        archived_media = self.archive / "youtube" / "channel" / self.video_id / "source.mp4"
        archived_media.write_bytes(b"tampered")
        with self.assertRaisesRegex(TransferError, "differs from catalog"):
            restore(self.new, self.archive)
        self.assertFalse(self.new.db_path.exists())

    def test_backup_rejects_running_jobs(self):
        self.catalog.upsert_item(ItemMetadata(
            "other123456", "channel", "Channel", "Other",
            "https://www.youtube.com/watch?v=other123456", "video", LiveStatus.VOD))
        self.catalog.enqueue_job("other123456", JobType.VOD_DOWNLOAD)
        self.catalog.claim_job("other123456")
        with self.assertRaisesRegex(TransferError, "Downloads are running"):
            backup(self.old, self.archive)
        self.assertFalse(self.archive.exists())

    def test_restore_rejects_existing_catalog(self):
        backup(self.old, self.archive)
        Catalog(self.new.db_path)
        with self.assertRaisesRegex(TransferError, "Catalog already exists"):
            restore(self.new, self.archive)
