"""spaCy NER detector with fail-loud loading.

If the profile sets ``enable_spacy=true`` and ``fail_if_spacy_unavailable=true``
and the model can't be loaded (spaCy missing or model not installed), this
module raises :class:`SpacyUnavailableError`. The caller (pipeline) decides
whether to abort the run — for ``strict_test_data`` we abort.
"""
from __future__ import annotations

import logging
from typing import Optional
from sanitiser.state import Detection, Span

logger = logging.getLogger(__name__)


class SpacyUnavailableError(RuntimeError):
    """Raised when spaCy or its model is required but not available."""


_LABEL_MAP = {
    "PERSON": "PERSON", "PER": "PERSON",
    "ORG": "ORG",
    "GPE": "LOCATION", "LOC": "LOCATION", "FAC": "LOCATION",
    "DATE": "GENERIC_DATE",
    "MONEY": "MONEY",
    "NORP": "GROUP",
}


class SpacyDetector:
    def __init__(self, model: str = "en_core_web_sm", *, fail_if_unavailable: bool = True) -> None:
        self.model = model
        self.fail = fail_if_unavailable
        self._nlp = None

    def load(self) -> None:
        try:
            import spacy
        except ImportError as e:
            if self.fail:
                raise SpacyUnavailableError(
                    "spaCy is not installed. `pip install spacy` and download the model."
                ) from e
            logger.warning("spaCy not installed; NER detector disabled.")
            return
        try:
            self._nlp = spacy.load(self.model)
        except OSError as e:
            if self.fail:
                raise SpacyUnavailableError(
                    "spaCy model " + repr(self.model) + " not found. Install with: "
                    "python -m spacy download " + self.model
                ) from e
            logger.warning("spaCy model %s missing; NER detector disabled.", self.model)
            self._nlp = None

    def is_available(self) -> bool:
        return self._nlp is not None

    def detect(self, text: str, *, page: int | None = None, chunk: int | None = None,
               offset: int = 0) -> list[Detection]:
        if self._nlp is None:
            return []
        max_len = getattr(self._nlp, "max_length", 1_000_000)
        snippet = text[:max_len]
        doc = self._nlp(snippet)
        out: list[Detection] = []
        for ent in doc.ents:
            mapped = _LABEL_MAP.get(ent.label_)
            if mapped is None:
                continue
            out.append(Detection(
                text=ent.text, entity_type=mapped, confidence=0.8,
                detector=f"spacy.{ent.label_}",
                span=Span(text=ent.text, start=offset + ent.start_char, end=offset + ent.end_char,
                          page=page, chunk=chunk),
                metadata={"spacy_label": ent.label_},
            ))
        return out
