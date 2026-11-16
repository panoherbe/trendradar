"""Unabhängige Nachprüfung: steht jedes gezeigte Zitat wörtlich in der gespeicherten Quelle?

Aufträge: gegen Titel, Los und Beschreibung des Auftrags (daten/belege_auftraege.jsonl).
Website: gegen den gespeicherten Volltext der Seite (roh/web/<domain>/<sha256[:16]>.txt, nicht im Repository).
Ebenso die Merkmale des Website-Steckbriefs.

    python -m trendradar.zitatpruefung --daten daten --roh roh/web --aus nachweis/2026-11-04/zitate.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .bewertung import zitat_geprueft


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--daten", type=Path, default=Path("daten"))
    ap.add_argument("--roh", type=Path, default=Path("roh/web"))
    ap.add_argument("--aus", type=Path, required=True)
    a = ap.parse_args(argv)
    auftraege = {}
    for line in open(a.daten / "belege_auftraege.jsonl", encoding="utf-8"):
        b = json.loads(line)
        auftraege[b["id"]] = " ".join([b["titel"], b["los"], b["text"]])
    seiten = {}
    for f in a.roh.glob("*/*.txt"):
        seiten[f.stem[:12]] = f.read_text(encoding="utf-8")
    web_urls = {}
    if (a.daten / "web_lauf.jsonl").exists():
        for line in open(a.daten / "web_lauf.jsonl", encoding="utf-8"):
            for s in json.loads(line).get("seiten", []):
                if s.get("sha256"):
                    web_urls.setdefault(s["url"], []).append(s["sha256"][:12])
    erg = {"auftrag": [0, 0], "web": [0, 0], "merkmale": [0, 0]}
    fehler = []
    for line in open(a.daten / "belegstellen.jsonl", encoding="utf-8"):
        b = json.loads(line)
        quelle = auftraege.get(b["beleg_id"]) if b["quelle"] == "auftrag" else seiten.get(b["beleg_id"][1:])
        ok = quelle is not None and zitat_geprueft(b["zitat"], quelle)
        erg[b["quelle"]][0] += 1
        erg[b["quelle"]][1] += ok
        if not ok:
            fehler.append({"beleg_id": b["beleg_id"], "zitat": b["zitat"][:120]})
    if (a.daten / "merkmale.jsonl").exists():
        for line in open(a.daten / "merkmale.jsonl", encoding="utf-8"):
            m = json.loads(line)
            texte = [seiten[s] for s in web_urls.get(m["url"], []) if s in seiten]
            ok = any(zitat_geprueft(m["zitat"], t) for t in texte)
            erg["merkmale"][0] += 1
            erg["merkmale"][1] += ok
            if not ok:
                fehler.append({"merkmal": m["wert"], "zitat": m["zitat"][:120]})
    out = {"zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    out.update({k: {"geprueft": v[0], "woertlich": v[1]} for k, v in erg.items()})
    out["nicht_gefunden"] = fehler[:50]
    a.aus.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("nicht_gefunden", "zeitpunkt_utc")} | {"fehler": len(fehler)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
