#!/usr/bin/env python3
"""CLI voor de Shorts-bot.

Voorbeelden:
    python -m shorts_bot.main doctor          # controleer de installatie
    python -m shorts_bot.main auth            # eenmalig YouTube autoriseren
    python -m shorts_bot.main script          # alleen een script, niets renderen
    python -m shorts_bot.main once --niche horror_mystery
    python -m shorts_bot.main run             # de planner, 24/7
    python -m shorts_bot.main status
"""

import argparse
import logging
import shutil
import subprocess
import sys
from datetime import date

from . import niches as niches_mod
from . import pipeline, scheduler
from .config import ShortsConfig
from .state import State


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def _load(config):
    state = State.load(config.state_dir / "state.json")
    state.roll_quota(date.today())
    return state


# --- commando's -------------------------------------------------------------


def cmd_doctor(config, args) -> int:
    """Controleer wat er klaarstaat en wat er nog ontbreekt."""
    ok = True

    def check(label, condition, hint=""):
        nonlocal ok
        mark = "OK " if condition else "MIS"
        print(f"  [{mark}] {label}")
        if not condition:
            ok = False
            if hint:
                print(f"         {hint}")

    print("\nInstellingen")
    print(f"  niches      {', '.join(config.niches)}")
    print(f"  per dag     {config.per_day} om {', '.join(config.post_times)} ({config.timezone})")
    print(f"  zichtbaar   {config.privacy}")
    print(f"  stem        {config.tts_backend} / {config.tts_voice}")
    print(f"  beeld       {config.video_backend} {config.ltx_width}x{config.ltx_height}, "
          f"{config.ltx_num_frames} frames x {config.shots_per_video} shots")

    print("\nProgramma's")
    check("ffmpeg", shutil.which(config.ffmpeg) is not None, "installeer ffmpeg")
    check("ffprobe", shutil.which(config.ffprobe) is not None, "hoort bij ffmpeg")

    print("\nSleutels en bestanden")
    check("ANTHROPIC_API_KEY", bool(config.anthropic_api_key), "zet hem in .env")
    check(
        "YouTube-token",
        config.youtube_token_file.exists(),
        "draai: python -m shorts_bot.main auth",
    )
    if config.video_backend == "ltx":
        check(
            f"LTX-repository ({config.ltx_repo})",
            config.ltx_repo.exists(),
            "git clone https://github.com/Lightricks/LTX-2 en zet LTX_REPO in .env",
        )
    if config.video_backend == "comfy":
        from .comfy import ComfyBackend

        check(
            f"workflow ({config.comfy_workflow.name})",
            config.comfy_workflow.exists(),
            "exporteer in ComfyUI via Workflow -> Export (API) en zet COMFY_WORKFLOW",
        )
        check(
            f"ComfyUI bereikbaar ({config.comfy_url})",
            ComfyBackend(config).reachable(),
            "start ComfyUI en laat het draaien terwijl de bot rendert",
        )

    print("\nPakketten")
    for module, hint in (
        ("anthropic", "de schrijver"),
        ("kokoro", "de stem"),
        ("faster_whisper", "de woord-timing"),
        ("googleapiclient", "de upload"),
    ):
        try:
            __import__(module)
            check(f"{module} ({hint})", True)
        except ImportError:
            check(f"{module} ({hint})", False, "pip install -r requirements-shorts.txt")

    if config.video_backend == "ltx" and config.ltx_repo.exists():
        print("\nBeschikbare LTX-vlaggen (controleer of --width en --height kloppen)")
        try:
            import shlex

            result = subprocess.run(
                shlex.split(config.ltx_python) + ["-m", config.ltx_module, "--help"],
                cwd=str(config.ltx_repo), capture_output=True, text=True, timeout=180,
            )
            output = result.stdout or result.stderr
            for line in output.splitlines():
                if line.strip().startswith("-"):
                    print(f"  {line.strip()}")
        except Exception as exc:
            print(f"  kon --help niet draaien: {exc}")

    print("\n" + ("Alles staat klaar." if ok else "Er ontbreekt nog iets, zie hierboven."))
    return 0 if ok else 1


