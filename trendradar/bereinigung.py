"""Kontaktdaten entfernen, bevor Text gespeichert, weiterverarbeitet, angezeigt oder an ein Modell gegeben wird.

Entfernt E-Mail-Adressen, Telefon- und Faxnummern (mit Hinweis wie „Tel.-Nr.“, „Durchwahl“, internationale Schreibweise,
Vorwahl mit Schrägstrich) und Namen nach einer Anrede („Frau Erika …“, „Herrn Dr. …“). Bewusst vorsichtig: CPV-Codes,
Postleitzahlen, Datumsangaben, Normen (DIN EN 14411) und Beträge bleiben stehen. Namen ohne Anrede erkennt dieser Schritt
nicht; deshalb werden Impressum, Kontakt und Karriere-Seiten gar nicht erst gelesen.
"""

from __future__ import annotations

import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TELEFON_KONTEXT = (r"(?:telefon(?:nummer)?|tel\.?\s?-?\s?nr\.?|tel\.?|fon|fax|telefax|durchwahl|mobil|handy|phone|hotline)"
                    r"\s*[:.]?\s*")
_NUMMER = r"(?:\+\d{2}|00\d{2}|\(0\d{2,5}\)|0\d{2,5})[\s/().-]{0,3}\d[\d\s/().-]{4,16}\d"
TELEFON_MIT_KONTEXT = re.compile(_TELEFON_KONTEXT + _NUMMER, re.IGNORECASE)
TELEFON_INTERNATIONAL = re.compile(r"(?<![\w/])(?:\+49|0049)[\s/().-]{0,3}\d[\d\s/().-]{5,16}\d(?![\w/])")
TELEFON_SCHRAEG = re.compile(r"(?<![\w/.-])0\d{2,5}\s?/\s?\d{3,}(?:[\s-]?\d+)*")
ANREDE_NAME = re.compile(r"\b(?:Frau|Herrn?|Hr\.|Fr\.)\s+(?:(?:Dr|Prof)\.\s+)*[A-ZÄÖÜ][a-zäöüß]+(?:[\s-][A-ZÄÖÜ][a-zäöüß]+)?")


def bereinige(text: str) -> str:
    text = EMAIL.sub("[E-Mail entfernt]", text)
    text = TELEFON_MIT_KONTEXT.sub("[Telefon entfernt]", text)
    text = TELEFON_INTERNATIONAL.sub("[Telefon entfernt]", text)
    text = TELEFON_SCHRAEG.sub("[Telefon entfernt]", text)
    text = ANREDE_NAME.sub("[Name entfernt]", text)
    return text
