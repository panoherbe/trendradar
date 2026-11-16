"""Datenbank: Schema anlegen, Datenstand laden (idempotent, in einer Transaktion), Verbindungen bereitstellen.

    python -m trendradar.db laden --daten daten
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from .taxonomie import STANDARD, Taxonomie, lade

SCHEMA_SQL = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
def _schema() -> str:
    return os.environ.get("DB_SCHEMA", "radar")


SCHEMA = _schema()

_pool = None


def dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://radar:radar@localhost:5432/radar")


def pool():
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(dsn(), min_size=2, max_size=int(os.environ.get("DB_POOL", "12")),
                               kwargs={"row_factory": dict_row, "options": f"-c search_path={_schema()},public"}, open=True)
    return _pool


@contextmanager
def verbindung():
    with pool().connection() as c:
        yield c


def lege_an(conn, schema: str = SCHEMA) -> None:
    conn.execute(SCHEMA_SQL.format(schema=schema))


def lade_taxonomie(conn, tax: Taxonomie) -> None:
    for i, m in enumerate(tax.megatrends.values()):
        conn.execute("INSERT INTO megatrend VALUES (%s,%s,%s,%s,%s)", (m.id, m.name, m.farbe, m.beschreibung, i))
    for s in tax.subtrends.values():
        conn.execute("INSERT INTO subtrend VALUES (%s,%s,%s,%s)", (s.id, s.name, s.beschreibung, s.aktiv))
        for j, f in enumerate(s.faehigkeiten):
            conn.execute("INSERT INTO faehigkeit VALUES (%s,%s,%s,%s,%s,%s,%s)",
                         (f.id, s.id, f.name, f.beschreibung, list(f.begriffe), list(f.cpv), j))
    for m in tax.megatrends.values():
        for i, sid in enumerate(m.subtrends):
            conn.execute("INSERT INTO linie VALUES (%s,%s,%s,%s)",
                         (m.id, sid, i, tax.subtrends[sid].megatrends[0] == m.id))


def berechne_kennzahlen(conn) -> None:
    conn.execute("DELETE FROM kennzahl")
    conn.execute("""
        INSERT INTO kennzahl
        SELECT b.subtrend_id, u.kreis_nuts,
               count(*) FILTER (WHERE b.status = 'bewertet'), count(*) FILTER (WHERE b.status = 'nicht_bewertbar'),
               count(*) FILTER (WHERE b.score >= 6), count(*) FILTER (WHERE b.score >= 8), coalesce(sum(b.score), 0)
          FROM bewertung b JOIN unternehmen u ON u.id = b.unternehmen_id
         GROUP BY 1, 2""")


def _int(x):
    return int(x) if x not in (None, "") else None


def _float(x):
    return float(x) if x not in (None, "") else None


def laden(daten: Path, taxonomie: Path = STANDARD, schema: str = SCHEMA) -> dict:
    """Ersetzt den Datenstand vollständig. Läuft in einer Transaktion: ein Fehler lässt den alten Stand stehen."""
    tax = lade(taxonomie)
    lauf = json.loads((daten / "lauf.json").read_text(encoding="utf-8"))
    if lauf.get("taxonomie") != tax.kennung:
        raise SystemExit(f"Datenstand passt nicht zur Taxonomie: {lauf.get('taxonomie')} ≠ {tax.kennung}. "
                         "Erst `python -m trendradar.bewertung` laufen lassen.")
    web = {}
    if (daten / "web_status.csv").exists():
        with open(daten / "web_status.csv", encoding="utf-8") as fh:
            web = {r["domain"]: r["status"] for r in csv.DictReader(fh)}
    with psycopg.connect(dsn(), autocommit=False) as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        lege_an(conn, schema)
        conn.execute(f"SET search_path TO {schema}, public")
        lade_taxonomie(conn, tax)
        with open(daten / "unternehmen.csv", encoding="utf-8") as fh, \
                conn.cursor().copy("COPY unternehmen (id,name,rechtsform,ort,plz,kreis_nuts,website,auftraege,web_status) FROM STDIN") as cp:
            for r in csv.DictReader(fh):
                cp.write_row((r["id"], r["name"], r["rechtsform"], r["ort"], r["plz"], r["kreis_nuts"],
                              r["website"] or None, int(r["auftraege"] or 0), web.get(r["website"])))
        with open(daten / "bewertungen.csv", encoding="utf-8") as fh, \
                conn.cursor().copy("COPY bewertung FROM STDIN") as cp:
            for r in csv.DictReader(fh):
                cp.write_row((r["unternehmen_id"], r["subtrend_id"], _int(r["score"]), r["status"], r["stufe"],
                              int(r["n_belege"]), int(r["k_faehigkeiten"]), _float(r["beste_naehe"])))
        with open(daten / "megatrends.csv", encoding="utf-8") as fh, \
                conn.cursor().copy("COPY megatrend_bewertung FROM STDIN") as cp:
            for r in csv.DictReader(fh):
                cp.write_row((r["unternehmen_id"], r["megatrend_id"], _int(r["max"]), _float(r["top3"]), r["status"],
                              int(r["n_subtrends"])))
        with open(daten / "belegstellen.jsonl", encoding="utf-8") as fh, conn.cursor().copy(
                "COPY belegstelle (unternehmen_id,subtrend_id,faehigkeit_id,typ,signal,treffer,naehe,beleg_id,quelle,url,datum,feld,zitat,geprueft,pruef_grund) FROM STDIN") as cp:
            for line in fh:
                b = json.loads(line)
                cp.write_row((b["unternehmen_id"], b["subtrend_id"], b["faehigkeit_id"], b["typ"], b["signal"],
                              b["treffer"], b["naehe"], b["beleg_id"], b["quelle"], b["url"], b["datum"], b["feld"],
                              b["zitat"], b.get("geprueft"), b.get("pruef_grund")))
        with open(daten / "belege_auftraege.jsonl", encoding="utf-8") as fh, conn.cursor().copy(
                "COPY auftrag FROM STDIN") as cp:
            for line in fh:
                b = json.loads(line)
                cp.write_row((b["id"], b["unternehmen_id"], b["url"], b["datum"], b["titel"], b["los"], b["cpv"],
                              b.get("vergabe_art")))
        if (daten / "merkmale.jsonl").exists():
            with open(daten / "merkmale.jsonl", encoding="utf-8") as fh, conn.cursor().copy(
                    "COPY merkmal (unternehmen_id,art,wert,zitat,url,datum) FROM STDIN") as cp:
                for line in fh:
                    m = json.loads(line)
                    cp.write_row((m["unternehmen_id"], m["art"], m["wert"], m["zitat"], m["url"], m["datum"]))
        if (daten / "cpv_de.csv").exists():
            with open(daten / "cpv_de.csv", encoding="utf-8") as fh, conn.cursor().copy("COPY cpv FROM STDIN") as cp:
                for r in csv.DictReader(fh):
                    cp.write_row((r["code"], r["bezeichnung"]))
        berechne_kennzahlen(conn)
        lauf["geladen_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("INSERT INTO stand VALUES ('lauf', %s)", (json.dumps(lauf),))
        ordner = sorted((p for p in (daten.parent / "nachweis").glob("*") if p.is_dir()), reverse=True)
        for name in ("crawl.json", "merkmale.json", "zitate.json", "lasttest.json", "pruefung.json",
                     "zweitbewertung_1_auswertung.json", "zweitbewertung_2_auswertung.json", "modellwechsel.json"):
            datei = next((o / name for o in ordner if (o / name).exists()), None)   # jüngster Nachweis je Datei
            if datei:
                conn.execute("INSERT INTO stand VALUES (%s, %s)",
                             (name.removesuffix(".json"), datei.read_text(encoding="utf-8")))
        conn.execute("ANALYZE")
        conn.commit()
        zahlen = {t: conn.execute(f"SELECT count(*) FROM {schema}.{t}").fetchone()[0]
                  for t in ("unternehmen", "bewertung", "belegstelle", "auftrag")}
    return zahlen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Datenstand in PostgreSQL laden")
    sub = ap.add_subparsers(dest="befehl", required=True)
    l = sub.add_parser("laden")
    l.add_argument("--daten", type=Path, default=Path("daten"))
    l.add_argument("--taxonomie", type=Path, default=STANDARD)
    k = sub.add_parser("kennzahlen", help="Kennzahlen eines Schemas neu berechnen")
    k.add_argument("--schema", default=SCHEMA)
    a = ap.parse_args(argv)
    if a.befehl == "laden":
        print(json.dumps(laden(a.daten, a.taxonomie), ensure_ascii=False))
    elif a.befehl == "kennzahlen":
        with psycopg.connect(dsn()) as conn:
            conn.execute(f"SET search_path TO {a.schema}, public")
            conn.execute(SCHEMA_SQL.format(schema=a.schema))
            berechne_kennzahlen(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
