# Messprotokoll Trend-Radar, 04.11.2026

Alle Zahlen dieser Seite stammen aus den Dateien in diesem Ordner; jede Datei nennt ihren Zeitpunkt (UTC). Gerechnet
auf einem Linux-Rechner mit 16 Threads, 62 GB RAM und einer NVIDIA RTX 3060 (12 GB), die sich die GPU mit anderen
Diensten teilt. Die Anwendung selbst braucht weder GPU noch Modell.

| Datei | Inhalt |
|---|---|
| `lauf.json` (in `daten/`) | Bewertungslauf: Taxonomie-Version, Regelversion, Schwellen, Mengen, Verteilung, Rechenzeit |
| `crawl.json` | Webabruf: Domains, Status, Seiten, Abrufe, Dauer |
| `merkmale.json` | Steckbrief: Vorschläge des Sprachmodells, übernommen, verworfen nach Grund |
| `auftraege.json` | Auszug aus den Tagesexporten: Exporte, Unternehmen, Aufträge, Dauer |
| `pruefung_modelllauf.json` | Prüfschritt, Lauf mit dem Modell (Zeiten je Paar) |
| `pruefung.json` | Prüfschritt, letzter Lauf aus dem Zwischenspeicher `daten/pruefung.jsonl` (gleiche Urteile, Verteilung vorher und nachher) |
| `zitate.json` | Nachprüfung aller gezeigten Zitate gegen die gespeicherten Quellen |
| `stichprobe_1.jsonl`, `zweitbewertung_1.jsonl`, `zweitbewertung_1_auswertung.json` | blinde Zweitbewertung, Lauf 1 (ohne Prüfschritt): gezogene Paare, Urteile, Auswertung |
| `stichprobe_2.jsonl`, `stichprobe_2_ziehung.json`, `zweitbewertung_2.jsonl`, `zweitbewertung_2_auswertung.json` | Lauf 2 (mit Prüfschritt, neue Stichprobe) |
| `tests.txt` | Ausgabe der automatischen Tests |
| `start.txt` | Zeit vom Bauen ohne Zwischenspeicher bis zur ersten Antwort |

## 1. Daten

- **Unternehmen:** 906 mit Rechtsform (GmbH, AG, KG, eG, UG, SE …) und Sitz in NUTS DED4 (Chemnitz, Erzgebirgskreis,
  Mittelsachsen, Vogtlandkreis, Zwickau), die mindestens einen öffentlichen Auftrag gewonnen haben. Einzelunternehmen,
  GbR, OHG, e. K. und Vereine bleiben draußen (oft Personennamen). Gleiche Firma = gleicher normierter Name (Kleinschreibung,
  nur Buchstaben und Ziffern); kein unscharfer Abgleich.
- **Aufträge:** 3.868 Zuordnungen Auftragnehmer → Los aus 1.433 Tagesexporten (01.12.2022–02.11.2026) des
  Bekanntmachungsservice, Lizenz CC0. Der OCDS-Export nennt Auftragnehmer nur als Partei; die Zuordnung zum Los kommt aus
  der eForms-Fassung derselben Bekanntmachung. Bei mehreren Auftragnehmern ohne eForms-Zuordnung wird nichts
  zugeordnet. Dauer 12,5 s mit 12 Prozessen (`auftraege.json`).
- **Kontaktdaten:** Titel, Los und Beschreibung werden vor dem Speichern bereinigt (E-Mail, Telefon, Name nach Anrede).
  Eine Durchsicht vor der Veröffentlichung fand vorher in einer Beschreibung Name, Durchwahl und E-Mail einer
  Ansprechperson; seitdem
  sind in `daten/belege_auftraege.jsonl` zwei Stellen ersetzt („[Name entfernt]“, „[Telefon entfernt]“, „[E-Mail entfernt]“),
  und ein Test prüft alle ausgelieferten Datendateien auf E-Mail-Adressen und Telefonnummern.
- **CPV-Bezeichnungen:** 1.022 verwendete Codes, alle mit deutscher Bezeichnung (Vokabular: 9.454 Codes).
- **Taxonomie:** eigene Beispiel-Taxonomie, Version `2026-11-04b`: 11 Megatrends, 47 Subtrends (17 auf mehreren Linien),
  188 Teilfähigkeiten mit 1.636 Fachbegriffen und 355 CPV-Präfixen.

## 2. Webabruf

136 verschiedene Domains (aus den Bekanntmachungen; 146 Unternehmen nennen eine Website), viele parallel, je Domain
höchstens ein Abruf pro Sekunde, höchstens 15 Seiten. Längste Domain 310 s (`crawl.json`, `dauer_s`).

