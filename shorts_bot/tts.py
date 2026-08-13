"""De stem: Kokoro-82M draait de voice-over in, op de CPU.

Bewust op de CPU: de 10 GB VRAM van de kaart is de bottleneck van dit project
en die is volledig voor LTX. Kokoro haalt op een processor makkelijk sneller
dan realtime.

Wie een andere stem wil, zet SHORTS_TTS_BACKEND=command en geeft in
SHORTS_TTS_COMMAND een eigen commando op met {text} en {out} erin. Zo hoeft er
geen code bij voor een nieuwe TTS-engine.
"""

import shlex
import subprocess
from pathlib import Path


class TTSError(RuntimeError):
    """De stem kon niet worden ingesproken."""


class KokoroTTS:
    """Kokoro-82M via het `kokoro`-pakket (Apache 2.0, commercieel vrij)."""

    def __init__(self, config):
        self.config = config
        self._pipeline = None

    def _load(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            from kokoro import KPipeline
        except ImportError as exc:
            raise TTSError(
                "pakket 'kokoro' ontbreekt — pip install -r requirements-shorts.txt"
            ) from exc
        self._pipeline = KPipeline(lang_code=self.config.tts_lang_code)
        return self._pipeline

    def speak(self, text: str, out_path: Path) -> Path:
        try:
            import numpy as np
            import soundfile as sf
        except ImportError as exc:
            raise TTSError(
                "numpy en soundfile zijn nodig voor de stem — "
                "pip install -r requirements-shorts.txt"
            ) from exc

        pipeline = self._load()
        chunks = []
        for item in pipeline(text, voice=self.config.tts_voice, speed=self.config.tts_speed):
            audio = item[-1]  # (graphemes, phonemes, audio)
            if hasattr(audio, "detach"):  # torch-tensor
                audio = audio.detach().cpu().numpy()
            chunks.append(np.asarray(audio, dtype="float32").reshape(-1))

        if not chunks:
            raise TTSError("Kokoro leverde geen audio op voor deze tekst")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), np.concatenate(chunks), self.config.tts_sample_rate)
        return out_path


class CommandTTS:
    """Escape-luik: roep een willekeurig extern TTS-commando aan.

    Het commando krijgt {text} en {out} ingevuld, bijvoorbeeld:
        SHORTS_TTS_COMMAND=piper -m nl.onnx -f {out} -- {text}
    """

    def __init__(self, config):
        self.config = config

    def speak(self, text: str, out_path: Path) -> Path:
        if not self.config.tts_command:
            raise TTSError("SHORTS_TTS_COMMAND is leeg terwijl backend 'command' actief is")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            part.replace("{text}", text).replace("{out}", str(out_path))
            for part in shlex.split(self.config.tts_command)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise TTSError(f"TTS-commando faalde ({result.returncode}): {result.stderr[-400:]}")
        if not out_path.exists():
            raise TTSError(f"TTS-commando schreef geen bestand naar {out_path}")
        return out_path


class SilentTTS:
    """Stille audio van vaste lengte — voor tests en droogdraaien zonder modellen."""

    def __init__(self, config, seconds: float = 30.0):
        self.config = config
        self.seconds = seconds

    def speak(self, text: str, out_path: Path) -> Path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.config.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"anullsrc=r={self.config.tts_sample_rate}:cl=mono",
            "-t", str(self.seconds),
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise TTSError(f"ffmpeg kon geen stilte schrijven: {result.stderr[-400:]}")
        return out_path


def make_tts(config):
    """Kies de stem-backend op basis van de configuratie."""
    backend = config.tts_backend.lower()
    if backend == "kokoro":
        return KokoroTTS(config)
    if backend == "command":
        return CommandTTS(config)
    if backend in ("silent", "dummy"):
        return SilentTTS(config)
    raise TTSError(
        f"onbekende SHORTS_TTS_BACKEND {config.tts_backend!r} — kies kokoro, command of silent"
    )
