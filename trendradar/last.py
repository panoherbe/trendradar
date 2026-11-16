"""Lasttest: 100.000 synthetische Unternehmen × 100 Themen (Subtrends), 10 gleichzeitige Nutzer.

Alles Synthetische liegt im eigenen Schema ``lasttest`` und mischt sich nie mit dem echten Datenstand.

    python -m trendradar.last matrix   --unternehmen 100000 --subtrends 100     # Rechenzeit der Ähnlichkeitsmatrix
    python -m trendradar.last erzeugen --unternehmen 100000 --subtrends 100     # Schema lasttest füllen
    python -m trendradar.last messen   --url http://127.0.0.1:8000 --nutzer 10 --dauer 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

KREISE = ["DED41", "DED42", "DED43", "DED44", "DED45"]
SCHEMA = "lasttest"


def matrix(n_firmen: int, n_sub: int, dim: int = 1024, teil: int = 4) -> dict:
    """Kosinus aller Unternehmens- gegen alle Teilfähigkeitsvektoren (normierte Zufallsvektoren, float32)."""
    import numpy as np

    rng = np.random.default_rng(1)
    F = rng.standard_normal((n_firmen, dim), dtype=np.float32)
    F /= np.linalg.norm(F, axis=1, keepdims=True)
    T = rng.standard_normal((n_sub * teil, dim), dtype=np.float32)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    t0 = time.perf_counter()
    for i in range(0, n_firmen, 10000):
        S = F[i:i + 10000] @ T.T
        S.reshape(len(S), n_sub, teil).max(axis=2)
    dauer = time.perf_counter() - t0
    return {"unternehmen": n_firmen, "subtrends": n_sub, "teilfaehigkeiten": n_sub * teil, "dimension": dim,
            "werte": n_firmen * n_sub, "sekunden": round(dauer, 2), "geraet": "CPU (NumPy, float32)"}


def erzeugen(n_firmen: int, n_sub: int, seed: int = 1) -> dict:
    import psycopg

    from .db import dsn, lege_an

    rnd = random.Random(seed)
    t0 = time.perf_counter()
    with psycopg.connect(dsn(), autocommit=False) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        lege_an(c, SCHEMA)
        c.execute(f"SET search_path TO {SCHEMA}, public")
        mids = [f"M{i:02d}" for i in range(1, 12)]
        for i, m in enumerate(mids):
            c.execute("INSERT INTO megatrend VALUES (%s,%s,%s,'',%s)", (m, f"Megatrend {i + 1}", "#0b5866", i))
        sids = [f"S{i:03d}" for i in range(1, n_sub + 1)]
        for j, s in enumerate(sids):
            c.execute("INSERT INTO subtrend VALUES (%s,%s,'',true)", (s, f"Subtrend {j + 1}"))
            c.execute("INSERT INTO linie VALUES (%s,%s,%s,true)", (mids[j % 11], s, j // 11))
            for k in range(4):
                c.execute("INSERT INTO faehigkeit VALUES (%s,%s,%s,'',%s,%s,%s)",
                          (f"{s}-F{k + 1}", s, f"Teilfähigkeit {k + 1}", ["x"], [], k))
        firmen = [(f"t{i:06d}", f"Testfirma {i:05d} GmbH", rnd.choice(KREISE)) for i in range(n_firmen)]
        with c.cursor().copy("COPY unternehmen (id,name,rechtsform,ort,plz,kreis_nuts,auftraege) FROM STDIN") as cp:
            for uid, name, kreis in firmen:
                cp.write_row((uid, name, "GmbH", "Testort", "09000", kreis, 1))
        t1 = time.perf_counter()
        n_bew = n_bel = 0
        megas, belege = [], []
        with c.cursor().copy("COPY bewertung FROM STDIN") as cp:
            for uid, _, _ in firmen:
                nb = rnd.random() < 0.03
                je_m = {m: [] for m in mids}
                for j, s in enumerate(sids):
                    if nb:
                        cp.write_row((uid, s, None, "nicht_bewertbar", "nicht bewertbar", 0, 0, None))
                    else:
                        r = rnd.random()
                        sc = rnd.randint(1, 3) if r < .7 else rnd.randint(4, 5) if r < .87 else rnd.randint(6, 7) if r < .96 else rnd.randint(8, 10)
                        nbel = 0 if sc < 6 else 1 if sc < 8 else 2
                        cp.write_row((uid, s, sc, "bewertet", "synthetisch", nbel, min(nbel, 2), 0.5))
                        je_m[mids[j % 11]].append(sc)
                        for b in range(nbel):
                            belege.append((uid, s, f"{s}-F{b + 1}", f"{uid}-{s}-{b}"))
                    n_bew += 1
                for m in mids:
                    w = sorted(je_m[m], reverse=True)
                    megas.append((uid, m, w[0] if w else None, round(sum(w[:3]) / len(w[:3]), 2) if w else None,
                                  "bewertet" if w else "nicht_bewertbar", len(w)))
        # eine Verbindung kann nur einen COPY zugleich führen: Belegstellen nach den Bewertungen
        with c.cursor().copy("COPY belegstelle (unternehmen_id,subtrend_id,faehigkeit_id,typ,signal,treffer,naehe,beleg_id,quelle,url,datum,feld,zitat) FROM STDIN") as cb:
            for uid, s, f, bid in belege:
                cb.write_row((uid, s, f, "eindeutig", "begriff", "x", 0.6, bid, "auftrag", "https://example.org/synthetisch",
                              "2025-01-01", "los", "Synthetischer Beleg für den Lasttest"))
        n_bel = len(belege)
        with c.cursor().copy("COPY megatrend_bewertung FROM STDIN") as cp:
            for row in megas:
                cp.write_row(row)
        lauf = {"synthetisch": True, "unternehmen": n_firmen, "subtrends": n_sub, "paare": n_bew,
                "zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        c.execute("INSERT INTO stand VALUES ('lauf', %s)", (json.dumps(lauf),))
        from .db import berechne_kennzahlen

        berechne_kennzahlen(c)
        c.commit()
        c.execute("ANALYZE")
        t2 = time.perf_counter()
        groesse = c.execute(f"SELECT pg_size_pretty(sum(pg_total_relation_size(quote_ident(schemaname)||'.'||quote_ident(tablename)))::bigint) "
                            f"FROM pg_tables WHERE schemaname = '{SCHEMA}'").fetchone()[0]
    return {"unternehmen": n_firmen, "subtrends": n_sub, "bewertungen": n_bew, "belegstellen": n_bel,
            "sekunden_laden": round(t2 - t0, 1), "sekunden_bewertungen": round(t2 - t1, 1), "groesse": groesse}


async def messen(url: str, nutzer: int, dauer: int, n_firmen: int, n_sub: int, seed: int = 2) -> dict:
    import httpx

    rnd = random.Random(seed)
    arten = {
        "Rangliste Subtrend (Filter Kreis, Score ≥ 6)": lambda: f"/api/v1/subtrends/S{rnd.randint(1, n_sub):03d}/unternehmen?kreis={rnd.choice(KREISE)}&min_score=6",
        "Rangliste Subtrend, Seite 20 zu 200": lambda: f"/api/v1/subtrends/S{rnd.randint(1, n_sub):03d}/unternehmen?pro_seite=200&seite=20",
        "Rangliste Megatrend (Mittel Top 3)": lambda: f"/api/v1/megatrends/M{rnd.randint(1, 11):02d}/unternehmen?regel=top3",
        "Unternehmensprofil (alle Themen mit Belegen)": lambda: f"/api/v1/unternehmen/t{rnd.randint(0, n_firmen - 1):06d}",
        "Namenssuche": lambda: f"/api/v1/unternehmen?suche=firma%20{rnd.randint(0, 9999):04d}",
        "Subtrend-Seite (HTML)": lambda: f"/subtrend/S{rnd.randint(1, n_sub):03d}",
    }
    zeiten: dict[str, list[float]] = {k: [] for k in arten}
    fehler: dict[str, int] = {}
    ende = time.monotonic() + dauer

    async def nutzer_lauf(i: int):
        async with httpx.AsyncClient(base_url=url, timeout=30, headers={"X-API-Key": f"last-{i}"}) as cl:
            while time.monotonic() < ende:
                art = rnd.choice(list(arten))
                t0 = time.perf_counter()
                try:
                    r = await cl.get(arten[art]())
                    if r.status_code != 200:
                        fehler[str(r.status_code)] = fehler.get(str(r.status_code), 0) + 1
                        continue
                except httpx.HTTPError as ex:
                    fehler[type(ex).__name__] = fehler.get(type(ex).__name__, 0) + 1
                    continue
                zeiten[art].append((time.perf_counter() - t0) * 1000)

    t0 = time.monotonic()
    await asyncio.gather(*(nutzer_lauf(i) for i in range(nutzer)))
    gesamt = time.monotonic() - t0

    def p(xs, q):
        return round(statistics.quantiles(xs, n=100)[q - 1], 1) if len(xs) >= 2 else (round(xs[0], 1) if xs else None)

    alle = [x for v in zeiten.values() for x in v]
    return {"nutzer": nutzer, "dauer_s": round(gesamt, 1), "anfragen": len(alle), "je_sekunde": round(len(alle) / gesamt, 1),
            "fehler": fehler, "ms_median": p(alle, 50), "ms_p95": p(alle, 95), "ms_max": round(max(alle), 1) if alle else None,
            "je_art": {k: {"n": len(v), "ms_median": p(v, 50), "ms_p95": p(v, 95)} for k, v in zeiten.items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="befehl", required=True)
    for name in ("matrix", "erzeugen", "messen"):
        s = sub.add_parser(name)
        s.add_argument("--unternehmen", type=int, default=100000)
        s.add_argument("--subtrends", type=int, default=100)
        if name == "messen":
            s.add_argument("--url", default="http://127.0.0.1:8000")
            s.add_argument("--nutzer", type=int, default=10)
            s.add_argument("--dauer", type=int, default=60)
        s.add_argument("--aus", type=Path, default=None)
    a = ap.parse_args(argv)
    if a.befehl == "matrix":
        erg = matrix(a.unternehmen, a.subtrends)
    elif a.befehl == "erzeugen":
        erg = erzeugen(a.unternehmen, a.subtrends)
    else:
        erg = asyncio.run(messen(a.url, a.nutzer, a.dauer, a.unternehmen, a.subtrends))
    erg["zeitpunkt_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = json.dumps(erg, ensure_ascii=False, indent=2)
    if a.aus:
        a.aus.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
