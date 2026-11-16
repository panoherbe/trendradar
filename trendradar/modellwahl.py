"""Modellwahl: Einbettungs- und Sprachmodelle auf den Aufgaben des Trend-Radars messen, gegen die blinde Zweitbewertung.

Maßstab sind die Paare Unternehmen × Subtrend der beiden blinden Zweitbewertungen vom 04.11.2026
(``nachweis/2026-11-04/``: ein großes kommerzielles Sprachmodell, nach der Rubrik, ohne Kenntnis der Scores und
der Belegauswahl). Diese Urteile hängen nicht vom Modell des Verfahrens ab und bleiben darum für jeden Modellwechsel
derselbe feste Maßstab.

    einbettung    je Modell die höchste Nähe Passage ↔ Teilfähigkeit je Paar: Wie gut ordnet sie die Zweitbewertung
                  (Spearman) und trennt Stufe 6–10 von 1–5 (AUC)?
    schwellen     Schwellen eines neuen Einbettungsmodells: derselbe Anteil aller Paare Passage × Teilfähigkeit wie
                  bei bge-m3 liegt über jeder Schwelle (Quantil-Abgleich, ohne Blick auf die Zweitbewertung)
    sprachmodell  je Modell der Prüfschritt auf den Paaren von Lauf 1 mit Stufe 6–10 vor der Prüfung, neu gezählt und
                  gegen die Zweitbewertung verglichen (Anteil innerhalb ±2), und der Steckbrief auf N Websites
                  (Vorschläge, übernommen, verworfen nach Grund); Sekunden und Tokens je Sekunde
    vergleich     zwei Datenstände auf denselben Paaren gegen die Zweitbewertung (vorher / nachher)

    python -m trendradar.modellwahl einbettung --modelle BAAI/bge-m3,qwen3-embedding:8b --aus nachweis/<datum>/modellwahl_einbettung.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import bewertung, merkmale, pruefschritt, sprachmodell
from .bewertung import SCHWELLEN, bewerte, faehigkeit_text, passagen_auftraege, passagen_web, vektor_cache
from .einbettung import einbetter
from .taxonomie import STANDARD, lade

ALT = "nachweis/2026-11-04"


def jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def zweitbewertung(nachweis: Path) -> list[dict]:
    """Paare beider Läufe mit dem Urteil der Zweitbewertung (ohne „nicht bewertbar“)."""
    out = []
    for lauf in (1, 2):
        paare = {json.loads(l)["nr"]: json.loads(l) for l in open(nachweis / f"stichprobe_{lauf}.jsonl", encoding="utf-8")}
        for line in open(nachweis / f"zweitbewertung_{lauf}.jsonl", encoding="utf-8"):
            u = json.loads(line)
            if u.get("score") in (None, "n. b.", "nb"):
                continue
            p = paare[u["nr"]]
            out.append({"lauf": lauf, "unternehmen_id": p["unternehmen_id"], "subtrend_id": p["subtrend_id"],
                        "zweit": int(u["score"])})
    return out


def passagen_fuer(daten: Path, web: Path, ids: set[str] | None = None):
    belege = [b for b in (json.loads(l) for l in open(daten / "belege_auftraege.jsonl", encoding="utf-8"))
              if ids is None or b["unternehmen_id"] in ids]
    laeufe = [d for d in (json.loads(l) for l in open(web, encoding="utf-8")) if ids is None or d["unternehmen_id"] in ids]
    return passagen_auftraege(belege) + passagen_web(laeufe)


def raenge(x) -> np.ndarray:
    x = np.asarray(x, float)
    o = np.argsort(x, kind="mergesort")
    r = np.empty(len(x))
    r[o] = np.arange(1, len(x) + 1)
    for v in np.unique(x):
        m = x == v
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


def spearman(a, b) -> float:
    return float(np.corrcoef(raenge(a), raenge(b))[0, 1])


def auc(werte, positiv) -> float:
    werte, positiv = np.asarray(werte, float), np.asarray(positiv, bool)
    n1, n0 = positiv.sum(), (~positiv).sum()
    r = raenge(werte)
    return float((r[positiv].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


# ---------------------------------------------------------------- Einbettung

def einbettung_messen(daten: Path, web: Path, nachweis: Path, modelle: list[str], roh: Path) -> dict:
    tax = lade(STANDARD)
    paare = zweitbewertung(nachweis)
    ps = passagen_fuer(daten, web, {p["unternehmen_id"] for p in paare})
    je_firma = defaultdict(list)
    for i, p in enumerate(ps):
        je_firma[p.unternehmen_id].append(i)
    fl = tax.faehigkeiten()
    je_sub = defaultdict(list)
    for j, (s, _) in enumerate(fl):
        je_sub[s.id].append(j)
    zweit = np.array([p["zweit"] for p in paare])
    erg = {"zeitpunkt_utc": jetzt(), "paare": len(paare), "passagen": len(ps), "teilfaehigkeiten": len(fl),
           "maßstab": "blinde Zweitbewertung 04.11.2026 (Lauf 1 und 2)", "modelle": {}}
    for m in modelle:
        e = einbetter(m, vektor_cache(m, roh))
        t0 = time.perf_counter()
        F = e.kodiere([faehigkeit_text(s, f) for s, f in fl], art="anfrage")
        P = e.kodiere([p.text for p in ps])
        sek = time.perf_counter() - t0
        e.speichere()
        naehe = np.array([float((P[je_firma[p["unternehmen_id"]]] @ F[je_sub[p["subtrend_id"]]].T).max())
                          if je_firma[p["unternehmen_id"]] else -1.0 for p in paare])
        erg["modelle"][m] = {
            "dimensionen": int(P.shape[1]),
            "spearman": round(spearman(naehe, zweit), 3),
            "auc_stufe_6_10": round(auc(naehe, zweit >= 6), 3),
            "auc_stufe_8_10": round(auc(naehe, zweit >= 8), 3),
            "neu_berechnet": e.neu_berechnet, "sekunden": round(sek, 1),
            "passagen_je_sekunde": round(e.neu_berechnet / sek, 1) if e.neu_berechnet and sek else None,
        }
        print(m, json.dumps(erg["modelle"][m]), file=sys.stderr, flush=True)
    return erg


def schwellen_abgleichen(daten: Path, web: Path, von: str, nach: str, roh: Path) -> dict:
    tax = lade(STANDARD)
    ps = passagen_fuer(daten, web)
    fl = tax.faehigkeiten()
    S = {}
    for m in (von, nach):
        e = einbetter(m, vektor_cache(m, roh))
        F = e.kodiere([faehigkeit_text(s, f) for s, f in fl], art="anfrage")
        P = e.kodiere([p.text for p in ps])
        e.speichere()
        S[m] = (P @ F.T).ravel()
    b = SCHWELLEN[von]
    punkte = {"klar": b["klar"], "schwach": b["schwach"], "schwach_hoch": b["schwach"] + b["schwach_plus"],
              "indirekt": b["indirekt"], "indirekt_hoch": b["indirekt"] + b["indirekt_plus"]}
    anteile, neu = {}, {}
    for k, t in punkte.items():
        anteile[k] = float((S[von] >= t).mean())
        neu[k] = float(np.quantile(S[nach], 1 - anteile[k]))
    return {
        "zeitpunkt_utc": jetzt(), "von": von, "nach": nach, "passagen": len(ps), "teilfaehigkeiten": len(fl),
        "paare_passage_x_teilfaehigkeit": int(S[von].size),
        "anteil_ueber_schwelle": {k: round(v, 6) for k, v in anteile.items()},
        "schwellen_von": {k: round(v, 3) for k, v in punkte.items()},
        "schwellen_nach": {"klar": round(neu["klar"], 3), "schwach": round(neu["schwach"], 3),
                           "schwach_plus": round(neu["schwach_hoch"] - neu["schwach"], 3),
                           "indirekt": round(neu["indirekt"], 3),
                           "indirekt_plus": round(neu["indirekt_hoch"] - neu["indirekt"], 3)},
    }


# ---------------------------------------------------------------- Sprachmodell

def sprachmodell_messen(daten: Path, web: Path, nachweis: Path, roh: Path, modelle: list[str], websites: int,
                        parallel: int, aus: Path, paare_max: int = 0) -> dict:
    import httpx

    tax = lade(STANDARD)
    alt = {(r["unternehmen_id"], r["subtrend_id"]): r for r in csv.DictReader(open(daten / "bewertungen.csv", encoding="utf-8"))}
    kand = [p for p in zweitbewertung(nachweis) if p["lauf"] == 1
            and int(alt[(p["unternehmen_id"], p["subtrend_id"])]["score_vor_pruefung"] or 0) >= pruefschritt.AB_STUFE]
    if paare_max:
        kand = kand[:paare_max]
    ids = {p["unternehmen_id"] for p in kand}
    firmen = {r["id"]: r for r in csv.DictReader(open(daten / "unternehmen.csv", encoding="utf-8"))}
    # Belegstellen vor dem Prüfschritt: Bewertung vom 04.11. (bge-m3, Vektoren aus dem Zwischenspeicher)
    bewertung.setze_schwellen("BAAI/bge-m3")
    e = einbetter("BAAI/bge-m3", vektor_cache("BAAI/bge-m3", roh))
    vorher = bewerte(tax, [firmen[i] for i in sorted(ids)], passagen_fuer(daten, web, ids), e)
    bew = {(r["unternehmen_id"], r["subtrend_id"]): r for r in vorher["bewertungen"]}
    stellen = defaultdict(list)
    for b in vorher["belegstellen"]:
        stellen[(b["unternehmen_id"], b["subtrend_id"])].append(b)
    abweichend = sum(int(bew[k]["score"]) != int(alt[k]["score_vor_pruefung"]) for k in
                     ((p["unternehmen_id"], p["subtrend_id"]) for p in kand))
    auftraege = {b["id"]: b for b in (json.loads(l) for l in open(daten / "belege_auftraege.jsonl", encoding="utf-8"))}

    def auswerten(urteil: dict[str, bool]) -> dict:
        n = pm2 = gleich = bestaetigt = geprueft = ohne = 0
        diffs = []
        tafel = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}      # Nachweis ja/nein: Verfahren (≥ 6) gegen Zweitbewertung (≥ 6)
        for p in kand:
            k = (p["unternehmen_id"], p["subtrend_id"])
            paar = stellen[k]
            best = []
            for b in paar:
                if b["typ"] != "eindeutig":
                    continue
                u = urteil.get(pruefschritt.schluessel(b))
                geprueft += 1
                ohne += u is None
                if u:
                    best.append(b)
            bestaetigt += len(best)
            neu = int(pruefschritt.neu_zaehlen(bew[k], best, any(b["typ"] == "indirekt" for b in paar))["score"])
            d = neu - p["zweit"]
            diffs.append(d)
            n += 1
            pm2 += abs(d) <= 2
            gleich += d == 0
            tafel[("t" if (neu >= 6) == (p["zweit"] >= 6) else "f") + ("p" if neu >= 6 else "n")] += 1
        tp, fp, fn, tn = tafel["tp"], tafel["fp"], tafel["fn"], tafel["tn"]
        return {"paare": n, "innerhalb_2": pm2, "anteil_innerhalb_2": round(pm2 / n, 3) if n else None,
                "genau_gleich": gleich, "mittlere_abweichung": round(float(np.mean(diffs)), 2) if diffs else None,
                "nachweis_ja_nein": {**tafel,
                                     "trefferquote": round(tp / (tp + fn), 3) if tp + fn else None,
                                     "genauigkeit": round(tp / (tp + fp), 3) if tp + fp else None,
                                     "ausgewogen": round((tp / (tp + fn) + tn / (tn + fp)) / 2, 3) if tp + fn and tn + fp else None},
                "belegstellen": geprueft, "bestaetigt": bestaetigt, "ohne_urteil": ohne}

    ohne_pruefung = {pruefschritt.schluessel(b): True for v in stellen.values() for b in v}
    erg = {"zeitpunkt_utc": jetzt(), "maßstab": "blinde Zweitbewertung 04.11.2026, Lauf 1, Paare mit Stufe 6–10 vor der Prüfung",
           "paare": len(kand), "neu_bewertet_abweichend_vom_04_11": abweichend,
           "ohne_pruefschritt": auswerten(ohne_pruefung), "modelle": {}}
    cache_alt = {}
    for line in open(daten / "pruefung.jsonl", encoding="utf-8"):
        d = json.loads(line)
        if d.get("modell") == "qwen2.5:7b-instruct":
            cache_alt[d["schluessel"]] = d["bestaetigt"]
    if cache_alt:
        erg["modelle"]["qwen2.5:7b-instruct"] = {"pruefschritt": auswerten(cache_alt),
                                                 "hinweis": "Urteile aus dem Zwischenspeicher vom 04.11.2026 (Ollama 0.20.6)"}
    # Websites für den Steckbrief: gleichmäßig über die alphabetische Liste verteilt
    laeufe = sorted((d for d in (json.loads(l) for l in open(web, encoding="utf-8")) if d.get("status") == "ok"),
                    key=lambda d: d["domain"])
    schritt = max(1, len(laeufe) // websites) if websites else 0
    auswahl = laeufe[::schritt][:websites] if websites else []
    basis = sprachmodell.url()
    for m in modelle:
        r = {"digest": "", "pruefschritt": None, "steckbrief": None}
        with httpx.Client(timeout=900) as client:
            r["digest"] = sprachmodell.digest(client, m, basis)
            urteil, zeiten, tps, fehler = {}, [], [], 0

            def eins(p):
                k = (p["unternehmen_id"], p["subtrend_id"])
                offen = [b for b in stellen[k] if b["typ"] == "eindeutig"]
                if not offen:
                    return p, offen, {}, 0.0, {}
                u, sek, z = pruefschritt.frage(client, m, firmen[p["unternehmen_id"]]["name"],
                                                tax.subtrends[p["subtrend_id"]], offen, auftraege, basis)
                return p, offen, u, sek, z

            t0 = time.perf_counter()
            with ThreadPoolExecutor(parallel) as ex:
                futs = [ex.submit(eins, p) for p in kand]
                for i, f in enumerate(as_completed(futs), 1):
                    try:
                        p, offen, u, sek, z = f.result()
                    except Exception as ex_:                       # Modellfehler zählen, nicht abbrechen
                        fehler += 1
                        print(f"{m}: {type(ex_).__name__}: {ex_}", file=sys.stderr, flush=True)
                        continue
                    if offen:
                        zeiten.append(sek)
                        if z.get("eval_count") and z.get("eval_duration"):
                            tps.append(z["eval_count"] / (z["eval_duration"] / 1e9))
                    for j, b in enumerate(offen, 1):
                        uj = u.get(j)
                        urteil[pruefschritt.schluessel(b)] = bool(uj and uj.get("bestaetigt") is True)
                    if i % 10 == 0:
                        print(f"{m}: {i}/{len(kand)} Paare", file=sys.stderr, flush=True)
            wand = time.perf_counter() - t0
            zeiten.sort()
            r["pruefschritt"] = {**auswerten(urteil), "modellfehler": fehler,
                                 "sekunden_je_paar_median": round(zeiten[len(zeiten) // 2], 1) if zeiten else None,
                                 "sekunden_gesamt_wand": round(wand, 1), "parallel": parallel,
                                 "ausgabe_tokens_je_sekunde_median": round(sorted(tps)[len(tps) // 2], 1) if tps else None}
            ps_info = client.get(f"{basis}/api/ps").json().get("models", [])
            r["geladen"] = [{"name": x.get("name"), "groesse_gb": round(x.get("size", 0) / 1e9, 1),
                             "davon_gpu_gb": round(x.get("size_vram", 0) / 1e9, 1)} for x in ps_info]
            # Steckbrief
            vorg = beh = 0
            gruende: dict[str, int] = {}
            zs = []
            for d in auswahl:
                pss = merkmale.auswahl(d)
                if not pss:
                    continue
                try:
                    liste, sek = merkmale.frage(client, basis, m, pss)
                except Exception as ex_:
                    gruende["modellfehler"] = gruende.get("modellfehler", 0) + 1
                    print(f"{m} {d['domain']}: {type(ex_).__name__}", file=sys.stderr, flush=True)
                    continue
                zs.append(sek)
                gesehen = set()
                for x in liste:
                    vorg += 1
                    g = merkmale.pruefe(x, pss)
                    sch = (x.get("art"), merkmale._norm(str(x.get("wert", ""))).lower())
                    if g is None and sch in gesehen:
                        g = "doppelt"
                    if g:
                        gruende[g] = gruende.get(g, 0) + 1
                        continue
                    gesehen.add(sch)
                    beh += 1
                print(f"{m} {d['domain']}: {len(liste)} Vorschläge, {sek:.0f} s", file=sys.stderr, flush=True)
            zs.sort()
            r["steckbrief"] = {"websites": len(zs), "vorgeschlagen": vorg, "behalten": beh,
                               "anteil_verworfen": round((vorg - beh) / vorg, 3) if vorg else None,
                               "verworfen_nach_grund": gruende,
                               "sekunden_je_website_median": round(zs[len(zs) // 2], 1) if zs else None}
        erg["modelle"][m] = r
        print(m, json.dumps(r, ensure_ascii=False), file=sys.stderr, flush=True)
        aus.write_text(json.dumps(erg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")   # Zwischenstand
    erg["steckbrief_websites"] = [d["domain"] for d in auswahl]
    gespeichert = defaultdict(int)
    ids_web = {d["unternehmen_id"] for d in auswahl}
    for line in open(daten / "merkmale.jsonl", encoding="utf-8"):
        x = json.loads(line)
        if x["unternehmen_id"] in ids_web:
            gespeichert[x["unternehmen_id"]] += 1
    erg["steckbrief_qwen2.5_behalten_gespeichert"] = sum(gespeichert.values())
    return erg


# ---------------------------------------------------------------- Vergleich vorher / nachher

def vergleich(nachweis: Path, vorher: Path, nachher: Path) -> dict:
    paare = zweitbewertung(nachweis)
    st = {}
    for name, pfad in (("vorher", vorher), ("nachher", nachher)):
        st[name] = {(r["unternehmen_id"], r["subtrend_id"]): r for r in csv.DictReader(open(pfad, encoding="utf-8"))}

    def zaehle(auswahl, name):
        n = pm2 = gleich = nb = 0
        for p in auswahl:
            r = st[name].get((p["unternehmen_id"], p["subtrend_id"]))
            if not r or r["status"] != "bewertet":
                nb += 1
                continue
            d = abs(int(r["score"]) - p["zweit"])
            n += 1
            pm2 += d <= 2
            gleich += d == 0
        return {"paare": n, "innerhalb_2": pm2, "anteil_innerhalb_2": round(pm2 / n, 3) if n else None,
                "genau_gleich": gleich, "nicht_bewertbar": nb}

    def hoch(p, name):
        r = st[name].get((p["unternehmen_id"], p["subtrend_id"]))
        return bool(r and r["status"] == "bewertet" and int(r["score"]) >= 6)

    teil = {
        "alle": paare,
        "lauf_1": [p for p in paare if p["lauf"] == 1],
        "lauf_2": [p for p in paare if p["lauf"] == 2],
        "zweitbewertung_6_10": [p for p in paare if p["zweit"] >= 6],
        "verfahren_6_10_vorher": [p for p in paare if hoch(p, "vorher")],
        "verfahren_6_10_nachher": [p for p in paare if hoch(p, "nachher")],
    }
    return {"zeitpunkt_utc": jetzt(), "maßstab": "blinde Zweitbewertung 04.11.2026, Lauf 1 und 2 (Endstand je Datenstand)",
            "vorher": str(vorher), "nachher": str(nachher),
            "teilmengen": {k: {"vorher": zaehle(v, "vorher"), "nachher": zaehle(v, "nachher")} for k, v in teil.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="befehl", required=True)
    for name in ("einbettung", "schwellen", "sprachmodell", "vergleich"):
        s = sub.add_parser(name)
        s.add_argument("--daten", type=Path, default=Path("daten"))
        s.add_argument("--web", type=Path, default=Path("daten/web_lauf.jsonl"))
        s.add_argument("--nachweis", type=Path, default=Path(ALT))
        s.add_argument("--roh", type=Path, default=Path("roh"))
        s.add_argument("--aus", type=Path, required=True)
        if name in ("einbettung", "sprachmodell"):
            s.add_argument("--modelle", required=True, help="durch Komma getrennt")
        if name == "schwellen":
            s.add_argument("--von", default="BAAI/bge-m3")
            s.add_argument("--nach", required=True)
        if name == "sprachmodell":
            s.add_argument("--websites", type=int, default=10)
            s.add_argument("--parallel", type=int, default=2)
            s.add_argument("--paare-max", type=int, default=0)
        if name == "vergleich":
            s.add_argument("--vorher", type=Path, required=True)
            s.add_argument("--nachher", type=Path, default=Path("daten/bewertungen.csv"))
    a = ap.parse_args(argv)
    a.aus.parent.mkdir(parents=True, exist_ok=True)
    if a.befehl == "einbettung":
        erg = einbettung_messen(a.daten, a.web, a.nachweis, [m for m in a.modelle.split(",") if m], a.roh)
    elif a.befehl == "schwellen":
        erg = schwellen_abgleichen(a.daten, a.web, a.von, a.nach, a.roh)
    elif a.befehl == "sprachmodell":
        erg = sprachmodell_messen(a.daten, a.web, a.nachweis, a.roh, [m for m in a.modelle.split(",") if m], a.websites, a.parallel,
                                  a.aus, a.paare_max)
    else:
        erg = vergleich(a.nachweis, a.vorher, a.nachher)
    a.aus.write_text(json.dumps(erg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(erg, ensure_ascii=False)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
