"""Sprachmodell lokal über Ollama: ein Aufruf, ein Ort für Standardmodell und Optionen.

Standard seit 11.11.2026 ist das Modell, das die Messung gewählt hat (``trendradar.modellwahl sprachmodell``,
Messprotokoll vom 11.11.2026); austauschbar über ``SPRACHMODELL`` und ``OLLAMA_URL``, etwa gegen einen Modelldienst
mit Verarbeitung in der EU. Denkmodus aus: gefragt sind kurze, prüfbare Antworten, keine langen Herleitungen.
"""

from __future__ import annotations

import os
import time

STANDARD_SPRACHMODELL = "qwen3.8:27b"


def modell() -> str:
    return os.environ.get("SPRACHMODELL", STANDARD_SPRACHMODELL)


def url() -> str:
    return os.environ.get("OLLAMA_URL", "http://ollama:11434").rstrip("/")


def chat(client, modellname: str, system: str, nutzer: str, optionen: dict, format_: dict | None = None,
         basis: str | None = None) -> tuple[str, float, dict]:
    """Antworttext, Sekunden und Zähler von Ollama (Tokens Eingabe/Ausgabe, Dauer)."""
    body = {"model": modellname, "stream": False, "think": False, "keep_alive": "30m",
            "options": {"temperature": 0, "seed": 1, **optionen},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": nutzer}]}
    if format_ is not None:
        body["format"] = format_
    t0 = time.perf_counter()
    r = client.post(f"{basis or url()}/api/chat", json=body, timeout=900)
    if r.status_code == 400 and "think" in r.text:      # Modelle ohne Denkmodus lehnen den Schalter ab
        body.pop("think")
        r = client.post(f"{basis or url()}/api/chat", json=body, timeout=900)
    r.raise_for_status()
    d = r.json()
    zaehler = {k: d.get(k) for k in ("prompt_eval_count", "eval_count", "prompt_eval_duration", "eval_duration")}
    return d["message"]["content"], time.perf_counter() - t0, zaehler


def digest(client, modellname: str, basis: str | None = None) -> str:
    for m in client.get(f"{basis or url()}/api/tags").json().get("models", []):
        if m.get("name") == modellname or m.get("model") == modellname:
            return m.get("digest", "")[:12]
    return ""
