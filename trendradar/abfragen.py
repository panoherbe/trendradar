"""Abfragen für Oberfläche und API. Filter, Sortierung und Seiten laufen in der Datenbank, nicht im Browser."""

from __future__ import annotations

import json

from .kooperation import Kandidat, finde_gruppen

KREISE = {
    "DED41": "Chemnitz",
    "DED42": "Erzgebirgskreis",
    "DED43": "Mittelsachsen",
    "DED44": "Vogtlandkreis",
    "DED45": "Zwickau",
}
SEITENGROESSEN = (50, 200)
BELEG_AB = 6            # ab Stufe 6 liegt mindestens ein eindeutiger Beleg vor


def stand(c) -> dict:
    out = {r["schluessel"]: r["wert"] for r in c.execute("SELECT schluessel, wert FROM stand")}
    return out


def megatrend_uebersicht(c) -> dict[str, dict]:
    """Je Megatrend: Unternehmen mit Nachweis (höchster Subtrend-Wert ≥ 6) und mit starkem Nachweis (≥ 8)."""
    rows = c.execute(f"""
        SELECT megatrend_id, count(*) FILTER (WHERE max >= {BELEG_AB}) AS n_beleg, count(*) FILTER (WHERE max >= 8) AS n_stark
          FROM megatrend_bewertung GROUP BY 1""").fetchall()
    return {r["megatrend_id"]: r for r in rows}


def top_namen(c, sids: list[str], n: int = 2) -> dict[str, list[str]]:
    """Je Subtrend die Namen der n Unternehmen mit den stärksten Nachweisen (für Kacheln)."""
    out = {}
    for sid in sids:
        out[sid] = [r["name"] for r in c.execute(f"""
            SELECT u.name FROM bewertung b JOIN unternehmen u ON u.id = b.unternehmen_id
             WHERE b.subtrend_id = %s AND b.score >= {BELEG_AB}
             ORDER BY b.score DESC, b.n_belege DESC, b.beste_naehe DESC NULLS LAST, u.name LIMIT %s""", (sid, n))]
    return out


def megatrends(c) -> list[dict]:
    ms = c.execute("SELECT id, name, farbe, beschreibung FROM megatrend ORDER BY position").fetchall()
    linien = c.execute("""
        SELECT l.megatrend_id, l.subtrend_id, l.position, l.primaer, s.name
          FROM linie l JOIN subtrend s ON s.id = l.subtrend_id WHERE s.aktiv ORDER BY l.megatrend_id, l.position""").fetchall()
    for m in ms:
        m["subtrends"] = [dict(subtrend_id=l["subtrend_id"], name=l["name"], primaer=l["primaer"])
                          for l in linien if l["megatrend_id"] == m["id"]]
    return ms


def subtrend_kennzahlen(c, kreis: str | None = None, sid: str | None = None) -> dict[str, dict]:
    """Je Subtrend: n bewertet, n nicht bewertbar, n mit Beleg (≥ 6), n stark (≥ 8), Durchschnitt mit n.

    Liest die beim Laden berechnete Tabelle ``kennzahl`` (je Subtrend und Kreis), nicht die Bewertungen selbst."""
    rows = c.execute("""
        SELECT subtrend_id, sum(n_bewertet)::int AS n_bewertet, sum(n_nicht_bewertbar)::int AS n_nicht_bewertbar,
               sum(n_beleg)::int AS n_beleg, sum(n_stark)::int AS n_stark, sum(summe_score)::bigint AS summe
          FROM kennzahl
         WHERE (%(kreis)s::text IS NULL OR kreis_nuts = %(kreis)s) AND (%(sid)s::text IS NULL OR subtrend_id = %(sid)s)
         GROUP BY subtrend_id""", {"kreis": kreis, "sid": sid}).fetchall()
    out = {}
    for r in rows:
        n = r["n_bewertet"] + r["n_nicht_bewertbar"]
        r["quote_nicht_bewertbar"] = round(r["n_nicht_bewertbar"] / n, 3) if n else None
        r["mittel"] = round(r.pop("summe") / r["n_bewertet"], 2) if r["n_bewertet"] else None
        out[r["subtrend_id"]] = r
    return out


def subtrend(c, sid: str) -> dict | None:
    s = c.execute("SELECT id, name, beschreibung, aktiv FROM subtrend WHERE id = %s", (sid,)).fetchone()
    if not s:
        return None
    s["megatrends"] = c.execute("""
        SELECT m.id, m.name, m.farbe, l.primaer FROM linie l JOIN megatrend m ON m.id = l.megatrend_id
         WHERE l.subtrend_id = %s ORDER BY NOT l.primaer, m.position""", (sid,)).fetchall()
    s["faehigkeiten"] = c.execute("""
        SELECT f.id, f.name, f.beschreibung, f.begriffe, f.cpv,
               (SELECT count(DISTINCT unternehmen_id) FROM belegstelle b
                 WHERE b.subtrend_id = f.subtrend_id AND b.faehigkeit_id = f.id AND b.typ = 'eindeutig') AS n_unternehmen
          FROM faehigkeit f WHERE f.subtrend_id = %s ORDER BY f.position""", (sid,)).fetchall()
    s["kennzahlen"] = subtrend_kennzahlen(c, sid=sid).get(sid, {})
    s["je_kreis"] = {k: subtrend_kennzahlen(c, k, sid).get(sid, {}) for k in KREISE}
    return s


