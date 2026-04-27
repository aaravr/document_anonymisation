"""Optional spaCy-based NER detector.

spaCy adds recall for free-form names (PERSON), organisations (ORG), and
addresses (GPE/LOC). It is optional: if spaCy isn't installed or no model is
loadable, this detector returns an empty list and logs a single warning.
"""
from __future__ import annotations

import logging
from typing import Optional

from idp_anonymiser.agent.state import Detection, DocumentSpan

logger = logging.getLogger(__name__)


_SPACY_TO_ENTITY: dict[str, str] = {
    "PERSON": "PERSON",
    "PER": "PERSON",
    "ORG": "ORG",
    "GPE": "ADDRESS",
    "LOC": "ADDRESS",
    "FAC": "ADDRESS",
    "DATE": "GENERIC_DATE",
}


class SpacyDetector:
    """Wraps a spaCy ``Language`` pipeline.

    Construction is cheap (lazy); the model is loaded on first call. Use
    :meth:`is_available` to detect whether the model loaded successfully.
    """

    def __init__(self, model_name: str = "en_core_web_sm") -> None:
        self.model_name = model_name
        self._nlp = None
        self._loaded = False
        self._available: Optional[bool] = None

    def _ensure_loaded(self) -> bool:
        if self._loaded:
            return self._available is True
        self._loaded = True
        try:
            import spacy

            self._nlp = spacy.load(self.model_name)
            self._available = True
        except ImportError:
            logger.info("spaCy not installed; spaCy NER detector disabled.")
            self._available = False
        except OSError:
            logger.warning(
                "spaCy model %s not available; spaCy NER detector disabled. "
                "Install with: python -m spacy download %s",
                self.model_name,
                self.model_name,
            )
            self._available = False
        return self._available

    def is_available(self) -> bool:
        return self._ensure_loaded()

    def detect(self, text: str) -> list[Detection]:
        if not self._ensure_loaded():
            return []
        out: list[Detection] = []
        # spaCy's default max length is 1_000_000; chunk longer inputs.
        doc = self._nlp(text[:1_000_000])  # type: ignore[union-attr]
        for ent in doc.ents:
            mapped = _SPACY_TO_ENTITY.get(ent.label_)
            if mapped is None:
                continue
            out.append(
                Detection(
                    text=ent.text,
                    entity_type=mapped,
                    confidence=0.75,
                    detector=f"spacy.{ent.label_}",
                    span=DocumentSpan(
                        text=ent.text,
                        start=ent.start_char,
                        end=ent.end_char,
                    ),
                    metadata={"spacy_label": ent.label_},
                )
            )
        return out
