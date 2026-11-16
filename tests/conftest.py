"""Gemeinsame Testdaten: eine kleine Taxonomie und erfundene Unternehmen, ohne Modell und ohne Netz."""

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("DB_SCHEMA", "test_radar")
os.environ.setdefault("API_PROTOKOLL", "1")

MINI_TAXONOMIE = """
version: "test-1"
titel: "Testtaxonomie"
megatrends:
  - id: M01
    name: "Klima & Energie"
    farbe: "#1b7f5b"
    subtrends: [S001, S002]
  - id: M02
    name: "Mobilitätswandel"
    farbe: "#3d5a99"
    subtrends: [S002, S003]
subtrends:
  - id: S001
    name: "Photovoltaik & Speicher"
    megatrends: [M01]
    faehigkeiten:
      - {id: S001-F1, name: "Solarmontage", beschreibung: "Lieferung und Montage von Photovoltaikanlagen auf Dächern", begriffe: ["photovoltaik", "pv-anlage"], cpv: ["09331"]}
      - {id: S001-F2, name: "Batteriespeicher", beschreibung: "Batteriespeicher und Wechselrichter für Solarstrom", begriffe: ["batteriespeicher", "wechselrichter"], cpv: []}
  - id: S002
    name: "Ladeinfrastruktur"
    megatrends: [M01, M02]
    faehigkeiten:
      - {id: S002-F1, name: "Ladesäulen", beschreibung: "Errichtung von Ladesäulen für Elektrofahrzeuge", begriffe: ["ladesäule", "ladeinfrastruktur", "wallbox"], cpv: ["3168"]}
      - {id: S002-F2, name: "Netzanschluss", beschreibung: "Netzanschluss und Mittelspannung für Ladeparks", begriffe: ["mittelspannung", "netzanschluss"], cpv: []}
      - {id: S002-F3, name: "Lastmanagement", beschreibung: "Software für Lastmanagement und Abrechnung von Ladevorgängen", begriffe: ["lastmanagement"], cpv: []}
  - id: S003
    name: "Fahrzeugleichtbau"
    megatrends: [M02]
    faehigkeiten:
      - {id: S003-F1, name: "Faserverbund", beschreibung: "Bauteile aus Faserverbund und Carbon für Fahrzeuge", begriffe: ["faserverbund", "carbon"], cpv: []}
"""

FIRMEN = [
    {"id": "u1", "name": "Sonnendach GmbH", "rechtsform": "GmbH", "ort": "Chemnitz", "plz": "09111", "kreis_nuts": "DED41", "website": "", "auftraege": "2"},
    {"id": "u2", "name": "Netzbau Erzgebirge GmbH & Co. KG", "rechtsform": "GmbH & Co. KG", "ort": "Annaberg-Buchholz", "plz": "09456", "kreis_nuts": "DED42", "website": "", "auftraege": "1"},
    {"id": "u3", "name": "Ladetechnik Zwickau AG", "rechtsform": "AG", "ort": "Zwickau", "plz": "08056", "kreis_nuts": "DED45", "website": "", "auftraege": "1"},
    {"id": "u4", "name": "Kurztext GmbH", "rechtsform": "GmbH", "ort": "Plauen", "plz": "08523", "kreis_nuts": "DED44", "website": "", "auftraege": "1"},
]

BELEGE = [
    {"id": "b1", "unternehmen_id": "u1", "quelle": "auftrag", "url": "https://example.org/1", "datum": "2025-03-01",
     "titel": "Neubau Grundschule, Los 7 Photovoltaik", "los": "Photovoltaikanlage 99 kWp mit Batteriespeicher",
     "text": "Lieferung und Montage einer Photovoltaikanlage auf dem Dach der Grundschule einschließlich Wechselrichter und Batteriespeicher. Ansprechpartner Tel. 0371 123456, info@example.org",
     "cpv": ["09331200"], "cpv_text": [], "vergabe_art": "works"},
    {"id": "b2", "unternehmen_id": "u1", "quelle": "auftrag", "url": "https://example.org/2", "datum": "2024-06-01",
     "titel": "Rathaus, PV-Anlage Erweiterung", "los": "", "text": "Erweiterung der bestehenden PV-Anlage um Module und Montagesystem auf dem Rathausdach in Chemnitz.",
     "cpv": ["09331200"], "cpv_text": [], "vergabe_art": "works"},
    {"id": "b3", "unternehmen_id": "u2", "quelle": "auftrag", "url": "https://example.org/3", "datum": "2025-01-10",
     "titel": "Ladepark Betriebshof, Netzanschluss", "los": "Mittelspannung und Netzanschluss", "text": "Herstellung des Netzanschlusses mit Mittelspannungsstation für den Ladepark des Betriebshofs mit zwölf Ladesäulen.",
     "cpv": ["45315000"], "cpv_text": [], "vergabe_art": "works"},
    {"id": "b4", "unternehmen_id": "u3", "quelle": "auftrag", "url": "https://example.org/4", "datum": "2025-05-05",
     "titel": "Ladeinfrastruktur Verwaltung", "los": "Ladesäulen und Lastmanagement", "text": "Lieferung von zwölf Ladesäulen mit Lastmanagement-Software und Abrechnung der Ladevorgänge für Dienstfahrzeuge.",
     "cpv": ["31681500"], "cpv_text": [], "vergabe_art": "goods"},
    {"id": "b5", "unternehmen_id": "u4", "quelle": "auftrag", "url": "https://example.org/5", "datum": "2025-05-05",
     "titel": "Los 2", "los": "", "text": "", "cpv": ["45000000"], "cpv_text": [], "vergabe_art": "works"},
]


@pytest.fixture
def mini_taxonomie(tmp_path) -> Path:
    p = tmp_path / "trends.yaml"
    p.write_text(MINI_TAXONOMIE, encoding="utf-8")
    return p


@pytest.fixture
def mini_daten(tmp_path, mini_taxonomie):
    """Bewertet die Testfirmen mit dem Hash-Einbetter und schreibt einen vollständigen Datenstand."""
    import csv

    from trendradar import bewertung

    d = tmp_path / "daten"
    d.mkdir()
    with open(d / "unternehmen.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, list(FIRMEN[0]))
        w.writeheader()
        w.writerows(FIRMEN)
    with open(d / "belege_auftraege.jsonl", "w", encoding="utf-8") as fh:
        for b in BELEGE:
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")
    alt = (bewertung.SIM_KLAR, bewertung.SIM_INDIREKT, bewertung.SIM_SCHWACH)
    bewertung.SIM_KLAR, bewertung.SIM_INDIREKT, bewertung.SIM_SCHWACH = 0.05, 0.9, 0.04
    try:
        bewertung.main(["--daten", str(d), "--taxonomie", str(mini_taxonomie), "--test-einbetter"])
    finally:
        bewertung.SIM_KLAR, bewertung.SIM_INDIREKT, bewertung.SIM_SCHWACH = alt
    return d
