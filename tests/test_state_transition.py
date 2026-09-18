import unittest
from data_acquisition.models import LocalStatus as S, can_transition
from data_acquisition.recorder import capture_is_complete


class StateTransitionTest(unittest.TestCase):
    def test_vod_path(self):
        for a,b in zip((S.DISCOVERED,S.QUEUED,S.DOWNLOADING,S.VALIDATING),
                       (S.QUEUED,S.DOWNLOADING,S.VALIDATING,S.COMPLETED)):
            self.assertTrue(can_transition(a,b))

    def test_live_path_and_invalid(self):
        for a,b in ((S.DISCOVERED,S.WAITING),(S.WAITING,S.RECORDING),(S.RECORDING,S.VALIDATING)):
            self.assertTrue(can_transition(a,b))
        self.assertFalse(can_transition(S.DISCOVERED,S.COMPLETED))

    def test_late_live_capture_requires_replay(self):
        self.assertFalse(capture_is_complete("2026-09-17T09:00:00+00:00","2026-09-17T08:00:00+00:00"))
        self.assertTrue(capture_is_complete("2026-09-17T08:00:02+00:00","2026-09-17T08:00:00+00:00"))
