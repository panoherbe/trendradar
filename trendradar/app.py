"""Oberfläche und API des Trend-Radars.

Oberfläche für Mitarbeitende ohne Technikhintergrund: Zukunftsfelder → Thema mit den passenden Unternehmen und je einem
Zitat → Unternehmensprofil → Partner finden; dazu Kreise, Liniennetz und „So funktioniert’s“. API unter /api/v1 (OpenAPI 3.1, Swagger UI unter /api/v1/docs): Seiten zu 50 oder 200
Einträgen, Fehler als JSON mit Korrelations-ID, 60 Anfragen je Minute und Client (HTTP 429 mit Retry-After),
Protokoll ohne Inhaltsdaten.
"""

from __future__ import annotations

import csv
import io
import math
import os
import re
import time
import uuid
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, abfragen as q, karte
from .db import verbindung
from .taxonomie import STANDARD, lade

HIER = Path(__file__).parent
RATE = int(os.environ.get("RATE_LIMIT_PRO_MINUTE", "60"))
KREIS_CODES = tuple(q.KREISE)

app = FastAPI(
    title="Trend-Radar API", version=__version__,
    description="Megatrends und Subtrends, bewertet gegen die Fähigkeiten regionaler Unternehmen, jede Bewertung "
                "mit Belegen (Quelle und wörtliches Zitat). Prototyp auf offenen Daten.",
    docs_url="/api/v1/docs", openapi_url="/api/v1/openapi.json", redoc_url=None,
)
app.mount("/static", StaticFiles(directory=HIER / "static"), name="static")
vorlagen = Jinja2Templates(directory=HIER / "templates")


# ---------- Formatierung (deutsch) ----------

def _zahl(x, stellen: int = 0):
    if x is None:
        return "–"
    s = f"{x:,.{stellen}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def _datum(s):
    if not s:
        return ""
    s = str(s)[:10]
    try:
        d = date.fromisoformat(s)
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return s


def _prozent(x, stellen: int = 0):
    return "–" if x is None else _zahl(100 * x, stellen) + " %"


def _erster_satz(text: str | None) -> str:
    """Erster Satz einer Beschreibung (für Kacheln); Abkürzungen wie „z. B.“ trennen nicht."""
    if not text:
        return ""
    kurz = ("z. B.", "u. a.", "d. h.", "z.B.", "u.a.", "bzw.", "ca.", "inkl.", "etc.", "Nr.")
    geschuetzt = text
    for i, k in enumerate(kurz):
        geschuetzt = geschuetzt.replace(k, f"\x00{i}\x01")
    m = re.search(r"[.!?](\s|$)", geschuetzt)
    satz = geschuetzt[: m.start() + 1] if m else geschuetzt
    for i, k in enumerate(kurz):
        satz = satz.replace(f"\x00{i}\x01", k)
    return satz


vorlagen.env.filters.update(zahl=_zahl, datum=_datum, prozent=_prozent, kreis=lambda k: q.KREISE.get(k, k or ""),
                            erster_satz=_erster_satz)
vorlagen.env.globals.update(KREISE=q.KREISE, version=__version__)


# ---------- Taxonomie und Karte (einmal je Taxonomie-Stand) ----------

_karte_cache: dict = {}


def taxonomie_und_layout():
    tax = lade(STANDARD)
    if tax.hash not in _karte_cache:
        _karte_cache[tax.hash] = karte.layout_gespeichert(tax, Path(STANDARD).with_name("karte.json"))
    return tax, _karte_cache[tax.hash]


# ---------- Middleware: Korrelations-ID, Rate-Limit, Protokoll ----------

_fenster: dict[str, deque] = defaultdict(deque)


def _client(request: Request) -> str:
    return request.headers.get("x-api-key") or (request.client.host if request.client else "unbekannt")


