"""De montage: shots aan elkaar, ondertitels erin, sfeergeluid onder de stem.

Alles gebeurt in één ffmpeg-aanroep vanuit de werkmap, zodat de bestandsnamen
in het filtergraph kort blijven — een pad met een dubbele punt of een spatie
erin breekt de `ass`-filter anders stilletjes.

De video wordt achteraan verlengd met een bevroren laatste beeld (`tpad`) en
daarna afgekapt op de lengte van de voice-over. Zo eindigt de Short altijd
precies op het laatste woord, ook als de stem net iets langer duurt dan de
gerenderde clips.
"""

import json
import subprocess
from pathlib import Path


class AssembleError(RuntimeError):
    """De montage is mislukt."""


def _run(cmd, cwd=None, what="ffmpeg"):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise AssembleError(f"{what} faalde ({result.returncode}):\n{result.stderr[-800:]}")
    return result


def probe_duration(path: Path, config) -> float:
    """Lengte van een mediabestand in seconden."""
    result = _run(
        [
            config.ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        what="ffprobe",
    )
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise AssembleError(f"kon de lengte van {path.name} niet uitlezen") from exc


def has_audio(path: Path, config) -> bool:
    """Heeft dit bestand een audiospoor? Bepaalt of we sfeergeluid kunnen mixen."""
    result = _run(
        [
            config.ffprobe, "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "json", str(path),
        ],
        what="ffprobe",
    )
    try:
        return bool(json.loads(result.stdout).get("streams"))
    except json.JSONDecodeError:
        return False


def write_concat_list(shots, list_path: Path) -> Path:
    """Het lijstbestand voor de concat-demuxer van ffmpeg."""
    lines = [f"file '{Path(shot).name}'" for shot in shots]
    list_path.write_text("\n".join(lines) + "\n")
    return list_path


def concat_shots(shots, work_dir: Path, config) -> Path:
    """Plak de losse shots achter elkaar tot één ruwe clip."""
    if not shots:
        raise AssembleError("geen shots om samen te voegen")
    list_path = write_concat_list(shots, work_dir / "shots.txt")
    out = work_dir / "raw.mp4"
    _run(
        [
            config.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", list_path.name,
            "-c", "copy", out.name,
        ],
        cwd=str(work_dir),
    )
    return out


def build_filter(config, mix_ambience: bool) -> str:
    """Het filtergraph: bijsnijden naar 9:16, ondertitels inbranden, audio mixen."""
    video = (
        f"[0:v]scale={config.out_width}:{config.out_height}:"
        "force_original_aspect_ratio=increase,"
        f"crop={config.out_width}:{config.out_height},"
        "tpad=stop_mode=clone:stop_duration=8,"
        "ass=captions.ass[v]"
    )
    if not mix_ambience:
        return video
    audio = (
        f"[0:a]volume={config.ambience_gain_db}dB,"
        "apad[amb];"
        "[1:a]volume=0dB[vo];"
        "[amb][vo]amix=inputs=2:duration=shortest:normalize=0[a]"
    )
    return f"{video};{audio}"


def render_final(raw_video: Path, voice: Path, ass_file: Path, out: Path, config) -> Path:
    """Zet de ruwe clip, de stem en de ondertitels om in de definitieve Short."""
    work_dir = raw_video.parent
    mix = config.ambience and has_audio(raw_video, config)

    cmd = [
        config.ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", raw_video.name,
        "-i", voice.name,
        "-filter_complex", build_filter(config, mix),
        "-map", "[v]",
        "-map", "[a]" if mix else "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(config.video_crf),
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest",
        out.name,
    ]
    if ass_file.parent != work_dir:
        raise AssembleError("het ondertitelbestand moet in dezelfde werkmap staan als de video")
    _run(cmd, cwd=str(work_dir))
    if not out.exists():
        raise AssembleError(f"ffmpeg schreef geen bestand naar {out}")
    return out
