"""Bewertung: jedes Unternehmen gegen jeden Subtrend, über die Fähigkeits-Brücke, mit Belegen.

Ablauf
1. Jede Quelle wird in Passagen zerlegt (Aufträge: Titel, Los, Beschreibung; Website: nummerierte Passagen).
2. Je Passage und Teilfähigkeit drei Signale: ein **Begriff** der Teilfähigkeit steht wörtlich in der Passage,
   ein **CPV-Code** des Auftrags liegt unter einem CPV-Präfix der Teilfähigkeit, und die **semantische Nähe**
   (Kosinus der Vektoren eines Einbettungsmodells von Passage und Beschreibung der Teilfähigkeit).
3. **Eindeutiger Beleg** = Begriff oder CPV-Code trifft UND die Passage ist semantisch nah genug (schützt vor
   Zufallstreffern eines Wortes). **Indirekter Bezug** = semantisch sehr nah, aber ohne Begriff oder Code.
4. Feste Rubrik je Subtrend (Skala 1–10):
     1      keine Anhaltspunkte
     2–3    schwacher Bezug (nur entfernte semantische Nähe)
     4–5    indirekter Bezug
     6–7    ein eindeutiger Beleg (7: dazu ein zweiter Beleg, eine zweite Teilfähigkeit oder ein indirekter Bezug)
     8–10   mehrere eindeutige Belege, davon mindestens zwei, deren Text die Fähigkeit nennt (9: drei solche
            Belege oder zwei Teilfähigkeiten; 10: zwei Teilfähigkeiten und mindestens vier Belege)
   Ein CPV-Code allein belegt höchstens Stufe 7: Codes wie „Elektroinstallation“ sind zu allgemein, um ein
   Unternehmen an die Spitze eines Subtrends zu stellen.
   Zu wenig Text heißt **„nicht bewertbar“**, ein eigener Status, nie eine 1.
5. Megatrend je Unternehmen nach zwei Regeln, beide gespeichert: höchster Subtrend-Wert und Mittel der drei
   höchsten Subtrend-Werte.

Jede Zahl führt auf Passagen mit Quelle (URL) zurück; jedes Zitat ist ein wörtlicher Ausschnitt der Quelle.

    python -m trendradar.bewertung --daten daten --web daten/web_lauf.jsonl --cache roh/vektoren.npz
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .bereinigung import bereinige
from .einbettung import STANDARD_MODELL, einbetter
from .taxonomie import STANDARD, Taxonomie, lade

# Schwellen je Einbettungsmodell. bge-m3: festgelegt am 04.11.2026 durch Sichtprüfung von Grenzfällen (Messprotokoll
# 04.11., Abschnitt Kalibrierung), BEVOR die blinde Zweitbewertung gezogen wurde. Jedes neue Modell bekommt seine
# Schwellen über denselben Anteil aller Paare Passage × Teilfähigkeit („Quantil-Abgleich“, ohne Blick auf die
# Zweitbewertung): ``python -m trendradar.modellwahl schwellen``. Im Auftrag: an geprüften Paaren kalibrieren.
REGELVERSION = "R3"
SCHWELLEN = {
    "BAAI/bge-m3": {"klar": 0.50, "schwach": 0.58, "schwach_plus": 0.04, "indirekt": 0.68, "indirekt_plus": 0.04},
}
SIM_KLAR = 0.50        # Mindestnähe, damit ein Begriffs- oder CPV-Treffer als eindeutiger Beleg zählt
SIM_INDIREKT = 0.68    # Nähe ohne Begriff/Code, ab der ein indirekter Bezug vorliegt (darunter meist Zufall)
SIM_SCHWACH = 0.58     # entfernte Nähe (Stufe 2–3); etwa die obersten 7 % der Paare ohne Beleg
SIM_INDIREKT_PLUS = 0.04   # ab SIM_INDIREKT + diesem Abstand: Stufe 5 statt 4
SIM_SCHWACH_PLUS = 0.04    # ab SIM_SCHWACH + diesem Abstand: Stufe 3 statt 2


def setze_schwellen(modell: str, datei: Path | None = None) -> dict:
    """Schwellen des Modells aktiv setzen. Abgeglichene Schwellen neuer Modelle stehen in ``daten/schwellen.json``
    (geschrieben von ``trendradar.modellwahl schwellen``); ein Modell ohne Schwellen muss erst abgeglichen werden."""
    global SIM_KLAR, SIM_INDIREKT, SIM_SCHWACH, SIM_INDIREKT_PLUS, SIM_SCHWACH_PLUS
    if datei and datei.exists():
        d = json.loads(datei.read_text(encoding="utf-8"))
        SCHWELLEN.setdefault(d["nach"], d["schwellen_nach"])
    if modell not in SCHWELLEN:
        raise SystemExit(f"Keine Schwellen für {modell}: erst `python -m trendradar.modellwahl schwellen` rechnen")
    s = SCHWELLEN[modell]
    SIM_KLAR, SIM_INDIREKT, SIM_SCHWACH = s["klar"], s["indirekt"], s["schwach"]
    SIM_INDIREKT_PLUS, SIM_SCHWACH_PLUS = s["indirekt_plus"], s["schwach_plus"]
    return s
MIN_ZEICHEN = 160      # weniger Beschreibungstext je Unternehmen: „nicht bewertbar“
MAX_ZITAT = 360


@dataclass
class Passage:
    pid: str
    unternehmen_id: str
    beleg_id: str
    quelle: str          # auftrag | web
    url: str
    datum: str
    feld: str            # titel | los | text | web
    text: str
    cpv: tuple[str, ...]


def _pid(*teile: str) -> str:
    return "p" + hashlib.sha256("\x00".join(teile).encode()).hexdigest()[:12]


def zerlege(text: str, max_len: int = MAX_ZITAT, min_len: int = 25) -> list[str]:
    """Wörtliche Ausschnitte an Satzgrenzen (der Text selbst bleibt unverändert, nur Leerraum vereinheitlicht)."""
    from .quellen.web import passagen

    return passagen(text, max_len=max_len, min_len=min_len)


def passagen_auftraege(belege: list[dict]) -> list[Passage]:
    out = []
    for b in belege:
        cpv = tuple(b.get("cpv") or [])
        felder = [("titel", b.get("titel", "")), ("los", b.get("los", ""))]
        for feld, t in felder:
            t = bereinige(t)
            if len(t) >= 8:
                out.append(Passage(_pid(b["id"], feld, t), b["unternehmen_id"], b["id"], "auftrag", b["url"],
                                   b.get("datum", ""), feld, t[:MAX_ZITAT], cpv))
        for t in zerlege(bereinige(b.get("text", ""))):
            out.append(Passage(_pid(b["id"], "text", t), b["unternehmen_id"], b["id"], "auftrag", b["url"],
                               b.get("datum", ""), "text", t, cpv))
    return out


def passagen_web(laeufe: list[dict]) -> list[Passage]:
    out = []
    for d in laeufe:
        if d.get("status") != "ok":
            continue
        for s in d.get("seiten", []):
            if s.get("status") != "ok":
                continue
            beleg = "w" + s["sha256"][:12]
            for p in s.get("passagen", []):
                p = bereinige(p)          # gleiche Regeln wie für Aufträge, auch für ältere Abrufe
                out.append(Passage(_pid(beleg, p), d["unternehmen_id"], beleg, "web", s["url"],
                                   s.get("abruf_utc", "")[:10], "web", p, ()))
    return out


def begriff_regex(begriffe: tuple[str, ...]) -> re.Pattern | None:
    """Lange Begriffe als Teilwort (Komposita: „Dachphotovoltaik“), kurze nur am Wortanfang."""
    if not begriffe:
        return None
    teile = []
    for b in sorted(set(begriffe), key=len, reverse=True):
        e = re.escape(b)
        teile.append(e if len(b) >= 7 else rf"(?<![a-zäöüß]){e}")
    return re.compile("|".join(teile))


def stufe(n_text: int, n_code: int, k_faehig: int, indirekt: bool, beste: float) -> int:
    """n_text: eindeutige Belege, deren Text die Fähigkeit nennt; n_code: eindeutige Belege nur über den CPV-Code."""
    n_klar = n_text + n_code
    if n_text >= 2:
        if k_faehig >= 2 and n_klar >= 4:
            return 10
        if k_faehig >= 2 or n_text >= 3:
            return 9
        return 8
    if n_klar >= 1:
        return 7 if (n_klar >= 2 or k_faehig >= 2 or indirekt) else 6
    if indirekt:
        return 5 if beste >= SIM_INDIREKT + SIM_INDIREKT_PLUS else 4
    if beste >= SIM_SCHWACH:
        return 3 if beste >= SIM_SCHWACH + SIM_SCHWACH_PLUS else 2
    return 1


STUFENTEXT = {
    1: "keine Anhaltspunkte", 2: "schwacher Bezug", 3: "schwacher Bezug", 4: "indirekter Bezug",
    5: "indirekter Bezug", 6: "ein eindeutiger Beleg", 7: "ein eindeutiger Beleg", 8: "mehrere eindeutige Belege",
    9: "mehrere eindeutige Belege", 10: "mehrere eindeutige Belege",
}


def vektor_cache(modell: str, roh: Path = Path("roh")) -> Path:
    """Ein Zwischenspeicher je Modell (Vektoren verschiedener Länge passen nicht in eine Datei)."""
    if modell == "BAAI/bge-m3":
        return roh / "vektoren.npz"                      # Stand 04.11.2026
    return roh / ("vektoren-" + re.sub(r"[^a-z0-9.]+", "-", modell.lower()) + ".npz")


def faehigkeit_text(s, f) -> str:
    return f"{s.name}: {f.name}. {f.beschreibung}"


def bewerte(tax: Taxonomie, unternehmen: list[dict], passagen: list[Passage], einbetter) -> dict:
    """Bewertet alle Unternehmen × Subtrends. Rückgabe: Tabellen als Listen von dicts."""
    paare = tax.faehigkeiten()
    f_vec = einbetter.kodiere([faehigkeit_text(s, f) for s, f in paare], art="anfrage")
    regex = [begriff_regex(f.begriffe) for _, f in paare]
    sub_index = defaultdict(list)
    for j, (s, f) in enumerate(paare):
        sub_index[s.id].append(j)
    je_firma = defaultdict(list)
    for p in passagen:
        je_firma[p.unternehmen_id].append(p)
    p_vec = einbetter.kodiere([p.text for p in passagen]) if passagen else np.zeros((0, f_vec.shape[1]))
    p_idx = {id(p): i for i, p in enumerate(passagen)}

    bewertungen, belegstellen, megatrends = [], [], []
    for u in unternehmen:
        ps = je_firma.get(u["id"], [])
        zeichen = sum(len(t) for t in {p.text for p in ps})
        if zeichen < MIN_ZEICHEN or not ps:
            for s in tax.subtrends.values():
                if s.aktiv:
                    bewertungen.append({"unternehmen_id": u["id"], "subtrend_id": s.id, "score": None,
                                        "status": "nicht_bewertbar", "stufe": "nicht bewertbar – zu wenig Beschreibungstext",
                                        "n_belege": 0, "k_faehigkeiten": 0, "beste_naehe": None})
            for m in tax.megatrends.values():
                megatrends.append({"unternehmen_id": u["id"], "megatrend_id": m.id, "max": None, "top3": None,
                                   "status": "nicht_bewertbar", "n_subtrends": 0})
            continue
        V = p_vec[[p_idx[id(p)] for p in ps]]
        S = V @ f_vec.T                                    # Passagen × Teilfähigkeiten
        texte = [p.text.lower() for p in ps]
        klar = np.zeros(S.shape, bool)
        art = {}
        for j, (s, f) in enumerate(paare):
            rx = regex[j]
            for i, p in enumerate(ps):
                treffer = None
                if rx is not None:
                    m = rx.search(texte[i])
                    if m:
                        treffer = ("begriff", m.group(0))
                if treffer is None and f.cpv and p.cpv:
                    c = next((c for c in p.cpv for pre in f.cpv if c.startswith(pre)), None)
                    if c:
                        treffer = ("cpv", c)
                if treffer and S[i, j] >= SIM_KLAR:
                    klar[i, j] = True
                    art[(i, j)] = treffer
        sub_scores = {}
        for s in tax.subtrends.values():
            if not s.aktiv:
                continue
            js = sub_index[s.id]
            Ks = klar[:, js]
            belege_klar = {ps[i].beleg_id for i in range(len(ps)) if Ks[i].any()}
            belege_text = {ps[i].beleg_id for i in range(len(ps)) for j in js
                           if klar[i, j] and art[(i, j)][0] == "begriff"}
            k = int(Ks.any(axis=0).sum())
            Ss = S[:, js]
            beste = float(Ss.max())
            indirekt = bool(((Ss >= SIM_INDIREKT) & ~Ks).any())
            sc = stufe(len(belege_text), len(belege_klar - belege_text), k, indirekt, beste)
            sub_scores[s.id] = sc
            bewertungen.append({"unternehmen_id": u["id"], "subtrend_id": s.id, "score": sc, "status": "bewertet",
                                "stufe": STUFENTEXT[sc], "n_belege": len(belege_klar), "k_faehigkeiten": k,
                                "beste_naehe": round(beste, 3)})
            if sc < 4:
                continue
            # Belegstellen: je Teilfähigkeit die beste eindeutige Passage je Beleg, sonst die nächste indirekte
            gezeigt = 0
            for jj, j in enumerate(js):
                f = paare[j][1]
                kandidaten = [(float(S[i, j]), i) for i in range(len(ps)) if klar[i, j]]
                typ = "eindeutig"
                if not kandidaten:
                    kandidaten = [(float(S[i, j]), i) for i in range(len(ps)) if S[i, j] >= SIM_INDIREKT]
                    typ = "indirekt"
                schon = set()
                for sim, i in sorted(kandidaten, reverse=True):
                    p = ps[i]
                    if p.beleg_id in schon or len(schon) >= 3:
                        continue
                    schon.add(p.beleg_id)
                    a = art.get((i, j), ("semantisch", ""))
                    belegstellen.append({
                        "unternehmen_id": u["id"], "subtrend_id": s.id, "faehigkeit_id": f.id, "typ": typ,
                        "signal": a[0], "treffer": a[1], "naehe": round(sim, 3), "beleg_id": p.beleg_id,
                        "quelle": p.quelle, "url": p.url, "datum": p.datum, "feld": p.feld, "zitat": p.text,
                    })
                    gezeigt += 1
        for m in tax.megatrends.values():
            werte = sorted((sub_scores[sid] for sid in m.subtrends if sid in sub_scores), reverse=True)
            megatrends.append({"unternehmen_id": u["id"], "megatrend_id": m.id,
                               "max": werte[0] if werte else None,
                               "top3": round(sum(werte[:3]) / len(werte[:3]), 2) if werte else None,
                               "status": "bewertet" if werte else "nicht_bewertbar", "n_subtrends": len(werte)})
    return {"bewertungen": bewertungen, "belegstellen": belegstellen, "megatrends": megatrends}


def zitat_geprueft(zitat: str, quelle: str) -> bool:
    """Ein Zitat gilt nur, wenn es wörtlich (bis auf Leerraum) in der bereinigten Quelle steht."""
    norm = lambda t: re.sub(r"\s+", " ", t).strip()
    return norm(zitat) in norm(bereinige(quelle))


def schreibe_webstatus(laeufe: list[dict], csv_pfad: Path, json_pfad: Path) -> None:
    """Status je Domain (ins Repository) und Kennzahlen des Abrufs; die Volltexte bleiben außerhalb."""
    zeilen = [{"domain": d["domain"], "status": d["status"], "grund": d.get("grund", ""),
               "seiten_gelesen": sum(s.get("status") == "ok" for s in d.get("seiten", [])),
               "abrufe": d.get("abrufe", 0), "dauer_s": d.get("dauer_s", 0)} for d in laeufe]
    _schreibe_csv(csv_pfad, sorted(zeilen, key=lambda z: z["domain"]))
    status: dict[str, int] = {}
    for d in laeufe:
        status[d["status"]] = status.get(d["status"], 0) + 1
    seiten = [s for d in laeufe for s in d.get("seiten", [])]
    datum = max((s.get("abruf_utc", "")[:10] for s in seiten if s.get("abruf_utc")), default="")
    json_pfad.parent.mkdir(parents=True, exist_ok=True)
    json_pfad.write_text(json.dumps({
        "zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "datum": datum, "domains": len(laeufe), "status": status,
        "quote_ok": round(status.get("ok", 0) / len(laeufe), 3) if laeufe else None,
        "seiten_ok": sum(s.get("status") == "ok" for s in seiten),
        "seiten_robots_gesperrt": sum(s.get("status") == "robots_gesperrt" for s in seiten),
        "seiten_fehler": sum(s.get("status") not in ("ok", "robots_gesperrt") for s in seiten),
        "abrufe": sum(d.get("abrufe", 0) for d in laeufe),
        "dauer_s": round(max((d.get("dauer_s", 0) for d in laeufe), default=0), 1),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _schreibe_csv(pfad: Path, zeilen: list[dict]) -> None:
    if not zeilen:
        pfad.write_text("")
        return
    with open(pfad, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(zeilen[0]))
        w.writeheader()
        w.writerows(zeilen)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--daten", type=Path, default=Path("daten"))
    ap.add_argument("--taxonomie", type=Path, default=STANDARD)
    ap.add_argument("--web", type=Path, default=None, help="JSONL des Webabrufs (optional)")
    ap.add_argument("--modell", default=STANDARD_MODELL)
    ap.add_argument("--cache", type=Path, default=None, help="Zwischenspeicher der Vektoren (Standard: je Modell in roh/)")
    ap.add_argument("--test-einbetter", action="store_true", help="ohne Modell (nur für Tests)")
    ap.add_argument("--nachweis", type=Path, default=None, help="Ordner für Kennzahlen des Webabrufs (crawl.json)")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    tax = lade(a.taxonomie)
    with open(a.daten / "unternehmen.csv", encoding="utf-8") as fh:
        unternehmen = list(csv.DictReader(fh))
    belege = [json.loads(l) for l in open(a.daten / "belege_auftraege.jsonl", encoding="utf-8")]
    laeufe = [json.loads(l) for l in open(a.web, encoding="utf-8")] if a.web and a.web.exists() else []
    ps = passagen_auftraege(belege) + passagen_web(laeufe)
    if a.test_einbetter:
        from .einbettung import HashEinbetter
        emb = HashEinbetter()
    else:
        setze_schwellen(a.modell, a.daten / "schwellen.json")
        emb = einbetter(a.modell, a.cache or vektor_cache(a.modell))
    t1 = time.monotonic()
    erg = bewerte(tax, unternehmen, ps, emb)
    t2 = time.monotonic()
    emb.speichere()
    _schreibe_csv(a.daten / "bewertungen.csv", erg["bewertungen"])
    _schreibe_csv(a.daten / "megatrends.csv", erg["megatrends"])
    with open(a.daten / "belegstellen.jsonl", "w", encoding="utf-8") as fh:
        for b in erg["belegstellen"]:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")
    bew = [b for b in erg["bewertungen"] if b["status"] == "bewertet"]
    lauf = {
        "zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "taxonomie": tax.kennung, "regelversion": REGELVERSION, "modell": emb.modell,
        "schwellen": {"sim_klar": SIM_KLAR, "sim_indirekt": SIM_INDIREKT, "sim_schwach": SIM_SCHWACH,
                      "sim_indirekt_plus": SIM_INDIREKT_PLUS, "sim_schwach_plus": SIM_SCHWACH_PLUS,
                      "min_zeichen": MIN_ZEICHEN},
        "unternehmen": len(unternehmen), "auftraege": len(belege),
        "websites": {"domains": len(laeufe), "gelesen": sum(d.get("status") == "ok" for d in laeufe)},
        "passagen": len(ps),
        "passagen_auftraege": sum(p.quelle == "auftrag" for p in ps), "passagen_web": sum(p.quelle == "web" for p in ps),
        "subtrends": sum(s.aktiv for s in tax.subtrends.values()), "teilfaehigkeiten": len(tax.faehigkeiten()),
        "paare": len(erg["bewertungen"]), "paare_bewertet": len(bew),
        "paare_nicht_bewertbar": len(erg["bewertungen"]) - len(bew),
        "verteilung": {str(k): sum(1 for b in bew if b["score"] == k) for k in range(1, 11)},
        "belegstellen": len(erg["belegstellen"]),
        "dauer_s": {"laden": round(t1 - t0, 1), "bewertung_inkl_einbettung": round(t2 - t1, 1)},
    }
    (a.daten / "lauf.json").write_text(json.dumps(lauf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from .karte import layout_gespeichert

    layout_gespeichert(tax, Path(a.taxonomie).with_name("karte.json"))   # Liniengrafik einmal je Taxonomie-Stand
    if laeufe:
        schreibe_webstatus(laeufe, a.daten / "web_status.csv", (a.nachweis or a.daten.parent / "nachweis" / datetime.now(timezone.utc).date().isoformat()) / "crawl.json")
    print(json.dumps(lauf, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
