"""Taxonomie laden und prüfen: Megatrends, Subtrends (auch auf mehreren Linien) und ihre Teilfähigkeiten.

Die Taxonomie ist eine Datei, keine Programmlogik: Trends anlegen, umbenennen oder stilllegen heißt diese Datei
ändern. Jede Bewertung trägt die Version und den Inhalts-Hash der Taxonomie, mit der sie entstanden ist.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

STANDARD = Path(os.environ.get("TAXONOMIE", Path(__file__).resolve().parent.parent / "taxonomie" / "trends.yaml"))


@dataclass(frozen=True)
class Faehigkeit:
    id: str
    name: str
    beschreibung: str
    begriffe: tuple[str, ...]
    cpv: tuple[str, ...]


@dataclass(frozen=True)
class Subtrend:
    id: str
    name: str
    beschreibung: str
    megatrends: tuple[str, ...]
    faehigkeiten: tuple[Faehigkeit, ...]
    aktiv: bool = True


@dataclass(frozen=True)
class Megatrend:
    id: str
    name: str
    farbe: str
    beschreibung: str
    subtrends: tuple[str, ...]


@dataclass
class Taxonomie:
    version: str
    titel: str
    megatrends: dict[str, Megatrend]
    subtrends: dict[str, Subtrend]
    hash: str = ""
    karte: dict = field(default_factory=dict)

    @property
    def kennung(self) -> str:
        return f"{self.version}+{self.hash[:8]}"

    def faehigkeiten(self) -> list[tuple[Subtrend, Faehigkeit]]:
        return [(s, f) for s in self.subtrends.values() if s.aktiv for f in s.faehigkeiten]


class TaxonomieFehler(ValueError):
    pass


def lade(pfad: Path | str = STANDARD) -> Taxonomie:
    roh = Path(pfad).read_bytes()
    d = yaml.safe_load(roh)
    megatrends = {
        m["id"]: Megatrend(m["id"], m["name"], m["farbe"], m.get("beschreibung", ""), tuple(m["subtrends"]))
        for m in d["megatrends"]
    }
    subtrends = {}
    for s in d["subtrends"]:
        fs = tuple(
            Faehigkeit(f["id"], f["name"], f["beschreibung"], tuple(b.lower() for b in f.get("begriffe") or []),
                       tuple(str(c) for c in f.get("cpv") or []))
            for f in s["faehigkeiten"]
        )
        subtrends[s["id"]] = Subtrend(s["id"], s["name"], s.get("beschreibung", ""), tuple(s["megatrends"]), fs,
                                      bool(s.get("aktiv", True)))
    t = Taxonomie(str(d["version"]), d.get("titel", ""), megatrends, subtrends,
                  hashlib.sha256(roh).hexdigest(), d.get("karte") or {})
    pruefe(t)
    return t


def pruefe(t: Taxonomie) -> None:
    """Bricht bei inkonsistenter Taxonomie ab, statt still falsch zu bewerten."""
    fehler = []
    ids = [f.id for s in t.subtrends.values() for f in s.faehigkeiten]
    if len(ids) != len(set(ids)):
        fehler.append("Teilfähigkeiten-IDs nicht eindeutig")
    for m in t.megatrends.values():
        for sid in m.subtrends:
            if sid not in t.subtrends:
                fehler.append(f"{m.id} nennt unbekannten Subtrend {sid}")
            elif m.id not in t.subtrends[sid].megatrends:
                fehler.append(f"{sid} steht auf Linie {m.id}, nennt {m.id} aber nicht")
    for s in t.subtrends.values():
        if not s.megatrends:
            fehler.append(f"{s.id} gehört zu keinem Megatrend")
        for mid in s.megatrends:
            if mid not in t.megatrends:
                fehler.append(f"{s.id} nennt unbekannten Megatrend {mid}")
            elif s.id not in t.megatrends[mid].subtrends:
                fehler.append(f"{s.id} nennt {mid}, steht dort aber nicht auf der Linie")
        if not s.faehigkeiten:
            fehler.append(f"{s.id} hat keine Teilfähigkeiten")
        for f in s.faehigkeiten:
            for c in f.cpv:
                if not (c.isdigit() and 4 <= len(c) <= 8):
                    fehler.append(f"{f.id}: CPV-Präfix {c!r} ungültig (4–8 Ziffern)")
    if fehler:
        raise TaxonomieFehler("; ".join(fehler))
