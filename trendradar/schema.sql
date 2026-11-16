-- Trend-Radar: Datenhaltung in PostgreSQL 16. Scores als schmale Tabelle (Unternehmen × Subtrend) mit Index je
-- Subtrend und Score, damit Filter und Seiten auf dem Server laufen, auch bei großen Beständen (Lasttest: 100.000
-- simulierte Unternehmen).
-- Jede Bewertung trägt Taxonomie-Version, Regelversion und Modell; nichts wird unbemerkt überschrieben.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE SCHEMA IF NOT EXISTS {schema};
SET search_path TO {schema}, public;

CREATE TABLE IF NOT EXISTS stand (
    schluessel text PRIMARY KEY,
    wert       jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS megatrend (
    id           text PRIMARY KEY,
    name         text NOT NULL,
    farbe        text NOT NULL,
    beschreibung text NOT NULL DEFAULT '',
    position     int  NOT NULL
);

CREATE TABLE IF NOT EXISTS subtrend (
    id           text PRIMARY KEY,
    name         text NOT NULL,
    beschreibung text NOT NULL DEFAULT '',
    aktiv        boolean NOT NULL DEFAULT true
);

-- Mehrfachzugehörigkeit: ein Subtrend liegt auf mehreren Linien.
CREATE TABLE IF NOT EXISTS linie (
    megatrend_id text NOT NULL REFERENCES megatrend(id),
    subtrend_id  text NOT NULL REFERENCES subtrend(id),
    position     int  NOT NULL,
    primaer      boolean NOT NULL,
    PRIMARY KEY (megatrend_id, subtrend_id)
);

CREATE TABLE IF NOT EXISTS faehigkeit (
    id           text PRIMARY KEY,
    subtrend_id  text NOT NULL REFERENCES subtrend(id),
    name         text NOT NULL,
    beschreibung text NOT NULL,
    begriffe     text[] NOT NULL DEFAULT '{{}}',
    cpv          text[] NOT NULL DEFAULT '{{}}',
    position     int  NOT NULL
);

CREATE TABLE IF NOT EXISTS unternehmen (
    id          text PRIMARY KEY,
    name        text NOT NULL,
    rechtsform  text,
    ort         text,
    plz         text,
    kreis_nuts  text,
    website     text,
    auftraege   int NOT NULL DEFAULT 0,
    web_status  text
);
CREATE INDEX IF NOT EXISTS unternehmen_name_trgm ON unternehmen USING gin (lower(name) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS unternehmen_kreis ON unternehmen (kreis_nuts);

CREATE TABLE IF NOT EXISTS bewertung (
    unternehmen_id text NOT NULL REFERENCES unternehmen(id),
    subtrend_id    text NOT NULL REFERENCES subtrend(id),
    score          smallint CHECK (score BETWEEN 1 AND 10),
    status         text NOT NULL CHECK (status IN ('bewertet', 'nicht_bewertbar')),
    stufe          text NOT NULL,
    n_belege       int NOT NULL DEFAULT 0,
    k_faehigkeiten int NOT NULL DEFAULT 0,
    beste_naehe    real,
    PRIMARY KEY (subtrend_id, unternehmen_id),
    CHECK ((status = 'bewertet') = (score IS NOT NULL))     -- „nicht bewertbar“ ist nie eine 1
);
CREATE INDEX IF NOT EXISTS bewertung_rang ON bewertung (subtrend_id, score DESC NULLS LAST, n_belege DESC);
CREATE INDEX IF NOT EXISTS bewertung_firma ON bewertung (unternehmen_id);

CREATE TABLE IF NOT EXISTS megatrend_bewertung (
    unternehmen_id text NOT NULL REFERENCES unternehmen(id),
    megatrend_id   text NOT NULL REFERENCES megatrend(id),
    max            smallint,
    top3           real,
    status         text NOT NULL,
    n_subtrends    int NOT NULL,
    PRIMARY KEY (megatrend_id, unternehmen_id)
);
CREATE INDEX IF NOT EXISTS megatrend_rang ON megatrend_bewertung (megatrend_id, max DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS megatrend_firma ON megatrend_bewertung (unternehmen_id);

CREATE TABLE IF NOT EXISTS belegstelle (
    id             bigserial PRIMARY KEY,
    unternehmen_id text NOT NULL REFERENCES unternehmen(id),
    subtrend_id    text NOT NULL REFERENCES subtrend(id),
    faehigkeit_id  text NOT NULL REFERENCES faehigkeit(id),
    typ            text NOT NULL CHECK (typ IN ('eindeutig', 'indirekt')),
    signal         text NOT NULL,
    treffer        text,
    naehe          real,
    beleg_id       text NOT NULL,
    quelle         text NOT NULL,
    url            text NOT NULL,
    datum          text,
    feld           text,
    zitat          text NOT NULL,
    geprueft       text,              -- 'bestaetigt' nach dem Prüfschritt des Sprachmodells
    pruef_grund    text
);
CREATE INDEX IF NOT EXISTS belegstelle_firma ON belegstelle (unternehmen_id, subtrend_id);
CREATE INDEX IF NOT EXISTS belegstelle_kette ON belegstelle (subtrend_id, faehigkeit_id) WHERE typ = 'eindeutig';

CREATE TABLE IF NOT EXISTS auftrag (
    beleg_id       text PRIMARY KEY,
    unternehmen_id text NOT NULL REFERENCES unternehmen(id),
    url            text NOT NULL,
    datum          text,
    titel          text,
    los            text,
    cpv            text[] NOT NULL DEFAULT '{{}}',
    art            text
);
CREATE INDEX IF NOT EXISTS auftrag_firma ON auftrag (unternehmen_id, datum DESC);

-- Kennzahlen je Subtrend und Kreis, beim Laden berechnet (der Datenstand ändert sich nur mit Import und
-- Bewertung): Karte und Subtrend-Seiten lesen hier statt Millionen Bewertungen zu zählen.
CREATE TABLE IF NOT EXISTS kennzahl (
    subtrend_id       text NOT NULL,
    kreis_nuts        text NOT NULL,
    n_bewertet        int NOT NULL,
    n_nicht_bewertbar int NOT NULL,
    n_beleg           int NOT NULL,
    n_stark           int NOT NULL,
    summe_score       bigint NOT NULL,
    PRIMARY KEY (subtrend_id, kreis_nuts)
);

-- Steckbrief aus der Website: Vorschläge eines Sprachmodells, nur mit wörtlich geprüftem Zitat.
CREATE TABLE IF NOT EXISTS merkmal (
    id             bigserial PRIMARY KEY,
    unternehmen_id text NOT NULL REFERENCES unternehmen(id),
    art            text NOT NULL,
    wert           text NOT NULL,
    zitat          text NOT NULL,
    url            text NOT NULL,
    datum          text
);
CREATE INDEX IF NOT EXISTS merkmal_firma ON merkmal (unternehmen_id);

CREATE TABLE IF NOT EXISTS cpv (
    code        text PRIMARY KEY,
    bezeichnung text NOT NULL
);

-- API-Protokoll ohne Inhaltsdaten (Pfad ohne Abfrageparameter), 90 Tage.
CREATE TABLE IF NOT EXISTS api_protokoll (
    zeit        timestamptz NOT NULL DEFAULT now(),
    client      text,
    methode     text,
    pfad        text,
    status      int,
    dauer_ms    real,
    korrelation text
);
CREATE INDEX IF NOT EXISTS api_protokoll_zeit ON api_protokoll (zeit);
