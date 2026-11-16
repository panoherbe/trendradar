"""Strukturierte Zusammenfassung einer Website: ein Sprachmodell schlägt Merkmale vor, der Code prüft jedes Zitat.

Das Modell erhält nur Passagen der Unternehmensdomain (vorher um E-Mail-Adressen und Telefonnummern bereinigt) und
füllt ein festes Schema: Tätigkeit, Produkt, Verfahren, Werkstoff, Zielbranche, Zertifizierung, Technologie.
**Jedes Merkmal nennt Passage und wörtliches Zitat. Der Code verwirft jedes Merkmal, dessen Zitat nicht wörtlich in
der genannten Passage steht.** Gezählt wird, wie viele Vorschläge das betrifft.

Das Modell läuft lokal über Ollama (Standard siehe ``trendradar.sprachmodell``); austauschbar über
``SPRACHMODELL`` und ``OLLAMA_URL``, etwa gegen einen Modelldienst mit Verarbeitung in der EU.

    python -m trendradar.merkmale --web daten/web_lauf.jsonl --aus daten/merkmale.jsonl --messung nachweis/2026-11-04/merkmale.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import sprachmodell
from .bereinigung import bereinige

ARTEN = ["Tätigkeit", "Produkt", "Verfahren", "Werkstoff", "Zielbranche", "Zertifizierung", "Technologie"]
MAX_ZEICHEN = 7000
PROMPT_VERSION = "M1"

SYSTEM = (
    "Du liest nummerierte Passagen der Website eines Unternehmens. Nenne nur, was dieses Unternehmen selbst tut, "
    "herstellt, anbietet oder nachweist. Keine Kunden, keine Partner, keine Personen, keine Werbesprüche. "
    "Jedes Merkmal braucht: art (genau einer der Werte " + ", ".join(ARTEN) + "), wert (kurz, 1 bis 6 Wörter, "
    "deutsch), passage (die Nummer, z. B. P3) und zitat: ein zusammenhängender Ausschnitt von 3 bis 25 Wörtern, "
    "Zeichen für Zeichen aus genau dieser Passage kopiert, ohne Änderung, ohne Auslassung. Höchstens 20 Merkmale. "
    "Wenn nichts belegt ist, gib eine leere Liste zurück."
)

SCHEMA = {
    "type": "object",
    "properties": {"merkmale": {"type": "array", "items": {
        "type": "object",
        "properties": {"art": {"type": "string", "enum": ARTEN}, "wert": {"type": "string"},
                       "passage": {"type": "string"}, "zitat": {"type": "string"}},
        "required": ["art", "wert", "passage", "zitat"]}}},
    "required": ["merkmale"],
}


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def pruefe(merkmal: dict, passagen: dict[str, dict]) -> str | None:
    """None, wenn das Merkmal gilt; sonst der Grund, warum es verworfen wird."""
    pid = str(merkmal.get("passage", "")).strip().upper()
    if not pid.startswith("P"):
        pid = "P" + pid
    if pid not in passagen:
        return "passage_unbekannt"
    zitat = _norm(str(merkmal.get("zitat", "")))
    if len(zitat.split()) < 2:
        return "zitat_zu_kurz"
    if zitat not in _norm(passagen[pid]["text"]):
        return "zitat_nicht_woertlich"
    if not str(merkmal.get("wert", "")).strip() or merkmal.get("art") not in ARTEN:
        return "unvollstaendig"
    return None


def auswahl(lauf: dict) -> dict[str, dict]:
    """Passagen einer Domain, nummeriert, bis MAX_ZEICHEN (Reihenfolge der Seitenauswahl)."""
    out, n, zeichen = {}, 0, 0
    for s in lauf.get("seiten", []):
        if s.get("status") != "ok":
            continue
        for p in s.get("passagen", []):
            if zeichen + len(p) > MAX_ZEICHEN:
                return out
            n += 1
            out[f"P{n}"] = {"text": bereinige(p), "url": s["url"], "datum": s.get("abruf_utc", "")[:10]}
            zeichen += len(p)
    return out


def frage(client, url: str, modell: str, passagen: dict[str, dict]) -> tuple[list[dict], float]:
    text = "\n".join(f"{pid}: {p['text']}" for pid, p in passagen.items())
    inhalt, sek, _ = sprachmodell.chat(client, modell, SYSTEM, text, {"num_ctx": 8192}, SCHEMA, basis=url)
    try:
        merkmale = json.loads(inhalt).get("merkmale", [])
    except (ValueError, AttributeError):
        merkmale = []
    return [m for m in merkmale if isinstance(m, dict)], sek


def nachbereinigen(pfad: Path) -> int:
    """Gespeicherte Merkmale mit den aktuellen Regeln bereinigen (ohne neuen Modellaufruf). Gibt die Zahl der
    geänderten Merkmale zurück; die Zitatprüfung bereinigt die Quelle genauso, Zitate bleiben prüfbar."""
    zeilen, geaendert = [], 0
    for line in open(pfad, encoding="utf-8"):
        m = json.loads(line)
        neu = {**m, "zitat": bereinige(m["zitat"]), "wert": bereinige(m["wert"])}
        geaendert += neu != m
        zeilen.append(neu)
    with open(pfad, "w", encoding="utf-8") as fh:
        for m in zeilen:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    return geaendert


def main(argv=None) -> int:
    import httpx

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--web", type=Path, required=True)
    ap.add_argument("--aus", type=Path, required=True)
    ap.add_argument("--messung", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--nur-bereinigen", action="store_true", help="vorhandene Merkmale nach den aktuellen Regeln bereinigen")
    a = ap.parse_args(argv)
    if a.nur_bereinigen:
        print(json.dumps({"geaendert": nachbereinigen(a.aus)}))
        return 0
    url = sprachmodell.url()
    modell = sprachmodell.modell()
    laeufe = [json.loads(l) for l in open(a.web, encoding="utf-8")]
    laeufe = [d for d in laeufe if d.get("status") == "ok"][: a.limit or None]
    gruende: dict[str, int] = {}
    vorgeschlagen = behalten = 0
    zeiten = []
    with httpx.Client() as client:
        digest = sprachmodell.digest(client, modell, url)
        with open(a.aus, "w", encoding="utf-8") as fh:
            for d in laeufe:
                ps = auswahl(d)
                if not ps:
                    continue
                try:
                    merkmale, sek = frage(client, url, modell, ps)
                except httpx.HTTPError as ex:
                    gruende["modellfehler"] = gruende.get("modellfehler", 0) + 1
                    print(f"{d['domain']}: {type(ex).__name__}", file=sys.stderr)
                    continue
                zeiten.append(sek)
                gesehen = set()
                for m in merkmale:
                    vorgeschlagen += 1
                    grund = pruefe(m, ps)
                    schluessel = (m.get("art"), _norm(str(m.get("wert", ""))).lower())
                    if grund is None and schluessel in gesehen:
                        grund = "doppelt"
                    if grund:
                        gruende[grund] = gruende.get(grund, 0) + 1
                        continue
                    gesehen.add(schluessel)
                    behalten += 1
                    pid = str(m["passage"]).strip().upper()
                    pid = pid if pid.startswith("P") else "P" + pid
                    fh.write(json.dumps({
                        "unternehmen_id": d["unternehmen_id"], "art": m["art"], "wert": _norm(m["wert"])[:80],
                        "zitat": _norm(m["zitat"]), "url": ps[pid]["url"], "datum": ps[pid]["datum"],
                    }, ensure_ascii=False) + "\n")
                print(f"{d['domain']}: {len(merkmale)} vorgeschlagen, {sek:.1f} s", file=sys.stderr)
    zeiten.sort()
    messung = {
        "zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "modell": modell, "digest": digest, "prompt": PROMPT_VERSION, "domains": len(laeufe),
        "vorgeschlagen": vorgeschlagen, "behalten": behalten, "verworfen": vorgeschlagen - behalten,
        "verworfen_nach_grund": gruende,
        "sekunden_je_domain_median": round(zeiten[len(zeiten) // 2], 1) if zeiten else None,
        "sekunden_gesamt": round(sum(zeiten), 1),
    }
    a.messung.write_text(json.dumps(messung, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(messung, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