def rangliste(c, sid: str, kreis: str | None = None, min_score: int | None = None, status: str | None = None,
              seite: int = 1, pro_seite: int = 50) -> dict:
    p = {"sid": sid, "kreis": kreis, "min": min_score, "status": status,
         "lim": pro_seite, "off": (seite - 1) * pro_seite}
    wo = """b.subtrend_id = %(sid)s
            AND (%(kreis)s::text IS NULL OR u.kreis_nuts = %(kreis)s)
            AND (%(min)s::int IS NULL OR b.score >= %(min)s)
            AND (%(status)s::text IS NULL OR b.status = %(status)s)"""
    gesamt = c.execute(f"SELECT count(*) AS n FROM bewertung b JOIN unternehmen u ON u.id = b.unternehmen_id WHERE {wo}",
                       p).fetchone()["n"]
    # erst die Seite bestimmen, dann nur für ihre Einträge den stärksten Beleg holen
    zeilen = c.execute(f"""
        WITH seite AS (
            SELECT u.id, u.name, u.ort, u.kreis_nuts, b.subtrend_id, b.score, b.status, b.stufe, b.n_belege,
                   b.k_faehigkeiten, b.beste_naehe
              FROM bewertung b JOIN unternehmen u ON u.id = b.unternehmen_id
             WHERE {wo}
             ORDER BY b.score DESC NULLS LAST, b.n_belege DESC, b.beste_naehe DESC NULLS LAST, u.name
             LIMIT %(lim)s OFFSET %(off)s)
        SELECT s.id, s.name, s.ort, s.kreis_nuts, s.score, s.status, s.stufe, s.n_belege, s.k_faehigkeiten,
               bs.zitat, bs.url, bs.quelle, bs.typ, bs.treffer, bs.signal, bs.datum, bs.geprueft, bs.pruef_grund, f.name AS faehigkeit
          FROM seite s
          LEFT JOIN LATERAL (
               SELECT * FROM belegstelle x WHERE x.unternehmen_id = s.id AND x.subtrend_id = s.subtrend_id
                ORDER BY x.typ = 'eindeutig' DESC, x.signal = 'begriff' DESC, x.naehe DESC LIMIT 1) bs ON true
          LEFT JOIN faehigkeit f ON f.id = bs.faehigkeit_id
         ORDER BY s.score DESC NULLS LAST, s.n_belege DESC, s.beste_naehe DESC NULLS LAST, s.name""", p).fetchall()
    return {"gesamt": gesamt, "seite": seite, "pro_seite": pro_seite, "eintraege": zeilen}


def megatrend_rangliste(c, mid: str, regel: str = "max", kreis: str | None = None, seite: int = 1,
                        pro_seite: int = 50) -> dict:
    spalte = "top3" if regel == "top3" else "max"
    p = {"mid": mid, "kreis": kreis, "lim": pro_seite, "off": (seite - 1) * pro_seite}
    wo = "mb.megatrend_id = %(mid)s AND mb.status = 'bewertet' AND (%(kreis)s::text IS NULL OR u.kreis_nuts = %(kreis)s)"
    gesamt = c.execute(f"SELECT count(*) AS n FROM megatrend_bewertung mb JOIN unternehmen u ON u.id = mb.unternehmen_id WHERE {wo}", p).fetchone()["n"]
    zeilen = c.execute(f"""
        SELECT u.id, u.name, u.ort, u.kreis_nuts, mb.max, mb.top3, mb.n_subtrends,
               (SELECT s.name FROM bewertung b JOIN linie l ON l.subtrend_id = b.subtrend_id AND l.megatrend_id = mb.megatrend_id
                  JOIN subtrend s ON s.id = b.subtrend_id
                 WHERE b.unternehmen_id = u.id ORDER BY b.score DESC NULLS LAST, b.n_belege DESC LIMIT 1) AS staerkster_subtrend
          FROM megatrend_bewertung mb JOIN unternehmen u ON u.id = mb.unternehmen_id
         WHERE {wo}
         ORDER BY mb.{spalte} DESC NULLS LAST, mb.max DESC, u.name LIMIT %(lim)s OFFSET %(off)s""", p).fetchall()
    return {"gesamt": gesamt, "regel": spalte, "seite": seite, "pro_seite": pro_seite, "eintraege": zeilen}


