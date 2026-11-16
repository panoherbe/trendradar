"""Kooperationsfinder: Gruppen regionaler Unternehmen, die zusammen alle Teilfähigkeiten eines Subtrends belegen.

Zugrunde liegen nur **eindeutige Belege** je Teilfähigkeit. Gruppen entstehen gierig (Mengenüberdeckung): Start mit
einem Unternehmen, das viele Teilfähigkeiten belegt; dann kommt hinzu, wer die meisten offenen Teilfähigkeiten
schließt, bei Gleichstand ein Unternehmen aus demselben Kreis, dann das mit mehr Belegen. Was niemand in der Region
belegt, wird als **Lücke** ausgewiesen statt verschwiegen. Ein Vorschlag, keine Empfehlung: die Belege zeigen, warum.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Kandidat:
    unternehmen_id: str
    faehigkeit_id: str
    belege: int
    naehe: float
    kreis: str = ""


@dataclass
class Gruppe:
    mitglieder: list[str]
    zuordnung: dict[str, str]          # Teilfähigkeit -> Unternehmen
    luecken: list[str]
    staerke: int = 0
    kreise: list[str] = field(default_factory=list)


def finde_gruppen(faehigkeiten: list[str], kandidaten: list[Kandidat], max_gruppen: int = 5,
                  max_groesse: int = 4, start_versuche: int = 25) -> tuple[list[Gruppe], list[str]]:
    """Gibt (Gruppen, Lücken der Region) zurück. Deterministisch: gleiche Eingabe, gleiche Gruppen."""
    deckt: dict[str, dict[str, Kandidat]] = {}
    kreis: dict[str, str] = {}
    for k in kandidaten:
        if k.faehigkeit_id not in faehigkeiten:
            continue
        alt = deckt.setdefault(k.unternehmen_id, {}).get(k.faehigkeit_id)
        if alt is None or (k.belege, k.naehe) > (alt.belege, alt.naehe):
            deckt[k.unternehmen_id][k.faehigkeit_id] = k
        kreis[k.unternehmen_id] = k.kreis
    region_luecken = [f for f in faehigkeiten if not any(f in d for d in deckt.values())]
    if not deckt:
        return [], region_luecken

    def staerke(u: str, fs) -> int:
        return sum(deckt[u][f].belege for f in fs if f in deckt[u])

    starts = sorted(deckt, key=lambda u: (-len(deckt[u]), -staerke(u, deckt[u]), u))[:start_versuche]
    gesehen, gruppen = set(), []
    for s in starts:
        mitglieder, offen = [s], [f for f in faehigkeiten if f not in deckt[s]]
        while offen and len(mitglieder) < max_groesse:
            kreise = {kreis[m] for m in mitglieder}
            beste = max(
                (u for u in deckt if u not in mitglieder),
                key=lambda u: (len(set(deckt[u]) & set(offen)), kreis[u] in kreise, staerke(u, offen), u),
                default=None,
            )
            if beste is None or not set(deckt[beste]) & set(offen):
                break
            mitglieder.append(beste)
            offen = [f for f in offen if f not in deckt[beste]]
        schluessel = frozenset(mitglieder)
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        zuordnung = {}
        for f in faehigkeiten:
            traeger = [m for m in mitglieder if f in deckt[m]]
            if traeger:
                zuordnung[f] = max(traeger, key=lambda m: (deckt[m][f].belege, deckt[m][f].naehe, m))
        gruppen.append(Gruppe(mitglieder, zuordnung, offen, sum(deckt[zuordnung[f]][f].belege for f in zuordnung),
                              sorted({kreis[m] for m in mitglieder})))
    gruppen.sort(key=lambda g: (len(g.luecken), len(g.mitglieder), len(g.kreise), -g.staerke, g.mitglieder))
    return gruppen[:max_gruppen], region_luecken
