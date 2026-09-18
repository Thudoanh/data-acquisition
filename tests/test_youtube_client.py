import unittest
from unittest.mock import patch

from data_acquisition.models import Channel, LiveStatus
from data_acquisition.youtube_client import YouTubeClient


class YouTubeClientTest(unittest.TestCase):
    def test_unavailable_detail_is_not_queued_from_flat_entry(self):
        client = YouTubeClient(3,0)
        def extract(url, flat, attempts=None):
            if flat:
                return {"entries": [{"id": "members_only", "live_status": "was_live"}]}
            raise RuntimeError("members-only")
        with patch.object(client,"_extract",side_effect=extract):
            self.assertEqual(list(client.scan_channel(Channel("c","https://www.youtube.com/@c"))),[])

    def test_keyword_filters_flat_entries_before_detail_fetch(self):
        client = YouTubeClient(1,0)
        channel = Channel("c","https://www.youtube.com/@c",live_keywords=("cau rong",))
        details = []
        def extract(url, flat, attempts=None):
            if flat:
                return {"entries": [{"id": "NUGc3nLuGsI", "title": "Camera Cầu Rồng"},
                                    {"id": "oC8ttZHG50I", "title": "Camera Hải Châu"}]}
            details.append(url)
            return {"id": "NUGc3nLuGsI", "title": "Camera Cầu Rồng", "live_status": "is_live"}
        with patch.object(client,"_extract",side_effect=extract):
            items = list(client.scan_channel(channel,keywords_only=True))
        self.assertEqual([item.video_id for item in items],["NUGc3nLuGsI"])
        self.assertEqual(len(details),1)

    def test_live_keyword_does_not_select_vod(self):
        channel = Channel("c","https://www.youtube.com/@c",live_keywords=("cau rong",))
        self.assertTrue(channel.selects("a","Camera Cầu Rồng",LiveStatus.LIVE))
        self.assertTrue(channel.selects("a","CAMERA CAU RONG",LiveStatus.UPCOMING))
        self.assertFalse(channel.selects("a","Camera Cầu Rồng",LiveStatus.VOD))