def suche(c, q: str | None = None, kreis: str | None = None, subtrend: str | None = None, min_score: int | None = None,
          status: str | None = None, seite: int = 1, pro_seite: int = 50) -> dict:
    p = {"q": f"%{q.lower()}%" if q else None, "kreis": kreis, "sid": subtrend, "min": min_score, "status": status,
         "lim": pro_seite, "off": (seite - 1) * pro_seite}
    join = "LEFT JOIN bewertung b ON b.unternehmen_id = u.id AND b.subtrend_id = %(sid)s" if subtrend else \
        "LEFT JOIN LATERAL (SELECT NULL::smallint AS score, NULL::text AS status) b ON true"
    wo = """(%(q)s::text IS NULL OR lower(u.name) LIKE %(q)s OR lower(u.ort) LIKE %(q)s)
            AND (%(kreis)s::text IS NULL OR u.kreis_nuts = %(kreis)s)
            AND (%(min)s::int IS NULL OR b.score >= %(min)s)
            AND (%(status)s::text IS NULL OR b.status = %(status)s)"""
    gesamt = c.execute(f"SELECT count(*) AS n FROM unternehmen u {join} WHERE {wo}", p).fetchone()["n"]
    zeilen = c.execute(f"""
        SELECT u.id, u.name, u.ort, u.kreis_nuts, u.auftraege, u.website, b.score, b.status,
               (SELECT count(*) FROM bewertung x WHERE x.unternehmen_id = u.id AND x.score >= {BELEG_AB}) AS n_subtrends_beleg
          FROM unternehmen u {join} WHERE {wo}
         ORDER BY b.score DESC NULLS LAST, n_subtrends_beleg DESC, u.name LIMIT %(lim)s OFFSET %(off)s""", p).fetchall()
    return {"gesamt": gesamt, "seite": seite, "pro_seite": pro_seite, "eintraege": zeilen}


def profil(c, uid: str) -> dict | None:
    u = c.execute("SELECT * FROM unternehmen WHERE id = %s", (uid,)).fetchone()
    if not u:
        return None
    u["kreis"] = KREISE.get(u["kreis_nuts"], u["kreis_nuts"])
    u["megatrends"] = c.execute("""
        SELECT m.id, m.name, m.farbe, mb.max, mb.top3, mb.status, mb.n_subtrends
          FROM megatrend m LEFT JOIN megatrend_bewertung mb ON mb.megatrend_id = m.id AND mb.unternehmen_id = %s
         ORDER BY m.position""", (uid,)).fetchall()
    u["subtrends"] = c.execute("""
        SELECT s.id, s.name, b.score, b.status, b.stufe, b.n_belege, b.k_faehigkeiten,
               (SELECT array_agg(m.farbe ORDER BY NOT l.primaer, m.position) FROM linie l JOIN megatrend m ON m.id = l.megatrend_id
                 WHERE l.subtrend_id = s.id) AS farben
          FROM bewertung b JOIN subtrend s ON s.id = b.subtrend_id
         WHERE b.unternehmen_id = %s ORDER BY b.score DESC NULLS LAST, b.n_belege DESC, s.id""", (uid,)).fetchall()
    belege = c.execute("""
        SELECT b.*, f.name AS faehigkeit FROM belegstelle b JOIN faehigkeit f ON f.id = b.faehigkeit_id
         WHERE b.unternehmen_id = %s ORDER BY b.subtrend_id, b.typ = 'eindeutig' DESC, b.signal = 'begriff' DESC, b.naehe DESC""", (uid,)).fetchall()
    je = {}
    for b in belege:
        je.setdefault(b["subtrend_id"], []).append(b)
    for s in u["subtrends"]:
        s["belege"] = je.get(s["id"], [])
    u["auftrag_liste"] = c.execute("""
        SELECT a.*, (SELECT array_agg(x.code || ' ' || coalesce(k.bezeichnung, '') ORDER BY x.ord)
                       FROM unnest(a.cpv) WITH ORDINALITY x(code, ord) LEFT JOIN cpv k ON k.code = x.code) AS cpv_text
          FROM auftrag a WHERE a.unternehmen_id = %s ORDER BY a.datum DESC""", (uid,)).fetchall()
    u["merkmale"] = c.execute("""SELECT art, wert, zitat, url, datum FROM merkmal WHERE unternehmen_id = %s
                                  ORDER BY array_position(ARRAY['Tätigkeit','Produkt','Verfahren','Werkstoff','Technologie',
                                  'Zertifizierung','Zielbranche'], art), id""", (uid,)).fetchall()
    u["web_seiten"] = sorted({(b["url"], b["datum"]) for b in belege if b["quelle"] == "web"})
    return u


