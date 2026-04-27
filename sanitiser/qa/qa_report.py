"""Build a QA report scanning the *sanitised* text for likely missed PII.

The QA report is intended to bias toward false positives — for test-data
sanitisation we'd rather flag too much than miss something real.

Flag types:
* POSSIBLE_MISSED_PERSON — capitalised multi-token noun phrase
* POSSIBLE_MISSED_ORG — text containing a legal suffix (Ltd, Limited, plc, ...)
* POSSIBLE_MISSED_ABBREVIATION — bare 2-6 letter all-caps token unexplained
* RESIDUAL_REGEX_PII — emails, phones, IBANs, etc. that survived
* VISUAL_REVIEW_REQUIRED — pages with images/signatures/logos/stamps
"""
from __future__ import annotations

import re
from sanitiser.state import QAFlag, VisualElement
from sanitiser.detect import regex_recognisers


_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:Ltd\.?|Limited|PLC|LLP|LLC|Inc\.?|Incorporated|Corp\.?|Corporation"
    r"|GmbH|AG|Bank|Group|Holdings|Trust|Partners)\b"
)
_CAPITALISED_NAME_RE = re.compile(
    r"\b(?:(?:[A-Z][a-zA-Z'\-]{2,}|[A-Z]\.)\s+){1,4}[A-Z][a-zA-Z'\-]{2,}\b"
)
_BARE_ABBREV_RE = re.compile(r"\b[A-Z]{2,6}\b")

# Common all-caps tokens that aren't entity abbreviations
_ABBREV_BLOCKLIST = {
    "PDF", "URL", "USA", "UK", "EU", "AI", "API", "JSON", "XML", "CSV", "HTML",
    "CEO", "CFO", "COO", "CRO", "CTO", "CIO", "VP", "EVP", "SVP", "MD",
    "GAAP", "IFRS", "SEC", "FDIC", "OECD", "OFAC", "AML", "KYC", "PII",
    "TRUE", "FALSE", "YES", "NO", "OK", "NA", "TBD", "ETC", "NB", "PS",
    "GDP", "EBITDA", "ROE", "ROA", "NPL", "RWA", "T1", "T2", "ESG",
    "FY", "Q1", "Q2", "Q3", "Q4", "USD", "GBP", "EUR", "CAD", "JPY",
}

_NAME_BLOCKLIST_FIRST_TOKENS = {
    "United", "States", "Annual", "Report", "First", "Second", "Third", "Fourth",
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December", "Page", "Section", "Chapter",
    "Table", "Figure", "Note", "Schedule", "Appendix",
}


def build_qa_report(
    pages: list[str],
    visuals: list[VisualElement],
    *,
    flag_capitalised: bool = True,
    flag_org_suffixes: bool = True,
    flag_abbreviations: bool = True,
    flag_residual_regex: bool = True,
) -> list[QAFlag]:
    flags: list[QAFlag] = []

    for page_idx, text in enumerate(pages):
        if flag_capitalised:
            for m in _CAPITALISED_NAME_RE.finditer(text):
                first_tok = m.group(0).split()[0]
                if first_tok in _NAME_BLOCKLIST_FIRST_TOKENS:
                    continue
                flags.append(QAFlag(
                    page=page_idx, severity="medium", type="POSSIBLE_MISSED_PERSON",
                    text=m.group(0),
                    reason="Capitalised multi-token noun phrase remains after sanitisation",
                    confidence=0.6,
                ))
        if flag_org_suffixes:
            for m in _LEGAL_SUFFIX_RE.finditer(text):
                # Capture the surrounding 1-4 capitalised tokens before the suffix
                left = max(0, m.start() - 80)
                window = text[left:m.end()]
                phrase = re.search(
                    r"((?:[A-Z][A-Za-z&.\-']*\s+){1,5}" + re.escape(m.group(0)) + r")",
                    window
                )
                phrase_text = phrase.group(1) if phrase else m.group(0)
                flags.append(QAFlag(
                    page=page_idx, severity="high", type="POSSIBLE_MISSED_ORG",
                    text=phrase_text.strip(),
                    reason="Legal suffix '" + m.group(0) + "' present in sanitised text",
                    confidence=0.75,
                ))
        if flag_abbreviations:
            seen = set()
            for m in _BARE_ABBREV_RE.finditer(text):
                tok = m.group(0)
                if tok in _ABBREV_BLOCKLIST or tok in seen:
                    continue
                seen.add(tok)
                flags.append(QAFlag(
                    page=page_idx, severity="low", type="POSSIBLE_MISSED_ABBREVIATION",
                    text=tok,
                    reason="Unexplained capitalised abbreviation remains; check it isn't an entity",
                    confidence=0.4,
                ))
        if flag_residual_regex:
            residuals = regex_recognisers.detect_regex(text, page=page_idx, chunk=None)
            for d in residuals:
                # Skip synthetic shapes we deliberately produce
                if "example" in d.text.lower() or ".test" in d.text.lower():
                    continue
                flags.append(QAFlag(
                    page=page_idx, severity="high", type="RESIDUAL_REGEX_PII",
                    text=d.text,
                    reason=d.entity_type + " pattern remains in sanitised text",
                    confidence=d.confidence,
                ))

    # Visual review flags
    for v in visuals:
        flags.append(QAFlag(
            page=v.page, severity="high" if not v.redacted else "low",
            type="VISUAL_REVIEW_REQUIRED",
            text=v.type, reason=v.reason + (" (redacted)" if v.redacted else ""),
            confidence=0.9,
        ))
    return flags
