# Messprotokoll Trend-Radar, 11.11.2026: Modellwechsel

Am 11.11.2026 wurden beide KI-Modelle auf die neueste Generation offener Modelle umgestellt und der ganze Bestand neu
gerechnet. **Einbettung:** `qwen3-embedding:4b` statt `BAAI/bge-m3`. **Sprachmodell** (Gegenprüfung und Firmenprofil):
`qwen3.8:27b` statt `qwen2.5:7b-instruct`. Beide laufen lokal über Ollama 0.40.16, beide unter Apache 2.0.

Gleich geblieben sind die Rohdaten (Aufträge und Website-Abrufe vom 04.11.2026), die Taxonomie, die Regelversion R3, die
Prompts (Gegenprüfung P3, Firmenprofil M1) und der Maßstab: die blinde Zweitbewertung vom 04.11.2026 mit 326 Paaren in
zwei Stichproben ([Messprotokoll 04.11.2026](../2026-11-04/MESSPROTOKOLL.md), Abschnitt 7). Alle Zahlen dieser Seite
stammen aus den Dateien in diesem Ordner; jede Datei nennt ihren Zeitpunkt (UTC).

| Datei | Inhalt |
|---|---|
| `modellwahl_einbettung.json` | Messung von fünf Einbettungsmodellen gegen die Zweitbewertung |
| `modellwahl_sprachmodell.json` | Messung des Sprachmodells gegen den Vorgänger auf 100 Prüffällen und 10 Websites, mit Geschwindigkeit |
| `schwellen.json` | Übertragung der Schwellen auf das neue Einbettungsmodell |
| `pruefung.json` | Gegenprüfung im ganzen Bestand |
| `merkmale.json` | Firmenprofil aus der Website im ganzen Bestand |
| `zitate.json` | Nachprüfung aller gezeigten Zitate |
| `vergleich.json` | Vorher/nachher gegen die Zweitbewertung, je Teilmenge |
| `zweitbewertung_1_auswertung.json`, `zweitbewertung_2_auswertung.json` | Auswertung je Stichprobe, hochgerechnet nach den Schichten der Ziehung |
| `modellwechsel.json` | die Kernzahlen vorher/nachher (die Seite „So funktioniert’s“ liest sie) |

Gerechnet wurde mit Docker; die Messung der Modelle (Abschnitte 1 und 2) lief auf der Hardware der Demo, einem
Linux-Rechner mit 16 Threads, 62 GB RAM und einer NVIDIA RTX 3060 (12 GB). Geschwindigkeiten nennt dieses Protokoll nur
aus dieser Messung; die Zeiten der Neuberechnung sind nicht ausgewiesen.

## 1. Einbettungsmodell

**Messweg.** Für jedes der 326 Paare der Zweitbewertung: die größte Kosinus-Ähnlichkeit zwischen einer Textstelle des
Unternehmens (15.983 Textstellen insgesamt) und einem Baustein des Themas. Gemessen wird, wie gut dieser eine Wert die
Paare so ordnet wie die Zweitbewertung: Rangkorrelation (Spearman) und die Fläche unter der ROC-Kurve für „Zweitbewertung
6–10“ bzw. „8–10“ gegen den Rest. Jedes Modell bekommt den Vorsatz, den sein Hersteller für Anfragen und Dokumente
empfiehlt. Kandidaten: der Vorgänger, das jüngste Modell (`embeddinggemma-2`) und `qwen3-embedding` in drei Größen.

| Modell | Dimensionen | Spearman | AUC 6–10 | AUC 8–10 | Textstellen je s (RTX 3060) |
|---|---|---|---|---|---|
| `BAAI/bge-m3` (bisher) | 1.024 | 0,583 | 0,758 | 0,744 | – (Vektoren vom 04.11.) |
| `embeddinggemma-2:latest` | 768 | 0,640 | 0,802 | 0,811 | 82 |
| `qwen3-embedding:0.6b` | 1.024 | 0,715 | 0,841 | 0,833 | 85 |
| **`qwen3-embedding:4b`** | 2.560 | **0,720** | **0,856** | **0,842** | 34 |
| `qwen3-embedding:8b` | 4.096 | 0,707 | 0,842 | 0,826 | 23 |

**Wahl:** `qwen3-embedding:4b`, in allen drei Maßen vorn. Das größere 8b-Modell ist nicht besser. Der Abstand der drei
Qwen-Größen ist klein (Spearman 0,707–0,720); der Abstand zum Vorgänger ist groß.

