"""Unternehmen der Region und ihre Aufträge aus den offenen Bekanntmachungen öffentlicher Aufträge.

Quelle: tägliche OCDS-Exporte des Bekanntmachungsservice (oeffentlichevergabe.de, Lizenz CC0),
abrufbar mit ``GET https://oeffentlichevergabe.de/api/notice-exports?pubDay=JJJJ-MM-TT&format=ocds.zip``.

Gelesen wird nur, was eine Bekanntmachung über einen vergebenen Auftrag und seinen Auftragnehmer sagt: Name,
Ort, Postleitzahl, NUTS-Kreis, Website und **was geliefert oder gebaut wurde** (Titel, Los, Beschreibung, CPV-Codes).
Die Felder für Ansprechpersonen, E-Mail, Telefon, Steuer- und Umsatzsteuernummern werden nie gelesen; aus Titel, Los und
Beschreibung entfernt ``bereinige`` vor dem Speichern E-Mail-Adressen, Telefonnummern und Namen nach einer Anrede.

Aufnahme nur für juristische Personen und Handelsgesellschaften mit Rechtsform im Namen (GmbH, AG, KG, eG,
UG, SE …). Einzelunternehmen, GbR, OHG und e. K. tragen oft den Namen einer Person und bleiben draußen.

    python -m trendradar.quellen.auftraege --archiv <Ordner mit JJJJ-MM-TT/ocds.zip> --aus daten
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from ..bereinigung import bereinige

REGION = "DED4"  # NUTS 2: Chemnitz (Stadt), Erzgebirgskreis, Mittelsachsen, Vogtlandkreis, Zwickau
KREISE = {
    "DED41": "Chemnitz",
    "DED42": "Erzgebirgskreis",
    "DED43": "Mittelsachsen",
    "DED44": "Vogtlandkreis",
    "DED45": "Zwickau",
}
DETAIL_URL = "https://oeffentlichevergabe.de/ui/de/search/details?noticeId={}"

_RECHTSFORM = re.compile(
    r"(?<![\w])(gmbh|mbh|ggmbh|ag|se|kgaa|kg|e\.\s?g\.|eg|ug|genossenschaft|gesellschaft mit beschränkter haftung"
    r"|aktiengesellschaft|ltd\.?|s\.\s?a\.|b\.\s?v\.|sp\. z o\.o\.|s\.r\.o\.|a\.s\.)(?![\w])",
    re.IGNORECASE,
)
_AUSSCHLUSS = re.compile(r"(?<![\w])(gbr|ohg|e\.\s?k\.|e\.\s?kfm\.|e\.\s?kfr\.|inh\.|inhaber|e\.\s?v\.)(?![\w])", re.IGNORECASE)


def rechtsform(name: str) -> str | None:
    """Rechtsform aus dem Namen, oder None, wenn das Unternehmen nicht aufgenommen wird."""
    if _AUSSCHLUSS.search(name):
        return None
    treffer = _RECHTSFORM.findall(name)
    if not treffer:
        return None
    letzte = treffer[-1].lower().replace(" ", "")
    if "kg" in [t.lower() for t in treffer] and any(t.lower() in ("gmbh", "mbh") for t in treffer):
        return "GmbH & Co. KG"
    return {
        "gmbh": "GmbH", "mbh": "GmbH", "ggmbh": "gGmbH", "ag": "AG", "se": "SE", "kgaa": "KGaA", "kg": "KG",
        "e.g.": "eG", "eg": "eG", "ug": "UG", "genossenschaft": "eG",
        "gesellschaftmitbeschränkterhaftung": "GmbH", "aktiengesellschaft": "AG",
    }.get(letzte, letzte.upper())


def schluessel(name: str) -> str:
    """Deterministischer Schlüssel: Kleinschreibung, nur Buchstaben und Ziffern. Kein Fuzzy-Abgleich."""
    s = name.casefold().replace("&", "und").replace("+", "plus")
    s = re.sub(r"\bco\b\.?", "co", s)
    return re.sub(r"[^0-9a-zäöüß]", "", s)


def unternehmens_id(key: str) -> str:
    return "u" + hashlib.sha256(key.encode()).hexdigest()[:10]


def domain(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip().lower()
    if "@" in u or " " in u:
        return None
    u = re.sub(r"^[a-z]+://", "", u).split("/")[0].split(":")[0]
    u = u.removeprefix("www.")
    return u if re.fullmatch(r"[a-z0-9äöü.-]+\.[a-z]{2,}", u) else None


def _text(x) -> str:
    return re.sub(r"\s+", " ", (x or "")).strip()


def _lokal(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def gewinner_lose(xml: bytes) -> dict[str, set[str]]:
    """eForms: Organisation -> Lose, die sie gewonnen hat (LotResult 'selec-w' -> LotTender -> TenderingParty).

    Der OCDS-Export nennt Auftragnehmer nur als Partei, ohne Zuordnung zum Los. Dispatch über den lokalen
    Namen, nie über das Präfix: Präfixe wechseln innerhalb eines Pakets.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return {}

    def kind_id(el, name):
        return [g.text.strip() for c in el if _lokal(c.tag) == name for g in c if _lokal(g.tag) == "ID" and g.text]

    def eigene_id(el):
        return next((c.text.strip() for c in el if _lokal(c.tag) == "ID" and c.text), None)

    ergebnisse, angebote, parteien = [], {}, {}
    for el in root.iter():
        n = _lokal(el.tag)
        if n == "LotResult":
            code = next((c.text for c in el if _lokal(c.tag) == "TenderResultCode"), None)
            if code and code.strip() == "selec-w":
                ergebnisse.append((kind_id(el, "LotTender"), kind_id(el, "TenderLot")))
        elif n == "LotTender" and any(_lokal(c.tag) == "TenderingParty" for c in el):
            angebote[eigene_id(el)] = (kind_id(el, "TenderingParty"), kind_id(el, "TenderLot"))
        elif n == "TenderingParty" and any(_lokal(c.tag) == "Tenderer" for c in el):
            parteien[eigene_id(el)] = kind_id(el, "Tenderer")
    out: dict[str, set[str]] = {}
    for tender_ids, lot_ids in ergebnisse:
        for tid in tender_ids:
            tpas, tlots = angebote.get(tid, ([], []))
            for tpa in tpas:
                for org in parteien.get(tpa, []):
                    out.setdefault(org, set()).update(lot_ids or tlots)
    return out


