"""Regex-based recognisers for structured PII / client-data identifiers.

Each recogniser returns a list of :class:`Detection` objects. We deliberately
keep these patterns conservative: they are the high-precision floor of the
detection stack, with NER and label-value rules layered above for recall.

LEI: 20 chars, ISO 17442, mod-97-10 checksum (validated).
IBAN: 15-34 alphanumerics, country prefix + mod-97 checksum (validated).
SWIFT/BIC: 8 or 11 chars, ISO 9362 format.
UK postcode: BS 7666 / Royal Mail standard pattern.
Phone: validated via the ``phonenumbers`` library where available.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from idp_anonymiser.agent.state import Detection, DocumentSpan


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Email - RFC 5322-ish but pragmatic
EMAIL_RE = re.compile(
    r"\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b"
)

# UK postcode (allowing one or no space between outward and inward parts)
UK_POSTCODE_RE = re.compile(
    r"\b(GIR ?0AA|"
    r"[A-PR-UWYZ](?:[0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)"
    r" ?[0-9][ABD-HJLNP-UW-Z]{2})\b",
    re.IGNORECASE,
)

# LEI - 20 chars: 18 alphanumeric + 2 check digits
LEI_RE = re.compile(r"\b[0-9A-Z]{18}[0-9]{2}\b")

# IBAN - country (2) + check digits (2) + BBAN (up to 30); allow optional spaces
IBAN_RE = re.compile(r"\b([A-Z]{2}[0-9]{2}(?:[ ]?[A-Z0-9]){11,30})\b")

# SWIFT/BIC - 4 letters bank, 2 letters country, 2 alphanumerics location, optional 3 alphanumerics branch
SWIFT_BIC_RE = re.compile(r"\b([A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?)\b")

# URL
URL_RE = re.compile(
    r"\b(?:https?://|www\.)[a-zA-Z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    re.IGNORECASE,
)

# UK company registration number (8 digits, or 2 letters + 6 digits)
UK_COMPANY_NO_RE = re.compile(r"\b(?:[A-Z]{2}[0-9]{6}|[0-9]{8})\b")

# UK bank sort code: 6 digits, often hyphen-separated 12-34-56
SORT_CODE_RE = re.compile(r"\b\d{2}[-\s]?\d{2}[-\s]?\d{2}\b")

# UK bank account number: 8 digits (only when in clear bank context — see metadata)
BANK_ACCOUNT_RE = re.compile(r"\b\d{8}\b")

# Generic date-like strings: dd/mm/yyyy, yyyy-mm-dd, "1 Jan 2020", "January 2020"
DATE_RE = re.compile(
    r"\b("
    r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
    r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"
    r"|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{2,4}"
    r")\b",
    re.IGNORECASE,
)

# US-style SSN (9 digits, often dashed); a tax id surrogate
US_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# UK NI number
UK_NIN_RE = re.compile(r"\b[ABCEGHJ-NPR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.IGNORECASE)

# UK / EU passport-like: 9 alphanumerics. Conservative — only when label says passport.
PASSPORT_RE = re.compile(r"\b[A-Z0-9]{9}\b")

# VAT number (very simplified): country prefix + 8-12 digits
VAT_RE = re.compile(r"\b(?:GB|DE|FR|IT|ES|NL|IE)\s?\d{8,12}\b", re.IGNORECASE)

# Phone fallback (used if phonenumbers library not available)
PHONE_FALLBACK_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=:))(\+?\d[\d \-().]{7,}\d)(?=\s|$|[,;])"
)


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def _iban_mod97_ok(iban: str) -> bool:
    """ISO 13616 mod-97 check."""
    s = iban.replace(" ", "").upper()
    if len(s) < 15 or len(s) > 34:
        return False
    rearranged = s[4:] + s[:4]
    # Replace letters A=10, B=11, ...
    digits = []
    for c in rearranged:
        if c.isdigit():
            digits.append(c)
        elif "A" <= c <= "Z":
            digits.append(str(ord(c) - 55))
        else:
            return False
    try:
        return int("".join(digits)) % 97 == 1
    except ValueError:
        return False


def _lei_mod97_ok(lei: str) -> bool:
    """ISO 17442 LEI checksum (mod 97-10)."""
    s = lei.upper()
    if len(s) != 20:
        return False
    digits = []
    for c in s:
        if c.isdigit():
            digits.append(c)
        elif "A" <= c <= "Z":
            digits.append(str(ord(c) - 55))
        else:
            return False
    try:
        return int("".join(digits)) % 97 == 1
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _make_detection(
    text: str,
    entity_type: str,
    confidence: float,
    detector: str,
    start: int,
    end: int,
    metadata: Optional[dict] = None,
) -> Detection:
    return Detection(
        text=text,
        entity_type=entity_type,
        confidence=confidence,
        detector=detector,
        span=DocumentSpan(text=text, start=start, end=end),
        metadata=metadata or {},
    )


def detect_emails(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in EMAIL_RE.finditer(text):
        out.append(
            _make_detection(m.group(0), "EMAIL", 0.99, "regex.email", m.start(), m.end())
        )
    return out


def detect_uk_postcodes(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in UK_POSTCODE_RE.finditer(text):
        out.append(
            _make_detection(
                m.group(0),
                "POSTCODE",
                0.95,
                "regex.uk_postcode",
                m.start(),
                m.end(),
                {"country": "GB"},
            )
        )
    return out


def detect_lei(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in LEI_RE.finditer(text):
        candidate = m.group(0)
        if _lei_mod97_ok(candidate):
            out.append(
                _make_detection(candidate, "LEI", 0.99, "regex.lei", m.start(), m.end())
            )
        else:
            # Still emit as a low-confidence LEI candidate; downstream may discard
            out.append(
                _make_detection(
                    candidate,
                    "LEI",
                    0.55,
                    "regex.lei",
                    m.start(),
                    m.end(),
                    {"checksum": "fail"},
                )
            )
    return out


def detect_iban(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in IBAN_RE.finditer(text):
        candidate = m.group(0).replace(" ", "")
        if _iban_mod97_ok(candidate):
            out.append(
                _make_detection(
                    m.group(0).strip(), "IBAN", 0.99, "regex.iban", m.start(), m.end()
                )
            )
        else:
            out.append(
                _make_detection(
                    m.group(0).strip(),
                    "IBAN",
                    0.5,
                    "regex.iban",
                    m.start(),
                    m.end(),
                    {"checksum": "fail"},
                )
            )
    return out


# Exclude common English words that match the BIC pattern shape (defensive).
_BIC_FALSE_POSITIVE_TOKENS = {"COMPANY", "ADDRESS", "CONTACT"}


def detect_swift_bic(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in SWIFT_BIC_RE.finditer(text):
        token = m.group(0)
        if token.upper() in _BIC_FALSE_POSITIVE_TOKENS:
            continue
        # SWIFT/BIC must contain at least 4 letters at start and 2 letter country code
        if not (token[:4].isalpha() and token[4:6].isalpha()):
            continue
        out.append(
            _make_detection(token, "SWIFT_BIC", 0.85, "regex.swift_bic", m.start(), m.end())
        )
    return out


def detect_urls(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in URL_RE.finditer(text):
        out.append(
            _make_detection(m.group(0), "URL", 0.95, "regex.url", m.start(), m.end())
        )
    return out


def detect_company_no(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in UK_COMPANY_NO_RE.finditer(text):
        token = m.group(0)
        # A bare 8-digit number is ambiguous (could be account number); leave at lower confidence
        confidence = 0.7 if token.isdigit() else 0.85
        out.append(
            _make_detection(
                token, "COMPANY_REG_NO", confidence, "regex.company_no", m.start(), m.end()
            )
        )
    return out


def detect_sort_code(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in SORT_CODE_RE.finditer(text):
        out.append(
            _make_detection(
                m.group(0), "SORT_CODE", 0.75, "regex.sort_code", m.start(), m.end()
            )
        )
    return out


def detect_bank_account(text: str) -> list[Detection]:
    """8-digit numeric strings are emitted as low-confidence bank-account candidates.

    These are deliberately low-confidence; the label-value detector should
    elevate them when the surrounding context says "Account Number".
    """
    out: list[Detection] = []
    for m in BANK_ACCOUNT_RE.finditer(text):
        out.append(
            _make_detection(
                m.group(0), "BANK_ACCOUNT", 0.45, "regex.bank_account", m.start(), m.end()
            )
        )
    return out


def detect_dates(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in DATE_RE.finditer(text):
        out.append(
            _make_detection(
                m.group(0), "GENERIC_DATE", 0.7, "regex.date", m.start(), m.end()
            )
        )
    return out


def detect_us_ssn(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in US_SSN_RE.finditer(text):
        out.append(
            _make_detection(
                m.group(0), "NATIONAL_ID", 0.95, "regex.us_ssn", m.start(), m.end()
            )
        )
    return out


def detect_uk_nin(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in UK_NIN_RE.finditer(text):
        out.append(
            _make_detection(
                m.group(0), "NATIONAL_ID", 0.92, "regex.uk_nin", m.start(), m.end()
            )
        )
    return out


def detect_vat(text: str) -> list[Detection]:
    out: list[Detection] = []
    for m in VAT_RE.finditer(text):
        out.append(
            _make_detection(m.group(0), "TAX_ID", 0.9, "regex.vat", m.start(), m.end())
        )
    return out


def detect_phones(text: str) -> list[Detection]:
    """Phone detection using ``phonenumbers`` if installed, regex fallback otherwise."""
    out: list[Detection] = []
    try:
        import phonenumbers

        for match in phonenumbers.PhoneNumberMatcher(text, region="GB"):
            out.append(
                _make_detection(
                    match.raw_string,
                    "PHONE",
                    0.95,
                    "phonenumbers",
                    match.start,
                    match.end,
                    {"region": "GB"},
                )
            )
        return out
    except ImportError:
        pass
    # Fallback: regex only
    for m in PHONE_FALLBACK_RE.finditer(text):
        candidate = m.group(1)
        digits = sum(1 for c in candidate if c.isdigit())
        if digits < 8:
            continue
        out.append(
            _make_detection(
                candidate,
                "PHONE",
                0.7,
                "regex.phone_fallback",
                m.start(1),
                m.end(1),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Composite
# ---------------------------------------------------------------------------


_RECOGNISERS: list[Callable[[str], list[Detection]]] = [
    detect_emails,
    detect_uk_postcodes,
    detect_lei,
    detect_iban,
    detect_swift_bic,
    detect_urls,
    detect_company_no,
    detect_sort_code,
    detect_bank_account,
    detect_dates,
    detect_us_ssn,
    detect_uk_nin,
    detect_vat,
    detect_phones,
]


def detect_all(text: str) -> list[Detection]:
    """Run every regex recogniser over ``text`` and return all detections."""
    out: list[Detection] = []
    for fn in _RECOGNISERS:
        try:
            out.extend(fn(text))
        except Exception:
            # A misbehaving regex must never take the pipeline down.
            continue
    return out


__all__ = [
    "detect_all",
    "detect_emails",
    "detect_uk_postcodes",
    "detect_lei",
    "detect_iban",
    "detect_swift_bic",
    "detect_urls",
    "detect_company_no",
    "detect_sort_code",
    "detect_bank_account",
    "detect_dates",
    "detect_us_ssn",
    "detect_uk_nin",
    "detect_vat",
    "detect_phones",
]
