from protego import Protego

from trendradar.bereinigung import bereinige
from trendradar.quellen.auftraege import aus_release, gewinner_lose, rechtsform, schluessel
from trendradar.quellen.web import ROBOTS_NAME, gleiche_domain, links_waehlen, passagen, tdm_vorbehalt


def test_nur_unternehmen_mit_rechtsform():
    assert rechtsform("Metallbau Reiher GmbH & Co.KG") == "GmbH & Co. KG"
    assert rechtsform("3D-Micromac AG") == "AG"
    assert rechtsform('"Glückauf" eG') == "eG"
    for person in ("Erika Musterfrau", "Beispiel Metall", "Malerbetrieb Max Mustermann e.K.", "Müller & Schmidt GbR",
                   "Förderverein e.V."):
        assert rechtsform(person) is None, person


def test_schluessel_ohne_fuzzy():
    assert schluessel("BplusL Infra Log GmbH") == schluessel("B plus L Infra log GmbH")
    assert schluessel("Wackler Service Group GmbH & Co. KG") == schluessel("Wackler Service Group GmbH und Co. KG")
    assert schluessel("Bauer GmbH") != schluessel("Baur GmbH")


EFORMS = b"""<ContractAwardNotice xmlns="urn:x" xmlns:efac="urn:efac" xmlns:ns9="urn:efac" xmlns:cbc="urn:cbc">
<efac:NoticeResult>
  <efac:LotResult><cbc:ID>RES-1</cbc:ID><cbc:TenderResultCode>selec-w</cbc:TenderResultCode>
    <efac:LotTender><cbc:ID>TEN-1</cbc:ID></efac:LotTender><efac:TenderLot><cbc:ID>LOT-1</cbc:ID></efac:TenderLot></efac:LotResult>
  <efac:LotResult><cbc:ID>RES-2</cbc:ID><cbc:TenderResultCode>open-nw</cbc:TenderResultCode>
    <efac:LotTender><cbc:ID>TEN-2</cbc:ID></efac:LotTender><efac:TenderLot><cbc:ID>LOT-2</cbc:ID></efac:TenderLot></efac:LotResult>
  <ns9:LotTender><cbc:ID>TEN-1</cbc:ID><efac:TenderingParty><cbc:ID>TPA-1</cbc:ID></efac:TenderingParty><efac:TenderLot><cbc:ID>LOT-1</cbc:ID></efac:TenderLot></ns9:LotTender>
  <efac:LotTender><cbc:ID>TEN-2</cbc:ID><efac:TenderingParty><cbc:ID>TPA-2</cbc:ID></efac:TenderingParty><efac:TenderLot><cbc:ID>LOT-2</cbc:ID></efac:TenderLot></efac:LotTender>
  <efac:TenderingParty><cbc:ID>TPA-1</cbc:ID><efac:Tenderer><cbc:ID>ORG-3</cbc:ID></efac:Tenderer></efac:TenderingParty>
  <efac:TenderingParty><cbc:ID>TPA-2</cbc:ID><efac:Tenderer><cbc:ID>ORG-4</cbc:ID></efac:Tenderer></efac:TenderingParty>
</efac:NoticeResult></ContractAwardNotice>"""


def test_eforms_gewinner_je_los_unabhaengig_vom_praefix():
    assert gewinner_lose(EFORMS) == {"ORG-3": {"LOT-1"}}


def _release(lieferanten):
    parties = [{"id": "ORG-1", "roles": ["buyer"], "address": {"region": "DE300"}}]
    for i, (name, region) in enumerate(lieferanten, start=3):
        parties.append({"id": f"ORG-{i}", "name": name, "roles": ["tenderer", "supplier"],
                        "address": {"region": region, "locality": "Chemnitz", "postalCode": "09111"},
                        "contactPoint": {"email": "a@example.org", "telephone": "0371 1"}, "identifier": {"id": "DE123"}})
    return {"id": "aaaa-01", "date": "2025-01-01", "parties": parties,
            "tender": {"title": "Schule", "lots": [{"id": "LOT-1", "title": "Los 1 Elektro", "description": "Elektroinstallation"},
                                                    {"id": "LOT-2", "title": "Los 2 Dach", "description": "Dachdecker"}],
                       "items": [{"relatedLot": "LOT-1", "classification": {"scheme": "CPV", "id": "45310000"}}]},
            "awards": [{"id": "LOT-1", "status": "active", "relatedLots": ["LOT-1"]},
                       {"id": "LOT-2", "status": "active", "relatedLots": ["LOT-2"]}]}