def aus_release(rel: dict, lose_je_org: dict[str, set[str]] | None = None) -> list[tuple[dict, dict]]:
    """(Unternehmen, Beleg)-Paare aus einer OCDS-Release; leer, wenn kein Auftragnehmer aus der Region.

    ``lose_je_org`` stammt aus der eForms-Fassung derselben Bekanntmachung. Fehlt sie und gibt es mehr als einen
    Auftragnehmer, wird nichts zugeordnet: lieber kein Beleg als ein falscher.
    """
    parties = {p.get("id"): p for p in rel.get("parties", [])}
    regional = {
        pid: p for pid, p in parties.items()
        if "supplier" in (p.get("roles") or [])
        and str((p.get("address") or {}).get("region", "")).startswith(REGION)
    }
    if not regional:
        return []
    tender = rel.get("tender") or {}
    lots = {l.get("id"): l for l in tender.get("lots") or []}
    items = tender.get("items") or []
    notice_uuid = re.sub(r"-\d{2}$", "", str(rel.get("id", "")))
    datum = str(rel.get("date", ""))[:10]
    vergebene = [l for aw in rel.get("awards") or [] if aw.get("status") not in ("unsuccessful", "cancelled")
                 for l in (aw.get("relatedLots") or [])]
    if not vergebene and not rel.get("awards"):
        return []
    lieferanten = [pid for pid, p in parties.items() if "supplier" in (p.get("roles") or [])]
    out = []
    for sid, p in regional.items():
        if lose_je_org is not None and sid in lose_je_org:
            seine = [l for l in lose_je_org[sid] if not vergebene or l in vergebene]
        elif len(lieferanten) == 1:
            seine = vergebene or list(lots) or [None]
        else:
            continue
        if not seine:
            continue
        aw = next((a for a in rel.get("awards") or [] if set(a.get("relatedLots") or []) & set(seine)), {})
        name = _text(p.get("name") or (p.get("identifier") or {}).get("legalName"))
        rf = rechtsform(name)
        if not rf:
            continue
        addr = p.get("address") or {}
        nuts = str(addr.get("region", ""))[:5]
        url = (p.get("details") or {}).get("url") or (p.get("contactPoint") or {}).get("url")
        key = schluessel(name)
        firma = {
            "id": unternehmens_id(key), "schluessel": key, "name": name, "rechtsform": rf,
            "ort": _text(addr.get("locality")), "plz": _text(addr.get("postalCode")),
            "kreis_nuts": nuts if nuts in KREISE else REGION, "website": domain(url),
        }
        for lot_id in seine:
            lot = lots.get(lot_id) or {}
            cpv = []
            for it in items:
                if lot_id and it.get("relatedLot") not in (lot_id, None):
                    continue
                for c in [it.get("classification") or {}] + (it.get("additionalClassifications") or []):
                    if str(c.get("scheme", "")).upper() == "CPV" and c.get("id"):
                        cpv.append((str(c["id"])[:8], _text(c.get("description"))))
            cpv = list(dict.fromkeys(cpv))
            beleg = {
                "quelle": "auftrag",
                "url": DETAIL_URL.format(notice_uuid),
                "datum": str(aw.get("date") or datum)[:10],
                "titel": bereinige(_text(tender.get("title"))),
                "los": bereinige(_text(lot.get("title"))) if lot.get("title") != tender.get("title") else "",
                "text": bereinige(_text(lot.get("description") or tender.get("description"))[:1200]),
                "cpv": [c for c, _ in cpv],
                "cpv_text": [t for _, t in cpv],
                "vergabe_art": tender.get("mainProcurementCategory") or "",
            }
            beleg["id"] = "b" + hashlib.sha256(
                json.dumps([firma["id"], beleg["url"], lot_id], ensure_ascii=False).encode()).hexdigest()[:12]
            out.append((firma, beleg))
    return out


