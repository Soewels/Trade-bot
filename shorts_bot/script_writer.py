"""De schrijver: Claude bedenkt het idee, het script en de YouTube-teksten.

Levert per video een vaste structuur op — titel, beschrijving, tags en één
narratieregel plus één beeldprompt per shot — zodat stem en beeld op elkaar
aansluiten. De titels van eerdere video's gaan mee in het verzoek, zodat de
bot zichzelf niet herhaalt.
"""

from dataclasses import dataclass

from . import niches as niches_mod

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "YouTube title, under 70 characters, no hashtags, no clickbait punctuation.",
        },
        "description": {
            "type": "string",
            "description": "Two or three sentences of YouTube description. No hashtags here.",
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Five to eight lowercase keyword tags, no '#' prefix.",
        },
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {
                        "type": "string",
                        "description": "One or two spoken sentences, roughly 5 seconds when read aloud.",
                    },
                    "visual": {
                        "type": "string",
                        "description": (
                            "A single camera shot described for a text-to-video model: subject, "
                            "camera move, lighting, lens, and the ambient sound of the scene."
                        ),
                    },
                },
                "required": ["narration", "visual"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "description", "tags", "shots"],
    "additionalProperties": False,
}


@dataclass
class Shot:
    narration: str
    visual: str


@dataclass
class Script:
    title: str
    description: str
    tags: list
    shots: list

    @property
    def voiceover(self) -> str:
        return " ".join(shot.narration.strip() for shot in self.shots)

    def full_description(self, niche) -> str:
        parts = [self.description.strip()]
        if niche.disclaimer:
            parts.append(niche.disclaimer)
        parts.append(" ".join(niche.hashtags))
        return "\n\n".join(parts)


class WriterError(RuntimeError):
    """De schrijver kon geen bruikbaar script leveren."""


def build_prompt(niche, config, recent_titles) -> str:
    seconds = config.shots_per_video * round(config.ltx_num_frames / max(config.ltx_fps, 1))
    lines = [
        f"Write one YouTube Short for a faceless channel in the '{niche.label}' niche.",
        "",
        "## Brief",
        niche.brief,
        "",
        "## Accuracy",
        niche.accuracy_rule,
        "",
        "## Format",
        f"- Exactly {config.shots_per_video} shots, in order.",
        f"- Each shot's narration must take about {round(config.ltx_num_frames / max(config.ltx_fps, 1))} "
        "seconds to read aloud at a normal pace — roughly 12 to 16 words.",
        f"- The whole script runs about {seconds} seconds.",
        "- Shot 1 is the hook: it must land in the first three seconds and give the viewer "
        "a concrete reason to keep watching. No 'in this video' openers.",
        "- The last shot is the payoff. Do not end on a call to action, a question to the "
        "audience, or an invitation to subscribe.",
        f"- Language: {config.language}.",
        "",
        "## Visual prompts",
        "Each shot's 'visual' is sent verbatim to a text-to-video model that also generates "
        "ambient audio. Describe one continuous camera shot — no cuts inside a shot. "
        "Name the subject, the camera movement, the light and the ambient sound. "
        "The shots should read as one sequence, not six unrelated images.",
        "Append nothing about style; the pipeline adds this house style to every prompt:",
        f"  {niche.visual_style}",
        "Because that style is appended automatically, never ask for on-screen text, "
        "captions, charts, logos, or identifiable real people in a visual.",
    ]
    if recent_titles:
        lines += [
            "",
            "## Already published — pick a genuinely different subject",
            *(f"- {t}" for t in recent_titles),
        ]
    return "\n".join(lines)


def write_script(config, niche, recent_titles=None) -> Script:
    """Vraag Claude om één compleet script. Gooit WriterError bij problemen."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - afhankelijk van omgeving
        raise WriterError(
            "pakket 'anthropic' ontbreekt — pip install -r requirements-shorts.txt"
        ) from exc

    if not config.anthropic_api_key:
        raise WriterError("ANTHROPIC_API_KEY ontbreekt in .env")

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    prompt = build_prompt(niche, config, recent_titles or [])

    try:
        response = client.messages.create(
            model=config.claude_model,
            max_tokens=8000,
            system=(
                "You write short-form video scripts for a faceless YouTube channel. "
                "You are precise with facts, you never pad, and you never write in the "
                "register of an AI assistant explaining itself. Write the script only."
            ),
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": SCRIPT_SCHEMA},
                "effort": config.claude_effort,
            },
        )
    except Exception as exc:  # pragma: no cover - netwerk
        raise WriterError(f"aanroep van de Claude API mislukte: {exc}") from exc

    if response.stop_reason == "refusal":
        raise WriterError(
            "Claude weigerde dit onderwerp — de bot slaat dit tijdslot over en "
            "probeert het volgende met een ander idee."
        )
    if response.stop_reason == "max_tokens":
        raise WriterError("antwoord liep tegen max_tokens; verhoog max_tokens of verlaag effort")

    text = next((b.text for b in response.content if b.type == "text"), "")
    return parse_script(text, config.shots_per_video)


def parse_script(text: str, expected_shots: int) -> Script:
    """Zet het JSON-antwoord om in een Script en controleer de vorm."""
    import json

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WriterError(f"antwoord was geen geldige JSON: {exc}") from exc

    shots = [Shot(narration=s["narration"].strip(), visual=s["visual"].strip())
             for s in data.get("shots", [])]
    if not shots:
        raise WriterError("script bevat geen shots")
    if len(shots) != expected_shots:
        # Geen harde fout: te veel shots knippen we af, te weinig accepteren we.
        shots = shots[:expected_shots] if len(shots) > expected_shots else shots

    title = data.get("title", "").strip()
    if not title:
        raise WriterError("script bevat geen titel")

    tags = [t.strip().lstrip("#") for t in data.get("tags", []) if t.strip()]
    return Script(
        title=title,
        description=data.get("description", "").strip(),
        tags=tags,
        shots=shots,
    )


def visual_prompt(shot: Shot, niche) -> str:
    """De volledige prompt die naar het videomodel gaat."""
    return f"{shot.visual.rstrip('. ')}. {niche.visual_style}"
