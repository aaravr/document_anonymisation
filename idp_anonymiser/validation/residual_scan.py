"""Run the regex recognisers over anonymised output to find residual high-confidence PII."""
from __future__ import annotations

from idp_anonymiser.agent.state import Detection
from idp_anonymiser.detection import regex_recognisers


def residual_scan(text: str, *, confidence_threshold: float = 0.85) -> list[Detection]:
    """Return any high-confidence detections that survived in the anonymised text.

    We deliberately skip free-form types like PERSON / ORG (because the
    replacements *are* synthetic names that look like names by construction).
    The point is to catch structured identifiers (LEI, IBAN, SWIFT, etc.) that
    were missed during planning.
    """
    detections = regex_recognisers.detect_all(text)
    return [d for d in detections if d.confidence >= confidence_threshold]