**Schwellen.** Die Schwellen der Regel R3 (klar, schwach, indirekt) waren am Vorgänger von Hand an Grenzfällen
festgelegt. Für das neue Modell gelten die Werte, über denen **derselbe Anteil** aller 5.120.932 Paare Textstelle ×
Baustein liegt wie beim Vorgänger (Quantil-Abgleich): klar 0,329 (vorher 0,50), schwach 0,446 + 0,069 (vorher 0,58 +
0,04), indirekt 0,596 + 0,033 (vorher 0,68 + 0,04). Die Zweitbewertung geht in diese Übertragung nicht ein.

## 2. Sprachmodell

**Messweg.** Die 100 Paare der Stichprobe 1, denen das Verfahren vor der Gegenprüfung Stufe 6–10 gab, mit denselben 256
Textstellen. Das Sprachmodell prüft jede Textstelle wie im Betrieb; gezählt wird, wie oft die Stufe danach innerhalb ±2
der Zweitbewertung liegt, und wie gut es „Nachweis ja/nein“ trifft (Nachweis = Stufe 6–10). Das Sprachmodell war
vorgegeben (neueste Generation); gemessen wurde es gegen den Vorgänger.

| | ohne Gegenprüfung | `qwen2.5:7b-instruct` (bisher) | **`qwen3.8:27b`** |
|---|---|---|---|
| innerhalb ±2 | 53 % | 61 % | **72 %** |
| genau gleich | 18 | 25 | 32 |
| mittlere Abweichung (Verfahren − Zweitbewertung) | +2,51 | −0,36 | −0,56 |
| Nachweis ja/nein: Trefferquote | 1,00 | 0,79 | 0,85 |
| Nachweis ja/nein: Genauigkeit | 0,47 | 0,66 | 0,77 |
| Nachweis ja/nein: ausgewogene Genauigkeit | 0,50 | 0,71 | 0,81 |
| bestätigte Textstellen von 256 | 256 | 113 | 105 |
| Modellfehler | – | 0 | 0 |

Die Urteile von `qwen2.5:7b-instruct` stammen aus dem Zwischenspeicher vom 04.11.2026 (gleicher Prompt).

**Firmenprofil auf 10 Websites:** `qwen3.8:27b` schlug 195 Merkmale vor, 193 bestanden die Zitatprüfung, 2 nicht (Zitat
nicht wörtlich).

**Geschwindigkeit auf der RTX 3060:** `qwen3.8:27b` belegt 19,1 GB, davon 10,2 GB im Grafikspeicher; der Rest läuft auf
der CPU. Gegenprüfung 19,6 s je Anfrage im Median, mit 2 parallelen Anfragen 12,6 s je Paar (100 Paare in 21 min),
5,9 Token je Sekunde; Firmenprofil 119 s je Website im Median. Zum Vergleich `qwen2.5:7b-instruct` am 04.11.: 2,4 s je
Paar. Für den Betrieb heißt das: entweder eine GPU, die das Modell ganz fasst, oder lange Läufe über Nacht.

**Nicht gemessen:** `gemma4:31b` und `gemma4:26b-a4b` (Lauf nach dem ersten Modell beendet, weil das Modell feststand).

## 3. Neuberechnung des Bestands

| | 04.11.2026 | 11.11.2026 |
|---|---|---|
| Paare bewertet / zu wenig Angaben | 39.104 / 3.478 | 39.104 / 3.478 |
| Paare vor der Gegenprüfung bei Stufe 6–10 | 2.125 | 2.539 |
| geprüfte Textstellen | 4.086 | 5.047 |
| bestätigt / aussortiert | 1.539 / 2.547 | 1.738 / 3.309 |
| Paare nach der Gegenprüfung bei Stufe 6–10 | 890 | 956 |
| gezeigte Belegstellen | 1.592 | 1.776 |
| Firmenprofil: Vorschläge / übernommen | 908 / 667 | 2.002 / 1.970 |
| Firmenprofil: verworfen (Zitat nicht wörtlich, Stelle fehlt, zu kurz) | 219 (24 %) | 26 (1,3 %) |
| Firmenprofil: doppelt | 22 | 6 |
| Nachprüfung: Zitate wörtlich in der gespeicherten Quelle | 2.259 von 2.259 | 3.746 von 3.746 |
| Modellfehler in der Gegenprüfung | 0 | 0 |