def test_zuordnung_nur_wenn_eindeutig():
    rel = _release([("Elektro Chemnitz GmbH", "DED41"), ("Dach GmbH", "DED45")])
    assert aus_release(rel, None) == []                              # zwei Auftragnehmer, keine Zuordnung: nichts
    paare = aus_release(rel, {"ORG-3": {"LOT-1"}, "ORG-4": {"LOT-2"}})
    assert {(f["name"], b["los"]) for f, b in paare} == {("Elektro Chemnitz GmbH", "Los 1 Elektro"), ("Dach GmbH", "Los 2 Dach")}
    f, b = paare[0]
    assert "email" not in str(f) and "DE123" not in str(f) and "telephone" not in str(f)
    assert b["cpv"] in (["45310000"], [])


def test_nur_auftragnehmer_aus_der_region():
    assert aus_release(_release([("Fremd GmbH", "DE300")]), None) == []


def test_bereinigung_entfernt_kontakt_laesst_fachliches():
    t = bereinige("Tel.: 0371 / 123 45-6, Fax 0371-99887, Mail: info@firma.example.org, +49 371 1234567. "
                  "CPV 45431000, PLZ 09111, DIN EN 14411, 1.475 m2 am 14.02.2025")
    assert "123 45" not in t and "99887" not in t and "@" not in t and "1234567" not in t
    for bleibt in ("45431000", "09111", "14411", "1.475 m2", "14.02.2025"):
        assert bleibt in t


def test_robots_wird_beachtet():
    r = Protego.parse("User-agent: *\nDisallow: /intern/\nCrawl-delay: 4\n\nUser-agent: TrendRadar-Prototyp\nDisallow: /privat/")
    assert not r.can_fetch("https://example.org/privat/a", ROBOTS_NAME)
    assert r.can_fetch("https://example.org/intern/a", ROBOTS_NAME)       # eigene Gruppe gilt (RFC 9309)
    assert r.can_fetch("https://example.org/leistungen", ROBOTS_NAME)


def test_tdm_vorbehalt():
    assert tdm_vorbehalt({"tdm-reservation": "1"}, "")
    assert tdm_vorbehalt({}, '<html><head><meta name="tdm-reservation" content="1"></head>')
    assert not tdm_vorbehalt({}, "<html></html>")


def test_seitenauswahl_meidet_impressum_und_kundenseiten():
    html = """<a href="/leistungen/">Leistungen</a><a href="/impressum">Impressum</a><a href="/referenzen/x">Referenzen</a>
              <a href="/produkte/drehteile">Drehteile</a><a href="https://example.net/x">fremd</a><a href="mailto:a@example.org">m</a>
              <a href="/datenschutz">Datenschutz</a><a href="/karriere">Jobs</a><a href="/">Start</a>"""
    urls = links_waehlen("https://example.com/", html, "example.com", 10)
    assert urls == ["https://example.com/leistungen/", "https://example.com/produkte/drehteile"]


def test_domainvergleich():
    assert gleiche_domain("www.example.com", "example.com") and gleiche_domain("shop.example.com", "example.com")
    assert not gleiche_domain("bauvorhaben-butler.de", "abn-maler-service.de")


def test_passagen_sind_woertlich():
    text = "Wir fertigen Präzisionsdrehteile aus Edelstahl. Zertifiziert nach ISO 13485 für die Medizintechnik.\n\nKurz."
    ps = passagen(text)
    assert ps and all(p in " ".join(text.split()) for p in ps)


def test_bereinigung_tel_nr_schraegstrich_und_anrede():
    t = bereinige("Objektbesichtigung mit Frau Erika Beispiel (Objektverwalter) Tel.-Nr. 0341/ 99998888 E-Mail: "
                  "a.beispiel@example.org; Rückfragen an Herrn Dr. Max Muster, Durchwahl 0371 123-45. Frauenklinik, "
                  "Herr der Lage, DN 15/80, 2026/2027, Los 3/12")
    for weg in ("Erika", "Beispiel", "99998888", "example.org", "Max Muster", "123-45"):
        assert weg not in t, weg
    for bleibt in ("Frauenklinik", "Herr der Lage", "DN 15/80", "2026/2027", "Los 3/12", "(Objektverwalter)"):
        assert bleibt in t, bleibt


def test_ausgelieferte_daten_ohne_kontaktdaten():
    """Die Dateien im Repository enthalten keine E-Mail-Adressen und keine Telefonnummern."""
    import json
    import re
    from pathlib import Path

    daten = Path("daten")
    if not (daten / "belege_auftraege.jsonl").exists():
        import pytest
        pytest.skip("kein Datenstand im Arbeitsverzeichnis")
    mail = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)
    for name in ("belege_auftraege.jsonl", "belegstellen.jsonl", "merkmale.jsonl"):
        for i, line in enumerate(open(daten / name, encoding="utf-8"), 1):
            d = json.loads(line)
            text = " ".join(str(d.get(k, "")) for k in ("titel", "los", "text", "zitat", "wert"))
            text = re.sub(r"\w+@FAU\b", "", text)          # Namen wie „NHR@FAU“ sind keine Adressen
            assert not mail.search(text), (name, i, mail.search(text).group(0))
            assert bereinige(text) == text, (name, i)
