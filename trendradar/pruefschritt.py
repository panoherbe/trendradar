"""Prüfschritt: ein Sprachmodell prüft die Belege der stärksten Kandidaten, die Rubrik zählt danach neu.

Die erste Bewertung (``bewertung``) ist bewusst großzügig: ein Fachbegriff oder CPV-Code plus semantische Nähe.
Das trifft auch Nebenpositionen in langen Leistungsverzeichnissen, bloße Gebäudetypen („Reinigung im
Feuerwehrgerätehaus“) und Werbefloskeln. Deshalb prüft hier ein Sprachmodell jede Belegstelle der Paare ab Stufe 6
gegen die Teilfähigkeiten des Subtrends: bestätigt wird nur, was zeigt, dass das Unternehmen die Fähigkeit selbst
als wesentlichen Teil seiner Leistung erbringt. Danach wird mit derselben festen Rubrik neu gezählt, nur mit den
bestätigten Belegen. Nicht bestätigte Belegstellen werden nicht mehr gezeigt, aber mit Grund gespeichert.

Urteile werden je Belegstelle und Modell zwischengespeichert (``daten/pruefung.jsonl``): ein erneuter Lauf mit
unverändertem Datenstand und Modell fragt das Modell nicht noch einmal und ergibt dieselben Zahlen. Urteile eines
anderen Modells zählen nicht; die Datei enthält nach dem Lauf nur die des aktuellen Modells.

    python -m trendradar.pruefschritt --daten daten --messung nachweis/2026-11-04/pruefung.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import sprachmodell
from .bewertung import STUFENTEXT, _schreibe_csv, setze_schwellen, stufe
from .taxonomie import STANDARD, lade

AB_STUFE = 6
PROMPT_VERSION = "P3"   # P3: Ausgabe ohne Grammatik-Zwang (der war auf der CPU der Engpass), JSON tolerant gelesen

SYSTEM = (
    "Du prüfst Belege für ein Trend-Radar. Du erhältst ein Unternehmen, einen Subtrend mit seinen Teilfähigkeiten "
    "und nummerierte Belegstellen: wörtliche Zitate aus öffentlichen Aufträgen, die das Unternehmen gewonnen hat, "
    "oder von seiner eigenen Website. Entscheide für jede Belegstelle, ob sie zeigt, dass das Unternehmen selbst die "
    "genannte Teilfähigkeit als wesentlichen Teil seiner Leistung erbracht hat oder anbietet. "
    "NICHT bestätigen: eine Nebenposition in einer langen Leistungsliste; nur der Gebäudetyp oder Ort der Arbeit "
    "(z. B. Reinigung in einer Feuerwache), außer die Teilfähigkeit ist der Bau oder die Ausstattung solcher Gebäude; "
    "Werbefloskeln ohne konkrete Leistung; Kunden, Partner oder Referenzen statt eigener Leistung; ein gleiches Wort "
    "für eine andere Sache. Im Zweifel nicht bestätigen. Antworte nur mit JSON der Form "
    '{"belege": [{"nr": 1, "bestaetigt": true, "grund": "höchstens acht Wörter"}]}, je Belegstelle ein Eintrag.'
)
SCHEMA = {
    "type": "object",
    "properties": {"belege": {"type": "array", "items": {
        "type": "object",
        "properties": {"nr": {"type": "integer"}, "bestaetigt": {"type": "boolean"}, "grund": {"type": "string"}},
        "required": ["nr", "bestaetigt", "grund"]}}},
    "required": ["belege"],
}


def lies_json(text: str) -> dict:
    """Erstes JSON-Objekt aus der Antwort (das Modell setzt es manchmal in einen Codeblock)."""
    a, e = text.find("{"), text.rfind("}")
    if a < 0 or e <= a:
        return {}
    try:
        d = json.loads(text[a:e + 1])
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def schluessel(b: dict) -> str:
    return hashlib.sha256("\x00".join([b["unternehmen_id"], b["subtrend_id"], b["faehigkeit_id"], b["beleg_id"],
                                       b["zitat"]]).encode()).hexdigest()[:20]


def prompt(firma: str, s, belege: list[dict], auftraege: dict) -> str:
    fn = {f.id: f for f in s.faehigkeiten}
    z = [f"Unternehmen: {firma}", f"Subtrend: {s.name} – {s.beschreibung}", "Teilfähigkeiten:"]
    z += [f"- {f.name}: {f.beschreibung}" for f in s.faehigkeiten]
    z.append("Belegstellen:")
    for i, b in enumerate(belege, 1):
        if b["quelle"] == "auftrag":
            a = auftraege.get(b["beleg_id"], {})
            herkunft = f"öffentlicher Auftrag „{a.get('titel', '')[:140]}“" + (f", Los „{a['los'][:100]}“" if a.get("los") else "")
        else:
            herkunft = "Website des Unternehmens"
        z.append(f"[{i}] Teilfähigkeit „{fn[b['faehigkeit_id']].name}“ · {herkunft}: „{b['zitat']}“")
    return "\n".join(z)


def frage(client, modell: str, firma: str, s, offen: list[dict], auftraege: dict, basis: str | None = None):
    """Urteile je Nummer der Belegstelle, Sekunden, Zähler von Ollama.

    Kontext 4096 reicht für jede Anfrage; ``PRUEF_NUM_CTX`` gleicht ihn an einen geteilten Ollama-Dienst an, der sonst
    das Modell bei jedem Wechsel des Kontexts neu lädt. Die Urteile hängen davon nicht ab."""
    optionen = {"num_ctx": int(os.environ.get("PRUEF_NUM_CTX", "4096")), "num_predict": 600}
    inhalt, sek, zaehler = sprachmodell.chat(client, modell, SYSTEM, prompt(firma, s, offen, auftraege),
                                             optionen, basis=basis)
    urteile = {u.get("nr"): u for u in lies_json(inhalt).get("belege", []) if isinstance(u, dict)}
    return urteile, sek, zaehler


def neu_zaehlen(bew: dict, bestaetigt: list[dict], indirekt: bool) -> dict:
    text = {b["beleg_id"] for b in bestaetigt if b["signal"] == "begriff"}
    alle = {b["beleg_id"] for b in bestaetigt}
    k = len({b["faehigkeit_id"] for b in bestaetigt})
    sc = stufe(len(text), len(alle - text), k, indirekt, float(bew["beste_naehe"] or 0))
    return {**bew, "score": sc, "stufe": STUFENTEXT[sc], "n_belege": len(alle), "k_faehigkeiten": k}


def main(argv=None) -> int:
    import httpx

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--daten", type=Path, default=Path("daten"))
    ap.add_argument("--taxonomie", type=Path, default=STANDARD)
    ap.add_argument("--messung", type=Path, required=True)
    ap.add_argument("--nur-cache", action="store_true", help="kein Modellaufruf; fehlende Urteile = nicht geprüft")
    a = ap.parse_args(argv)
    modell = sprachmodell.modell()
    tax = lade(a.taxonomie)
    lauf_vorher = json.loads((a.daten / "lauf.json").read_text(encoding="utf-8"))
    if lauf_vorher["modell"] != "hash-test":           # neu zählen mit den Schwellen des Einbettungsmodells
        setze_schwellen(lauf_vorher["modell"], a.daten / "schwellen.json")
    firmen = {r["id"]: r["name"] for r in csv.DictReader(open(a.daten / "unternehmen.csv", encoding="utf-8"))}
    auftraege = {json.loads(l)["id"]: json.loads(l) for l in open(a.daten / "belege_auftraege.jsonl", encoding="utf-8")}
    bew = list(csv.DictReader(open(a.daten / "bewertungen.csv", encoding="utf-8")))
    stellen = [json.loads(l) for l in open(a.daten / "belegstellen.jsonl", encoding="utf-8")]
    je_paar = defaultdict(list)
    for b in stellen:
        je_paar[(b["unternehmen_id"], b["subtrend_id"])].append(b)
    cache_pfad = a.daten / "pruefung.jsonl"
    cache = {}
    if cache_pfad.exists():
        for line in open(cache_pfad, encoding="utf-8"):
            d = json.loads(line)
            if d.get("modell") == modell and d.get("prompt") == PROMPT_VERSION:
                cache[d["schluessel"]] = d
    kandidaten = [r for r in bew if r["status"] == "bewertet" and int(r["score"]) >= AB_STUFE]
    zeiten, fehler = [], 0
    auftrag = []
    for r in kandidaten:
        eindeutig = [b for b in je_paar[(r["unternehmen_id"], r["subtrend_id"])] if b["typ"] == "eindeutig"]
        offen = [b for b in eindeutig if schluessel(b) not in cache]
        if offen and not a.nur_cache:
            auftrag.append((r, offen))

    from concurrent.futures import ThreadPoolExecutor, as_completed

    # nur Urteile des aktuellen Modells behalten (die älteren stehen in der Git-Geschichte)
    with open(cache_pfad, "w", encoding="utf-8") as fh:
        for d in cache.values():
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with httpx.Client() as client, open(cache_pfad, "a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(int(os.environ.get("PARALLEL", "2"))) as ex:
        laufend = {ex.submit(frage, client, modell, firmen[r["unternehmen_id"]], tax.subtrends[r["subtrend_id"]],
                             offen, auftraege): (r, offen) for r, offen in auftrag}
        for i, fut in enumerate(as_completed(laufend), 1):
            r, offen = laufend[fut]
            try:
                urteile, sek, _ = fut.result()
            except (httpx.HTTPError, ValueError, KeyError):
                fehler += 1
                continue
            zeiten.append(sek)
            for j, b in enumerate(offen, 1):
                u = urteile.get(j)
                d = {"schluessel": schluessel(b), "bestaetigt": bool(u and u.get("bestaetigt") is True),
                     "grund": str((u or {}).get("grund", "kein Urteil zurückgegeben"))[:200], "modell": modell,
                     "prompt": PROMPT_VERSION}
                cache[d["schluessel"]] = d
                fh.write(json.dumps(d, ensure_ascii=False) + "\n")
            fh.flush()
            if i % 100 == 0:
                print(f"{i}/{len(auftrag)} Paare geprüft", file=sys.stderr, flush=True)
    # neu zählen: geprüfte Paare nur mit bestätigten Belegen
    neue_bew, neue_stellen, geaendert = [], [], 0
    ungeprueft = 0
    for r in bew:
        paar = je_paar[(r["unternehmen_id"], r["subtrend_id"])]
        if r["status"] != "bewertet" or int(r["score"]) < AB_STUFE:
            neue_bew.append({**r, "score_vor_pruefung": r["score"]})
            neue_stellen += paar
            continue
        best, indirekt = [], any(b["typ"] == "indirekt" for b in paar)
        for b in paar:
            if b["typ"] != "eindeutig":
                continue
            u = cache.get(schluessel(b))
            if u is None:
                ungeprueft += 1          # ohne Urteil gilt ein Beleg nicht als bestätigt
            elif u["bestaetigt"]:
                best.append({**b, "geprueft": "bestaetigt", "pruef_grund": u["grund"]})
        n = neu_zaehlen(r, best, indirekt)
        geaendert += int(n["score"]) != int(r["score"])
        neue_bew.append({**n, "score_vor_pruefung": r["score"]})
        neue_stellen += best + [b for b in paar if b["typ"] == "indirekt"] if int(n["score"]) >= 4 else best
    _schreibe_csv(a.daten / "bewertungen.csv", neue_bew)
    with open(a.daten / "belegstellen.jsonl", "w", encoding="utf-8") as fh:
        for b in neue_stellen:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")
    # Megatrends aus den geprüften Subtrend-Werten
    scores = defaultdict(dict)
    for r in neue_bew:
        if r["status"] == "bewertet":
            scores[r["unternehmen_id"]][r["subtrend_id"]] = int(r["score"])
    mt = []
    for uid in firmen:
        for m in tax.megatrends.values():
            w = sorted((scores[uid][s] for s in m.subtrends if s in scores.get(uid, {})), reverse=True)
            mt.append({"unternehmen_id": uid, "megatrend_id": m.id, "max": w[0] if w else None,
                       "top3": round(sum(w[:3]) / len(w[:3]), 2) if w else None,
                       "status": "bewertet" if w else "nicht_bewertbar", "n_subtrends": len(w)})
    _schreibe_csv(a.daten / "megatrends.csv", mt)
    geprueft = [cache[schluessel(b)] for r in kandidaten for b in je_paar[(r["unternehmen_id"], r["subtrend_id"])]
                if b["typ"] == "eindeutig" and schluessel(b) in cache]
    vorher = defaultdict(int)
    for r in bew:
        if r["status"] == "bewertet":
            vorher[str(r["score"])] += 1
    vert = defaultdict(int)
    for r in neue_bew:
        if r["status"] == "bewertet":
            vert[str(r["score"])] += 1
    erg = {
        "zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "modell": modell,
        "prompt": PROMPT_VERSION, "paare_geprueft": len(kandidaten), "belegstellen_geprueft": len(geprueft),
        "bestaetigt": sum(g["bestaetigt"] for g in geprueft), "nicht_bestaetigt": sum(not g["bestaetigt"] for g in geprueft),
        "paare_geaendert": geaendert, "modellfehler": fehler, "belegstellen_ohne_urteil": ungeprueft,
        "modellaufrufe_in_diesem_lauf": len(zeiten),   # 0 = alle Urteile aus dem Zwischenspeicher, Zeiten siehe erster Lauf
        "sekunden_je_paar_median": round(sorted(zeiten)[len(zeiten) // 2], 1) if zeiten else None,
        "sekunden_gesamt": round(sum(zeiten), 1),
        "verteilung_vorher": dict(sorted(vorher.items(), key=lambda kv: int(kv[0]))), "verteilung_nachher": dict(sorted(vert.items(), key=lambda kv: int(kv[0]))),
    }
    lauf = json.loads((a.daten / "lauf.json").read_text(encoding="utf-8"))
    lauf["pruefschritt"] = {k: erg[k] for k in ("modell", "prompt", "paare_geprueft", "bestaetigt", "nicht_bestaetigt", "paare_geaendert")}
    lauf["verteilung"] = {str(k): vert.get(str(k), 0) for k in range(1, 11)}
    lauf["belegstellen"] = len(neue_stellen)
    (a.daten / "lauf.json").write_text(json.dumps(lauf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    a.messung.write_text(json.dumps(erg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(erg, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
