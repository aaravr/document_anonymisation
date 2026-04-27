"""Optional Microsoft Presidio analyzer wrapper.

Presidio brings a wider catalogue of recognisers (credit cards, US/EU IDs, etc.).
It depends on spaCy under the hood. If neither library is available we degrade
silently — the regex recognisers cover the high-value KYC identifiers.
"""
from __future__ import annotations

import logging
from typing import Optional

from idp_anonymiser.agent.state import Detection, DocumentSpan

logger = logging.getLogger(__name__)


# Map Presidio entity codes to our canonical entity types.
_PRESIDIO_TO_ENTITY: dict[str, str] = {
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "URL": "URL",
    "DATE_TIME": "GENERIC_DATE",
    "LOCATION": "ADDRESS",
    "ORGANIZATION": "ORG",
    "IBAN_CODE": "IBAN",
    "US_SSN": "NATIONAL_ID",
    "US_PASSPORT": "PASSPORT",
    "UK_NHS": "NATIONAL_ID",
    "CREDIT_CARD": "BANK_ACCOUNT",
}


class PresidioDetector:
    """Lazy wrapper around Presidio's ``AnalyzerEngine``."""

    def __init__(self, language: str = "en") -> None:
        self.language = language
        self._analyzer = None
        self._loaded = False
        self._available: Optional[bool] = None

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return self._available is True
        self._loaded = True
        try:
            from presidio_analyzer import AnalyzerEngine

            self._analyzer = AnalyzerEngine()
            self._available = True
        except ImportError:
            logger.info("Presidio not installed; Presidio detector disabled.")
            self._available = False
        except Exception as exc:  # presidio depends on spaCy models at init
            logger.warning("Presidio init failed (%s); detector disabled.", exc)
            self._available = False
        return self._available

    def is_available(self) -> bool:
        return self._ensure_loaded()

    def detect(self, text: str) -> list[Detection]:
        if not self._ensure_loaded():
            return []
        results = self._analyzer.analyze(text=text, language=self.language)  # type: ignore[union-attr]
        out: list[Detection] = []
        for r in results:
            mapped = _PRESIDIO_TO_ENTITY.get(r.entity_type)
            if mapped is None:
                continue
            snippet = text[r.start : r.end]
            out.append(
                Detection(
                    text=snippet,
                    entity_type=mapped,
                    confidence=float(r.score),
                    detector=f"presidio.{r.entity_type}",
                    span=DocumentSpan(text=snippet, start=r.start, end=r.end),
                    metadata={"presidio_type": r.entity_type},
                )
            )
        return out