@app.middleware("http")
async def protokoll(request: Request, call_next):
    kid = request.headers.get("x-korrelations-id") or uuid.uuid4().hex[:16]
    request.state.kid = kid
    t0 = time.perf_counter()
    ist_api = request.url.path.startswith("/api/v1") and not request.url.path.startswith(("/api/v1/docs", "/api/v1/openapi"))
    if ist_api and RATE > 0:
        f, jetzt = _fenster[_client(request)], time.monotonic()
        while f and jetzt - f[0] > 60:
            f.popleft()
        if len(f) >= RATE:
            warte = int(60 - (jetzt - f[0])) + 1
            antwort = JSONResponse(status_code=429, headers={"Retry-After": str(warte), "X-Korrelations-ID": kid},
                                   content={"fehler": {"code": 429, "meldung": f"Höchstens {RATE} Anfragen je Minute",
                                                       "korrelations_id": kid}})
            _schreibe_protokoll(request, 429, t0, kid)
            return antwort
        f.append(jetzt)
    antwort = await call_next(request)
    antwort.headers["X-Korrelations-ID"] = kid
    if ist_api:
        _schreibe_protokoll(request, antwort.status_code, t0, kid)
    return antwort


def _schreibe_protokoll(request: Request, status: int, t0: float, kid: str) -> None:
    if os.environ.get("API_PROTOKOLL", "1") != "1":
        return
    try:
        with verbindung() as c:
            c.execute("INSERT INTO api_protokoll (client, methode, pfad, status, dauer_ms, korrelation) "
                      "VALUES (%s,%s,%s,%s,%s,%s)",
                      (_client(request)[:64], request.method, request.url.path[:200], status,
                       round((time.perf_counter() - t0) * 1000, 2), kid))
            c.execute("DELETE FROM api_protokoll WHERE zeit < now() - interval '90 days'")
    except Exception:
        pass  # das Protokoll darf die Antwort nie verhindern


def _fehler(request: Request, code: int, meldung: str):
    return JSONResponse(status_code=code, content={"fehler": {"code": code, "meldung": meldung,
                                                              "korrelations_id": getattr(request.state, "kid", "")}})


