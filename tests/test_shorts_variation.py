"""Unit tests: de bot mag niet elke keer hetzelfde onderwerp opleveren.

Twee mechanismen samen zorgen daarvoor: een invalshoek die per video rouleert,
en een geheugen van eerder geschreven titels waar ook niet-geplaatste concepten
in zitten.
"""

import tempfile
import unittest
from pathlib import Path

from shorts_bot import niches
from shorts_bot.config import ShortsConfig
from shorts_bot.script_writer import build_prompt, pick_angle
from shorts_bot.state import Posted, State


def make_state():
    return State(path=Path(tempfile.mkdtemp()) / "state.json")


class TestAngles(unittest.TestCase):
    def test_every_niche_has_several_angles(self):
        for niche in niches.NICHES.values():
            self.assertGreaterEqual(len(niche.angles), 8, niche.key)

    def test_angles_are_unique(self):
        for niche in niches.NICHES.values():
            self.assertEqual(len(set(niche.angles)), len(niche.angles), niche.key)

    def test_never_repeats_the_previous_angle(self):
        niche = niches.HORROR_MYSTERY
        previous = niche.angles[0]
        for _ in range(60):
            self.assertNotEqual(pick_angle(niche, previous), previous)

    def test_spreads_across_the_options(self):
        # Zonder spreiding heeft het roulerende deel geen zin.
        niche = niches.MARKETS_FINANCE
        seen = {pick_angle(niche) for _ in range(200)}
        self.assertGreaterEqual(len(seen), len(niche.angles) - 1)

    def test_survives_a_niche_with_one_angle(self):
        single = niches.MARKETS_FINANCE.__class__(
            key="x", label="X", caption_style="pop", highlight="#FFFFFF",
            youtube_category="24", brief="b", angles=("enige invalshoek",),
            visual_style="v", hashtags=(), accuracy_rule="a",
        )
        self.assertEqual(pick_angle(single, "enige invalshoek"), "enige invalshoek")


class TestPromptCarriesTheAngle(unittest.TestCase):
    def setUp(self):
        self.config = ShortsConfig()

    def test_angle_appears_in_the_prompt(self):
        angle = "a transmission nobody could trace"
        text = build_prompt(niches.HORROR_MYSTERY, self.config, [], angle)
        self.assertIn(angle, text)

    def test_prompt_warns_against_the_obvious_example(self):
        text = build_prompt(niches.HORROR_MYSTERY, self.config, [], "iets")
        self.assertIn("most famous example", text)

    def test_without_an_angle_the_section_is_absent(self):
        text = build_prompt(niches.HORROR_MYSTERY, self.config, [], "")
        self.assertNotIn("This one's angle", text)

    def test_earlier_titles_are_listed(self):
        text = build_prompt(
            niches.MARKETS_FINANCE, self.config, ["Een oude titel"], "iets"
        )
        self.assertIn("Een oude titel", text)


class TestDraftMemory(unittest.TestCase):
    def setUp(self):
        self.state = make_state()

    def test_a_previewed_script_counts(self):
        # Dit was de bug: `script` plaatst niets, dus de lijst bleef leeg en de
        # schrijver kreeg elke keer hetzelfde onderwerp terug.
        self.state.remember_draft("The Ourang Medan")
        self.assertIn("The Ourang Medan", self.state.recent_titles(10))

    def test_drafts_and_posted_are_merged(self):
        self.state.record(Posted(slot="s1", niche="n", title="Geplaatst", video_id="a"))
        self.state.remember_draft("Concept")
        titles = self.state.recent_titles(10)
        self.assertIn("Geplaatst", titles)
        self.assertIn("Concept", titles)

    def test_no_duplicates(self):
        self.state.record(Posted(slot="s1", niche="n", title="Zelfde", video_id="a"))
        self.state.remember_draft("Zelfde")
        self.assertEqual(self.state.recent_titles(10).count("Zelfde"), 1)

    def test_newest_first(self):
        self.state.remember_draft("Oud")
        self.state.remember_draft("Nieuw")
        self.assertEqual(self.state.recent_titles(1), ["Nieuw"])

    def test_empty_title_is_ignored(self):
        self.state.remember_draft("")
        self.assertEqual(self.state.recent_titles(10), [])

    def test_drafts_survive_a_restart(self):
        self.state.remember_draft("Concept")
        self.state.remember_angle("horror_mystery", "een invalshoek")
        self.state.save()

        again = State.load(self.state.path)
        self.assertIn("Concept", again.recent_titles(10))
        self.assertEqual(again.last_angles["horror_mystery"], "een invalshoek")

    def test_draft_list_stays_bounded(self):
        # Anders groeit het statusbestand ongelimiteerd en zwelt de prompt mee.
        for i in range(400):
            self.state.remember_draft(f"Titel {i}")
        self.state.save()
        again = State.load(self.state.path)
        self.assertLessEqual(len(again.drafts), 200)


if __name__ == "__main__":
    unittest.main()
