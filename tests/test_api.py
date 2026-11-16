"""API und Oberfläche gegen PostgreSQL. Übersprungen, wenn keine Datenbank erreichbar ist."""

import os

import pytest

psycopg = pytest.importorskip("psycopg")


def _db_da() -> bool:
    try:
        with psycopg.connect(os.environ.get("DATABASE_URL", "postgresql://radar:radar@localhost:5432/radar"), connect_timeout=3):
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_da(), reason="keine PostgreSQL-Datenbank erreichbar")


@pytest.fixture
def client(mini_daten, mini_taxonomie, monkeypatch):
    from fastapi.testclient import TestClient

    from trendradar import app as appmod, db, taxonomie

    db.laden(mini_daten, mini_taxonomie, schema="test_radar")
    monkeypatch.setattr(taxonomie, "STANDARD", mini_taxonomie)
    monkeypatch.setattr(appmod, "STANDARD", mini_taxonomie)
    appmod._fenster.clear()
    return TestClient(appmod.app)


def test_megatrends_und_mehrfachzugehoerigkeit(client):
    r = client.get("/api/v1/megatrends")
    assert r.status_code == 200
    linien = {m["id"]: [s["subtrend_id"] for s in m["subtrends"]] for m in r.json()}
    assert "S002" in linien["M01"] and "S002" in linien["M02"]


def test_rangliste_mit_beleg_und_seiten(client):
    r = client.get("/api/v1/subtrends/S001/unternehmen", params={"pro_seite": 50})
    d = r.json()
    assert d["gesamt"] == 4 and d["eintraege"][0]["id"] == "u1"
    assert d["eintraege"][0]["zitat"] and d["eintraege"][0]["url"].startswith("https://")
    assert d["eintraege"][-1]["status"] == "nicht_bewertbar"
    assert client.get("/api/v1/subtrends/S001/unternehmen", params={"status": "nicht_bewertbar"}).json()["gesamt"] == 1


def test_fehler_als_json_mit_korrelations_id(client):
    r = client.get("/api/v1/subtrends/S001/unternehmen", params={"pro_seite": 30})
    assert r.status_code == 422 and r.json()["fehler"]["korrelations_id"] == r.headers["X-Korrelations-ID"]
    r = client.get("/api/v1/unternehmen/gibtsnicht")
    assert r.status_code == 404 and r.json()["fehler"]["code"] == 404


def test_rate_limit_429_mit_retry_after(client, monkeypatch):
    from trendradar import app as appmod

    monkeypatch.setattr(appmod, "RATE", 3)
    codes = [client.get("/api/v1/stand", headers={"X-API-Key": "test-limit"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = client.get("/api/v1/stand", headers={"X-API-Key": "test-limit"})
    assert int(r.headers["Retry-After"]) >= 1


def test_protokoll_ohne_inhaltsdaten(client):
    client.get("/api/v1/unternehmen", params={"suche": "Sonnendach"})
    from trendradar.db import verbindung

    with verbindung() as c:
        pfade = [r["pfad"] for r in c.execute("SELECT pfad FROM api_protokoll ORDER BY zeit DESC LIMIT 5")]
    assert "/api/v1/unternehmen" in pfade and not any("Sonnendach" in p for p in pfade)


def test_passende_trends_mit_quellen(client):
    d = client.get("/api/v1/unternehmen/u1/passende-trends").json()
    assert d[0]["id"] == "S001" and d[0]["quellen"] and d[0]["quellen"][0]["url"].startswith("https://")


def test_kooperation_und_export(client):
    k = client.get("/api/v1/kooperation/S002").json()
    assert k["gruppen"] and {s["unternehmen_id"] for s in k["gruppen"][0]["schritte"] if s["unternehmen_id"]} <= {"u2", "u3"}
    csv = client.get("/api/v1/subtrends/S001/export", params={"format": "csv"})
    assert csv.status_code == 200 and "Sonnendach" in csv.content.decode("utf-8-sig")
    xlsx = client.get("/api/v1/subtrends/S001/export", params={"format": "xlsx"})
    assert xlsx.content[:2] == b"PK"


def test_oberflaeche(client):
    for pfad in ("/", "/karte", "/subtrend/S002", "/subtrend/S002?alle=1", "/unternehmen/u1", "/unternehmen?q=zwickau",
                 "/kooperation/S002", "/kooperation/S002?kreis=DED41", "/megatrend/M01?regel=top3", "/region", "/methodik",
                 "/api/v1/openapi.json", "/static/schrift/inter-latin-wght-normal.woff2"):
        r = client.get(pfad)
        assert r.status_code == 200, pfad
    assert "nicht bewertbar" in client.get("/unternehmen/u4").text
    # so sendet das Filterformular leere Felder
    r = client.get("/subtrend/S001", params={"kreis": "DED41", "min_score": "", "status": ""})
    assert r.status_code == 200 and "Sonnendach GmbH" in r.text
    assert client.get("/unternehmen", params={"q": "", "kreis": "", "subtrend": "", "min_score": ""}).status_code == 200
    assert client.get("/subtrend/S001", params={"min_score": "11"}).status_code == 422


def test_themenseite_zeigt_ohne_angabe_nur_nachweise(client):
    """Ohne Filter zeigt die Themenseite nur Unternehmen mit Nachweis (Stufe 6–10); „alle=1“ zeigt auch Hinweise."""
    import re

    eintraege = client.get("/api/v1/subtrends/S001/unternehmen", params={"pro_seite": 200}).json()["eintraege"]
    mit = [e["name"] for e in eintraege if (e["score"] or 0) >= 6]
    ohne = [e["name"] for e in eintraege if e["status"] == "bewertet" and e["score"] < 6]
    seite = client.get("/subtrend/S001").text
    gezeigt = re.findall(r'<h3><a href="/unternehmen/[^"]+">([^<]+)</a></h3>', seite)
    assert set(gezeigt) == set(mit)
    if ohne:
        alle = client.get("/subtrend/S001", params={"alle": 1}).text
        assert any(n in alle for n in ohne)