@app.exception_handler(HTTPException)
async def http_fehler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return _fehler(request, exc.status_code, str(exc.detail))
    return vorlagen.TemplateResponse(request, "fehler.html", {"code": exc.status_code, "meldung": exc.detail},
                                     status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def eingabe_fehler(request: Request, exc: RequestValidationError):
    teile = "; ".join(f"{'.'.join(str(x) for x in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    if request.url.path.startswith("/api/"):
        return _fehler(request, 422, f"Ungültige Angabe – {teile}")
    return vorlagen.TemplateResponse(request, "fehler.html", {"code": 422, "meldung": teile}, status_code=422)


# ---------- Oberfläche ----------

def _score_filter(wert: str | None) -> int | None:
    """Formulare senden ein leeres Feld als „min_score=“: leer heißt kein Filter, sonst 1–10."""
    if wert is None or wert.strip() == "":
        return None
    if not wert.strip().isdigit() or not 1 <= int(wert) <= 10:
        raise HTTPException(422, "Score ab: erlaubt sind 1 bis 10")
    return int(wert)


def _seite(request: Request, name: str, **ctx):
    return vorlagen.TemplateResponse(request, name, ctx)


def _radar(tax, ueb: dict) -> list[dict]:
    """Punkte des Radarbilds auf der Startseite: je Megatrend ein Punkt, größer und näher an der Mitte bei mehr
    Unternehmen mit Nachweis."""
    groesste = max((u["n_beleg"] for u in ueb.values()), default=1) or 1
    punkte = []
    ms = list(tax.megatrends.values())
    for i, m in enumerate(ms):
        n = ueb.get(m.id, {}).get("n_beleg", 0)
        anteil = n / groesste
        winkel = -math.pi / 2 + 2 * math.pi * i / len(ms)
        abstand = 172 - 108 * anteil
        r = 9 + 15 * math.sqrt(anteil)
        c, s = math.cos(winkel), math.sin(winkel)
        punkte.append({"id": m.id, "name": m.name, "kurz": m.name.split(" &")[0].split(",")[0], "farbe": m.farbe, "n": n,
                       "r": round(r, 1), "x": round(200 + abstand * c, 1), "y": round(200 + abstand * s, 1),
                       "tx": round(200 + (abstand + r + 7) * c, 1), "ty": round(200 + (abstand + r + 7) * s + 4.5, 1),
                       "anker": "start" if c > .25 else ("end" if c < -.25 else "middle")})
    return punkte


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def start(request: Request):
    tax, _ = taxonomie_und_layout()
    with verbindung() as c:
        ueb = q.megatrend_uebersicht(c)
        st = q.stand(c)
    return _seite(request, "start.html", stand=st, tax=tax, ueb=ueb, radar=_radar(tax, ueb))


@app.get("/karte", response_class=HTMLResponse, include_in_schema=False)
def karte_seite(request: Request):
    tax, lay = taxonomie_und_layout()
    with verbindung() as c:
        kz = q.subtrend_kennzahlen(c)
        st = q.stand(c)
    return _seite(request, "karte.html", karte_svg=karte.svg(tax, lay, kz), stand=st, tax=tax, kz=kz)


@app.get("/megatrend/{mid}", response_class=HTMLResponse, include_in_schema=False)
def megatrend_seite(request: Request, mid: str, regel: Literal["max", "top3"] = "max", kreis: str | None = None,
                    seite: int = Query(1, ge=1)):
    tax, _ = taxonomie_und_layout()
    if mid not in tax.megatrends:
        raise HTTPException(404, "Megatrend nicht gefunden")
    with verbindung() as c:
        rl = q.megatrend_rangliste(c, mid, regel, kreis or None, seite, 10)
        kz = q.subtrend_kennzahlen(c)
        top = q.top_namen(c, tax.megatrends[mid].subtrends)
    return _seite(request, "megatrend.html", m=tax.megatrends[mid], tax=tax, rl=rl, kz=kz, top=top, regel=regel,
                  kreis=kreis or "")


@app.get("/subtrend/{sid}", response_class=HTMLResponse, include_in_schema=False)
def subtrend_seite(request: Request, sid: str, kreis: str | None = None, min_score: str | None = None,
                   status: str | None = None, alle: bool = False, seite: int = Query(1, ge=1), pro_seite: int = 10):
    """Ohne Angabe zeigt die Seite nur Unternehmen mit Nachweis (Stufe 6–10); ``alle=1`` zeigt auch Hinweise."""
    offen = _score_filter(min_score)
    mindest = offen if min_score is not None else (None if alle else q.BELEG_AB)
    with verbindung() as c:
        s = q.subtrend(c, sid)
        if not s:
            raise HTTPException(404, "Subtrend nicht gefunden")
        rl = q.rangliste(c, sid, kreis or None, mindest, status or None, seite, pro_seite if pro_seite in (10, 50, 200) else 10)
    return _seite(request, "subtrend.html", s=s, rl=rl, kreis=kreis or "", alle=alle, min_score_offen=min_score)


@app.get("/unternehmen", response_class=HTMLResponse, include_in_schema=False)
def unternehmen_liste(request: Request, q_: str | None = Query(None, alias="q"), kreis: str | None = None,
                      subtrend: str | None = None, min_score: str | None = None,
                      seite: int = Query(1, ge=1)):
    min_score = _score_filter(min_score)
    tax, _ = taxonomie_und_layout()
    with verbindung() as c:
        erg = q.suche(c, q_ or None, kreis or None, subtrend or None, min_score, None, seite, 20)
    return _seite(request, "unternehmen_liste.html", erg=erg, tax=tax, q=q_ or "", kreis=kreis or "",
                  subtrend=subtrend or "", min_score=min_score)


@app.get("/unternehmen/{uid}", response_class=HTMLResponse, include_in_schema=False)
def unternehmen_seite(request: Request, uid: str):
    with verbindung() as c:
        u = q.profil(c, uid)
    if not u:
        raise HTTPException(404, "Unternehmen nicht gefunden")
    return _seite(request, "unternehmen.html", u=u)


@app.get("/kooperation/{sid}", response_class=HTMLResponse, include_in_schema=False)
def kooperation_seite(request: Request, sid: str, kreis: str | None = None):
    with verbindung() as c:
        s = q.subtrend(c, sid)
        if not s:
            raise HTTPException(404, "Subtrend nicht gefunden")
        k = q.kooperation(c, sid, kreis or None)
    return _seite(request, "kooperation.html", s=s, k=k, kreis=kreis or "")


@app.get("/region", response_class=HTMLResponse, include_in_schema=False)
def region_seite(request: Request):
    tax, _ = taxonomie_und_layout()
    with verbindung() as c:
        r = q.region(c)
    return _seite(request, "region.html", r=r, tax=tax)


@app.get("/methodik", response_class=HTMLResponse, include_in_schema=False)
def methodik_seite(request: Request):
    tax, _ = taxonomie_und_layout()
    with verbindung() as c:
        st = q.stand(c)
    return _seite(request, "methodik.html", stand=st, tax=tax)


# ---------- API /api/v1 ----------

def Seitengroesse(pro_seite: int = Query(50, description="Einträge je Seite: 50 oder 200")) -> int:
    if pro_seite not in q.SEITENGROESSEN:
        raise HTTPException(422, "pro_seite: erlaubt sind 50 oder 200")
    return pro_seite


@app.get("/api/v1/stand", tags=["Stand"], summary="Datenstand, Taxonomie-Version, Modell, Regelversion")
def api_stand():
    with verbindung() as c:
        return q.stand(c)


@app.get("/api/v1/megatrends", tags=["Taxonomie"], summary="Megatrends mit ihren Subtrends (Linien und Stationen)")
def api_megatrends():
    with verbindung() as c:
        return q.megatrends(c)


@app.get("/api/v1/subtrends/{sid}", tags=["Taxonomie"],
         summary="Subtrend mit Teilfähigkeiten und Kennzahlen (n, Mittelwert, Quote „nicht bewertbar“, je Kreis)")
def api_subtrend(sid: str):
    with verbindung() as c:
        s = q.subtrend(c, sid)
    if not s:
        raise HTTPException(404, "Subtrend nicht gefunden")
    return s


@app.get("/api/v1/subtrends/{sid}/unternehmen", tags=["Bewertung"],
         summary="Rangliste der Unternehmen für einen Subtrend, je Eintrag der stärkste Beleg")
def api_rangliste(sid: str, kreis: Literal[KREIS_CODES] | None = None,  # type: ignore[valid-type]
                  min_score: int | None = Query(None, ge=1, le=10),
                  status: Literal["bewertet", "nicht_bewertbar"] | None = None,
                  seite: int = Query(1, ge=1), pro_seite: int = Depends(Seitengroesse)):
    with verbindung() as c:
        if not q.subtrend(c, sid):
            raise HTTPException(404, "Subtrend nicht gefunden")
        return q.rangliste(c, sid, kreis, min_score, status, seite, pro_seite)


@app.get("/api/v1/megatrends/{mid}/unternehmen", tags=["Bewertung"],
         summary="Rangliste für einen Megatrend; Regel umschaltbar: höchster Subtrend-Wert oder Mittel der drei höchsten")
def api_megatrend_rangliste(mid: str, regel: Literal["max", "top3"] = "max",
                            kreis: Literal[KREIS_CODES] | None = None,  # type: ignore[valid-type]
                            seite: int = Query(1, ge=1), pro_seite: int = Depends(Seitengroesse)):
    tax, _ = taxonomie_und_layout()
    if mid not in tax.megatrends:
        raise HTTPException(404, "Megatrend nicht gefunden")
    with verbindung() as c:
        return q.megatrend_rangliste(c, mid, regel, kreis, seite, pro_seite)


@app.get("/api/v1/unternehmen", tags=["Unternehmen"], summary="Unternehmen suchen und filtern")
def api_unternehmen(suche: str | None = Query(None, max_length=100), kreis: Literal[KREIS_CODES] | None = None,  # type: ignore[valid-type]
                    subtrend: str | None = None, min_score: int | None = Query(None, ge=1, le=10),
                    status: Literal["bewertet", "nicht_bewertbar"] | None = None,
                    seite: int = Query(1, ge=1), pro_seite: int = Depends(Seitengroesse)):
    with verbindung() as c:
        return q.suche(c, suche, kreis, subtrend, min_score, status, seite, pro_seite)


@app.get("/api/v1/unternehmen/{uid}", tags=["Unternehmen"],
         summary="Unternehmensprofil: alle Megatrend- und Subtrend-Werte mit Belegen und Quellen")
def api_profil(uid: str):
    with verbindung() as c:
        u = q.profil(c, uid)
    if not u:
        raise HTTPException(404, "Unternehmen nicht gefunden")
    return u


@app.get("/api/v1/unternehmen/{uid}/passende-trends", tags=["Unternehmen"],
         summary="Beispielabfrage: passende Trends für ein Unternehmen, mit Quellen")
def api_passende_trends(uid: str, n: int = Query(5, ge=1, le=20)):
    with verbindung() as c:
        if not q.profil(c, uid):
            raise HTTPException(404, "Unternehmen nicht gefunden")
        return q.passende_trends(c, uid, n)


@app.get("/api/v1/kooperation/{sid}", tags=["Kooperation"],
         summary="Kooperationsfinder: Gruppen, die alle Teilfähigkeiten eines Subtrends belegen, und Lücken")
def api_kooperation(sid: str, kreis: Literal[KREIS_CODES] | None = None):  # type: ignore[valid-type]
    with verbindung() as c:
        if not q.subtrend(c, sid):
            raise HTTPException(404, "Subtrend nicht gefunden")
        return q.kooperation(c, sid, kreis)


EXPORT_SPALTEN = ["id", "name", "ort", "kreis_nuts", "score", "status", "stufe", "n_belege", "k_faehigkeiten",
                  "faehigkeit", "zitat", "url"]


@app.get("/api/v1/subtrends/{sid}/export", tags=["Export"], summary="Rangliste als CSV oder XLSX")
def api_export(sid: str, format: Literal["csv", "xlsx"] = "csv", kreis: Literal[KREIS_CODES] | None = None,  # type: ignore[valid-type]
               min_score: int | None = Query(None, ge=1, le=10)):
    with verbindung() as c:
        s = q.subtrend(c, sid)
        if not s:
            raise HTTPException(404, "Subtrend nicht gefunden")
        zeilen = []
        seite = 1
        while True:
            rl = q.rangliste(c, sid, kreis, min_score, None, seite, 200)
            zeilen += rl["eintraege"]
            if seite * 200 >= rl["gesamt"]:
                break
            seite += 1
    name = f"trendradar_{sid}_{date.today().isoformat()}"
    if format == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, EXPORT_SPALTEN, extrasaction="ignore", delimiter=";")
        w.writeheader()
        w.writerows(zeilen)
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = sid
    ws.append(EXPORT_SPALTEN)
    for z in zeilen:
        ws.append([z.get(k) for k in EXPORT_SPALTEN])
    out = io.BytesIO()
    wb.save(out)
    return Response(out.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})


@app.get("/api", include_in_schema=False)
def api_wurzel():
    return RedirectResponse("/api/v1/docs")


@app.get("/gesund", include_in_schema=False)
def gesund():
    with verbindung() as c:
        n = c.execute("SELECT count(*) AS n FROM unternehmen").fetchone()["n"]
    return {"status": "ok", "unternehmen": n}
