"""Videobackend via ComfyUI: de route voor kaarten met weinig VRAM.

De officiële LTX-opdrachtregel wil de volledige gewichten van 46 GB. Op een
kaart met 10 GB is dat onbruikbaar. ComfyUI kan wél overweg met de
gekwantiseerde GGUF-versies van 14 à 17 GB en parkeert wat niet past in je
werkgeheugen — trager, maar het draait.

ComfyUI heeft een HTTP-API, dus automatiseren gaat prima: workflow insturen,
wachten tot hij klaar is, resultaat ophalen.

De workflow zelf komt uit jouw ComfyUI, niet uit deze code. Bouw hem daar,
exporteer hem via *Workflow -> Export (API)*, en zet op de plekken die per
video moeten wisselen een van deze plaatshouders:

    %PROMPT%   de beeldprompt van dit shot
    %SEED%     het toevalsgetal
    %WIDTH%    breedte in pixels
    %HEIGHT%   hoogte in pixels
    %FRAMES%   aantal frames

Zo blijft de bot werken als jij je workflow verbouwt of ComfyUI zijn nodes
hernoemt — er staat geen enkele node-naam in deze code.
"""

import json
import time
from pathlib import Path

VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov", ".gif")


class ComfyError(RuntimeError):
    """De ComfyUI-koppeling kon geen clip opleveren."""


def substitute(node, values):
    """Vervang de plaatshouders in een workflow, met behoud van types.

    Een waarde die exact een plaatshouder is wordt het echte type (een getal
    blijft een getal); staat de plaatshouder middenin een tekst, dan wordt hij
    als tekst ingevuld.
    """
    if isinstance(node, dict):
        return {key: substitute(value, values) for key, value in node.items()}
    if isinstance(node, list):
        return [substitute(value, values) for value in node]
    if isinstance(node, str):
        if node in values:
            return values[node]
        result = node
        for token, value in values.items():
            result = result.replace(token, str(value))
        return result
    return node


def load_workflow(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ComfyError(
            f"workflowbestand niet gevonden: {path}. Exporteer je workflow in "
            "ComfyUI via Workflow -> Export (API) en zet het pad in COMFY_WORKFLOW."
        ) from exc
    except json.JSONDecodeError as exc:
        raise ComfyError(f"workflow {path} is geen geldige JSON: {exc}") from exc

    if "nodes" in data and "links" in data:
        raise ComfyError(
            f"{path} is de gewone workflow, niet de API-versie. Exporteer opnieuw "
            "via Workflow -> Export (API) — dat levert een ander bestand op."
        )
    return data


def find_outputs(history: dict) -> list:
    """Zoek de gerenderde bestanden in het antwoord van /history."""
    found = []
    for node_output in (history.get("outputs") or {}).values():
        for entries in node_output.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("filename"):
                    found.append(entry)
    # Video's eerst: sommige workflows leveren ook nog losse voorbeeldplaatjes.
    found.sort(key=lambda e: not e["filename"].lower().endswith(VIDEO_SUFFIXES))
    return found


class ComfyBackend:
    """Stuurt shots naar een draaiende ComfyUI en haalt het resultaat op."""

    def __init__(self, config):
        self.config = config

    # --- HTTP ---------------------------------------------------------------

    def _requests(self):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover
            raise ComfyError("pakket 'requests' ontbreekt") from exc
        return requests

    def _url(self, path: str) -> str:
        return f"{self.config.comfy_url.rstrip('/')}{path}"

    def reachable(self) -> bool:
        """Draait ComfyUI? Voor `doctor`, zodat je niet pas bij het renderen faalt."""
        requests = self._requests()
        try:
            return requests.get(self._url("/system_stats"), timeout=5).ok
        except Exception:
            return False

    # --- renderen -----------------------------------------------------------

    def build_prompt_payload(self, prompt: str, seed: int) -> dict:
        cfg = self.config
        workflow = load_workflow(cfg.comfy_workflow)
        raw = json.dumps(workflow)
        if "%PROMPT%" not in raw:
            raise ComfyError(
                f"in {cfg.comfy_workflow} staat nergens %PROMPT%. Zet die plaatshouder "
                "in het tekstveld van je positieve prompt, anders krijgt elke video "
                "hetzelfde beeld."
            )
        return substitute(
            workflow,
            {
                "%PROMPT%": prompt,
                "%SEED%": seed,
                "%WIDTH%": cfg.ltx_width,
                "%HEIGHT%": cfg.ltx_height,
                "%FRAMES%": cfg.ltx_num_frames,
            },
        )

    def submit(self, payload: dict) -> str:
        requests = self._requests()
        try:
            response = requests.post(
                self._url("/prompt"),
                json={"prompt": payload, "client_id": "shorts-bot"},
                timeout=60,
            )
        except Exception as exc:
            raise ComfyError(
                f"kan ComfyUI niet bereiken op {self.config.comfy_url} — draait het? ({exc})"
            ) from exc

        if not response.ok:
            raise ComfyError(
                f"ComfyUI weigerde de workflow ({response.status_code}): "
                f"{response.text[:500]}"
            )
        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"ComfyUI gaf geen prompt_id terug: {response.text[:300]}")
        return prompt_id

    def wait(self, prompt_id: str) -> dict:
        requests = self._requests()
        deadline = time.monotonic() + self.config.comfy_timeout
        while time.monotonic() < deadline:
            time.sleep(self.config.comfy_poll_seconds)
            try:
                response = requests.get(self._url(f"/history/{prompt_id}"), timeout=30)
            except Exception:
                continue  # ComfyUI is druk; gewoon opnieuw proberen
            if not response.ok:
                continue
            history = response.json().get(prompt_id)
            if not history:
                continue
            status = (history.get("status") or {}).get("status_str", "")
            if status == "error":
                raise ComfyError(f"ComfyUI meldde een fout bij het renderen: {history.get('status')}")
            if history.get("outputs"):
                return history
        raise ComfyError(
            f"ComfyUI was na {self.config.comfy_timeout}s nog niet klaar met dit shot; "
            "verhoog COMFY_TIMEOUT of verklein LTX_WIDTH/LTX_HEIGHT"
        )

    def download(self, entry: dict, out_path: Path) -> Path:
        requests = self._requests()
        params = {
            "filename": entry["filename"],
            "subfolder": entry.get("subfolder", ""),
            "type": entry.get("type", "output"),
        }
        response = requests.get(self._url("/view"), params=params, timeout=300)
        if not response.ok:
            raise ComfyError(f"kon {entry['filename']} niet ophalen ({response.status_code})")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(response.content)
        return out_path

    def render(self, prompt: str, out_path: Path, seed: int) -> Path:
        payload = self.build_prompt_payload(prompt, seed)
        history = self.wait(self.submit(payload))

        outputs = find_outputs(history)
        if not outputs:
            raise ComfyError(
                "de workflow liep, maar leverde geen bestand op. Staat er een "
                "opslag-node in (bijvoorbeeld Save Video) aan het einde?"
            )

        first = outputs[0]
        if not first["filename"].lower().endswith(VIDEO_SUFFIXES):
            raise ComfyError(
                f"de workflow leverde {first['filename']} op, geen video. Laat hem "
                "eindigen op een node die een videobestand wegschrijft."
            )
        return self.download(first, out_path)
