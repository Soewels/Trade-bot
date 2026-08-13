"""Exportstand: alle onderdelen los in één map, klaar voor CapCut.

De bot kan zelf monteren en uploaden, maar soms wil je een video met de hand
oppoetsen. Deze stand rendert alles en stopt dan: je krijgt de losse clips, de
stem, de ondertitels en de YouTube-teksten in een map die je zo in CapCut
sleept.

Waarom er zowel een .srt als een .ass in zit: CapCut importeert alleen SRT
(Desktop en Web; de mobiele app kan het niet). Het ASS-bestand bevat de
gestileerde karaoke-versie die de bot zelf zou inbranden, voor editors die
daar wél mee overweg kunnen.
"""

import shutil
from pathlib import Path

from . import captions

LEESMIJ = """\
Wat staat hier?
===============

De Shorts-bot heeft alle onderdelen van deze video gerenderd, maar niets
gepubliceerd. Monteer hem zelf en upload met de hand.

  short.mp4        Wat de bot zelf zou hebben gepost. Handig als referentie,
                   of gewoon om te uploaden als je niets wilt aanpassen.
  shots/           De losse clips, in volgorde. Beeld en sfeergeluid zitten
                   erin; de voice-over nog niet.
  voice.wav        De ingesproken voice-over, kaal.
  captions.srt     De ondertitels. Dit is het bestand voor CapCut.
  captions.ass     Dezelfde ondertitels, maar met de opmaak van de bot
                   (gekleurd woord dat meeloopt). Niet voor CapCut.
  script.txt       De narratie per shot, plus de beeldprompt die is gebruikt.
  youtube.txt      Titel, beschrijving en tags om te plakken bij het uploaden.

Importeren in CapCut
====================

  1. Nieuw project, sleep de bestanden uit shots/ op de tijdlijn, op volgorde.
  2. Sleep voice.wav eronder als los audiospoor.
  3. Wil je het sfeergeluid van de clips houden, zet het dan een stuk of
     achttien decibel zachter dan de stem, anders praat het eroverheen.
  4. Tekst -> Automatische ondertiteling -> Lokale ondertiteling -> Importeren,
     en kies captions.srt.
  5. Exporteren op 1080x1920.

Let op: ondertitels importeren kan alleen in CapCut op de computer of in de
browser. De mobiele app kan geen ondertitelbestand inlezen.
"""


def _script_text(script, niche) -> str:
    lines = [script.title, "=" * len(script.title), ""]
    for index, shot in enumerate(script.shots, start=1):
        lines.append(f"[{index}] {shot.narration}")
        lines.append(f"    beeld: {shot.visual}")
        lines.append("")
    lines.append(f"Niche: {niche.label}")
    lines.append(f"Ondertitelstijl van de bot: {niche.caption_style}")
    return "\n".join(lines)


def _youtube_text(script, niche) -> str:
    return "\n".join(
        [
            "TITEL",
            script.title,
            "",
            "BESCHRIJVING",
            script.full_description(niche),
            "",
            "TAGS",
            ", ".join(script.tags),
            "",
            f"CATEGORIE  {niche.youtube_category}",
        ]
    )


def export_assets(config, niche, script, work: Path, final: Path, words) -> Path:
    """Zet alles wat bij deze video hoort in een eigen exportmap."""
    target = Path(config.export_dir) / work.name
    (target / "shots").mkdir(parents=True, exist_ok=True)

    if final.exists():
        shutil.copy2(final, target / "short.mp4")

    for shot in sorted(work.glob("shot_*.mp4")):
        shutil.copy2(shot, target / "shots" / shot.name)

    voice = work / "voice.wav"
    if voice.exists():
        shutil.copy2(voice, target / "voice.wav")

    ass_file = work / "captions.ass"
    if ass_file.exists():
        shutil.copy2(ass_file, target / "captions.ass")

    (target / "captions.srt").write_text(captions.build_srt(words), encoding="utf-8")
    (target / "script.txt").write_text(_script_text(script, niche), encoding="utf-8")
    (target / "youtube.txt").write_text(_youtube_text(script, niche), encoding="utf-8")
    (target / "LEESMIJ.txt").write_text(LEESMIJ, encoding="utf-8")

    return target
