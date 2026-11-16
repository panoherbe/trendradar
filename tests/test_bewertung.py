import csv
import json

from trendradar import bewertung
from trendradar.bewertung import begriff_regex, stufe, zitat_geprueft


def test_rubrik_stufen():
    assert stufe(0, 0, 0, False, 0.10) == 1
    assert stufe(0, 0, 0, False, bewertung.SIM_SCHWACH) == 2
    assert stufe(0, 0, 0, True, bewertung.SIM_INDIREKT) == 4
    assert stufe(1, 0, 1, False, 0.7) == 6
    assert stufe(1, 0, 2, False, 0.7) == 7
    assert stufe(1, 1, 1, False, 0.7) == 7
    assert stufe(2, 0, 1, False, 0.7) == 8
    assert stufe(3, 0, 1, False, 0.7) == 9
    assert stufe(2, 0, 2, False, 0.7) == 9
    assert stufe(4, 0, 2, False, 0.7) == 10
    assert stufe(2, 2, 2, False, 0.7) == 10


def test_cpv_code_allein_hoechstens_sieben():
    assert stufe(0, 1, 1, False, 0.7) == 6
    assert stufe(0, 12, 3, True, 0.9) == 7


def test_begriffe_kompositum_und_wortanfang():
    rx = begriff_regex(("photovoltaik", "wallbox", "carbon"))
    assert rx.search("dachphotovoltaikanlage")          # langer Begriff auch im Kompositum
    assert rx.search("eine wallbox je stellplatz")
    assert rx.search("carbonfaser")
    assert not rx.search("hydrocarbonat")                # kurzer Begriff nur am Wortanfang


def test_datenstand_vollstaendig_und_nachvollziehbar(mini_daten):
    lauf = json.loads((mini_daten / "lauf.json").read_text())
    assert lauf["taxonomie"].startswith("test-1+")
    with open(mini_daten / "bewertungen.csv") as fh:
        bew = list(csv.DictReader(fh))
    assert len(bew) == 4 * 3                              # jedes Unternehmen × jeder Subtrend
    je = {(b["unternehmen_id"], b["subtrend_id"]): b for b in bew}
    # zu wenig Text: eigener Status, nie eine 1
    for s in ("S001", "S002", "S003"):
        assert je[("u4", s)]["status"] == "nicht_bewertbar" and je[("u4", s)]["score"] == ""
    assert int(je[("u1", "S001")]["score"]) >= 8          # zwei Aufträge mit Photovoltaik
    assert int(je[("u3", "S002")]["score"]) >= 7          # Ladesäulen und Lastmanagement
    assert int(je[("u1", "S003")]["score"]) < 6           # kein Leichtbau-Beleg


def test_jedes_zitat_steht_woertlich_in_der_quelle(mini_daten):
    belege = {json.loads(l)["id"]: json.loads(l) for l in open(mini_daten / "belege_auftraege.jsonl")}
    stellen = [json.loads(l) for l in open(mini_daten / "belegstellen.jsonl")]
    assert stellen
    for s in stellen:
        b = belege[s["beleg_id"]]
        quelle = " ".join([b["titel"], b["los"], b["text"]])
        assert zitat_geprueft(s["zitat"], quelle), s["zitat"]
        assert "@" not in s["zitat"] and "123456" not in s["zitat"]   # Kontaktdaten entfernt


def test_megatrend_beide_regeln(mini_daten):
    with open(mini_daten / "megatrends.csv") as fh:
        mt = {(r["unternehmen_id"], r["megatrend_id"]): r for r in csv.DictReader(fh)}
    with open(mini_daten / "bewertungen.csv") as fh:
        bew = {(r["unternehmen_id"], r["subtrend_id"]): r for r in csv.DictReader(fh)}
    werte = sorted([int(bew[("u1", "S001")]["score"]), int(bew[("u1", "S002")]["score"])], reverse=True)
    assert int(mt[("u1", "M01")]["max"]) == werte[0]
    assert float(mt[("u1", "M01")]["top3"]) == round(sum(werte) / 2, 2)
    assert mt[("u4", "M01")]["status"] == "nicht_bewertbar"


def test_zitatpruefung_verwirft_abweichung():
    assert zitat_geprueft("Montage einer  Photovoltaikanlage", "Lieferung und Montage einer Photovoltaikanlage.")
    assert not zitat_geprueft("Montage zweier Photovoltaikanlagen", "Lieferung und Montage einer Photovoltaikanlage.")


