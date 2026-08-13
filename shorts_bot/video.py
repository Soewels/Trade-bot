"""Het beeld: LTX-2.3 rendert per shot een clip met sfeergeluid.

De officiële LTX-2 repository levert een module die je als script aanroept. Dat
is wat hier gebeurt: één subprocess per shot. Vlagnamen die per versie kunnen
verschillen (breedte, hoogte, extra opties) staan in de configuratie, zodat een
nieuwe LTX-versie geen codewijziging vraagt — `python -m shorts_bot.main doctor`
laat de beschikbare vlaggen van jouw installatie zien.

Naast LTX zit er een dummy-backend in die met ffmpeg een testclip maakt. Daarmee
draait de hele pipeline op een machine zonder GPU, wat de tests en het
droogdraaien mogelijk maakt.
"""

import shlex
import subprocess
from pathlib import Path


class VideoError(RuntimeError):
    """Een shot kon niet worden gerenderd."""


class LtxBackend:
    """LTX-2.3 via de meegeleverde pipeline-module in de LTX-2 repository."""

    def __init__(self, config):
        self.config = config

    def _model_path(self, relative: str) -> str:
        if not relative:
            return ""
        path = Path(relative)
        if not path.is_absolute():
            path = self.config.ltx_repo / path
        return str(path)

    def build_command(self, prompt: str, out_path: Path, seed: int) -> list:
        cfg = self.config
        cmd = shlex.split(cfg.ltx_python) + ["-m", cfg.ltx_module]
        cmd += ["--transformer-path", self._model_path(cfg.ltx_transformer)]
        cmd += ["--text-encoder-path", self._model_path(cfg.ltx_text_encoder)]
        cmd += ["--video-vae-path", self._model_path(cfg.ltx_video_vae)]
        if cfg.ltx_audio_vae:
            cmd += ["--audio-vae-path", self._model_path(cfg.ltx_audio_vae)]
        if cfg.ltx_spatial_upsampler:
            cmd += ["--spatial-upsampler-path", self._model_path(cfg.ltx_spatial_upsampler)]
        cmd += [cfg.ltx_width_flag, str(cfg.ltx_width)]
        cmd += [cfg.ltx_height_flag, str(cfg.ltx_height)]
        cmd += ["--num-frames", str(cfg.ltx_num_frames)]
        cmd += ["--seed", str(seed)]
        if cfg.ltx_quantization:
            cmd += ["--quantization", cfg.ltx_quantization]
        if cfg.ltx_offload:
            cmd += ["--offload", cfg.ltx_offload]
        if cfg.ltx_extra_args:
            cmd += shlex.split(cfg.ltx_extra_args)
        cmd += ["--output-path", str(out_path), "--prompt", prompt]
        return cmd

    def render(self, prompt: str, out_path: Path, seed: int) -> Path:
        cfg = self.config
        if not cfg.ltx_repo.exists():
            raise VideoError(
                f"LTX-repository niet gevonden op {cfg.ltx_repo} — zet LTX_REPO in .env"
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = self.build_command(prompt, out_path, seed)
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cfg.ltx_repo),
                capture_output=True,
                text=True,
                timeout=cfg.ltx_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise VideoError(
                f"LTX liep langer dan {cfg.ltx_timeout}s voor één shot; "
                "verhoog LTX_TIMEOUT of verlaag LTX_WIDTH/LTX_HEIGHT"
            ) from exc

        if result.returncode != 0:
            tail = (result.stderr or result.stdout)[-800:]
            raise VideoError(f"LTX faalde ({result.returncode}):\n{tail}")
        if not out_path.exists():
            raise VideoError(f"LTX gaf geen fout maar schreef niets naar {out_path}")
        return out_path


class DummyBackend:
    """Testclip met ffmpeg: bewegend kleurverloop plus een zachte toon."""

    def __init__(self, config):
        self.config = config

    def render(self, prompt: str, out_path: Path, seed: int) -> Path:
        cfg = self.config
        seconds = round(cfg.ltx_num_frames / max(cfg.ltx_fps, 1), 2)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            cfg.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi",
            "-i", f"gradients=s={cfg.ltx_width}x{cfg.ltx_height}:d={seconds}:r={cfg.ltx_fps}",
            "-f", "lavfi",
            "-i", f"sine=frequency={220 + (seed % 6) * 40}:duration={seconds}",
            "-shortest",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "28",
            "-c:a", "aac", "-b:a", "96k",
            str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise VideoError(f"ffmpeg kon geen testclip maken: {result.stderr[-400:]}")
        return out_path


def make_video_backend(config):
    backend = config.video_backend.lower()
    if backend == "ltx":
        return LtxBackend(config)
    if backend in ("dummy", "test"):
        return DummyBackend(config)
    raise VideoError(
        f"onbekende SHORTS_VIDEO_BACKEND {config.video_backend!r} — kies ltx of dummy"
    )
