"""Megatrend-Map als Liniengrafik: jeder Megatrend eine Linie, jeder Subtrend eine Station.

Ein Subtrend auf mehreren Linien ist eine Umsteigestation: seine Stationen stehen auf allen Linien in derselben
Spalte und sind verbunden. Die Anordnung wird aus der Taxonomie berechnet (deterministisch), damit ein neuer oder
umbenannter Trend keine Handarbeit an der Grafik braucht:

* Zeilenfolge der Linien so, dass Umsteigestationen möglichst benachbarte Linien verbinden;
* Spalten je Subtrend so, dass auf keiner Linie zwei Stationen dieselbe Spalte haben und keine Verbindung durch
  eine fremde Station läuft; möglichst wenige Spalten.
"""

from __future__ import annotations

import html
import json
import random
from pathlib import Path

from .taxonomie import Taxonomie

SPALTE = 132
ZEILE = 66
LINKS = 240
OBEN = 34


def _spalten(tax: Taxonomie, reihen: dict[str, int]) -> dict[str, int] | None:
    subs = [s for s in tax.subtrends.values() if s.aktiv]
    belegt: dict[tuple[int, int], str] = {}                     # (zeile, spalte) -> Subtrend
    durchgang: dict[tuple[int, int], str] = {}                  # Verbindung läuft durch (zeile, spalte)
    spalte = {}
    reihenfolge = sorted(subs, key=lambda s: (-len(s.megatrends),
                                              -(max(reihen[m] for m in s.megatrends) - min(reihen[m] for m in s.megatrends)),
                                              s.id))
    for s in reihenfolge:
        zs = sorted(reihen[m] for m in s.megatrends)
        dazwischen = [z for z in range(zs[0], zs[-1] + 1) if z not in zs]
        c = 0
        while True:
            if all((z, c) not in belegt and (z, c) not in durchgang for z in zs) and \
                    all((z, c) not in belegt for z in dazwischen):
                break
            c += 1
            if c > 40:
                return None
        spalte[s.id] = c
        for z in zs:
            belegt[(z, c)] = s.id
        for z in dazwischen:
            durchgang[(z, c)] = s.id
    return spalte


def layout(tax: Taxonomie, versuche: int = 2500, seed: int = 7) -> dict:
    ms = list(tax.megatrends)
    rnd = random.Random(seed)
    geteilt = [s for s in tax.subtrends.values() if s.aktiv and len(s.megatrends) > 1]

    def kosten(reihen):
        span = sum(max(reihen[m] for m in s.megatrends) - min(reihen[m] for m in s.megatrends) - (len(s.megatrends) - 1)
                   for s in geteilt)
        sp = _spalten(tax, reihen)
        if sp is None:
            return 10**9, None
        return span * 20 + (max(sp.values()) + 1) * 6, sp

    bester = None
    reihen0 = {m: i for i, m in enumerate(ms)}
    for v in range(versuche):
        if v == 0:
            folge = ms[:]
        else:
            folge = ms[:]
            rnd.shuffle(folge)
        reihen = {m: i for i, m in enumerate(folge)}
        k, sp = kosten(reihen)
        # lokale Verbesserung durch Vertauschen benachbarter Zeilen
        verbessert = True
        while verbessert:
            verbessert = False
            for i in range(len(folge) - 1):
                f2 = folge[:]
                f2[i], f2[i + 1] = f2[i + 1], f2[i]
                r2 = {m: j for j, m in enumerate(f2)}
                k2, sp2 = kosten(r2)
                if k2 < k:
                    folge, reihen, k, sp, verbessert = f2, r2, k2, sp2, True
        if bester is None or k < bester[0]:
            bester = (k, dict(reihen), sp)
    _, reihen, sp = bester
    # Spalten je Linie verdichten: Lücken am Rand vermeiden ist Sache der Zeichnung; hier nur die Zuordnung.
    return {"reihen": reihen or reihen0, "spalten": sp, "n_spalten": max(sp.values()) + 1}