def test_schwellen_aus_datei_und_unbekanntes_modell(tmp_path):
    import pytest

    datei = tmp_path / "schwellen.json"
    datei.write_text(json.dumps({"nach": "test-modell:1b", "schwellen_nach": {
        "klar": 0.4, "schwach": 0.45, "schwach_plus": 0.02, "indirekt": 0.55, "indirekt_plus": 0.03}}))
    alt = (bewertung.SIM_KLAR, bewertung.SIM_INDIREKT, bewertung.SIM_SCHWACH,
           bewertung.SIM_INDIREKT_PLUS, bewertung.SIM_SCHWACH_PLUS)
    try:
        bewertung.setze_schwellen("test-modell:1b", datei)
        assert (bewertung.SIM_KLAR, bewertung.SIM_INDIREKT, bewertung.SIM_INDIREKT_PLUS) == (0.4, 0.55, 0.03)
        assert stufe(0, 0, 0, True, 0.59) == 5 and stufe(0, 0, 0, True, 0.57) == 4
        with pytest.raises(SystemExit):
            bewertung.setze_schwellen("unbekannt:7b")
    finally:
        (bewertung.SIM_KLAR, bewertung.SIM_INDIREKT, bewertung.SIM_SCHWACH,
         bewertung.SIM_INDIREKT_PLUS, bewertung.SIM_SCHWACH_PLUS) = alt
        bewertung.SCHWELLEN.pop("test-modell:1b", None)


def test_einbetter_wahl_und_vorsatz():
    from trendradar.einbettung import Einbetter, OllamaEinbetter, einbetter

    assert isinstance(einbetter("qwen3-embedding:8b"), OllamaEinbetter)
    assert isinstance(einbetter("BAAI/bge-m3"), Einbetter)
    e = OllamaEinbetter("qwen3-embedding:8b")
    assert e._vorsatz("anfrage").startswith("Instruct: ") and e._vorsatz("dokument") == ""
    assert Einbetter("BAAI/bge-m3")._vorsatz("anfrage") == ""


def test_rangmasse():
    from trendradar.modellwahl import auc, spearman

    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert auc([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0
    assert auc([0.5, 0.5, 0.5, 0.5], [True, False, True, False]) == 0.5


def test_hochrechnung_nach_schichten_der_ziehung(tmp_path):
    """Nach einem Neurechnen zählt die Schicht der Ziehung, nicht der neue Scorebereich."""
    from trendradar.stichprobe import auswerten

    kopf = "unternehmen_id,subtrend_id,score,status\n"
    (tmp_path / "alt").mkdir()
    (tmp_path / "alt" / "bewertungen.csv").write_text(
        kopf + "".join(f"u{i},S1,{9 if i < 2 else 1},bewertet\n" for i in range(100)), encoding="utf-8")
    (tmp_path / "bewertungen.csv").write_text(
        kopf + "".join(f"u{i},S1,1,bewertet\n" for i in range(100)), encoding="utf-8")   # neu: alles Stufe 1
    (tmp_path / "blind.jsonl").write_text("".join(json.dumps({"nr": i, "unternehmen_id": f"u{i}", "subtrend_id": "S1", "unternehmen": f"U{i}", "subtrend": "S1"})
                                                  + "\n" for i in (0, 1, 50, 51)), encoding="utf-8")
    (tmp_path / "urteile.jsonl").write_text("".join(json.dumps({"nr": i, "score": s}) + "\n"
                                                    for i, s in ((0, 9), (1, 2), (50, 1), (51, 1))), encoding="utf-8")
    ohne = auswerten(tmp_path, tmp_path / "blind.jsonl", tmp_path / "urteile.jsonl")
    mit = auswerten(tmp_path, tmp_path / "blind.jsonl", tmp_path / "urteile.jsonl",
                    schichten=tmp_path / "alt" / "bewertungen.csv")
    assert ohne["anteil_innerhalb_2_hochgerechnet"] == 0.75           # alle vier in Schicht 1–3: verzerrt
    assert mit["anteil_innerhalb_2_hochgerechnet"] == round((2 * 0.5 + 98 * 1.0) / 100, 3)
    assert mit["hochgerechnet_nach"] == "Schichten der Ziehung"