def cmd_auth(config, args) -> int:
    from . import youtube

    path = youtube.authorise(config)
    print(f"Token opgeslagen in {path}")
    print(
        "\nLet op: staat je Google-project nog als 'unaudited', dan zet YouTube elke\n"
        "upload op privé, ongeacht SHORTS_PRIVACY. Dat is geen fout in deze bot."
    )
    return 0


def cmd_script(config, args) -> int:
    """Alleen het script tonen — geen GPU, geen upload."""
    from . import script_writer

    niche = niches_mod.get(args.niche or config.niches[0])
    state = _load(config)
    angle = script_writer.pick_angle(niche, state.last_angles.get(niche.key, ""))
    script = script_writer.write_script(
        config, niche, state.recent_titles(config.dedupe_history), angle
    )
    # Ook een script dat je alleen bekijkt telt mee, anders krijg je bij de
    # volgende keer hetzelfde onderwerp terug.
    state.remember_draft(script.title)
    state.remember_angle(niche.key, angle)
    state.save()

    print(f"\nInvalshoek: {angle}")
    print(f"\n{script.title}\n{'=' * len(script.title)}\n")
    for index, shot in enumerate(script.shots, start=1):
        print(f"[{index}] {shot.narration}")
        print(f"    beeld: {shot.visual}\n")
    print("Beschrijving:")
    print(script.full_description(niche))
    return 0


def cmd_once(config, args) -> int:
    if getattr(args, "export", False):
        config.export = True
    niche = niches_mod.get(args.niche) if args.niche else niches_mod.rotate(
        config.niches, _load(config).successful()
    )
    state = _load(config)
    result = pipeline.run_and_record(config, niche, state, slot=f"handmatig-{date.today()}")
    if result.error:
        print(f"Mislukt: {result.error}")
        return 1
    print(f"Klaar: {result.title}")
    if result.video_id:
        print(f"https://youtube.com/shorts/{result.video_id}")
    elif config.export:
        print(f"\nExportmap: {result.path}")
        print("Sleep de bestanden uit shots/ op de tijdlijn en importeer")
        print("captions.srt via Tekst -> Automatische ondertiteling -> Lokale ondertiteling.")
        print("Zie LEESMIJ.txt in die map voor de volledige stappen.")
    else:
        print(f"Bestand: {result.path}")
    return 0


def cmd_run(config, args) -> int:
    state = _load(config)
    try:
        scheduler.run_forever(config, state)
    except KeyboardInterrupt:
        print("\nGestopt.")
    return 0


def cmd_status(config, args) -> int:
    state = _load(config)
    print(f"Geplaatst: {state.successful()} video's, waarvan {state.posted_today(date.today())} vandaag")
    print(f"Quotum:    {state.quota_used}/{config.youtube_quota_limit} eenheden "
          f"({config.uploads_left_today(state.quota_used)} uploads over vandaag)")
    print()
    for entry in state.posted[-12:]:
        mark = entry.video_id or f"MISLUKT ({entry.error[:60]})"
        print(f"  {entry.slot:24} {entry.niche:16} {mark}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shorts_bot", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="controleer de installatie")
    sub.add_parser("auth", help="eenmalig YouTube autoriseren")
    sub.add_parser("status", help="toon wat er is geplaatst")
    sub.add_parser("run", help="de planner, blijft draaien")

    for name, helptext in (("script", "alleen een script"), ("once", "één video, nu")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--niche", help=f"een van: {', '.join(sorted(niches_mod.NICHES))}")
        if name == "once":
            p.add_argument(
                "--export",
                action="store_true",
                help="niet uploaden, maar alle onderdelen los wegschrijven voor CapCut",
            )

    return parser


COMMANDS = {
    "doctor": cmd_doctor,
    "auth": cmd_auth,
    "script": cmd_script,
    "once": cmd_once,
    "run": cmd_run,
    "status": cmd_status,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    config = ShortsConfig.from_env()
    config.ensure_dirs()
    try:
        return COMMANDS[args.command](config, args)
    except KeyError as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        return 1
    except RuntimeError as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