| Ergebnis | Domains |
|---|---|
| gelesen | 117 (86 %) |
| kein lesbarer Text (z. B. nur JavaScript) | 5 |
| Weiterleitung auf eine fremde Domain (verworfen, könnte ein anderes Unternehmen sein) | 5 |
| Zertifikatsfehler | 4 |
| robots.txt sperrt die Startseite | 2 |
| HTTP-Fehler (403, 429) | 2 |
| nicht erreichbar | 1 |

1.182 Seiten gelesen, 5 Seiten wegen robots.txt nicht abgerufen, 1.419 Abrufe. 14.591 Passagen.

## 3. Bewertung und Kalibrierung

42.582 Paare (906 × 47) in 22,8 s (mit vorberechneten Vektoren), davon 3.478 „nicht bewertbar“ (74 Unternehmen mit weniger als 160 Zeichen
Beschreibungstext). Einbettungsmodell `BAAI/bge-m3`, Vektoren zwischengespeichert.

Die Regeln wurden **vor** der ersten Zweitbewertung an Grenzfällen festgelegt, durch Sichtprüfung:

| Version | Änderung | Grund |
|---|---|---|
| R1 | erste Schwellen (eindeutig ab Nähe 0,47, indirekt ab 0,60) | – |
| R2 | eindeutig ab 0,50, indirekt ab 0,68, schwach ab 0,58 | indirekte Treffer zwischen 0,60 und 0,67 waren meist Zufall („Lieferung von Büro- und Haushaltsbedarf“ → IT-Betrieb 0,61); ab etwa 0,68 sachlich richtig („Prüfung von Brückenbauwerken“ → Bauwerksprüfung 0,69) |
| R3 | ein CPV-Code allein trägt höchstens Stufe 7; ab Stufe 8 müssen zwei Belege die Fähigkeit im Text nennen | ein Logistik- und Infrastrukturunternehmen stand mit allgemeinen Elektroinstallations-Codes auf Platz 1 bei „Photovoltaik & Speicher“ |
| Taxonomie `2026-11-04b` | 13 zu allgemeine Fachbegriffe gestrichen: „hoai“, „objektplanung“, „fachplanung“, „leistungsphase“, „ausführungsplanung“ (BIM), „außenanlage“ (Spielplätze), „edelstahlrohr“, „kompressor“ (Wasserstoff), „metallbau“, „stahlbau“ (Laserbearbeitung), „batteriesystem“, „stromschiene“ (E-Fahrzeuge), „bildungszentrum“ (Weiterbildung) | Planungsbüros zählten über HOAI-Wörter zu „Digitales Planen & Bauen“, eine Notbeleuchtungs-Zentralbatterie zu E-Fahrzeugen, Gewerke am Gebäude zu Weiterbildung |

Verteilung nach R3, vor dem Prüfschritt (`pruefung.json`, `verteilung_vorher`): Stufe 1: 34.303 · 2: 2.128 · 3: 530 · 4: 16 · 5: 2 · 6: 1.206 · 7: 258 ·
8: 196 · 9: 301 · 10: 164.

## 4. Steckbrief aus der Website

`qwen2.5:7b-instruct` (Digest `845dbda0ea48`), lokal über Ollama 0.20.6, Prompt M1, 117 Websites, je Website bis zu
7.000 Zeichen Passagen. 14,5 s je Website im Median.

| | Anzahl |
|---|---|
| vorgeschlagen | 908 |
| übernommen (Zitat wörtlich in der genannten Passage) | 667 |
| verworfen: Zitat nicht wörtlich | 173 |
| verworfen: genannte Passage existiert nicht | 29 |
| verworfen: Zitat kürzer als zwei Wörter | 17 |
| verworfen: doppelt | 22 |

219 von 908 Vorschlägen (24 %) hätten ohne die Prüfung ein falsches oder nicht auffindbares Zitat gezeigt.

## 5. Prüfschritt

`qwen2.5:7b-instruct`, lokal über Ollama 0.20.6 (3 parallele Anfragen, alles auf der GPU), Prompt P3, Temperatur 0.
Zahlen und Zeiten des Modelllaufs: `pruefung_modelllauf.json`; `pruefung.json` ist der spätere Lauf aus dem
Zwischenspeicher (gleiche Urteile, ohne Zeiten).
Geprüft wird jede Belegstelle der Paare ab Stufe 6: Zeigt das Zitat, dass das Unternehmen die Teilfähigkeit **selbst und
als wesentlichen Teil seiner Leistung** erbringt? Nicht bestätigen soll das Modell Nebenpositionen in langen
Leistungsverzeichnissen, bloße Gebäudetypen, Werbefloskeln, Kunden statt eigener Leistung und Wortgleichheit bei anderer
Sache; im Zweifel nicht. Danach zählt die Rubrik neu, nur mit bestätigten Belegen. Die Urteile liegen je Belegstelle in
`daten/pruefung.jsonl` (mit Begründung); ein erneuter Lauf auf demselben Datenstand fragt das Modell nicht noch einmal.

