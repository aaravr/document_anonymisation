"""Regex recognisers for structured PII / identifiers.

Same set as the IDP package but kept self-contained so this tool can be
extracted independently. All recognisers operate on a single chunk of text.
"""
from __future__ import annotations

import re
from sanitiser.state import Detection, Span


EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
URL_RE = re.compile(r"\b(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@\!$&'()*+,;=%]+", re.IGNORECASE)
UK_POSTCODE_RE = re.compile(
    r"\b(GIR ?0AA|"
    r"[A-PR-UWYZ](?:[0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)"
    r" ?[0-9][ABD-HJLNP-UW-Z]{2})\b",
    re.IGNORECASE,
)
LEI_RE = re.compile(r"\b[0-9A-Z]{18}[0-9]{2}\b")
IBAN_RE = re.compile(r"\b([A-Z]{2}[0-9]{2}(?:[ ]?[A-Z0-9]){11,30})\b")
SWIFT_BIC_RE = re.compile(r"\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")
US_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
UK_NIN_RE = re.compile(r"\b[A-CEGHJ-NPR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.IGNORECASE)
SORT_CODE_RE = re.compile(r"\b\d{2}[-\s]?\d{2}[-\s]?\d{2}\b")
DATE_RE = re.compile(
    r"\b("
    r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
    r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"
    r"|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{2,4}"
    r")\b",
    re.IGNORECASE,
)
ACCOUNT_RE = re.compile(r"\b\d{8,12}\b")  # generic numeric account-like
COMPANY_NO_RE = re.compile(r"\b(?:[A-Z]{2}[0-9]{6}|[0-9]{8})\b")
PHONE_RE = re.compile(r"(?:(?<=\s)|(?<=^)|(?<=:))(\+?\d[\d \-().]{7,}\d)(?=\s|$|[,;])")


def _det(t, et, c, det_name, page, chunk, lo, hi, off, meta=None):
    return Detection(
        text=t, entity_type=et, confidence=c, detector=det_name,
        span=Span(text=t, start=off + lo, end=off + hi, page=page, chunk=chunk),
        metadata=meta or {},
    )


def detect_regex(text: str, *, page: int | None, chunk: int | None, offset: int = 0) -> list[Detection]:
    """Run all regex recognisers over ``text``. ``offset`` is added to every
    char position so the resulting spans are absolute within the page."""
    out: list[Detection] = []
    for m in EMAIL_RE.finditer(text):
        out.append(_det(m.group(0), "EMAIL", 0.99, "regex.email", page, chunk, m.start(), m.end(), offset))
    for m in URL_RE.finditer(text):
        out.append(_det(m.group(0), "URL", 0.95, "regex.url", page, chunk, m.start(), m.end(), offset))
    for m in UK_POSTCODE_RE.finditer(text):
        out.append(_det(m.group(0), "POSTCODE", 0.95, "regex.uk_postcode", page, chunk, m.start(), m.end(), offset))
    for m in LEI_RE.finditer(text):
        out.append(_det(m.group(0), "LEI", 0.9, "regex.lei", page, chunk, m.start(), m.end(), offset))
    for m in IBAN_RE.finditer(text):
        out.append(_det(m.group(0).strip(), "IBAN", 0.9, "regex.iban", page, chunk, m.start(), m.end(), offset))
    for m in SWIFT_BIC_RE.finditer(text):
        token = m.group(0)
        # Avoid common all-caps words that pattern-match BIC shape
        if token.upper() in {"COMPANY", "ADDRESS", "CONTACT", "BUSINESS", "MILLION"}:
            continue
        out.append(_det(token, "SWIFT_BIC", 0.85, "regex.swift_bic", page, chunk, m.start(), m.end(), offset))
    for m in US_SSN_RE.finditer(text):
        out.append(_det(m.group(0), "NATIONAL_ID", 0.95, "regex.us_ssn", page, chunk, m.start(), m.end(), offset))
    for m in UK_NIN_RE.finditer(text):
        out.append(_det(m.group(0), "NATIONAL_ID", 0.92, "regex.uk_nin", page, chunk, m.start(), m.end(), offset))
    for m in SORT_CODE_RE.finditer(text):
        out.append(_det(m.group(0), "SORT_CODE", 0.75, "regex.sort_code", page, chunk, m.start(), m.end(), offset))
    for m in DATE_RE.finditer(text):
        out.append(_det(m.group(0), "GENERIC_DATE", 0.7, "regex.date", page, chunk, m.start(), m.end(), offset))
    for m in COMPANY_NO_RE.finditer(text):
        out.append(_det(m.group(0), "COMPANY_REG_NO", 0.7, "regex.company_no", page, chunk, m.start(), m.end(), offset))
    # Phone fallback (use phonenumbers when available)
    try:
        import phonenumbers
        for match in phonenumbers.PhoneNumberMatcher(text, region="GB"):
            out.append(_det(match.raw_string, "PHONE", 0.95, "phonenumbers", page, chunk, match.start, match.end, offset))
    except ImportError:
        for m in PHONE_RE.finditer(text):
            out.append(_det(m.group(1), "PHONE", 0.7, "regex.phone", page, chunk, m.start(1), m.end(1), offset))
    return out
