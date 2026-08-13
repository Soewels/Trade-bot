"""Ondertitels: woord-timings uit Whisper, omgezet naar een ASS-bestand.

Belangrijk detail voor de nauwkeurigheid: we timen de kále stem van Kokoro,
vóórdat het sfeergeluid van LTX eronder wordt gemixt. Op schone narratie ligt
de afwijking rond de 30-40 ms; met achtergrondgeluid loopt dat op naar 80-100 ms.
Die volgorde is gratis nauwkeurigheid.

Drie stijlen:
  block  Hele zin op een donker blok, actief woord gemarkeerd. Leesbaar —
         voor finance, waar percentages, jaartallen en namen moeten blijven staan.
  pop    Vier woorden tegelijk, actief woord springt op en kleurt. Ritme —
         voor horror, waar de sfeer belangrijker is dan de precieze woorden.
  word   Eén woord tegelijk, groot en centraal. Maximaal ritme, minimale inhoud.
"""

from dataclasses import dataclass

GROUP_SIZE = {"block": 6, "pop": 4, "word": 1}
FONT_SIZE = {"block": 74, "pop": 88, "word": 150}


@dataclass
class Word:
    text: str
    start: float
    end: float


class CaptionError(RuntimeError):
    """De ondertiteling kon niet worden opgebouwd."""


def transcribe(audio_path, config) -> list:
    """Haal woord-timestamps uit de voice-over met faster-whisper."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise CaptionError(
            "pakket 'faster-whisper' ontbreekt — pip install -r requirements-shorts.txt"
        ) from exc

    model = WhisperModel(
        config.whisper_model,
        device=config.whisper_device,
        compute_type=config.whisper_compute,
    )
    segments, _ = model.transcribe(str(audio_path), word_timestamps=True)

    words = []
    for segment in segments:
        for word in (segment.words or []):
            text = word.word.strip()
            if text:
                words.append(Word(text=text, start=float(word.start), end=float(word.end)))
    if not words:
        raise CaptionError("Whisper vond geen woorden in de voice-over")
    return words


# --- ASS-opbouw ------------------------------------------------------------


def ass_colour(hex_colour: str) -> str:
    """#RRGGBB naar het &HBBGGRR& dat ASS gebruikt (omgekeerde volgorde)."""
    value = hex_colour.lstrip("#")
    if len(value) != 6:
        raise CaptionError(f"kleur {hex_colour!r} moet de vorm #RRGGBB hebben")
    return f"&H{value[4:6]}{value[2:4]}{value[0:2]}&".upper()


def ass_time(seconds: float) -> str:
    """Seconden naar h:mm:ss.cc, het tijdformaat van ASS."""
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{int(secs):02d}.{int(round(secs % 1 * 100)):02d}"


def escape(text: str) -> str:
    """Accolades zijn in ASS het begin van een opdracht — onschadelijk maken."""
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def group_words(words, max_words: int = 6, max_chars: int = 42):
    """Groepeer woorden tot leesbare regels, met een knip op zinseindes.

    Voor het SRT-bestand dat je in CapCut importeert. Anders dan bij de
    karaoke-ondertitels telt hier leesbaarheid: een regel die midden in een zin
    afbreekt terwijl er een punt vlakbij staat, leest slechter dan een korte regel.
    """
    lines, current, length = [], [], 0
    for word in words:
        current.append(word)
        length += len(word.text) + 1
        ends_sentence = word.text.rstrip().endswith((".", "?", "!", ":"))
        if ends_sentence or len(current) >= max_words or length >= max_chars:
            lines.append(current)
            current, length = [], 0
    if current:
        lines.append(current)
    return lines


def srt_time(seconds: float) -> str:
    """Seconden naar HH:MM:SS,mmm — het tijdformaat van SRT."""
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    whole = int(secs)
    millis = int(round((secs - whole) * 1000))
    if millis == 1000:  # afronding mag niet naar 1,000 doorslaan
        whole, millis = whole + 1, 0
    return f"{int(hours):02d}:{int(minutes):02d}:{whole:02d},{millis:03d}"


def build_srt(words, max_words: int = 6) -> str:
    """Bouw een gewoon SRT-bestand — het formaat dat CapCut kan importeren."""
    if not words:
        raise CaptionError("geen woorden om te ondertitelen")

    blocks = []
    for index, line in enumerate(group_words(words, max_words), start=1):
        start, end = line[0].start, line[-1].end
        if end <= start:
            end = start + 0.4
        text = " ".join(word.text for word in line).strip()
        blocks.append(f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n")
    return "\n".join(blocks)


def _style_block(style: str, config, highlight: str) -> str:
    size = FONT_SIZE.get(style, 80)
    font = config.caption_font
    white = "&H00FFFFFF"
    outline = "&H00000000"
    if style == "block":
        # BorderStyle 3 tekent een gevuld vlak achter de tekst: het leesblok.
        return (
            f"Style: Cap,{font},{size},{white},{white},{outline},&H96000000,"
            f"-1,0,0,0,100,100,0,0,3,16,0,2,90,90,360,1"
        )
    if style == "word":
        return (
            f"Style: Cap,{font},{size},{white},{white},{outline},&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,7,3,5,90,90,90,1"
        )
    return (
        f"Style: Cap,{font},{size},{white},{white},{outline},&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,6,3,2,90,90,380,1"
    )


def _render(group, active, style: str, highlight: str) -> str:
    colour = ass_colour(highlight)
    parts = []
    for word in group:
        text = escape(word.text)
        if word is active:
            if style == "pop":
                parts.append(f"{{\\c{colour}\\fscx116\\fscy116}}{text}{{\\r}}")
            elif style == "word":
                parts.append(f"{{\\c{colour}}}{text.upper()}{{\\r}}")
            else:
                parts.append(f"{{\\c{colour}}}{text}{{\\r}}")
        else:
            parts.append(text)
    return " ".join(parts)


def build_ass(words, style: str, config, highlight: str = "#FFD24A") -> str:
    """Bouw het complete ASS-bestand voor deze woordenreeks."""
    if style not in GROUP_SIZE:
        raise CaptionError(
            f"onbekende ondertitelstijl {style!r} — kies block, pop of word"
        )
    if not words:
        raise CaptionError("geen woorden om te ondertitelen")

    groups = chunk(list(words), GROUP_SIZE[style])

    # Plat maken zodat elk woord weet welk woord er ná hem komt: de regel blijft
    # staan tot het volgende woord begint, anders knippert de tekst in de gaten.
    flat = [(group, word) for group in groups for word in group]

    events = []
    for index, (group, word) in enumerate(flat):
        end = flat[index + 1][1].start if index + 1 < len(flat) else word.end
        if end <= word.start:
            end = word.start + 0.12
        events.append(
            "Dialogue: 0,"
            f"{ass_time(word.start)},{ass_time(end)},Cap,,0,0,0,,"
            f"{_render(group, word, style, highlight)}"
        )

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {config.out_width}",
        f"PlayResY: {config.out_height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        _style_block(style, config, highlight),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    return "\n".join(header + events) + "\n"