| | Anzahl |
|---|---|
| geprüfte Paare (Stufe 6–10 vor der Prüfung) | 2.125 |
| geprüfte Belegstellen | 4.086 |
| bestätigt | 1.539 |
| nicht bestätigt | 2.547 |
| Paare neu eingestuft | 1.674 |
| Modellfehler / Belegstellen ohne Urteil | 0 / 0 |
| Dauer je Paar (Median) | 2,4 s |

Verteilung danach: 1: 34.957 · 2: 2.497 · 3: 734 · 4: 22 · 5: 4 · 6: 527 · 7: 96 · 8: 101 · 9: 127 · 10: 39. Stufe 6–10: 890 Paare
(vorher 2.125).

**Zwei Fehlversuche, beide verworfen und hier vermerkt:** Mit festem JSON-Schema (Prompt P1) und mit `format: json` (P2)
lief die Auswahl der Ausgabewörter auf der CPU (GPU 0–2 % ausgelastet, 8–46 s je Anfrage); mit 6 parallelen Anfragen lagen
6 von 29 Schichten des Modells auf der CPU. Ohne Grammatik-Zwang und mit 3 Anfragen: 1–3 s je Anfrage. Die Urteile der
Fehlversuche wurden gelöscht; alle gezählten Urteile stammen aus P3.

## 6. Nachprüfung aller gezeigten Zitate

Jedes gezeigte Zitat wird gegen die gespeicherte Quelle geprüft: Aufträge gegen Titel, Los und Beschreibung,
Website-Zitate und Steckbrief-Merkmale gegen den gespeicherten Volltext der Seite (der nicht im Repository liegt).

| Quelle | geprüft | wörtlich |
|---|---|---|
| öffentliche Aufträge | 1.113 | 1.113 |
| Websites | 479 | 479 |
| Steckbrief-Merkmale | 667 | 667 |

## 7. Blinde Zweitbewertung

**Messweg.** Gezogen wird je Scorebereich (1–3, 4–5, 6–7, 8–10) bis zu 50 Paare (der Bereich 4–5 hat nur 18). Die
Zweitbewertung sieht den Unternehmensnamen, alle Aufträge (Titel, Los, Beschreibung, gekürzt), die ersten 25
Website-Passagen in Abrufreihenfolge, den Subtrend mit seinen Teilfähigkeiten und die Rubrik – **nicht** den Score und
nicht die Belegauswahl des Verfahrens. Zweitbewerter ist ein großes kommerzielles Sprachmodell (nicht lokal, nicht Teil der Anwendung), in drei unabhängigen
Teilen. Das ersetzt keine Referenzbewertung durch Fachleute; es macht Abweichungen sichtbar und den Messweg prüfbar.
Gezählt wird der Anteil innerhalb ±2 Punkten über die ganze geschichtete Stichprobe, außerdem je Bereich gleich
gewichtet und hochgerechnet auf die Verteilung im Bestand.

**Lauf 1 (ohne Prüfschritt, Regel R3), 168 Paare:**

| Bereich | n | innerhalb ±2 | genau gleich |
|---|---|---|---|
| 1–3 | 50 | 98 % | 38 |
| 4–5 | 18 | 61 % | 6 |
| 6–7 | 50 | 50 % | 9 |
| 8–10 | 50 | 56 % | 6 |
| **gesamt (geschichtete Stichprobe)** | 168 | **67 %** | 59 |
| je Bereich gleich gewichtet | | 66 % | |
| hochgerechnet auf den Bestand | | 95,5 % | |

Die Hochrechnung ist hoch, weil 94 % aller Paare in Stufe 1–3 liegen. Wo das Verfahren einen Beleg sieht (6–10), lag die
Zweitbewertung nur in 53 % der Fälle innerhalb ±2, **und fast immer niedriger** (51 von 55 Abweichungen). Die Gründe der
Zweitbewertung, gruppiert: Nebenposition in einem langen Leistungsverzeichnis („nur Elektroauftrag mit Nebenposition
Abbrucharbeiten“), bloßer Gebäudetyp („Gebäudereinigung in Feuerwehrgerätehäusern“ → Brandschutz), Werbefloskeln
(„Werbeagentur; nur Floskeln ‚Daten sicher‘“ → Cybersicherheit), allgemeine Gewerke (Sanitär und Heizung → Klimaanpassung).
Alle 55 Abweichungen mit Begründung: `zweitbewertung_1_auswertung.json`.

**Folgerung:** Genau dafür ist der Prüfschritt (Abschnitt 5) gebaut: ein Sprachmodell prüft die Belege der Paare ab
Stufe 6 auf diese Fehlerarten. Lauf 2 misst ihn an einer **neuen** Stichprobe (andere Paare, anderer Zufallsstart), damit
die Messung nicht auf denselben Paaren beruht, an denen der Fehler gefunden wurde.

