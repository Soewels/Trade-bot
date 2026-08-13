"""Herstart-veilige status: wat is er gepost, wanneer, en hoeveel quotum is op.

Eén JSON-bestand in de state-map. De bot leest dit bij elke start, zodat een
crash of reboot nooit tot dubbele uploads leidt en het YouTube-dagquotum
correct wordt bijgehouden.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path


@dataclass
class Posted:
    """Eén gepubliceerde (of geprobeerde) video."""

    slot: str            # "2026-08-13T09:10" — uniek per geplande post
    niche: str
    title: str
    video_id: str = ""
    posted_at: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return dict(
            slot=self.slot,
            niche=self.niche,
            title=self.title,
            video_id=self.video_id,
            posted_at=self.posted_at,
            error=self.error,
        )


@dataclass
class State:
    path: Path
    posted: list = field(default_factory=list)
    drafts: list = field(default_factory=list)
    last_angles: dict = field(default_factory=dict)
    quota_day: str = ""
    quota_used: int = 0

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            # Kapot statusbestand mag de bot niet blokkeren; begin schoon.
            return cls(path=path)
        return cls(
            path=path,
            posted=[Posted(**item) for item in raw.get("posted", [])],
            drafts=list(raw.get("drafts", [])),
            last_angles=dict(raw.get("last_angles", {})),
            quota_day=raw.get("quota_day", ""),
            quota_used=int(raw.get("quota_used", 0)),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "posted": [p.to_dict() for p in self.posted],
            "drafts": self.drafts[-200:],
            "last_angles": self.last_angles,
            "quota_day": self.quota_day,
            "quota_used": self.quota_used,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.path)  # atomair: nooit een half geschreven statusbestand

    # --- slots -------------------------------------------------------------

    def has_slot(self, slot: str) -> bool:
        """Is dit tijdslot al succesvol afgehandeld?"""
        return any(p.slot == slot and p.video_id for p in self.posted)

    def record(self, entry: Posted) -> None:
        self.posted.append(entry)

    def recent_titles(self, limit: int) -> list:
        """Titels die de schrijver moet vermijden.

        Bewust inclusief concepten die nooit geplaatst zijn: wie `script` een
        paar keer draait om de kwaliteit te beoordelen, moet niet elke keer
        hetzelfde onderwerp terugkrijgen.
        """
        titles = [p.title for p in self.posted if p.title] + list(self.drafts)
        seen, unique = set(), []
        for title in reversed(titles):          # nieuwste eerst
            if title not in seen:
                seen.add(title)
                unique.append(title)
        return unique[:limit]

    def remember_draft(self, title: str) -> None:
        """Leg een geschreven script vast, ook als het nooit gepubliceerd wordt."""
        if title and title not in self.drafts:
            self.drafts.append(title)

    def remember_angle(self, niche: str, angle: str) -> None:
        self.last_angles[niche] = angle

    def count_for_niche(self, niche: str) -> int:
        return sum(1 for p in self.posted if p.niche == niche and p.video_id)

    def successful(self) -> int:
        return sum(1 for p in self.posted if p.video_id)

    # --- quotum ------------------------------------------------------------

    def roll_quota(self, today: date) -> None:
        """Zet het quotum terug bij een nieuwe dag (YouTube reset om middernacht PT)."""
        stamp = today.isoformat()
        if self.quota_day != stamp:
            self.quota_day = stamp
            self.quota_used = 0

    def spend_quota(self, units: int) -> None:
        self.quota_used += units

    def posted_today(self, today: date) -> int:
        stamp = today.isoformat()
        return sum(
            1 for p in self.posted
            if p.video_id and p.posted_at[:10] == stamp
        )


def utc_stamp() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