def lies_zip(pfad: str) -> list[tuple[dict, dict]]:
    """Ein Tagesexport: ocds.zip, daneben eforms.zip für die Zuordnung Auftragnehmer -> Los."""
    out = []
    ef_pfad = Path(pfad).with_name("eforms.zip")
    try:
        ef = zipfile.ZipFile(ef_pfad) if ef_pfad.exists() else None
    except zipfile.BadZipFile:
        ef = None
    ef_namen = {Path(n).stem: n for n in ef.namelist()} if ef else {}
    try:
        with zipfile.ZipFile(pfad) as z:
            for n in z.namelist():
                if not n.endswith(".json"):
                    continue
                try:
                    pkg = json.loads(z.read(n))
                except ValueError:
                    continue
                for rel in pkg.get("releases") or [pkg]:
                    parteien = rel.get("parties") or []
                    if not any("supplier" in (p.get("roles") or [])
                               and str((p.get("address") or {}).get("region", "")).startswith(REGION)
                               for p in parteien):
                        continue
                    stamm = ef_namen.get(Path(n).stem)
                    lose = gewinner_lose(ef.read(stamm)) if stamm else None
                    out.extend(aus_release(rel, lose))
    except zipfile.BadZipFile:
        pass
    finally:
        if ef:
            ef.close()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archiv", required=True, type=Path, help="Ordner mit JJJJ-MM-TT/ocds.zip")
    ap.add_argument("--aus", required=True, type=Path)
    ap.add_argument("--prozesse", type=int, default=8)
    ap.add_argument("--nachweis", type=Path, default=None, help="Kennzahlen des Laufs als JSON (auftraege.json)")
    a = ap.parse_args(argv)
    t0 = time.monotonic()
    zips = sorted(str(p) for p in a.archiv.glob("*/ocds.zip"))
    firmen: dict[str, dict] = {}
    belege: dict[str, dict] = {}
    with ProcessPoolExecutor(a.prozesse) as ex:
        for paare in ex.map(lies_zip, zips, chunksize=4):
            for f, b in paare:
                alt = firmen.get(f["id"])
                if alt is None:
                    firmen[f["id"]] = {**f, "auftraege": 0}
                    alt = firmen[f["id"]]
                alt["website"] = alt["website"] or f["website"]
                # Versionen derselben Bekanntmachung zählen einmal: gleiche Beleg-ID überschreibt.
                b["unternehmen_id"] = f["id"]
                belege[b["id"]] = b
    for b in belege.values():
        firmen[b["unternehmen_id"]]["auftraege"] += 1
    a.aus.mkdir(parents=True, exist_ok=True)
    felder = ["id", "name", "rechtsform", "ort", "plz", "kreis_nuts", "website", "auftraege"]
    with open(a.aus / "unternehmen.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, felder, extrasaction="ignore")
        w.writeheader()
        for f in sorted(firmen.values(), key=lambda f: f["name"].casefold()):
            w.writerow(f)
    with open(a.aus / "belege_auftraege.jsonl", "w", encoding="utf-8") as fh:
        for b in sorted(belege.values(), key=lambda b: (b["unternehmen_id"], b["datum"], b["id"])):
            fh.write(json.dumps(b, ensure_ascii=False) + "\n")
    erg = {"zeitpunkt_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "tagesexporte": len(zips), "erster_tag": Path(zips[0]).parent.name if zips else None,
           "letzter_tag": Path(zips[-1]).parent.name if zips else None, "unternehmen": len(firmen),
           "belege": len(belege), "mit_website": sum(1 for f in firmen.values() if f["website"]),
           "websites_eindeutig": len({f["website"] for f in firmen.values() if f["website"]}),
           "prozesse": a.prozesse, "dauer_s": round(time.monotonic() - t0, 1)}
    if a.nachweis:
        a.nachweis.parent.mkdir(parents=True, exist_ok=True)
        a.nachweis.write_text(json.dumps(erg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(erg, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
