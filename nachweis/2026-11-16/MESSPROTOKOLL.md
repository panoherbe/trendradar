# Messprotokoll Trend-Radar, 16.11.2026: Lasttest mit simulierten Daten

Kurz: 100.000 simulierte Unternehmen × 100 Themen = 10 Mio. Bewertungen, 10 gleichzeitige Nutzer, drei Läufe zu je
60 s. Im ersten Lauf, direkt nach dem Laden, antworteten 95 % der Anfragen in unter 1,2 s, 3 von 1.565 Anfragen
endeten mit einem Serverfehler. Zwei Wiederholungen liefen ohne Fehler, 95 % unter 0,62 s.

Alle Zahlen dieser Seite stammen aus den Dateien in diesem Ordner; jede Datei nennt ihren Zeitpunkt (UTC). **Die Daten
sind simuliert** (zufällige Unternehmen, Bewertungen und Belege); gemessen wird nur, wie schnell Datenbank und Anwendung
bei dieser Größe antworten, nicht die Qualität der Einordnung.

| Datei | Inhalt |
|---|---|
| `lasttest_matrix.json` | Rechenzeit der Ähnlichkeitsmatrix in dieser Größe (synthetische Vektoren) |
| `lasttest_erzeugen.json` | Erzeugen und Laden des simulierten Bestands |
| `lasttest_messen.json` | Lauf 1, direkt nach dem Laden |
| `lasttest_wiederholung_1.json`, `lasttest_wiederholung_2.json` | Wiederholungen |
| `lasttest.json` | Zusammenfassung (Lauf 1 oben, Wiederholungen unter `wiederholungen`); die Seite „So funktioniert’s“ liest sie |

## 1. Aufbau

- **Rechner:** Linux-Server mit Intel Core i7-11700B (8 Kerne, 16 Threads), 62 GB RAM; Docker Compose 2.30,
  PostgreSQL 16.12. Auf demselben Rechner liefen andere Dienste mit; die Rechnerlast (Mittel über 1 Minute) steht je
  Lauf in `lasttest.json` (zwischen 5,5 und 10,6).
- **Bestand:** eigenes Schema `lasttest` in derselben Datenbank, getrennt vom echten Datenstand: 100.000 Unternehmen,
  11 Zukunftsfelder, 100 Themen mit je 4 Bausteinen, 10.000.000 Bewertungen, 1.648.342 Belegstellen, 1.933 MB. Erzeugen
  und Laden: 304 s.
- **Anwendung:** eine zweite Instanz mit 2 Prozessen wie im Betrieb, ohne Anfragebegrenzung.
- **Last:** 10 gleichzeitige Nutzer rufen 60 s lang zufällig sechs Arten von Seiten und API-Abfragen ab (Tabelle unten).
- **Ähnlichkeitsmatrix** in dieser Größe (100.000 Unternehmens- gegen 400 Bausteinvektoren, 1.024 Dimensionen, 10 Mio.
  Themenwerte): 1,04 s auf der CPU (NumPy, float32, synthetische Vektoren).

## 2. Ergebnis

| | Lauf 1 (direkt nach dem Laden) | Wiederholung 1 (Instanz neu gestartet) | Wiederholung 2 (Datenbank und Instanz neu gestartet) |
|---|---|---|---|
| Antworten in 60 s | 1.562 | 2.113 | 2.054 |
| je Sekunde | 25,9 | 35,2 | 34,2 |
| Fehler | 3 (HTTP 500) | 0 | 0 |
| Median | 290 ms | 261 ms | 287 ms |
| 95 % unter | 1.142 ms | 620 ms | 589 ms |
| längste Antwort | 1.935 ms | 1.253 ms | 1.093 ms |

Median je Art der Anfrage (Lauf 1 / Wiederholung 1 / Wiederholung 2):

| Art | Median |
|---|---|
| Rangliste eines Themas, gefiltert nach Kreis und Stufe ≥ 6 | 266 / 248 / 283 ms |
| Rangliste eines Themas, Seite 20 zu 200 Einträgen | 867 / 336 / 346 ms |
| Rangliste eines Zukunftsfelds (Mittel der drei höchsten) | 468 / 411 / 454 ms |
| Unternehmensprofil (alle Themen mit Belegen) | 20 / 20 / 22 ms |
| Namenssuche | 228 / 206 / 231 ms |
| Themenseite (HTML) | 266 / 283 / 285 ms |

**Die 3 Fehler in Lauf 1:** Ursache nicht ermittelt. Das Protokoll der Instanz wurde nach dem Lauf mit ihr gelöscht;
zwei Wiederholungen, die zweite nach einem Neustart der Datenbank, liefen ohne Fehler. Lauf 1 begann wenige Sekunden
nach dem Laden der 10 Mio. Zeilen (Datenbank noch mit Aufräumarbeiten beschäftigt, Annahme); auch die langsame Seite 20
zu 200 Einträgen (867 ms statt rund 340 ms) passt dazu.

## 3. Selbst nachmessen

Mit dem gestarteten Compose-Projekt (`docker compose up -d`, Projektname `trendradar`):

```bash
M="-v $PWD/nachweis/neu:/app/messung"; mkdir -p nachweis/neu
docker compose run --rm $M app python -m trendradar.last matrix   --aus messung/lasttest_matrix.json
docker compose run --rm $M app python -m trendradar.last erzeugen --aus messung/lasttest_erzeugen.json
docker run -d --name trendradar-last --network trendradar_default -e DB_SCHEMA=lasttest -e RATE_LIMIT_PRO_MINUTE=0 \
  -e DATABASE_URL=postgresql://radar:radar@db:5432/radar trendradar-app \
  uvicorn trendradar.app:app --host 0.0.0.0 --port 8000 --workers 2
docker compose run --rm $M app python -m trendradar.last messen --url http://trendradar-last:8000 --aus messung/lasttest_messen.json
docker rm -f trendradar-last
```

Größe und Dauer stellen `--unternehmen`, `--subtrends`, `--nutzer` und `--dauer` ein (Vorgabe: 100.000, 100, 10, 60 s).

## 4. Was sich am 16.11.2026 sonst geändert hat

- **Bezeichner vereinheitlicht:** Die Quelle „öffentliche Aufträge“ heißt in Daten und Code jetzt `auftrag`
  (`daten/belege_auftraege.jsonl`, `trendradar/quellen/auftraege.py`, `nachweis/2026-11-04/auftraege.json`, Schlüssel
  `auftrag` in beiden `zitate.json`, `passagen_auftraege` in `daten/lauf.json`). Werte und Zahlen sind unverändert; nur
  die Namen.
- **Oberfläche:** „Beispielregion Südwestsachsen“ als Bezeichnung der Region.
- **Der frühere Lasttest** vom 04.11.2026 ist durch diesen ersetzt (Abschnitt 8 des Protokolls vom 04.11.2026).
- **Tests:** 40 von 40 grün (`tests.txt`).

## 5. Was diese Messung nicht zeigt

- Die Daten sind simuliert; echte Bestände verteilen sich anders über Themen und Kreise.
- Die Antwortzeiten gelten für diesen Rechner, diese Größe und die Last anderer Dienste während der Messung.
- Gemessen ist die Anwendung ohne vorgeschalteten Webserver, ohne Anmeldung und ohne Zwischenspeicher.
