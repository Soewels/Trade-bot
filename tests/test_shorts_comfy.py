"""Unit tests: de ComfyUI-koppeling, zonder draaiende ComfyUI.

Getest wordt wat er misgaat als de gebruiker iets vergeet — een verkeerd
geëxporteerde workflow, een ontbrekende plaatshouder, een workflow die geen
video oplevert. Dat zijn de fouten die je anders pas na twintig minuten
renderen ontdekt.
"""

import json
import tempfile
import unittest
from pathlib import Path

from shorts_bot import video
from shorts_bot.comfy import (
    ComfyBackend,
    ComfyError,
    find_outputs,
    load_workflow,
    substitute,
)
from shorts_bot.config import ShortsConfig

WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": "%SEED%", "steps": 8, "denoise": 1.0},
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "%PROMPT%, cinematic, film grain"},
    },
    "7": {
        "class_type": "EmptyLatentVideo",
        "inputs": {"width": "%WIDTH%", "height": "%HEIGHT%", "length": "%FRAMES%"},
    },
}


def write_workflow(data) -> Path:
    path = Path(tempfile.mkdtemp()) / "workflow.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestSubstitute(unittest.TestCase):
    def setUp(self):
        self.values = {
            "%PROMPT%": "een leeg dek in de mist",
            "%SEED%": 977,
            "%WIDTH%": 544,
            "%HEIGHT%": 960,
            "%FRAMES%": 121,
        }

    def test_numbers_stay_numbers(self):
        # Een exacte plaatshouder moet zijn echte type terugkrijgen; ComfyUI
        # weigert een workflow waarin seed als tekst binnenkomt.
        result = substitute(WORKFLOW, self.values)
        self.assertEqual(result["3"]["inputs"]["seed"], 977)
        self.assertIsInstance(result["3"]["inputs"]["seed"], int)

    def test_placeholder_inside_text_is_filled_in(self):
        result = substitute(WORKFLOW, self.values)
        self.assertEqual(
            result["6"]["inputs"]["text"],
            "een leeg dek in de mist, cinematic, film grain",
        )

    def test_untouched_values_survive(self):
        result = substitute(WORKFLOW, self.values)
        self.assertEqual(result["3"]["inputs"]["steps"], 8)
        self.assertEqual(result["3"]["inputs"]["denoise"], 1.0)

    def test_resolution_comes_through(self):
        result = substitute(WORKFLOW, self.values)
        self.assertEqual(result["7"]["inputs"]["width"], 544)
        self.assertEqual(result["7"]["inputs"]["length"], 121)

    def test_lists_are_walked(self):
        data = {"a": {"inputs": {"link": ["6", 0], "text": "%PROMPT%"}}}
        result = substitute(data, self.values)
        self.assertEqual(result["a"]["inputs"]["link"], ["6", 0])
        self.assertEqual(result["a"]["inputs"]["text"], "een leeg dek in de mist")


class TestLoadWorkflow(unittest.TestCase):
    def test_reads_an_api_workflow(self):
        self.assertEqual(load_workflow(write_workflow(WORKFLOW)), WORKFLOW)

    def test_rejects_the_wrong_export(self):
        # De gewone export heeft 'nodes' en 'links'; die vorm accepteert /prompt niet.
        path = write_workflow({"nodes": [], "links": [], "version": 0.4})
        with self.assertRaises(ComfyError) as ctx:
            load_workflow(path)
        self.assertIn("Export (API)", str(ctx.exception))

    def test_missing_file_names_the_setting(self):
        with self.assertRaises(ComfyError) as ctx:
            load_workflow(Path("/bestaat/niet.json"))
        self.assertIn("COMFY_WORKFLOW", str(ctx.exception))

    def test_broken_json_is_reported(self):
        path = Path(tempfile.mkdtemp()) / "kapot.json"
        path.write_text("{ dit is geen json")
        with self.assertRaises(ComfyError):
            load_workflow(path)


class TestPayload(unittest.TestCase):
    def setUp(self):
        self.config = ShortsConfig()
        self.config.video_backend = "comfy"

    def test_builds_a_complete_payload(self):
        self.config.comfy_workflow = write_workflow(WORKFLOW)
        payload = ComfyBackend(self.config).build_prompt_payload("een kade", 5)
        self.assertIn("een kade", payload["6"]["inputs"]["text"])
        self.assertEqual(payload["3"]["inputs"]["seed"], 5)

    def test_workflow_without_prompt_placeholder_is_rejected(self):
        # Zonder %PROMPT% krijgt elke video hetzelfde beeld — dat wil je weten
        # voordat je zes shots lang staat te renderen.
        self.config.comfy_workflow = write_workflow(
            {"6": {"class_type": "CLIPTextEncode", "inputs": {"text": "vaste tekst"}}}
        )
        with self.assertRaises(ComfyError) as ctx:
            ComfyBackend(self.config).build_prompt_payload("een kade", 1)
        self.assertIn("%PROMPT%", str(ctx.exception))


class TestFindOutputs(unittest.TestCase):
    def test_finds_a_video(self):
        history = {"outputs": {"9": {"gifs": [{"filename": "shot.mp4", "subfolder": "v"}]}}}
        self.assertEqual(find_outputs(history)[0]["filename"], "shot.mp4")

    def test_video_wins_from_a_preview_image(self):
        # Veel workflows leveren naast de video ook een voorbeeldplaatje op.
        history = {
            "outputs": {
                "8": {"images": [{"filename": "preview.png"}]},
                "9": {"videos": [{"filename": "shot.mp4"}]},
            }
        }
        self.assertEqual(find_outputs(history)[0]["filename"], "shot.mp4")

    def test_empty_history_gives_nothing(self):
        self.assertEqual(find_outputs({"outputs": {}}), [])
        self.assertEqual(find_outputs({}), [])

    def test_odd_entries_are_skipped(self):
        history = {"outputs": {"9": {"text": ["klaar"], "gifs": [{"filename": "a.mp4"}]}}}
        self.assertEqual(len(find_outputs(history)), 1)


class TestBackendSelection(unittest.TestCase):
    def test_comfy_is_selectable(self):
        config = ShortsConfig()
        config.video_backend = "comfy"
        self.assertIsInstance(video.make_video_backend(config), ComfyBackend)

    def test_unknown_backend_lists_the_options(self):
        config = ShortsConfig()
        config.video_backend = "sora"
        with self.assertRaises(video.VideoError) as ctx:
            video.make_video_backend(config)
        self.assertIn("comfy", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
