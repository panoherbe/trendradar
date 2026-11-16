"""Webanreicherung: öffentliche Unternehmenswebsites höflich lesen und in nummerierte Passagen zerlegen.

Ein Domain-Lauf hat fünf Stufen; jede speichert ihr Ergebnis, ein Abbruch setzt an der letzten fertigen Domain an.

1. Vorprüfung: Erreichbarkeit, robots.txt (Parser nach RFC 9309), Nutzungsvorbehalt für Text- und Data-Mining
   (§ 44b UrhG: robots.txt, HTTP-Kopf und Meta-Angabe ``tdm-reservation``). Gesperrtes wird nie abgerufen; der
   Grund wird gespeichert.
2. Seitenauswahl: Startseite und interne Links, bevorzugt Produkte, Leistungen, Technologie, Branchen,
   Zertifikate, Über uns. Impressum, Datenschutz, Kontakt, AGB, Karriere sowie Referenz- und Kundenseiten (sie
   beschreiben oft die Kunden, nicht die eigene Leistung) werden nie gelesen.
3. Abruf: höchstens ein Abruf pro Sekunde je Domain (ein strengeres Crawl-delay gilt), höchstens zwei Versuche,
   eigener User-Agent mit Kontaktadresse. Viele Domains parallel, jede einzelne langsam. Keine Logins, keine
   Captchas, kein JavaScript.
4. Textgewinnung mit trafilatura; gespeichert werden URL, Abrufzeit (UTC), HTTP-Status oder Fehlergrund,
   Inhalts-Hash und der Text in nummerierten Passagen.
5. Bereinigung: E-Mail-Adressen und Telefonnummern werden entfernt, bevor eine Passage weiterverarbeitet wird.

Volltexte bleiben in ``--roh`` (nicht im Repository); weitergegeben werden nur Passagen, die als Beleg dienen.

    python -m trendradar.quellen.web --unternehmen daten/unternehmen.csv --roh roh/web --aus daten/web_passagen.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlsplit

from ..bereinigung import bereinige

USER_AGENT = "TrendRadar-Prototyp/0.1 (+https://panoherbe.com; robots.txt und tdm-reservation werden beachtet)"
ROBOTS_NAME = "TrendRadar-Prototyp"
MAX_SEITEN = 15
MIN_ABSTAND_S = 1.0
ZEITLIMIT_S = 15.0
MAX_BYTES = 3_000_000

BEVORZUGT = re.compile(
    r"produkt|leistung|technolog|kompetenz|branche|zertifi|ueber-uns|über-uns|uber-uns|about|unternehmen|"
    r"fertigung|verfahren|werkstoff|loesung|lösung|service|portfolio|projekt|maschinen|anlagen|entwicklung|forschung",
    re.IGNORECASE,
)
NIE = re.compile(
    r"impressum|datenschutz|privacy|kontakt|contact|agb|cookie|karriere|jobs|stellen|login|anmelden|warenkorb|"
    r"cart|checkout|newsletter|disclaimer|legal|imprint|barrierefreiheitserkl|sitemap|cdn-cgi|wp-json|feed|"
    # Referenz- und Kundenseiten beschreiben oft die Kunden, nicht die eigene Leistung (z. B. bei Agenturen)
    r"referenz|kunden|clients|cases|"
    r"\.(pdf|jpe?g|png|gif|zip|docx?|xlsx?|mp4|svg)$",
    re.IGNORECASE,
)


@dataclass
class DomainErgebnis:
    unternehmen_id: str
    domain: str
    status: str = "offen"           # ok | dns_fehler | nicht_erreichbar | ssl_fehler | http_fehler | weiterleitung_fremd | robots_gesperrt | tdm_vorbehalt | kein_text
    grund: str = ""
    start_url: str = ""
    seiten: list[dict] = field(default_factory=list)
    abrufe: int = 0
    dauer_s: float = 0.0


def tdm_vorbehalt(kopf: dict[str, str], html: str) -> bool:
    """TDMRep (W3C Community Group): HTTP-Kopf oder Meta-Angabe tdm-reservation = 1."""
    if kopf.get("tdm-reservation", "").strip() == "1":
        return True
    return bool(re.search(r"<meta[^>]+name=[\"']tdm-reservation[\"'][^>]+content=[\"']1[\"']", html[:20000], re.I))


def passagen(text: str, max_len: int = 360, min_len: int = 40) -> list[str]:
    """Text in Passagen an Absatz- und Satzgrenzen; jede Passage ist ein wörtlicher Ausschnitt (nach Bereinigung)."""
    out: list[str] = []
    for absatz in re.split(r"\n+", text):
        absatz = re.sub(r"\s+", " ", absatz).strip()
        if len(absatz) < min_len:
            continue
        saetze = re.split(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ0-9„\"])", absatz)
        aktuell = ""
        for s in saetze:
            if len(aktuell) + len(s) + 1 <= max_len:
                aktuell = f"{aktuell} {s}".strip()
            else:
                if len(aktuell) >= min_len:
                    out.append(aktuell)
                aktuell = s[:max_len]
        if len(aktuell) >= min_len:
            out.append(aktuell)
    seen, eindeutig = set(), []
    for p in out:
        if p not in seen:
            seen.add(p)
            eindeutig.append(p)
    return eindeutig


def gleiche_domain(a: str, b: str) -> bool:
    """firma.de, www.firma.de und shop.firma.de gelten als dieselbe Website; firma-neu.de nicht."""
    a, b = a.lower().removeprefix("www."), b.lower().removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _norm_url(u: str) -> str:
    t = urlsplit(u)
    return f"{t.netloc.lower().removeprefix('www.')}{t.path.rstrip('/') or '/'}"


def links_waehlen(basis: str, html: str, host: str, n: int) -> list[str]:
    import lxml.html

    try:
        doc = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return []
    kandidaten: dict[str, int] = {}
    for a in doc.iter("a"):
        href = a.get("href")
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urldefrag(urljoin(basis, href))[0]
        teile = urlsplit(url)
        if teile.scheme not in ("http", "https") or teile.netloc.lower().removeprefix("www.") != host:
            continue
        if NIE.search(teile.path) or teile.query:
            continue
        text = " ".join(a.itertext()).strip().lower()
        punkte = 2 * bool(BEVORZUGT.search(teile.path)) + bool(BEVORZUGT.search(text)) - teile.path.count("/") * 0.1
        kandidaten[url] = max(kandidaten.get(url, -9), punkte)
    sortiert, gesehen = [], {_norm_url(basis)}
    for u in sorted(kandidaten, key=lambda u: (-kandidaten[u], len(u))):
        if _norm_url(u) not in gesehen:
            gesehen.add(_norm_url(u))
            sortiert.append(u)
    return sortiert[:n]


class Domainlauf:
    def __init__(self, client, eintrag: dict, roh: Path):
        self.c = client
        self.e = DomainErgebnis(eintrag["id"], eintrag["website"])
        self.roh = roh
        self.letzter = 0.0
        self.abstand = MIN_ABSTAND_S
        self.robots = None

    async def _warte(self):
        jetzt = time.monotonic()
        if jetzt - self.letzter < self.abstand:
            await asyncio.sleep(self.abstand - (jetzt - self.letzter))
        self.letzter = time.monotonic()

    async def _hole(self, url: str):
        import httpx

        for versuch in (1, 2):
            await self._warte()
            self.e.abrufe += 1
            try:
                r = await self.c.get(url)
                return r, None
            except httpx.ConnectError as ex:
                fehler = "dns_fehler" if "name" in str(ex).lower() or "resolve" in str(ex).lower() else "nicht_erreichbar"
                if "ssl" in str(ex).lower() or "certificate" in str(ex).lower():
                    fehler = "ssl_fehler"
                letzter = (fehler, str(ex)[:160])
            except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ReadError) as ex:
                letzter = ("nicht_erreichbar", type(ex).__name__)
            except httpx.HTTPError as ex:
                letzter = ("nicht_erreichbar", type(ex).__name__)
        return None, letzter

    def _erlaubt(self, url: str) -> bool:
        return self.robots is None or self.robots.can_fetch(url, ROBOTS_NAME)

    async def lauf(self) -> DomainErgebnis:
        from protego import Protego
        import trafilatura

        t0 = time.monotonic()
        start = f"https://{self.e.domain}/"
        r, fehler = await self._hole(start)
        if r is None:
            r2, fehler2 = await self._hole(f"https://www.{self.e.domain}/")
            r, fehler = (r2, fehler2) if r2 is not None else (None, fehler)
        if r is None:
            self.e.status, self.e.grund = fehler
            self.e.dauer_s = round(time.monotonic() - t0, 1)
            return self.e
        basis = str(r.url)
        host = urlsplit(basis).netloc.lower().removeprefix("www.")
        self.e.start_url = basis
        if not gleiche_domain(host, self.e.domain):
            # Lieber kein Beleg als ein falscher: eine fremde Zieldomain kann ein anderes Unternehmen sein.
            self.e.status, self.e.grund = "weiterleitung_fremd", f"leitet weiter auf {host}"
            self.e.dauer_s = round(time.monotonic() - t0, 1)
            return self.e
        # robots.txt des tatsächlichen Hosts (nach Weiterleitung)
        teile = urlsplit(basis)
        rr, _ = await self._hole(f"{teile.scheme}://{teile.netloc}/robots.txt")
        if rr is not None and rr.status_code == 200 and len(rr.content) < 500_000:
            self.robots = Protego.parse(rr.text)
            verz = self.robots.crawl_delay(ROBOTS_NAME)
            if verz:
                self.abstand = max(MIN_ABSTAND_S, min(float(verz), 30.0))
        if not self._erlaubt(basis):
            self.e.status, self.e.grund = "robots_gesperrt", "robots.txt sperrt die Startseite"
            self.e.dauer_s = round(time.monotonic() - t0, 1)
            return self.e
        if r.status_code >= 400:
            self.e.status, self.e.grund = "http_fehler", f"HTTP {r.status_code}"
            self.e.dauer_s = round(time.monotonic() - t0, 1)
            return self.e
        html = r.text[:MAX_BYTES]
        if tdm_vorbehalt({k.lower(): v for k, v in r.headers.items()}, html):
            self.e.status, self.e.grund = "tdm_vorbehalt", "Nutzungsvorbehalt (tdm-reservation)"
            self.e.dauer_s = round(time.monotonic() - t0, 1)
            return self.e
        warteschlange = [(basis, html, r.status_code)]
        for url in links_waehlen(basis, html, host, MAX_SEITEN - 1):
            if not self._erlaubt(url):
                self.e.seiten.append({"url": url, "status": "robots_gesperrt"})
                continue
            rs, f = await self._hole(url)
            if rs is None:
                self.e.seiten.append({"url": url, "status": f[0]})
                continue
            ctype = rs.headers.get("content-type", "")
            if rs.status_code >= 400 or "html" not in ctype:
                self.e.seiten.append({"url": str(rs.url), "status": f"HTTP {rs.status_code}" if rs.status_code >= 400 else "kein_html"})
                continue
            h = rs.text[:MAX_BYTES]
            if tdm_vorbehalt({k.lower(): v for k, v in rs.headers.items()}, h):
                self.e.seiten.append({"url": str(rs.url), "status": "tdm_vorbehalt"})
                continue
            warteschlange.append((str(rs.url), h, rs.status_code))
        ordner = self.roh / self.e.domain
        ordner.mkdir(parents=True, exist_ok=True)
        gesamt = 0
        schon: set[str] = set()   # Navigation und Fußzeilen wiederholen sich auf jeder Seite: je Domain nur einmal
        for url, h, status in warteschlange:
            text = trafilatura.extract(h, url=url, include_comments=False, include_tables=True, favor_recall=True) or ""
            text = bereinige(text)
            ps = [p for p in passagen(text) if p not in schon]
            schon.update(ps)
            gesamt += sum(len(p) for p in ps)
            sha = hashlib.sha256(h.encode("utf-8", "replace")).hexdigest()
            (ordner / f"{sha[:16]}.txt").write_text(text, encoding="utf-8")
            self.e.seiten.append({
                "url": url, "status": "ok", "http": status, "sha256": sha,
                "abruf_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "zeichen": len(text), "passagen": ps,
            })
        self.e.status = "ok" if gesamt >= 200 else "kein_text"
        if self.e.status == "kein_text":
            self.e.grund = "weniger als 200 Zeichen lesbarer Text (z. B. nur JavaScript)"
        self.e.dauer_s = round(time.monotonic() - t0, 1)
        return self.e


async def alle(eintraege: list[dict], roh: Path, fertig: dict[str, dict], parallel: int, ausgabe: Path):
    import httpx

    sem = asyncio.Semaphore(parallel)
    limits = httpx.Limits(max_connections=parallel * 2, max_keepalive_connections=parallel)
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.5"},
                                 timeout=ZEITLIMIT_S, follow_redirects=True, limits=limits) as client:
        async def eins(e):
            async with sem:
                try:
                    erg = await Domainlauf(client, e, roh).lauf()
                except Exception as ex:  # ein Fehler einer Domain hält den Lauf nicht an
                    erg = DomainErgebnis(e["id"], e["website"], "nicht_erreichbar", f"{type(ex).__name__}: {str(ex)[:120]}")
                with open(ausgabe, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(erg.__dict__, ensure_ascii=False) + "\n")
                return erg

        return await asyncio.gather(*(eins(e) for e in eintraege if e["website"] not in fertig))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--unternehmen", type=Path, required=True)
    ap.add_argument("--roh", type=Path, required=True, help="Ordner für Volltexte (nicht ins Repository)")
    ap.add_argument("--aus", type=Path, required=True, help="JSONL je Domain (Status, Seiten, Passagen)")
    ap.add_argument("--parallel", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)
    with open(a.unternehmen, encoding="utf-8") as fh:
        eintraege = [r for r in csv.DictReader(fh) if r.get("website")]
    # eine Domain nur einmal (mehrere Firmen können dieselbe Website nennen)
    eindeutig = {}
    for r in eintraege:
        eindeutig.setdefault(r["website"], r)
    eintraege = list(eindeutig.values())[: a.limit or None]
    fertig = {}
    if a.aus.exists():
        for line in a.aus.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            fertig[d["domain"]] = d
    t0 = time.monotonic()
    neu = asyncio.run(alle(eintraege, a.roh, fertig, a.parallel, a.aus))
    stats: dict[str, int] = {}
    for line in a.aus.read_text(encoding="utf-8").splitlines():
        s = json.loads(line)["status"]
        stats[s] = stats.get(s, 0) + 1
    print(json.dumps({"domains": len(eintraege), "neu": len(neu), "dauer_s": round(time.monotonic() - t0, 1),
                      "status": stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