Das neue Sprachmodell schlägt mehr als doppelt so viele Merkmale vor und hält sich fast immer an den Wortlaut. Die
Gegenprüfung lief über alle 2.539 Paare; ein Teil der Urteile kam aus dem Zwischenspeicher eines unterbrochenen Laufs
desselben Modells mit demselben Prompt (gleiche Urteile).

## 4. Vorher/nachher gegen die Zweitbewertung

Verglichen wird jeweils der **Endstand** (nach der Gegenprüfung) beider Datenstände auf denselben Paaren. Deshalb weicht
„vorher“ für Stichprobe 1 von den 67 % im Protokoll vom 04.11. ab: jene Zahl galt dem Stand vor der Gegenprüfung.

| Teilmenge | Paare | vorher | nachher |
|---|---|---|---|
| alle Paare beider Stichproben | 326 | 72 % | **78 %** |
| Stichprobe 1 | 168 | 72 % | 82 % |
| Stichprobe 2 | 158 | 71 % | 73 % |
| wo der Radar einen Nachweis zeigt (Stufe 6–10 im jeweiligen Stand) | 156 → 121 | 63 % | **76 %** |
| wo die Zweitbewertung einen Nachweis sieht (6–10) | 122 | 74 % | 73 % |

**Hochgerechnet auf alle Paare** (nach den Schichten, in denen gezogen wurde): Stichprobe 1 95,5 % → 96,9 %,
Stichprobe 2 91,2 % → 91,3 %.

**Was das heißt.** Wo der Radar einen Nachweis zeigt, stimmt er deutlich öfter (63 % → 76 %). Wo die Zweitbewertung
einen Nachweis sieht, findet der Radar ihn nicht öfter als vorher (74 % → 73 %). Die Fehlerart hat sich gedreht: am
04.11. lag der Radar bei Abweichungen fast immer **zu hoch** (51 von 55 in Stichprobe 1); jetzt liegt er öfter **zu
niedrig** (20 von 31 in Stichprobe 1, 27 von 42 in Stichprobe 2). Beispiele aus den Begründungen der Zweitbewertung:
Aufträge zur ökologischen Baubegleitung von Hochwasserschutz (Radar 3, Zweitbewertung 9), Einführung einer
CAFM-Software (3 gegen 6), Planung eines Rückbaus (3 gegen 7). Alle Abweichungen mit Begründung stehen in den beiden
Auswertungsdateien.

## 5. Korrektur der Hochrechnung

Die Auswertung rechnete bisher nach den Stufenbereichen des **aktuellen** Stands hoch. Das stimmt nur, solange seit der
Ziehung nicht neu gerechnet wurde. Nach dem Neurechnen landen Paare, die aus den hohen Bereichen gezogen wurden, im
großen Bereich 1–3 und bekommen dort ein Gewicht, das ihnen nicht zusteht; die Hochrechnung fiel dadurch scheinbar auf
82 % bzw. 74 %. Jetzt wird nach den Schichten der Ziehung hochgerechnet (`trendradar.stichprobe auswerten --schichten
… --ziehung …`); für den Stand vom 04.11. ergibt das genau die damaligen 95,5 % und 91,2 %. Ein Test sichert das ab.

## 6. Tests

40 automatische Tests, alle grün (vorher 35; neu u. a. Schwellen aus Datei, Wahl des Einbettungsmodells, Rangmaße,
Hochrechnung nach den Schichten der Ziehung).

## 7. Was diese Messung nicht zeigt

- **Die Zweitbewertung ist kein Fachurteil.** Sie stammt von einem großen kommerziellen Sprachmodell (nicht lokal, nicht Teil der Anwendung) nach derselben
  Rubrik. Sie macht Abweichungen sichtbar; eine Referenzbewertung durch Fachleute ersetzt sie nicht.
- **Auswahl am Maßstab.** Das Einbettungsmodell wurde an derselben Zweitbewertung gewählt, an der es danach gemessen
  wird. Bei fünf Kandidaten mit großem Abstand zum Vorgänger ist der Effekt klein, aber nicht null.
- **Kleine Stichproben.** 121 bis 168 Paare je Teilmenge; ein Unterschied von wenigen Prozentpunkten (z. B. 74 % → 73 %)
  ist kein Befund.
- **Die Websites sind vom 04.11.2026.** Neu gerechnet wurde auf denselben Rohdaten, nicht neu abgerufen.
