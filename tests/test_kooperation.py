from trendradar.kooperation import Kandidat, finde_gruppen

F = ["F1", "F2", "F3"]


def test_findet_vollstaendige_gruppe_und_bevorzugt_gleichen_kreis():
    k = [Kandidat("a", "F1", 3, .7, "DED41"), Kandidat("a", "F2", 1, .6, "DED41"),
         Kandidat("b", "F3", 1, .6, "DED42"), Kandidat("c", "F3", 1, .6, "DED41")]
    gruppen, luecken = finde_gruppen(F, k)
    assert luecken == []
    assert gruppen[0].mitglieder == ["a", "c"]           # gleiche Abdeckung, aber derselbe Kreis
    assert gruppen[0].luecken == [] and set(gruppen[0].zuordnung) == set(F)


def test_luecke_wird_ausgewiesen():
    gruppen, luecken = finde_gruppen(F, [Kandidat("a", "F1", 1, .7), Kandidat("b", "F2", 1, .7)])
    assert luecken == ["F3"]
    assert gruppen[0].luecken == ["F3"]


def test_deterministisch():
    k = [Kandidat(u, f, 1, .5) for u in "abcdef" for f in F[: (ord(u) % 3) + 1]]
    assert [g.mitglieder for g in finde_gruppen(F, k)[0]] == [g.mitglieder for g in finde_gruppen(F, list(reversed(k)))[0]]
