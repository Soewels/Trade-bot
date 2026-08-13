"""De keten van één video: script, stem, beeld, timing, montage, upload.

Elke stap schrijft naar een eigen werkmap per video, zodat een mislukte stap
niets van een eerdere video overschrijft en je achteraf kunt zien waar het
misging (zet SHORTS_KEEP_WORK=1 om de werkmappen te bewaren).
"""

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import assemble, captions, export, script_writer, tts, video, youtube
from .state import Posted, utc_stamp

log = logging.getLogger("shorts")


@dataclass
class Result:
    title: str
    niche: str
    video_id: str = ""
    path: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.video_id) or (bool(self.path) and not self.error)


def _work_dir(config, niche) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = Path(config.work_dir) / f"{stamp}-{niche.key}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_once(config, niche, state, slot: str = "") -> Result:
    """Maak één Short en publiceer hem. Geeft altijd een Result terug."""
    config.ensure_dirs()
    work = _work_dir(config, niche)

    try:
        log.info("[%s] script schrijven", niche.key)
        script = script_writer.write_script(
            config, niche, state.recent_titles(config.dedupe_history)
        )
        log.info("[%s] titel: %s", niche.key, script.title)

        log.info("[%s] stem inspreken (%s)", niche.key, config.tts_voice)
        voice = tts.make_tts(config).speak(script.voiceover, work / "voice.wav")

        backend = video.make_video_backend(config)
        shots = []
        for index, shot in enumerate(script.shots, start=1):
            log.info("[%s] shot %d/%d renderen", niche.key, index, len(script.shots))
            prompt = script_writer.visual_prompt(shot, niche)
            shots.append(backend.render(prompt, work / f"shot_{index:02d}.mp4", seed=index * 977))

        log.info("[%s] woorden timen", niche.key)
        words = captions.transcribe(voice, config)
        ass_text = captions.build_ass(words, niche.caption_style, config, niche.highlight)
        ass_path = work / "captions.ass"
        ass_path.write_text(ass_text, encoding="utf-8")

        log.info("[%s] monteren", niche.key)
        raw = assemble.concat_shots(shots, work, config)
        final = assemble.render_final(raw, voice, ass_path, work / "short.mp4", config)

        if config.export:
            target = export.export_assets(config, niche, script, work, final, words)
            log.info("[%s] geëxporteerd naar %s — niet geüpload", niche.key, target)
            return Result(title=script.title, niche=niche.key, path=str(target))

        if config.dry_run:
            keep = Path(config.state_dir) / f"dryrun-{work.name}.mp4"
            shutil.copy2(final, keep)
            log.info("[%s] droogdraaien: niet geüpload, bewaard als %s", niche.key, keep)
            return Result(title=script.title, niche=niche.key, path=str(keep))

        log.info("[%s] uploaden als %s", niche.key, config.privacy)
        video_id = youtube.upload(config, final, script, niche)
        log.info("[%s] klaar: https://youtube.com/shorts/%s", niche.key, video_id)
        return Result(
            title=script.title, niche=niche.key, video_id=video_id, path=str(final)
        )

    except Exception as exc:
        log.error("[%s] mislukt: %s", niche.key, exc)
        return Result(title="", niche=niche.key, error=str(exc))

    finally:
        if not config.keep_work_dirs and not config.dry_run:
            shutil.rmtree(work, ignore_errors=True)


def run_and_record(config, niche, state, slot: str) -> Result:
    """Draai één video en leg het resultaat vast in de status."""
    result = run_once(config, niche, state, slot)
    state.record(
        Posted(
            slot=slot,
            niche=niche.key,
            title=result.title,
            video_id=result.video_id,
            posted_at=utc_stamp() if result.video_id else "",
            error=result.error,
        )
    )
    if result.video_id:
        state.spend_quota(config.youtube_upload_cost)
    state.save()
    return result
