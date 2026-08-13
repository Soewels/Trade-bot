"""Unit tests: het SRT-bestand en de exportmap voor CapCut.

CapCut leest alleen SRT, en weigert een bestand met afwijkende tijdcodes. Deze
tests bewaken precies dat formaat.
"""

import re
import tempfile
import unittest
from pathlib import Path

from shorts_bot import export, niches
from shorts_bot.captions import CaptionError, Word, build_srt, group_words, srt_time
from shorts_bot.config import ShortsConfig
from shorts_bot.script_writer import Script, Shot

SAMPLE = [
    Word("The", 0.00, 0.30), Word("4%", 0.30, 0.70), Word("rule", 0.70, 1.05),
    Word("isn't", 1.05, 1.40), Word("a", 1.40, 1.52), Word("rule.", 1.52, 2.00),
    Word("It", 2.00, 2.20), Word("was", 2.20, 2.45), Word("one", 2.45, 2.70),
    Word("spreadsheet.", 2.70, 3.40),
]


class TestSrtTime(unittest.TestCase):
    def test_format_uses_a_comma(self):
        # SRT scheidt milliseconden met een komma, ASS met een punt.
        self.assertEqual(srt_time(1.05), "00:00:01,050")

    def test_zero(self):
        self.assertEqual(srt_time(0), "00:00:00,000")

    def test_hours_are_padded(self):
        self.assertEqual(srt_time(3671.5), "01:01:11,500")

    def test_rounding_does_not_overflow(self):
        # 1.9999 mag geen "1,000" milliseconden opleveren.
        stamp = srt_time(1.9999)
        self.assertNotIn(",1000", stamp)
        self.assertEqual(stamp, "00:00:02,000")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(srt_time(-5.0), "00:00:00,000")


class TestGrouping(unittest.TestCase):
    def test_breaks_on_a_full_stop(self):
        lines = group_words(SAMPLE, max_words=6)
        self.assertTrue(lines[0][-1].text.endswith("."))

    def test_respects_the_word_limit(self):
        lines = group_words(SAMPLE, max_words=3)
        self.assertTrue(all(len(line) <= 3 for line in lines))

    def test_every_word_survives(self):
        lines = group_words(SAMPLE, max_words=4)
        flat = [word for line in lines for word in line]
        self.assertEqual(len(flat), len(SAMPLE))

    def test_trailing_words_are_not_dropped(self):
        words = [Word("een", 0, 1), Word("twee", 1, 2), Word("drie", 2, 3)]
        lines = group_words(words, max_words=10)
        self.assertEqual(len(lines[0]), 3)


class TestBuildSrt(unittest.TestCase):
    def test_blocks_are_numbered_from_one(self):
        text = build_srt(SAMPLE)
        self.assertTrue(text.startswith("1\n"))

    def test_every_block_has_a_timecode(self):
        text = build_srt(SAMPLE)
        arrows = re.findall(
            r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", text
        )
        numbers = re.findall(r"^\d+$", text, flags=re.MULTILINE)
        self.assertEqual(len(arrows), len(numbers))

    def test_all_text_survives(self):
        text = build_srt(SAMPLE)
        for word in SAMPLE:
            self.assertIn(word.text, text)

    def test_end_always_follows_start(self):
        # Een blok met gelijke start en eind laat CapCut de import weigeren.
        words = [Word("Alleen", 1.0, 1.0)]
        text = build_srt(words)
        start, end = text.splitlines()[1].split(" --> ")
        self.assertNotEqual(start, end)

    def test_empty_input_is_rejected(self):
        with self.assertRaises(CaptionError):
            build_srt([])


class TestExportFolder(unittest.TestCase):
    def setUp(self):
        self.config = ShortsConfig()
        self.config.export_dir = Path(tempfile.mkdtemp()) / "export"
        self.work = Path(tempfile.mkdtemp()) / "20260813-120000-markets_finance"
        (self.work).mkdir(parents=True)

        # Nep-onderdelen, net genoeg om het kopieerwerk te controleren.
        for name in ("shot_01.mp4", "shot_02.mp4", "voice.wav", "captions.ass"):
            (self.work / name).write_text("x")
        self.final = self.work / "short.mp4"
        self.final.write_text("x")

        self.script = Script(
            title="The 4% Rule Was Never Actually a Rule",
            description="Waar het getal vandaan komt.",
            tags=["investing", "money"],
            shots=[Shot(narration="Regel een.", visual="Een bureau bij lamplicht.")],
        )

    def _export(self):
        return export.export_assets(
            self.config, niches.MARKETS_FINANCE, self.script, self.work, self.final, SAMPLE
        )

    def test_creates_every_expected_file(self):
        target = self._export()
        for name in ("short.mp4", "voice.wav", "captions.srt", "captions.ass",
                     "script.txt", "youtube.txt", "LEESMIJ.txt"):
            self.assertTrue((target / name).exists(), f"{name} ontbreekt")

    def test_shots_keep_their_order(self):
        target = self._export()
        names = sorted(p.name for p in (target / "shots").iterdir())
        self.assertEqual(names, ["shot_01.mp4", "shot_02.mp4"])

    def test_youtube_text_is_ready_to_paste(self):
        target = self._export()
        text = (target / "youtube.txt").read_text()
        self.assertIn(self.script.title, text)
        self.assertIn("not financial advice", text)
        self.assertIn("#investing", text)

    def test_script_text_carries_the_visual_prompts(self):
        target = self._export()
        text = (target / "script.txt").read_text()
        self.assertIn("Een bureau bij lamplicht", text)

    def test_readme_points_at_the_srt(self):
        target = self._export()
        self.assertIn("captions.srt", (target / "LEESMIJ.txt").read_text())

    def test_missing_final_video_does_not_break_the_export(self):
        self.final.unlink()
        target = self._export()
        self.assertFalse((target / "short.mp4").exists())
        self.assertTrue((target / "captions.srt").exists())


if __name__ == "__main__":
    unittest.main()
