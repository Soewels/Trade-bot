"""Unit tests: de schrijver, de nichepakketten en de montage-opdracht.

Geen netwerk: alleen het verwerken van een antwoord en het opbouwen van
opdrachten wordt getest.
"""

import json
import tempfile
import unittest
from pathlib import Path

from shorts_bot import assemble, niches, video
from shorts_bot.config import ShortsConfig
from shorts_bot.script_writer import (
    Shot,
    WriterError,
    build_prompt,
    parse_script,
    visual_prompt,
)


def answer(shots=6, **overrides):
    payload = {
        "title": "The 4% Rule Was Never Actually a Rule",
        "description": "Waar het getal vandaan komt.",
        "tags": ["investing", "#money"],
        "shots": [
            {"narration": f"Regel {i}.", "visual": f"Shot {i}, langzame push-in."}
            for i in range(1, shots + 1)
        ],
    }
    payload.update(overrides)
    return json.dumps(payload)


class TestParseScript(unittest.TestCase):
    def test_reads_a_complete_answer(self):
        script = parse_script(answer(), 6)
        self.assertEqual(len(script.shots), 6)
        self.assertTrue(script.title.startswith("The 4%"))

    def test_voiceover_joins_the_shots(self):
        script = parse_script(answer(shots=3), 3)
        self.assertEqual(script.voiceover, "Regel 1. Regel 2. Regel 3.")

    def test_hash_is_stripped_from_tags(self):
        # De tags gaan als los veld naar YouTube; een '#' hoort daar niet in.
        script = parse_script(answer(), 6)
        self.assertNotIn("#money", script.tags)
        self.assertIn("money", script.tags)

    def test_too_many_shots_are_trimmed(self):
        script = parse_script(answer(shots=9), 6)
        self.assertEqual(len(script.shots), 6)

    def test_fewer_shots_are_accepted(self):
        script = parse_script(answer(shots=4), 6)
        self.assertEqual(len(script.shots), 4)

    def test_broken_json_is_reported(self):
        with self.assertRaises(WriterError):
            parse_script("{ geen json", 6)

    def test_missing_title_is_rejected(self):
        with self.assertRaises(WriterError):
            parse_script(answer(title=""), 6)

    def test_missing_shots_is_rejected(self):
        with self.assertRaises(WriterError):
            parse_script(answer(shots=0), 6)


class TestDescription(unittest.TestCase):
    def test_finance_carries_a_disclaimer(self):
        script = parse_script(answer(), 6)
        text = script.full_description(niches.MARKETS_FINANCE)
        self.assertIn("not financial advice", text)
        self.assertIn("#investing", text)

    def test_horror_has_no_disclaimer(self):
        script = parse_script(answer(), 6)
        text = script.full_description(niches.HORROR_MYSTERY)
        self.assertNotIn("financial advice", text)
        self.assertIn("#unexplained", text)


class TestPrompt(unittest.TestCase):
    def setUp(self):
        self.config = ShortsConfig()

    def test_prompt_names_the_shot_count(self):
        text = build_prompt(niches.MARKETS_FINANCE, self.config, [])
        self.assertIn(f"Exactly {self.config.shots_per_video} shots", text)

    def test_recent_titles_are_included(self):
        text = build_prompt(niches.HORROR_MYSTERY, self.config, ["Een oude titel"])
        self.assertIn("Een oude titel", text)

    def test_accuracy_rule_travels_with_the_niche(self):
        text = build_prompt(niches.HORROR_MYSTERY, self.config, [])
        self.assertIn("disputed", text)

    def test_visual_prompt_appends_the_house_style(self):
        shot = Shot(narration="x", visual="Een leeg dek in de mist")
        prompt = visual_prompt(shot, niches.HORROR_MYSTERY)
        self.assertTrue(prompt.startswith("Een leeg dek in de mist."))
        self.assertIn("desaturated cold palette", prompt)


