"""Einbettungen: quelloffenes, mehrsprachiges Modell auf dem eigenen Rechner. Kein Text verlässt die Umgebung.

Modellnamen mit Doppelpunkt (``name:größe``, z. B. ``qwen3-embedding:4b``) laufen lokal über Ollama (``OLLAMA_URL``),
Namen ohne Doppelpunkt (z. B. ``BAAI/bge-m3``, Stand 04.11.2026) über sentence-transformers. Welches Modell Standard
ist, entscheidet eine Messung (``trendradar.modellwahl einbettung``, Messprotokoll). Jeder Vektor wird mit Modellname
und Text im Zwischenspeicher abgelegt; ein Modellwechsel heißt: ``--modell`` ändern und neu rechnen. Für Tests gibt es
``HashEinbetter`` ohne Modell.

Anfragen (Teilfähigkeiten) und Dokumente (Passagen) bekommen je Modell den Vorsatz, den der Hersteller empfiehlt.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import numpy as np

STANDARD_MODELL = "qwen3-embedding:4b"   # Modellwahl 11.11.2026: bestes Rangmaß der fünf Kandidaten

# Vorsätze nach den Modellkarten der Hersteller; Schlüssel ist der Modellname vor dem Doppelpunkt.
_AUFGABE = ("Given a description of a business capability, retrieve passages from public contracts or company "
            "websites showing that a company provides this capability")
VORSATZ = {
    "qwen3-embedding": {"anfrage": f"Instruct: {_AUFGABE}\nQuery: ", "dokument": ""},
    "embeddinggemma-2": {"anfrage": "task: search result | query: ", "dokument": "title: none | text: "},
}


def _schluessel(modell: str, text: str) -> str:
    return hashlib.sha256(f"{modell}\x00{text}".encode()).hexdigest()[:24]


def einbetter(modell: str = STANDARD_MODELL, cache: Path | None = None, url: str | None = None):
    """Ollama für ``name:größe``, sonst sentence-transformers."""
    if ":" in modell:
        return OllamaEinbetter(modell, cache, url)
    return Einbetter(modell, cache)


class _Zwischenspeicher:
    modell: str

    def _lade(self, cache: Path | None) -> None:
        self.cache = cache
        self._speicher: dict[str, np.ndarray] = {}
        self.neu_berechnet = 0
        if cache and cache.exists():
            z = np.load(cache, allow_pickle=False)
            self._speicher = dict(zip(z["schluessel"].tolist(), z["vektoren"]))

    def _vorsatz(self, art: str) -> str:
        return VORSATZ.get(self.modell.split(":")[0], {}).get(art, "")

    def kodiere(self, texte: list[str], stapel: int = 32, art: str = "dokument") -> np.ndarray:
        v = self._vorsatz(art)
        texte = [v + t for t in texte]
        keys = [_schluessel(self.modell, t) for t in texte]
        fehlend = sorted({(k, t) for k, t in zip(keys, texte) if k not in self._speicher}, key=lambda x: len(x[1]))
        if fehlend:
            vek = self._rechne([t for _, t in fehlend], stapel)
            for (k, _), vec in zip(fehlend, vek):
                self._speicher[k] = vec
            self.neu_berechnet += len(fehlend)
        return np.stack([self._speicher[k] for k in keys]) if keys else np.zeros((0, 1), np.float32)

    def speichere(self) -> None:
        if self.cache and self._speicher:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            ks = list(self._speicher)
            np.savez(self.cache, schluessel=np.array(ks), vektoren=np.stack([self._speicher[k] for k in ks]))


class Einbetter(_Zwischenspeicher):
    """sentence-transformers (z. B. ``BAAI/bge-m3``); braucht das Paket nur für Texte, die nicht im Speicher sind."""

    def __init__(self, modell: str = "BAAI/bge-m3", cache: Path | None = None, geraet: str | None = None):
        self.modell = modell
        self._st = None
        self._geraet = geraet
        self._lade(cache)

    def _rechne(self, texte: list[str], stapel: int) -> np.ndarray:
        if self._st is None:
            from sentence_transformers import SentenceTransformer   # nur für Modelle ohne Ollama

            self._st = SentenceTransformer(self.modell, device=self._geraet)
            if self._st.device.type == "cuda":
                self._st.half()
        return self._st.encode(texte, batch_size=stapel, normalize_embeddings=True, show_progress_bar=False,
                               convert_to_numpy=True).astype(np.float32)


class OllamaEinbetter(_Zwischenspeicher):
    """Einbettung über die Ollama-API (``/api/embed``), Vektoren normiert."""

    def __init__(self, modell: str = STANDARD_MODELL, cache: Path | None = None, url: str | None = None):
        self.modell = modell
        self.url = (url or os.environ.get("OLLAMA_URL", "http://ollama:11434")).rstrip("/")
        self._lade(cache)

    def _rechne(self, texte: list[str], stapel: int) -> np.ndarray:
        import httpx

        out = []
        with httpx.Client(timeout=900) as c:
            for i in range(0, len(texte), stapel):
                r = c.post(f"{self.url}/api/embed", json={"model": self.modell, "input": texte[i:i + stapel],
                                                         "truncate": True, "keep_alive": "30m"})
                r.raise_for_status()
                out += r.json()["embeddings"]
        v = np.asarray(out, np.float32)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.where(n == 0, 1, n)


class HashEinbetter:
    """Deterministischer Ersatz ohne Modell (für Tests): Wortstämme als Hash-Merkmale, normiert."""

    modell = "hash-test"
    neu_berechnet = 0

    def __init__(self, dim: int = 512):
        self.dim = dim

    def kodiere(self, texte: list[str], stapel: int = 64, art: str = "dokument") -> np.ndarray:
        out = np.zeros((len(texte), self.dim), np.float32)
        for i, t in enumerate(texte):
            for w in re.findall(r"[a-zäöüß]{4,}", t.lower()):
                out[i, int(hashlib.md5(w[:6].encode()).hexdigest(), 16) % self.dim] += 1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(n == 0, 1, n)

    def speichere(self) -> None:
        pass
