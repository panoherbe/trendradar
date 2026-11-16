"""Deutsche Bezeichnungen des CPV-Vokabulars (Common Procurement Vocabulary 2008).

Quelle: SPARQL-Endpunkt des Amts für Veröffentlichungen der EU (EU Vocabularies, Schema
http://data.europa.eu/cpv/cpv), Weiterverwendung nach Beschluss 2011/833/EU mit Quellenangabe.
Gespeichert werden nur die Codes, die in den Belegen vorkommen.

    python -m trendradar.quellen.cpv --belege daten/belege_auftraege.jsonl --aus daten/cpv_de.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ENDPUNKT = "https://publications.europa.eu/webapi/rdf/sparql"
ABFRAGE = """PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?code ?label_de WHERE {
  ?c skos:inScheme <http://data.europa.eu/cpv/cpv> ; skos:notation ?code ; skos:prefLabel ?label_de .
  FILTER(lang(?label_de) = "de")
} ORDER BY ?code"""


def hole() -> dict[str, str]:
    url = f"{ENDPUNKT}?{urllib.parse.urlencode({'query': ABFRAGE, 'format': 'text/csv'})}"
    req = urllib.request.Request(url, headers={"User-Agent": "TrendRadar-Prototyp/0.1 (+https://panoherbe.com)"})
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8")
    return {row["code"][:8]: row["label_de"] for row in csv.DictReader(io.StringIO(text))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--belege", type=Path, required=True)
    ap.add_argument("--aus", type=Path, required=True)
    a = ap.parse_args(argv)
    alle = hole()
    genutzt = sorted({c for line in open(a.belege, encoding="utf-8") for c in json.loads(line)["cpv"]})
    with open(a.aus, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["code", "bezeichnung"])
        for c in genutzt:
            if c in alle:
                w.writerow([c, alle[c]])
    print(json.dumps({"vokabular": len(alle), "genutzt": len(genutzt),
                      "ohne_bezeichnung": sum(c not in alle for c in genutzt)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
