"""Unit tests: de ondertitels — kleuren, tijden en de opbouw van het ASS-bestand.

Dit is de enige stap met echte logica die zonder GPU, model of netwerk te
testen valt, dus hij krijgt de meeste aandacht.
"""

import unittest

from shorts_bot.captions import (
    CaptionError,
    Word,
    ass_colour,
    ass_time,
    build_ass,
    chunk,
    escape,
)
from shorts_bot.config import ShortsConfig


def words(*pairs):
    """Hulpje: [("The", 0.0, 0.4), ...] naar Word-objecten."""
    return [Word(text=t, start=s, end=e) for t, s, e in pairs]


SAMPLE = words(
    ("The", 0.00, 0.30),
    ("4%", 0.30, 0.70),
    ("rule", 0.70, 1.05),
    ("isn't", 1.05, 1.40),
    ("a", 1.40, 1.52),
    ("rule.", 1.52, 2.00),
    ("It", 2.00, 2.20),
    ("was", 2.20, 2.45),
)


class TestColour(unittest.TestCase):
    def test_rgb_becomes_bgr(self):
        # ASS draait de volgorde om: #FFD24A wordt &H4AD2FF&
        self.assertEqual(ass_colour("#FFD24A"), "&H4AD2FF&")

    def test_hash_is_optional(self):
        self.assertEqual(ass_colour("9FD2F5"), ass_colour("#9FD2F5"))

    def test_bad_colour_is_rejected(self):
        with self.assertRaises(CaptionError):
            ass_colour("#FFF")


class TestTime(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(ass_time(0), "0:00:00.00")

    def test_centiseconds(self):
        self.assertEqual(ass_time(1.05), "0:00:01.05")

    def test_minutes_and_hours(self):
        self.assertEqual(ass_time(3671.5), "1:01:11.50")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(ass_time(-3.0), "0:00:00.00")


class TestHelpers(unittest.TestCase):
    def test_chunk_splits_evenly(self):
        self.assertEqual(chunk([1, 2, 3, 4, 5], 2), [[1, 2], [3, 4], [5]])

    def test_escape_neutralises_braces(self):
        # Accolades starten in ASS een opdracht; ze mogen niet uit de tekst komen.
        self.assertNotIn("{", escape("a {\\b1}bold hack"))
        self.assertNotIn("}", escape("a {\\b1}bold hack"))

    def test_escape_flattens_newlines(self):
        self.assertNotIn("\n", escape("twee\nregels"))


class TestBuildAss(unittest.TestCase):
    def setUp(self):
        self.config = ShortsConfig()

    def test_header_uses_output_resolution(self):
        text = build_ass(SAMPLE, "block", self.config)
        self.assertIn("PlayResX: 1080", text)
        self.assertIn("PlayResY: 1920", text)

    def test_one_event_per_word(self):
        text = build_ass(SAMPLE, "pop", self.config)
        events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
        self.assertEqual(len(events), len(SAMPLE))

    def test_every_word_appears_somewhere(self):
        text = build_ass(SAMPLE, "block", self.config)
        for word in SAMPLE:
            self.assertIn(word.text, text)

    def test_events_have_no_gaps(self):
        # Een regel blijft staan tot het volgende woord begint, anders knippert
        # de ondertitel in de stiltes tussen woorden.
        text = build_ass(SAMPLE, "pop", self.config)
        events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
        ends = [l.split(",")[2] for l in events]
        starts = [l.split(",")[1] for l in events]
        self.assertEqual(ends[:-1], starts[1:])

    def test_active_word_is_coloured(self):
        text = build_ass(SAMPLE, "block", self.config, highlight="#FFD24A")
        self.assertIn("&H4AD2FF&", text)

    def test_pop_style_scales_the_active_word(self):
        text = build_ass(SAMPLE, "pop", self.config)
        self.assertIn("\\fscx", text)

    def test_block_style_draws_a_box(self):
        # BorderStyle 3 is het gevulde vlak achter de tekst.
        text = build_ass(SAMPLE, "block", self.config)
        style_line = next(l for l in text.splitlines() if l.startswith("Style:"))
        self.assertEqual(style_line.split(",")[15], "3")

    def test_word_style_shows_one_word_at_a_time(self):
        text = build_ass(SAMPLE, "word", self.config)
        events = [l for l in text.splitlines() if l.startswith("Dialogue:")]
        first = events[0].split(",,")[-1]
        self.assertNotIn(" ", first.replace("{\\r}", "").split("}")[-1].strip())

    def test_last_event_ends_after_it_starts(self):
        single = words(("Alleen", 1.0, 1.0))
        text = build_ass(single, "pop", self.config)
        event = next(l for l in text.splitlines() if l.startswith("Dialogue:"))
        start, end = event.split(",")[1], event.split(",")[2]
        self.assertNotEqual(start, end)

    def test_unknown_style_is_rejected(self):
        with self.assertRaises(CaptionError):
            build_ass(SAMPLE, "karaoke-deluxe", self.config)

    def test_empty_input_is_rejected(self):
        with self.assertRaises(CaptionError):
            build_ass([], "pop", self.config)


if __name__ == "__main__":
    unittest.main()