class TestNiches(unittest.TestCase):
    def test_rotation_alternates(self):
        keys = ["markets_finance", "horror_mystery"]
        self.assertEqual(niches.rotate(keys, 0).key, "markets_finance")
        self.assertEqual(niches.rotate(keys, 1).key, "horror_mystery")
        self.assertEqual(niches.rotate(keys, 2).key, "markets_finance")

    def test_unknown_niche_names_the_alternatives(self):
        with self.assertRaises(KeyError) as ctx:
            niches.get("kookvideos")
        self.assertIn("markets_finance", str(ctx.exception))

    def test_each_niche_has_a_valid_caption_style(self):
        for niche in niches.NICHES.values():
            self.assertIn(niche.caption_style, ("block", "pop", "word"))

    def test_empty_rotation_is_rejected(self):
        with self.assertRaises(ValueError):
            niches.rotate([], 0)


class TestFfmpeg(unittest.TestCase):
    def setUp(self):
        self.config = ShortsConfig()

    def test_filter_crops_to_the_output_size(self):
        graph = assemble.build_filter(self.config, mix_ambience=False)
        self.assertIn("crop=1080:1920", graph)
        self.assertIn("ass=captions.ass", graph)

    def test_filter_pads_the_video(self):
        # Zonder tpad kapt -shortest de video af als de stem net langer duurt.
        graph = assemble.build_filter(self.config, mix_ambience=False)
        self.assertIn("tpad", graph)

    def test_ambience_is_ducked_under_the_voice(self):
        graph = assemble.build_filter(self.config, mix_ambience=True)
        self.assertIn(f"volume={self.config.ambience_gain_db}dB", graph)
        self.assertIn("amix", graph)

    def test_without_ambience_there_is_no_mix(self):
        graph = assemble.build_filter(self.config, mix_ambience=False)
        self.assertNotIn("amix", graph)

    def test_concat_list_uses_bare_filenames(self):
        # Het lijstbestand staat naast de clips; een volledig pad met spaties of
        # een dubbele punt breekt de concat-demuxer.
        work = Path(tempfile.mkdtemp())
        path = assemble.write_concat_list(
            [work / "shot_01.mp4", work / "shot_02.mp4"], work / "shots.txt"
        )
        self.assertEqual(path.read_text().splitlines(),
                         ["file 'shot_01.mp4'", "file 'shot_02.mp4'"])


class TestLtxCommand(unittest.TestCase):
    def setUp(self):
        self.config = ShortsConfig()
        self.backend = video.LtxBackend(self.config)

    def test_command_carries_prompt_and_output(self):
        cmd = self.backend.build_command("een lege kade", Path("/tmp/out.mp4"), seed=7)
        self.assertIn("--prompt", cmd)
        self.assertEqual(cmd[cmd.index("--prompt") + 1], "een lege kade")
        self.assertEqual(cmd[cmd.index("--output-path") + 1], "/tmp/out.mp4")

    def test_resolution_flags_are_configurable(self):
        self.config.ltx_width_flag = "--w"
        self.config.ltx_height_flag = "--h"
        cmd = self.backend.build_command("x", Path("/tmp/out.mp4"), seed=1)
        self.assertIn("--w", cmd)
        self.assertNotIn("--width", cmd)

    def test_model_paths_become_absolute(self):
        cmd = self.backend.build_command("x", Path("/tmp/out.mp4"), seed=1)
        transformer = cmd[cmd.index("--transformer-path") + 1]
        self.assertTrue(Path(transformer).is_absolute())

    def test_empty_upsampler_is_omitted(self):
        self.config.ltx_spatial_upsampler = ""
        cmd = self.backend.build_command("x", Path("/tmp/out.mp4"), seed=1)
        self.assertNotIn("--spatial-upsampler-path", cmd)

    def test_extra_args_are_passed_through(self):
        self.config.ltx_extra_args = "--guidance 4.5"
        cmd = self.backend.build_command("x", Path("/tmp/out.mp4"), seed=1)
        self.assertIn("--guidance", cmd)
        self.assertIn("4.5", cmd)

    def test_unknown_backend_is_rejected(self):
        self.config.video_backend = "sora"
        with self.assertRaises(video.VideoError):
            video.make_video_backend(self.config)


if __name__ == "__main__":
    unittest.main()
