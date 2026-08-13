"""Unit tests: de status overleeft een herstart en houdt het quotum bij."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from shorts_bot.config import ShortsConfig
from shorts_bot.state import Posted, State


class TestRoundTrip(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "state.json"

    def test_missing_file_gives_empty_state(self):
        state = State.load(self.path)
        self.assertEqual(state.posted, [])

    def test_survives_a_restart(self):
        state = State.load(self.path)
        state.record(Posted(slot="s1", niche="markets_finance", title="Titel", video_id="abc"))
        state.save()

        again = State.load(self.path)
        self.assertEqual(again.successful(), 1)
        self.assertEqual(again.posted[0].title, "Titel")

    def test_corrupt_file_does_not_crash_the_bot(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ dit is geen json")
        state = State.load(self.path)
        self.assertEqual(state.posted, [])

    def test_save_is_atomic(self):
        # Er mag nooit een half geschreven statusbestand achterblijven.
        state = State.load(self.path)
        state.record(Posted(slot="s1", niche="n", title="t", video_id="v"))
        state.save()
        json.loads(self.path.read_text())
        self.assertFalse(self.path.with_suffix(".tmp").exists())


class TestSlots(unittest.TestCase):
    def setUp(self):
        self.state = State(path=Path(tempfile.mkdtemp()) / "state.json")

    def test_has_slot_only_counts_success(self):
        self.state.record(Posted(slot="s1", niche="n", title="", error="boem"))
        self.assertFalse(self.state.has_slot("s1"))
        self.state.record(Posted(slot="s1", niche="n", title="t", video_id="abc"))
        self.assertTrue(self.state.has_slot("s1"))

    def test_recent_titles_skips_failures(self):
        self.state.record(Posted(slot="s1", niche="n", title="Eerste", video_id="a"))
        self.state.record(Posted(slot="s2", niche="n", title="", error="boem"))
        self.assertEqual(self.state.recent_titles(10), ["Eerste"])

    def test_recent_titles_respects_the_limit(self):
        for i in range(30):
            self.state.record(Posted(slot=f"s{i}", niche="n", title=f"T{i}", video_id="v"))
        self.assertEqual(len(self.state.recent_titles(5)), 5)

    def test_count_per_niche(self):
        self.state.record(Posted(slot="a", niche="horror_mystery", title="t", video_id="1"))
        self.state.record(Posted(slot="b", niche="markets_finance", title="t", video_id="2"))
        self.state.record(Posted(slot="c", niche="horror_mystery", title="t", video_id="3"))
        self.assertEqual(self.state.count_for_niche("horror_mystery"), 2)

    def test_posted_today_filters_on_date(self):
        self.state.record(
            Posted(slot="a", niche="n", title="t", video_id="1", posted_at="2026-08-13T09:12:00Z")
        )
        self.state.record(
            Posted(slot="b", niche="n", title="t", video_id="2", posted_at="2026-08-12T09:12:00Z")
        )
        self.assertEqual(self.state.posted_today(date(2026, 8, 13)), 1)


class TestQuota(unittest.TestCase):
    def setUp(self):
        self.state = State(path=Path(tempfile.mkdtemp()) / "state.json")
        self.config = ShortsConfig()

    def test_new_day_resets_the_quota(self):
        self.state.roll_quota(date(2026, 8, 13))
        self.state.spend_quota(3200)
        self.state.roll_quota(date(2026, 8, 14))
        self.assertEqual(self.state.quota_used, 0)

    def test_same_day_keeps_the_quota(self):
        self.state.roll_quota(date(2026, 8, 13))
        self.state.spend_quota(1600)
        self.state.roll_quota(date(2026, 8, 13))
        self.assertEqual(self.state.quota_used, 1600)

    def test_default_quota_allows_six_uploads(self):
        # 10.000 eenheden gedeeld door 1600 per upload.
        self.assertEqual(self.config.uploads_left_today(0), 6)

    def test_quota_runs_out(self):
        self.assertEqual(self.config.uploads_left_today(9600), 0)

    def test_three_a_day_fits_comfortably(self):
        used = 3 * self.config.youtube_upload_cost
        self.assertGreaterEqual(self.config.uploads_left_today(used), 1)


if __name__ == "__main__":
    unittest.main()
