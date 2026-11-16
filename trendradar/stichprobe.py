"""Qualitätsmessung: blinde Zweitbewertung einer geschichteten Stichprobe nach derselben Rubrik.

``ziehen`` zieht je Scorebereich (1–3, 4–5, 6–7, 8–10) gleich viele Paare Unternehmen × Subtrend und schreibt sie
**ohne** Score und ohne die Belegauswahl des Verfahrens: die Zweitbewertung sieht alle Aufträge und die ersten
Website-Passagen in Abrufreihenfolge. ``auswerten`` vergleicht: Anteil innerhalb ±2 Punkten,
genau gleich, je Bereich, und hochgerechnet auf die Verteilung im Bestand.

    python -m trendradar.stichprobe ziehen --n 50 --aus nachweis/2026-11-04/stichprobe_blind.jsonl
    python -m trendradar.stichprobe auswerten --blind nachweis/2026-11-04/stichprobe_blind.jsonl --urteile nachweis/2026-11-04/zweitbewertung.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from .taxonomie import STANDARD, lade

BEREICHE = {"1–3": range(1, 4), "4–5": range(4, 6), "6–7": range(6, 8), "8–10": range(8, 11)}
RUBRIK = ("1 keine Anhaltspunkte · 2–3 schwacher Bezug · 4–5 indirekter Bezug · 6–7 ein eindeutiger Beleg · "
          "8–10 mehrere eindeutige Belege · n. b. = zu wenig Text, um zu urteilen")


def bereich(score: int) -> str:
    return next(k for k, r in BEREICHE.items() if score in r)


def ziehen(daten: Path, n: int, aus: Path, seed: int = 11, ohne: Path | None = None) -> dict:
    tax = lade(STANDARD)
    with open(daten / "bewertungen.csv", encoding="utf-8") as fh:
        bew = [r for r in csv.DictReader(fh) if r["status"] == "bewertet"]
    with open(daten / "unternehmen.csv", encoding="utf-8") as fh:
        firmen = {r["id"]: r for r in csv.DictReader(fh)}
    auftraege = defaultdict(list)
    for line in open(daten / "belege_auftraege.jsonl", encoding="utf-8"):
        b = json.loads(line)
        auftraege[b["unternehmen_id"]].append(b)
    web = {}
    if (daten / "web_lauf.jsonl").exists():
        for line in open(daten / "web_lauf.jsonl", encoding="utf-8"):
            d = json.loads(line)
            if d.get("status") == "ok":
                ps = [p for s in d["seiten"] if s.get("status") == "ok" for p in s.get("passagen", [])]
                web[d["unternehmen_id"]] = ps[:25]
    rnd = random.Random(seed)
    schon = set()
    if ohne and ohne.exists():                     # Paare einer früheren Stichprobe nicht noch einmal ziehen
        schon = {(json.loads(l)["unternehmen_id"], json.loads(l)["subtrend_id"]) for l in open(ohne, encoding="utf-8")}
    je = defaultdict(list)
    for r in bew:
        if (r["unternehmen_id"], r["subtrend_id"]) not in schon:
            je[bereich(int(r["score"]))].append(r)
    gezogen = []
    for k in BEREICHE:
        gezogen += rnd.sample(je[k], min(n, len(je[k])))
    rnd.shuffle(gezogen)
    with open(aus, "w", encoding="utf-8") as fh:
        for i, r in enumerate(gezogen, 1):
            s = tax.subtrends[r["subtrend_id"]]
            u = firmen[r["unternehmen_id"]]
            fh.write(json.dumps({
                "nr": i, "unternehmen_id": u["id"], "subtrend_id": s.id, "unternehmen": u["name"],
                "subtrend": s.name, "subtrend_beschreibung": s.beschreibung,
                "teilfaehigkeiten": [f"{f.name}: {f.beschreibung}" for f in s.faehigkeiten],
                "auftraege": [" · ".join(x for x in (a["titel"], a["los"], a["text"][:400]) if x)
                              for a in auftraege[u["id"]][:12]],
                "website_passagen": web.get(u["id"], []),
                "rubrik": RUBRIK,
            }, ensure_ascii=False) + "\n")
    # ohne Texte, fürs Repository: welche Paare gezogen wurden (die Auswertung braucht nur diese)
    paare = aus.with_name(aus.name.replace("_blind", ""))
    if paare != aus:
        with open(paare, "w", encoding="utf-8") as fh:
            for i, r in enumerate(gezogen, 1):
                fh.write(json.dumps({"nr": i, "unternehmen_id": r["unternehmen_id"], "subtrend_id": r["subtrend_id"],
                                     "unternehmen": firmen[r["unternehmen_id"]]["name"],
                                     "subtrend": tax.subtrends[r["subtrend_id"]].name}, ensure_ascii=False) + "\n")
    bestand = {k: len(v) for k, v in je.items()}
    return {"seed": seed, "n_je_bereich": n, "gezogen": len(gezogen), "je_bereich": {k: min(n, len(je[k])) for k in BEREICHE},
            "bestand": bestand}


def auswerten(daten: Path, blind: Path, urteile: Path, spalte: str = "score", schichten: Path | None = None,
              schicht_spalte: str = "score", ziehung: Path | None = None) -> dict:
    """``spalte``: 'score' (aktueller Stand) oder 'score_vor_pruefung' (Lauf 1, vor dem Prüfschritt).

    Hochgerechnet wird nach den Schichten der Ziehung: ``schichten`` ist der Bewertungsstand, aus dem gezogen wurde
    (``schicht_spalte`` dessen Scorespalte), ``ziehung`` optional die Ziehungsdatei mit dem Bestand je Schicht.
    Ohne ``schichten`` gelten die Scorebereiche des aktuellen Stands; das stimmt nur, solange seit der Ziehung
    nicht neu gerechnet wurde (sonst verzerrt: Paare aus hohen Schichten landen in der großen Schicht 1–3)."""
    with open(daten / "bewertungen.csv", encoding="utf-8") as fh:
        bew = {(r["unternehmen_id"], r["subtrend_id"]): r for r in csv.DictReader(fh)}
    if schichten:
        with open(schichten, encoding="utf-8") as fh:
            alt = {(r["unternehmen_id"], r["subtrend_id"]): r for r in csv.DictReader(fh)}
    else:
        alt, schicht_spalte = bew, spalte
    paare = {json.loads(l)["nr"]: json.loads(l) for l in open(blind, encoding="utf-8")}
    erg = defaultdict(lambda: {"n": 0, "pm2": 0, "gleich": 0, "nb": 0})
    schicht = defaultdict(lambda: {"n": 0, "pm2": 0})
    abweichungen = []
    for line in open(urteile, encoding="utf-8"):
        u = json.loads(line)
        p = paare[u["nr"]]
        unser = int(bew[(p["unternehmen_id"], p["subtrend_id"])][spalte])
        k = bereich(unser)
        e = erg[k]
        e["n"] += 1
        s = schicht[bereich(int(alt[(p["unternehmen_id"], p["subtrend_id"])][schicht_spalte]))]
        s["n"] += 1
        if u.get("score") in (None, "n. b.", "nb"):
            e["nb"] += 1
            continue
        d = abs(int(u["score"]) - unser)
        e["pm2"] += d <= 2
        e["gleich"] += d == 0
        s["pm2"] += d <= 2
        if d > 2:
            abweichungen.append({"nr": u["nr"], "unternehmen": p["unternehmen"], "subtrend": p["subtrend"],
                                 "verfahren": unser, "zweitbewertung": int(u["score"]), "begruendung": u.get("begruendung", "")})
    bestand = defaultdict(int)
    if ziehung:
        bestand.update(json.loads(ziehung.read_text(encoding="utf-8"))["bestand"])
    else:
        for r in alt.values():
            if r["status"] == "bewertet":
                bestand[bereich(int(r[schicht_spalte]))] += 1
    n = sum(e["n"] for e in erg.values())
    pm2 = sum(e["pm2"] for e in erg.values())
    gewichtet = (sum(bestand[k] * schicht[k]["pm2"] / schicht[k]["n"] for k in schicht if schicht[k]["n"])
                 / sum(bestand[k] for k in schicht))
    from datetime import datetime, timezone

    return {
        "zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spalte": spalte,
        "paare": n, "innerhalb_2": pm2, "anteil_innerhalb_2": round(pm2 / n, 3) if n else None,
        "anteil_innerhalb_2_bereiche_gleich_gewichtet": round(sum(e["pm2"] / e["n"] for e in erg.values() if e["n"])
                                                            / sum(1 for e in erg.values() if e["n"]), 3) if n else None,
        "anteil_innerhalb_2_hochgerechnet": round(gewichtet, 3),
        "hochgerechnet_nach": "Schichten der Ziehung" if schichten else "Scorebereichen des aktuellen Stands",
        "je_schicht_der_ziehung": {k: {"n": schicht[k]["n"], "innerhalb_2": schicht[k]["pm2"], "bestand": bestand[k]}
                                   for k in BEREICHE if schicht[k]["n"]},
        "genau_gleich": sum(e["gleich"] for e in erg.values()),
        "zweitbewertung_nicht_bewertbar": sum(e["nb"] for e in erg.values()),
        "je_bereich": {k: dict(erg[k], anteil_innerhalb_2=round(erg[k]["pm2"] / erg[k]["n"], 3) if erg[k]["n"] else None)
                       for k in BEREICHE},
        "abweichungen_ueber_2": abweichungen,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="befehl", required=True)
    z = sub.add_parser("ziehen")
    z.add_argument("--daten", type=Path, default=Path("daten"))
    z.add_argument("--n", type=int, default=50)
    z.add_argument("--aus", type=Path, required=True)
    z.add_argument("--seed", type=int, default=11)
    z.add_argument("--ohne", type=Path, default=None, help="frühere Stichprobe, deren Paare ausgeschlossen werden")
    w = sub.add_parser("auswerten")
    w.add_argument("--daten", type=Path, default=Path("daten"))
    w.add_argument("--blind", type=Path, required=True)
    w.add_argument("--urteile", type=Path, required=True)
    w.add_argument("--aus", type=Path, default=None)
    w.add_argument("--spalte", default="score", help="score oder score_vor_pruefung (Lauf 1)")
    w.add_argument("--schichten", type=Path, default=None, help="Bewertungsstand der Ziehung (bewertungen.csv)")
    w.add_argument("--schicht-spalte", default="score", help="Scorespalte dieses Stands, nach der gezogen wurde")
    w.add_argument("--ziehung", type=Path, default=None, help="Ziehungsdatei mit dem Bestand je Schicht")
    a = ap.parse_args(argv)
    erg = (ziehen(a.daten, a.n, a.aus, a.seed, a.ohne) if a.befehl == "ziehen" else
           auswerten(a.daten, a.blind, a.urteile, a.spalte, a.schichten, a.schicht_spalte, a.ziehung))
    if a.befehl == "ziehen":
        a.aus.with_name(a.aus.name.replace("_blind.jsonl", "_ziehung.json")).write_text(
            json.dumps(erg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    text = json.dumps(erg, ensure_ascii=False, indent=2)
    if getattr(a, "aus", None) and a.befehl == "auswerten":
        a.aus.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
