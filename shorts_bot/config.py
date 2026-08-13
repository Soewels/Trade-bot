"""Instellingen voor de Shorts-bot, gelezen uit .env (zie .env.example).

Alles wat per machine of per kanaal verschilt staat hier, zodat je niets in de
code hoeft aan te passen. De standaardwaarden gaan uit van een RTX 3080 met
10 GB VRAM: LTX-2.3 in Q4-kwantisatie, 544x960 renderen en daarna opschalen.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimale .env-lader: KEY=VALUE, '#' is commentaar (zoals config.py)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_dotenv(PROJECT_ROOT / ".env")


def _str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, "1" if default else "0").strip() in ("1", "true", "yes", "on")


def _list(key: str, default: str) -> list:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class ShortsConfig:
    """Alle instellingen van de bot in één object."""

    # --- wat en hoe vaak ---------------------------------------------------
    niches: list = field(default_factory=lambda: ["markets_finance", "horror_mystery"])
    language: str = "en"
    per_day: int = 3
    post_times: list = field(default_factory=lambda: ["09:10", "14:20", "19:30"])
    jitter_minutes: int = 20
    timezone: str = "Europe/Amsterdam"
    privacy: str = "private"

    # --- mappen ------------------------------------------------------------
    state_dir: Path = PROJECT_ROOT / ".shorts_state"
    work_dir: Path = PROJECT_ROOT / ".shorts_work"
    keep_work_dirs: bool = False
    dry_run: bool = False

    # --- schrijver (Claude) ------------------------------------------------
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5"
    claude_effort: str = "medium"
    shots_per_video: int = 6
    dedupe_history: int = 60

    # --- stem (Kokoro) -----------------------------------------------------
    tts_backend: str = "kokoro"
    tts_voice: str = "am_michael"
    tts_lang_code: str = "a"
    tts_speed: float = 1.0
    tts_sample_rate: int = 24000
    tts_command: str = ""

    # --- beeld (LTX-2.3) ---------------------------------------------------
    video_backend: str = "ltx"
    ltx_repo: Path = Path.home() / "LTX-2"
    ltx_module: str = "ltx_pipelines.distilled"
    ltx_python: str = "uv run python"
    ltx_transformer: str = "models/ltx-2.3/diffusion_models/ltx-2.3-distilled-transformer-q4_k_s.gguf"
    ltx_text_encoder: str = "models/ltx-2.3/text_encoders/text-encoder.safetensors"
    ltx_video_vae: str = "models/ltx-2.3/vae/video-vae.safetensors"
    ltx_audio_vae: str = "models/ltx-2.3/vae/audio-vae.safetensors"
    ltx_spatial_upsampler: str = ""
    ltx_width: int = 544
    ltx_height: int = 960
    ltx_num_frames: int = 121
    ltx_fps: int = 24
    ltx_quantization: str = "fp8-cast"
    ltx_offload: str = "cpu"
    ltx_width_flag: str = "--width"
    ltx_height_flag: str = "--height"
    ltx_extra_args: str = ""
    ltx_timeout: int = 1800

    # --- woord-timing (Whisper) --------------------------------------------
    whisper_model: str = "base.en"
    whisper_device: str = "cpu"
    whisper_compute: str = "int8"

    # --- montage -----------------------------------------------------------
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    out_width: int = 1080
    out_height: int = 1920
    caption_font: str = "Arial"
    caption_font_file: str = ""
    ambience: bool = True
    ambience_gain_db: float = -18.0
    video_crf: int = 20

    # --- YouTube -----------------------------------------------------------
    youtube_client_secrets: Path = PROJECT_ROOT / "client_secret.json"
    youtube_token_file: Path = PROJECT_ROOT / ".shorts_state" / "youtube_token.json"
    youtube_quota_limit: int = 10000
    youtube_upload_cost: int = 1600
    made_for_kids: bool = False

    @classmethod
    def from_env(cls) -> "ShortsConfig":
        state_dir = Path(_str("SHORTS_STATE_DIR", str(PROJECT_ROOT / ".shorts_state"))).expanduser()
        return cls(
            niches=_list("SHORTS_NICHES", "markets_finance,horror_mystery"),
            language=_str("SHORTS_LANGUAGE", "en"),
            per_day=_int("SHORTS_PER_DAY", 3),
            post_times=_list("SHORTS_POST_TIMES", "09:10,14:20,19:30"),
            jitter_minutes=_int("SHORTS_JITTER_MINUTES", 20),
            timezone=_str("SHORTS_TIMEZONE", "Europe/Amsterdam"),
            privacy=_str("SHORTS_PRIVACY", "private"),
            state_dir=state_dir,
            work_dir=Path(_str("SHORTS_WORK_DIR", str(PROJECT_ROOT / ".shorts_work"))).expanduser(),
            keep_work_dirs=_bool("SHORTS_KEEP_WORK", False),
            dry_run=_bool("SHORTS_DRY_RUN", False),
            anthropic_api_key=_str("ANTHROPIC_API_KEY"),
            claude_model=_str("SHORTS_CLAUDE_MODEL", "claude-opus-5"),
            claude_effort=_str("SHORTS_CLAUDE_EFFORT", "medium"),
            shots_per_video=_int("SHORTS_SHOTS", 6),
            dedupe_history=_int("SHORTS_DEDUPE_HISTORY", 60),
            tts_backend=_str("SHORTS_TTS_BACKEND", "kokoro"),
            tts_voice=_str("SHORTS_TTS_VOICE", "am_michael"),
            tts_lang_code=_str("SHORTS_TTS_LANG", "a"),
            tts_speed=_float("SHORTS_TTS_SPEED", 1.0),
            tts_sample_rate=_int("SHORTS_TTS_SAMPLE_RATE", 24000),
            tts_command=_str("SHORTS_TTS_COMMAND"),
            video_backend=_str("SHORTS_VIDEO_BACKEND", "ltx"),
            ltx_repo=Path(_str("LTX_REPO", str(Path.home() / "LTX-2"))).expanduser(),
            ltx_module=_str("LTX_MODULE", "ltx_pipelines.distilled"),
            ltx_python=_str("LTX_PYTHON", "uv run python"),
            ltx_transformer=_str("LTX_TRANSFORMER", ShortsConfig.ltx_transformer),
            ltx_text_encoder=_str("LTX_TEXT_ENCODER", ShortsConfig.ltx_text_encoder),
            ltx_video_vae=_str("LTX_VIDEO_VAE", ShortsConfig.ltx_video_vae),
            ltx_audio_vae=_str("LTX_AUDIO_VAE", ShortsConfig.ltx_audio_vae),
            ltx_spatial_upsampler=_str("LTX_SPATIAL_UPSAMPLER"),
            ltx_width=_int("LTX_WIDTH", 544),
            ltx_height=_int("LTX_HEIGHT", 960),
            ltx_num_frames=_int("LTX_NUM_FRAMES", 121),
            ltx_fps=_int("LTX_FPS", 24),
            ltx_quantization=_str("LTX_QUANTIZATION", "fp8-cast"),
            ltx_offload=_str("LTX_OFFLOAD", "cpu"),
            ltx_width_flag=_str("LTX_WIDTH_FLAG", "--width"),
            ltx_height_flag=_str("LTX_HEIGHT_FLAG", "--height"),
            ltx_extra_args=_str("LTX_EXTRA_ARGS"),
            ltx_timeout=_int("LTX_TIMEOUT", 1800),
            whisper_model=_str("SHORTS_WHISPER_MODEL", "base.en"),
            whisper_device=_str("SHORTS_WHISPER_DEVICE", "cpu"),
            whisper_compute=_str("SHORTS_WHISPER_COMPUTE", "int8"),
            ffmpeg=_str("SHORTS_FFMPEG", "ffmpeg"),
            ffprobe=_str("SHORTS_FFPROBE", "ffprobe"),
            out_width=_int("SHORTS_OUT_WIDTH", 1080),
            out_height=_int("SHORTS_OUT_HEIGHT", 1920),
            caption_font=_str("SHORTS_CAPTION_FONT", "Arial"),
            caption_font_file=_str("SHORTS_CAPTION_FONT_FILE"),
            ambience=_bool("SHORTS_AMBIENCE", True),
            ambience_gain_db=_float("SHORTS_AMBIENCE_DB", -18.0),
            video_crf=_int("SHORTS_VIDEO_CRF", 20),
            youtube_client_secrets=Path(
                _str("YOUTUBE_CLIENT_SECRETS", str(PROJECT_ROOT / "client_secret.json"))
            ).expanduser(),
            youtube_token_file=Path(
                _str("YOUTUBE_TOKEN_FILE", str(state_dir / "youtube_token.json"))
            ).expanduser(),
            youtube_quota_limit=_int("YOUTUBE_QUOTA_LIMIT", 10000),
            youtube_upload_cost=_int("YOUTUBE_UPLOAD_COST", 1600),
            made_for_kids=_bool("YOUTUBE_MADE_FOR_KIDS", False),
        )

    def uploads_left_today(self, used_units: int) -> int:
        """Hoeveel uploads het resterende dagquotum nog toelaat."""
        if self.youtube_upload_cost <= 0:
            return self.per_day
        return max(0, (self.youtube_quota_limit - used_units) // self.youtube_upload_cost)

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.work_dir.mkdir(parents=True, exist_ok=True)
