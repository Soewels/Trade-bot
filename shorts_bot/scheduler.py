"""De planning: 2 tot 3 video's per dag, op vaste tijden met wat speling.

De speling is bewust deterministisch — dezelfde dag geeft altijd dezelfde
tijden. Zo blijft de bot herstart-veilig: na een reboot berekent hij precies
dezelfde tijdslots en ziet hij in de status welke daarvan al gedaan zijn.
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from . import niches as niches_mod
from . import pipeline

log = logging.getLogger("shorts")


def _zone(name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # pragma: no cover - ontbrekende tzdata
        log.warning("tijdzone %s niet gevonden, val terug op lokale tijd", name)
        return None


@dataclass(frozen=True)
class Slot:
    key: str          # "2026-08-13T09:10" — stabiele sleutel in de status
    when: datetime
    index: int


def _jitter(day: date, index: int, minutes: int) -> int:
    """Verschuiving in minuten, stabiel per dag en per tijdslot."""
    if minutes <= 0:
        return 0
    seed = f"{day.isoformat()}:{index}".encode()
    digest = hashlib.sha256(seed).digest()
    span = minutes * 2 + 1
    return (digest[0] % span) - minutes


def slots_for(day: date, config) -> list:
    """De geplande tijdstippen voor één dag, in volgorde."""
    tz = _zone(config.timezone)
    times = config.post_times[: max(0, config.per_day)]
    result = []
    for index, stamp in enumerate(times):
        try:
            hour, minute = (int(part) for part in stamp.split(":", 1))
        except ValueError:
            log.warning("ongeldige tijd %r in SHORTS_POST_TIMES, overgeslagen", stamp)
            continue
        base = datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)
        when = base + timedelta(minutes=_jitter(day, index, config.jitter_minutes))
        result.append(Slot(key=f"{day.isoformat()}T{stamp}", when=when, index=index))
    return sorted(result, key=lambda s: s.when)


MAX_ATTEMPTS = 2


def settled(state) -> set:
    """Tijdslots waar de bot klaar mee is: geslaagd, of te vaak geprobeerd.

    Zonder die tweede voorwaarde blijft een tijdslot dat structureel faalt —
    een model dat ontbreekt, een verlopen token — in een strakke lus hangen.
    """
    done = {p.slot for p in state.posted if p.video_id}
    attempts = {}
    for entry in state.posted:
        attempts[entry.slot] = attempts.get(entry.slot, 0) + 1
    return done | {slot for slot, count in attempts.items() if count >= MAX_ATTEMPTS}


def _align_tz(now: datetime, tz):
    """Zet `now` in dezelfde tijdzone-modus als de tijdslots.

    Zonder dit knalt de vergelijking op 'offset-naive and offset-aware' zodra
    de tijdzone ontbreekt of de aanroeper een kale datetime meegeeft.
    """
    if tz is not None and now.tzinfo is None:
        return now.replace(tzinfo=tz)
    if tz is None and now.tzinfo is not None:
        return now.replace(tzinfo=None)
    return now


def next_slot(config, state, now: datetime = None):
    """Het eerstvolgende tijdslot dat nog niet is afgehandeld."""
    tz = _zone(config.timezone)
    now = _align_tz(now or datetime.now(tz), tz)
    done = settled(state)
    for day in (now.date(), now.date() + timedelta(days=1)):
        for slot in slots_for(day, config):
            if slot.key in done:
                continue
            if slot.when > now:
                return slot
            if day == now.date():
                return slot  # gemist tijdslot van vandaag: alsnog draaien
    return None


def run_forever(config, state) -> None:
    """De hoofdlus: wacht tot het volgende tijdslot en maak dan een video."""
    tz = _zone(config.timezone)
    log.info(
        "planner gestart — %d per dag om %s (%s), speling ±%d min",
        config.per_day, ", ".join(config.post_times), config.timezone,
        config.jitter_minutes,
    )

    while True:
        now = _align_tz(datetime.now(tz), tz)
        state.roll_quota(now.date())

        slot = next_slot(config, state, now)
        if slot is None:
            time.sleep(300)
            continue

        wait = (slot.when - now).total_seconds()
        if wait > 0:
            log.info("volgende video om %s (%.0f min)", slot.when.strftime("%H:%M"), wait / 60)
            time.sleep(min(wait, 900))  # in stukjes, zodat een herstart snel reageert
            continue

        left = config.uploads_left_today(state.quota_used)
        if left <= 0 and not config.dry_run:
            log.warning("dagquotum van YouTube op — wachten tot morgen")
            time.sleep(1800)
            continue

        niche = niches_mod.rotate(config.niches, state.successful())
        log.info("tijdslot %s — niche %s", slot.key, niche.key)
        pipeline.run_and_record(config, niche, state, slot.key)