**Lauf 2 (mit Prüfschritt):**

| Bereich | n | innerhalb ±2 | genau gleich |
|---|---|---|---|
| 1–3 | 50 | 92 % | 33 |
| 4–5 | 8 | 62 % | 1 |
| 6–7 | 50 | 56 % | 9 |
| 8–10 | 50 | 66 % | 10 |
| **gesamt (geschichtete Stichprobe)** | 158 | **71 %** | 53 |
| je Bereich gleich gewichtet | | 69 % | |
| hochgerechnet auf den Bestand | | 91,2 % | |

Neue Stichprobe (Zufallsstart 23, ohne die 168 Paare von Lauf 1; `stichprobe_2_ziehung.json`, nachgezogen und identisch), derselbe Messweg, dieselbe Anweisung an die
Zweitbewertung. **Ergebnis:** In den Stufen 6–10 stieg die Übereinstimmung von 53 % auf 61 %, insgesamt von 67 % auf
71 %. In Stufe 1–3 sank sie von 98 % auf 92 %: der strenge Prüfschritt hat auch
richtige Belege verworfen (Zweitbewertung höher als das Verfahren in 12 von 46 Abweichungen, in Lauf 1 waren es 4 von 55).
Hochgerechnet auf den Bestand sank der Anteil darum von 95,5 % auf 91,2 %.

**Was übrig bleibt:** Das Verfahren liegt noch in 34 von 46 Abweichungen höher. Meist hat das kleine Modell einen
Beleg bestätigt, den die Zweitbewertung klar verwirft (Lkw-Lieferungen → Ladeinfrastruktur, Baustellencontainer →
ÖPNV, Fahrzeugaufbau → Leichtbau). **In den Stufen 6–10 reicht die Übereinstimmung noch nicht.** Nächste Hebel,
in dieser Reihenfolge: ein größeres Modell im Prüfschritt (gegen dieselbe Stichprobe gemessen), Kalibrierung der
Schwellen an einer Referenzstichprobe von Fachleuten, schärfere Fachbegriffe für die Subtrends mit den meisten
Abweichungen. Alle 46 Abweichungen mit Begründung: `zweitbewertung_2_auswertung.json`.

## 8. Lasttest

Der Lasttest dieses Stands ist am 16.11.2026 durch eine neue Messung mit einer eigenen, runden Größe ersetzt
(100.000 simulierte Unternehmen × 100 Themen): [Messprotokoll 16.11.2026](../2026-11-16/MESSPROTOKOLL.md). Aus dem
damaligen Lauf stammt eine Korrektur, die im Code steht: Ranglisten bestimmen erst die Seite und holen dann nur für
deren Einträge den stärksten Beleg; Kennzahlen je Subtrend und Kreis werden beim Laden berechnet (Tabelle `kennzahl`).

## 9. Tests

35 automatische Tests, alle bestanden (`tests.txt`, Lauf vom 04.11.2026, Docker, PostgreSQL 16, 8,9 s): Rubrik
und Stufen, „nicht bewertbar“ nie als 1, Zitatprüfung, Zuordnung Auftragnehmer → Los aus eForms (auch bei wechselnden
Namensraum-Präfixen), nur Unternehmen mit Rechtsform, keine Kontaktdaten in den Daten, robots.txt nach RFC 9309,
Nutzungsvorbehalt, Seitenauswahl, Kontaktdaten-Bereinigung (auch „Tel.-Nr.“, Vorwahl mit Schrägstrich, Name nach Anrede), keine E-Mail-Adressen und
Telefonnummern in den ausgelieferten Datendateien, Taxonomie-Prüfung, Megatrend-Map ohne Kollisionen,
Kooperationsfinder (Lücken, gleicher Kreis, deterministisch), API (Seiten 50/200, Fehler mit Korrelations-ID,
Rate-Limit mit Retry-After, Protokoll ohne Inhaltsdaten, Export CSV und XLSX), Oberfläche einschließlich leerer
Formularfelder. Die Tests brauchen kein Modell (ein Hash-Einbetter ersetzt bge-m3).

## 10. Was diese Messung nicht zeigt

- Die Zweitbewertung ist ein Sprachmodell, keine Fachperson. Eine Übereinstimmung mit einer Referenzbewertung durch
  Fachleute ist damit nicht nachgewiesen.
- Die Schwellen sind an Grenzfällen von Hand gesetzt, nicht kalibriert.
- Die Unternehmen sind eine Auswahl (Gewinner öffentlicher Aufträge), kein Registerbestand; die Ergebnisse je Kreis
  beschreiben diese Auswahl.
- Ein Unternehmen hat in den Bekanntmachungen nur die Region (DED4) als Sitz, keinen Kreis; die Kreisspalten der
  Oberfläche summieren sich deshalb auf 905.
