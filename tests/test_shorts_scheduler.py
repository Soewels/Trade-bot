"""Unit tests: de planning blijft herstart-veilig en herhaalt zichzelf niet."""

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from shorts_bot.config import ShortsConfig
from shorts_bot.scheduler import MAX_ATTEMPTS, next_slot, settled, slots_for
from shorts_bot.state import Posted, State


def make_config(**kwargs):
    config = ShortsConfig()
    config.post_times = ["09:10", "14:20", "19:30"]
    config.per_day = 3
    config.jitter_minutes = 20
    for key, value in kwargs.items():
        setattr(config, key, value)
    return config


def make_state():
    return State(path=Path(tempfile.mkdtemp()) / "state.json")


class TestSlots(unittest.TestCase):
    def test_one_slot_per_configured_time(self):
        slots = slots_for(date(2026, 8, 13), make_config())
        self.assertEqual(len(slots), 3)

    def test_per_day_limits_the_slots(self):
        slots = slots_for(date(2026, 8, 13), make_config(per_day=2))
        self.assertEqual(len(slots), 2)

    def test_jitter_is_deterministic(self):
        # Zelfde dag moet altijd dezelfde tijden geven, anders draait de bot na
        # een herstart op andere momenten en telt hij tijdslots dubbel.
        config = make_config()
        first = slots_for(date(2026, 8, 13), config)
        second = slots_for(date(2026, 8, 13), config)
        self.assertEqual([s.when for s in first], [s.when for s in second])

    def test_jitter_differs_between_days(self):
        config = make_config()
        a = slots_for(date(2026, 8, 13), config)[0].when.time()
        b = slots_for(date(2026, 9, 24), config)[0].when.time()
        self.assertNotEqual(a, b)

    def test_jitter_stays_within_bounds(self):
        config = make_config(jitter_minutes=20)
        for day in (date(2026, 8, d) for d in range(1, 29)):
            for slot in slots_for(day, config):
                planned = datetime.strptime(slot.key.split("T")[1], "%H:%M").time()
                base = slot.when.replace(hour=planned.hour, minute=planned.minute)
                self.assertLessEqual(abs((slot.when - base).total_seconds()), 20 * 60)

    def test_zero_jitter_keeps_exact_times(self):
        slots = slots_for(date(2026, 8, 13), make_config(jitter_minutes=0))
        self.assertEqual([s.when.strftime("%H:%M") for s in slots],
                         ["09:10", "14:20", "19:30"])

    def test_slots_are_sorted(self):
        slots = slots_for(date(2026, 8, 13), make_config())
        self.assertEqual([s.when for s in slots], sorted(s.when for s in slots))

    def test_invalid_time_is_skipped(self):
        slots = slots_for(date(2026, 8, 13), make_config(post_times=["09:10", "kwart over"]))
        self.assertEqual(len(slots), 1)


class TestSettled(unittest.TestCase):
    def test_successful_slot_is_settled(self):
        state = make_state()
        state.record(Posted(slot="s1", niche="n", title="t", video_id="abc"))
        self.assertIn("s1", settled(state))

    def test_single_failure_is_retried(self):
        state = make_state()
        state.record(Posted(slot="s1", niche="n", title="", error="boem"))
        self.assertNotIn("s1", settled(state))

    def test_repeated_failure_stops_retrying(self):
        # Anders blijft een structurele fout in een strakke lus hangen.
        state = make_state()
        for _ in range(MAX_ATTEMPTS):
            state.record(Posted(slot="s1", niche="n", title="", error="boem"))
        self.assertIn("s1", settled(state))


class TestNextSlot(unittest.TestCase):
    def setUp(self):
        self.config = make_config(jitter_minutes=0)
        self.state = make_state()

    def test_picks_the_first_future_slot(self):
        now = datetime(2026, 8, 13, 8, 0)
        slot = next_slot(self.config, self.state, now.replace(tzinfo=None))
        self.assertTrue(slot.key.endswith("09:10"))

    def test_missed_slot_today_still_runs(self):
        now = datetime(2026, 8, 13, 10, 0)
        slot = next_slot(self.config, self.state, now)
        self.assertTrue(slot.key.endswith("09:10"))

    def test_done_slot_is_skipped(self):
        self.state.record(
            Posted(slot="2026-08-13T09:10", niche="n", title="t", video_id="abc")
        )
        slot = next_slot(self.config, self.state, datetime(2026, 8, 13, 10, 0))
        self.assertTrue(slot.key.endswith("14:20"))

    def test_rolls_over_to_tomorrow(self):
        for stamp in ("09:10", "14:20", "19:30"):
            self.state.record(
                Posted(slot=f"2026-08-13T{stamp}", niche="n", title="t", video_id="x")
            )
        slot = next_slot(self.config, self.state, datetime(2026, 8, 13, 23, 0))
        self.assertTrue(slot.key.startswith("2026-08-14"))


if __name__ == "__main__":
    unittest.main()
