"""Nichepakketten: alles wat per onderwerp verschilt, als data in plaats van code.

Een niche bepaalt waar de video over gaat, hoe hij eruitziet, welke
ondertitelstijl erbij hoort en onder welke YouTube-categorie hij valt.
Een niche toevoegen is een blok hieronder bijschrijven — geen codewijziging
elders in de bot.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Niche:
    key: str
    label: str
    caption_style: str          # "block" | "pop" | "word"
    highlight: str              # kleur van het actieve woord, hex
    youtube_category: str       # YouTube categoryId
    brief: str                  # instructies voor de schrijver
    visual_style: str           # wordt achter elke beeldprompt geplakt
    hashtags: tuple
    accuracy_rule: str          # hoe de bot met onzekere feiten omgaat
    disclaimer: str = ""


MARKETS_FINANCE = Niche(
    key="markets_finance",
    label="Markets & finance",
    caption_style="block",
    highlight="#FFD24A",
    youtube_category="27",  # Education
    brief=(
        "Explain one counter-intuitive fact about markets, money or investing. "
        "Pick something a reasonably informed person believes but has slightly wrong — "
        "a rule of thumb whose origin undercuts it, a statistic that only holds in one "
        "country or era, a piece of jargon that hides a simpler idea. "
        "Open by stating the common belief, then take it apart with a concrete number, "
        "date or name. Close on the underlying principle, not on a call to action. "
        "Never predict prices, never recommend a specific security, never imply guaranteed returns."
    ),
    visual_style=(
        "Cinematic live-action, shallow depth of field, 35mm, warm tungsten and "
        "late-afternoon light, subtle film grain, muted earth tones with amber "
        "highlights. Documentary realism. No text, no captions, no numbers, no charts "
        "rendered as graphics, no logos, no recognisable real people."
    ),
    hashtags=("#investing", "#money", "#personalfinance", "#markets", "#finance"),
    accuracy_rule=(
        "Every number, name and date must be one you are confident is correct. "
        "If you cannot state a fact with confidence, choose a different fact rather "
        "than hedging in the script."
    ),
    disclaimer="Educational content, not financial advice.",
)


HORROR_MYSTERY = Niche(
    key="horror_mystery",
    label="Horror & mystery",
    caption_style="pop",
    highlight="#9FD2F5",
    youtube_category="24",  # Entertainment
    brief=(
        "Tell one unexplained or unsettling story in a single arc: the event, the "
        "detail that makes it strange, and the twist that reframes it. "
        "Favour maritime disappearances, sealed rooms, transmissions, abandoned places "
        "and archival oddities over gore or violence. "
        "The strongest ending is usually the limit of the evidence itself."
    ),
    visual_style=(
        "Cinematic live-action, desaturated cold palette, heavy atmosphere — fog, dust, "
        "low light or overexposed grey sky. 16mm documentary grain, slow handheld drift. "
        "Empty spaces, no people visible. No text, no captions, no logos, no gore, "
        "no blood, no recognisable real people."
    ),
    hashtags=("#unexplained", "#mystery", "#horrorstories", "#creepy", "#lostmedia"),
    accuracy_rule=(
        "If the story rests on a disputed or unverified source, the script must say so "
        "out loud in the final two lines. Do not present a legend as documented fact. "
        "The admission is the payoff, not a weakness — 'the story is real, the ship may "
        "not be' lands harder than pretending otherwise."
    ),
)


NICHES = {n.key: n for n in (MARKETS_FINANCE, HORROR_MYSTERY)}


def get(key: str) -> Niche:
    """Haal een niche op; onbekende sleutel geeft een duidelijke fout."""
    try:
        return NICHES[key]
    except KeyError:
        known = ", ".join(sorted(NICHES))
        raise KeyError(f"onbekende niche {key!r} — beschikbaar: {known}") from None


def rotate(keys, posted_count: int) -> Niche:
    """Wissel de niches af op volgorde van het aantal reeds geposte video's."""
    if not keys:
        raise ValueError("geen niches geconfigureerd")
    return get(keys[posted_count % len(keys)])