def passende_trends(c, uid: str, n: int = 5) -> list[dict]:
    """Beispielabfrage der API: die stärksten Subtrends eines Unternehmens, jeweils mit Quellen."""
    rows = c.execute("""
        SELECT s.id, s.name, b.score, b.stufe, b.n_belege FROM bewertung b JOIN subtrend s ON s.id = b.subtrend_id
         WHERE b.unternehmen_id = %s AND b.score >= %s ORDER BY b.score DESC, b.n_belege DESC LIMIT %s""",
                     (uid, BELEG_AB, n)).fetchall()
    for r in rows:
        r["quellen"] = c.execute("""
            SELECT DISTINCT ON (url) url, quelle, datum, zitat FROM belegstelle
             WHERE unternehmen_id = %s AND subtrend_id = %s AND typ = 'eindeutig' ORDER BY url, naehe DESC""",
                                 (uid, r["id"])).fetchall()
    return rows


def kooperation(c, sid: str, kreis: str | None = None, max_gruppen: int = 5) -> dict:
    fs = c.execute("SELECT id, name FROM faehigkeit WHERE subtrend_id = %s ORDER BY position", (sid,)).fetchall()
    rows = c.execute("""
        SELECT b.unternehmen_id, b.faehigkeit_id, count(DISTINCT b.beleg_id) AS belege, max(b.naehe) AS naehe,
               u.kreis_nuts, u.name
          FROM belegstelle b JOIN unternehmen u ON u.id = b.unternehmen_id
         WHERE b.subtrend_id = %(sid)s AND b.typ = 'eindeutig' AND (%(kreis)s::text IS NULL OR u.kreis_nuts = %(kreis)s)
         GROUP BY 1, 2, 5, 6""", {"sid": sid, "kreis": kreis}).fetchall()
    kand = [Kandidat(r["unternehmen_id"], r["faehigkeit_id"], r["belege"], float(r["naehe"] or 0), r["kreis_nuts"])
            for r in rows]
    gruppen, luecken = finde_gruppen([f["id"] for f in fs], kand, max_gruppen=max_gruppen)
    namen = {r["unternehmen_id"]: r for r in rows}
    out = []
    for g in gruppen:
        schritte = []
        for f in fs:
            uid = g.zuordnung.get(f["id"])
            beleg = None
            if uid:
                beleg = c.execute("""SELECT zitat, url, quelle, datum, treffer FROM belegstelle
                                      WHERE unternehmen_id = %s AND faehigkeit_id = %s AND typ = 'eindeutig'
                                      ORDER BY signal = 'begriff' DESC, naehe DESC LIMIT 1""", (uid, f["id"])).fetchone()
            schritte.append({"faehigkeit_id": f["id"], "faehigkeit": f["name"], "unternehmen_id": uid,
                             "unternehmen": namen[uid]["name"] if uid else None,
                             "kreis": KREISE.get(namen[uid]["kreis_nuts"]) if uid else None, "beleg": beleg})
        out.append({"mitglieder": [{"id": m, "name": namen[m]["name"], "kreis": KREISE.get(namen[m]["kreis_nuts"])}
                                   for m in g.mitglieder],
                    "schritte": schritte, "luecken": g.luecken, "belege": g.staerke})
    anbieter = {f["id"]: len({r["unternehmen_id"] for r in rows if r["faehigkeit_id"] == f["id"]}) for f in fs}
    return {"faehigkeiten": [dict(f, n_unternehmen=anbieter[f["id"]]) for f in fs], "gruppen": out,
            "luecken_region": luecken}


def region(c) -> dict:
    rows = c.execute("""
        SELECT mb.megatrend_id, u.kreis_nuts,
               count(*) AS n, count(*) FILTER (WHERE mb.status = 'nicht_bewertbar') AS n_nicht_bewertbar,
               count(*) FILTER (WHERE mb.max >= %s) AS n_beleg, round(avg(mb.max)::numeric, 2) AS mittel
          FROM megatrend_bewertung mb JOIN unternehmen u ON u.id = mb.unternehmen_id
         GROUP BY 1, 2""", (BELEG_AB,)).fetchall()
    zellen = {}
    for r in rows:
        r["mittel"] = float(r["mittel"]) if r["mittel"] is not None else None
        r["quote_nicht_bewertbar"] = round(r["n_nicht_bewertbar"] / r["n"], 3) if r["n"] else None
        zellen[(r["megatrend_id"], r["kreis_nuts"])] = r
    firmen = {r["kreis_nuts"]: r["n"] for r in c.execute("SELECT kreis_nuts, count(*) AS n FROM unternehmen GROUP BY 1")}
    max_anteil = max((z["n_beleg"] / z["n"] for z in zellen.values() if z["n"]), default=1) or 1
    return {"zellen": zellen, "firmen_je_kreis": firmen, "max_anteil": max_anteil}


def json_default(o):
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=json_default)