def layout_gespeichert(tax: Taxonomie, pfad: Path) -> dict:
    """Layout aus ``karte.json`` neben der Taxonomie, wenn es zum Inhalts-Hash passt; sonst neu berechnen."""
    try:
        d = json.loads(pfad.read_text(encoding="utf-8"))
        if d.get("taxonomie_hash") == tax.hash:
            return {"reihen": d["reihen"], "spalten": d["spalten"], "n_spalten": d["n_spalten"]}
    except (OSError, ValueError, KeyError):
        pass
    lay = layout(tax)
    try:
        pfad.write_text(json.dumps({"taxonomie_hash": tax.hash, **lay}, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return lay


def _umbruch(text: str, breite: int = 19) -> list[str]:
    worte, zeilen, z = text.split(), [], ""
    for w in worte:
        if len(z) + len(w) + 1 > breite and z:
            zeilen.append(z)
            z = w
        else:
            z = f"{z} {w}".strip()
    if z:
        zeilen.append(z)
    if len(zeilen) > 2:
        zeilen = [zeilen[0], " ".join(zeilen[1:])[: breite - 1] + "…"]
    return zeilen


def svg(tax: Taxonomie, lay: dict, kennzahlen: dict[str, dict] | None = None, link: str = "/subtrend/{}",
        megatrend_link: str = "/megatrend/{}") -> str:
    kennzahlen = kennzahlen or {}
    reihen, spalten = lay["reihen"], lay["spalten"]
    breite = LINKS + lay["n_spalten"] * SPALTE + 20
    hoehe = OBEN + len(reihen) * ZEILE + 30
    maxn = max([k.get("n_beleg", 0) for k in kennzahlen.values()] + [1])
    x = lambda c: LINKS + c * SPALTE + SPALTE // 2
    y = lambda r: OBEN + r * ZEILE
    e = html.escape
    teile = [f'<svg class="karte" viewBox="0 0 {breite} {hoehe}" xmlns="http://www.w3.org/2000/svg" role="img" '
             f'aria-label="Megatrend-Map: {len(reihen)} Megatrends als Linien, Subtrends als Stationen">']
    # Umsteigeverbindungen zuerst: die Linien laufen darüber, die Stationen liegen obenauf
    for s in tax.subtrends.values():
        if s.id not in spalten or len(s.megatrends) < 2:
            continue
        zs = sorted(reihen[mm] for mm in s.megatrends)
        teile.append(f'<line x1="{x(spalten[s.id])}" y1="{y(zs[0])}" x2="{x(spalten[s.id])}" y2="{y(zs[-1])}" '
                     f'class="umstieg"/>')
    # Linien
    for mid, r in sorted(reihen.items(), key=lambda kv: kv[1]):
        m = tax.megatrends[mid]
        cs = sorted(spalten[s] for s in m.subtrends if s in spalten)
        if not cs:
            continue
        teile.append(f'<a href="{e(megatrend_link.format(mid))}" class="ml"><text x="{LINKS - 14}" y="{y(r) + 5}" '
                     f'text-anchor="end" class="mname" fill="{e(m.farbe)}">{e(m.name)}</text></a>')
        teile.append(f'<line x1="{x(cs[0]) - 26}" y1="{y(r)}" x2="{x(cs[-1]) + 26}" y2="{y(r)}" stroke="{e(m.farbe)}" '
                     f'stroke-width="8" stroke-linecap="round"/>')
    # Stationen
    for s in tax.subtrends.values():
        if s.id not in spalten:
            continue
        c = spalten[s.id]
        zs = sorted(reihen[mm] for mm in s.megatrends)
        k = kennzahlen.get(s.id, {})
        n = k.get("n_beleg", 0)
        staerke = n / maxn
        titel = f"{s.name}: {n} Unternehmen mit eindeutigem Beleg"
        teile.append(f'<a href="{e(link.format(s.id))}" class="station" data-subtrend="{e(s.id)}"><title>{e(titel)}</title>')
        for z in zs:
            farbe = tax.megatrends[next(mm for mm in s.megatrends if reihen[mm] == z)].farbe
            teile.append(f'<circle cx="{x(c)}" cy="{y(z)}" r="7.5" fill="#fff" stroke="{e(farbe)}" stroke-width="3"/>')
            if n:
                teile.append(f'<circle cx="{x(c)}" cy="{y(z)}" r="{2.2 + 3.3 * staerke:.1f}" fill="{e(farbe)}"/>')
        ty = y(zs[-1]) + 21
        for i, zeile in enumerate(_umbruch(s.name)):
            teile.append(f'<text x="{x(c)}" y="{ty + i * 12}" text-anchor="middle" class="sname">{e(zeile)}</text>')
        teile.append(f'<text x="{x(c)}" y="{ty + len(_umbruch(s.name)) * 12}" text-anchor="middle" class="szahl">'
                     f'{n if n else "–"}</text>')
        teile.append("</a>")
    teile.append("</svg>")
    return "".join(teile)
