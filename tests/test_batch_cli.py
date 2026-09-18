import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import download_video  # noqa: E402


class BatchCliTest(unittest.TestCase):
    def test_read_list_skips_comments_and_empty_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "videos.txt"
            path.write_text("\ufeff# note\n\n https://youtu.be/NUGc3nLuGsI \n oC8ttZHG50I\n", encoding="utf-8")
            self.assertEqual(download_video.read_list(path),
                             ["https://youtu.be/NUGc3nLuGsI", "oC8ttZHG50I"])

    def test_batch_deduplicates_and_continues_after_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "videos.txt"
            path.write_text("NUGc3nLuGsI\nhttps://youtu.be/NUGc3nLuGsI\noC8ttZHG50I\n")
            config = root / "configs" / "sources.yaml"
            config.parent.mkdir()
            config.write_text("""poll_interval_sec: 180
output: {root_dir: data/raw/youtube}
download: {max_height: 1080, merge_format: mp4}
live: {try_from_start: true, recovery_from_replay: true}
retry: {max_attempts: 3, retry_delay_sec: 0}
channels:
  - {id: c, url: 'https://www.youtube.com/@c', enabled: true}
""")
            calls = []
            def fake_download(video_id, *_args):
                calls.append(video_id)
                if video_id == "oC8ttZHG50I":
                    raise RuntimeError("unavailable")
                return "COMPLETED", "done"
            out, err = io.StringIO(), io.StringIO()
            argv = ["download_video.py", "--file", str(path), "--config", str(config)]
            with patch.object(sys, "argv", argv), patch.object(download_video,"download_one",side_effect=fake_download), \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as exit_result:
                    download_video.main()
            self.assertEqual(exit_result.exception.code,1)
            self.assertEqual(calls,["NUGc3nLuGsI","oC8ttZHG50I"])
            self.assertIn("duplicate in list",out.getvalue())
            self.assertIn("completed=1",out.getvalue())
            self.assertIn("failed=1",out.getvalue())
