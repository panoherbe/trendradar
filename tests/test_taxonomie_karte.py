import pytest

from trendradar import karte
from trendradar.taxonomie import STANDARD, TaxonomieFehler, lade


def test_mitgelieferte_taxonomie_ist_konsistent():
    t = lade(STANDARD)
    assert len(t.megatrends) == 11
    assert 40 <= len(t.subtrends) <= 60
    assert sum(len(s.megatrends) > 1 for s in t.subtrends.values()) >= 8
    for s in t.subtrends.values():
        assert 2 <= len(s.faehigkeiten) <= 5, s.id


def test_inkonsistente_taxonomie_bricht_ab(tmp_path, mini_taxonomie):
    kaputt = mini_taxonomie.read_text().replace("subtrends: [S002, S003]", "subtrends: [S003]")
    p = tmp_path / "kaputt.yaml"
    p.write_text(kaputt)
    with pytest.raises(TaxonomieFehler):
        lade(p)


@pytest.mark.parametrize("quelle", ["mini", "standard"])
def test_karte_ohne_kollision(quelle, mini_taxonomie):
    t = lade(mini_taxonomie if quelle == "mini" else STANDARD)
    lay = karte.layout(t, versuche=60)
    reihen, spalten = lay["reihen"], lay["spalten"]
    belegt = {}
    for s in t.subtrends.values():
        for m in s.megatrends:
            pos = (reihen[m], spalten[s.id])
            assert pos not in belegt, f"{s.id} und {belegt.get(pos)} auf derselben Stelle"
            belegt[pos] = s.id
    for s in t.subtrends.values():                     # keine Umsteigeverbindung läuft durch eine fremde Station
        zs = sorted(reihen[m] for m in s.megatrends)
        for z in range(zs[0] + 1, zs[-1]):
            if z not in zs:
                assert (z, spalten[s.id]) not in belegt
    svg = karte.svg(t, lay, {})
    assert svg.count('class="station"') == len(t.subtrends)
