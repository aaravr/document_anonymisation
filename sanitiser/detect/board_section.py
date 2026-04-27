"""Recognise board / executive / governance sections and treat them as high-risk.

When a section header like 'Board of Directors' or 'Executive Committee' is
seen, every following capitalised name-like phrase up to the next section
header is treated as PERSON with high confidence. This catches dense lists of
names that often appear without per-person labels.
"""
from __future__ import annotations

import re
from sanitiser.state import Detection, Span


_SECTION_HEADERS = re.compile(
    r"^\s*(?:Board of Directors|Board Members|Executive Committee|Senior Management|"
    r"Senior Officers|Officers|Leadership Team|Directors|Audit Committee|"
    r"Risk Committee|Nominating Committee|Governance Committee|"
    r"Management Committee|Executive Officers)\s*:?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# A capitalised name pattern: 2-5 tokens, each starting with uppercase letter.
# Allows initials with periods (e.g. "Katharine B. Stevenson") and apostrophes.
_NAME_PATTERN = re.compile(
    r"\b(?:(?:[A-Z][a-zA-Z'\-]+|[A-Z]\.)\s+){1,4}[A-Z][a-zA-Z'\-]+\b"
)


# Words that frequently start a multi-token capitalised phrase but aren't names.
_NAME_BLOCKLIST = {
    "United", "States", "Bank", "Group", "Holdings", "Limited", "Annual", "Report",
    "First", "Quarter", "Second", "Third", "Fourth", "Fiscal", "Year",
    "Risk", "Audit", "Capital", "Reserve", "Federal", "European",
}


def detect_board_sections(text: str, *, page: int | None, chunk: int | None,
                          offset: int = 0) -> list[Detection]:
    out: list[Detection] = []
    headers = list(_SECTION_HEADERS.finditer(text))
    if not headers:
        return out
    bounds: list[tuple[int, int]] = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else min(len(text), start + 4000)
        bounds.append((start, end))
    for s, e in bounds:
        chunk_text = text[s:e]
        for nm in _NAME_PATTERN.finditer(chunk_text):
            matched_text = nm.group(0)
            if "\n" in matched_text or "\r" in matched_text:
                continue  # PERSON names must be single-line
            tok0 = matched_text.split()[0]
            if tok0 in _NAME_BLOCKLIST:
                continue
            abs_start = offset + s + nm.start()
            out.append(Detection(
                text=nm.group(0), entity_type="PERSON", confidence=0.85,
                detector="board_section",
                span=Span(text=nm.group(0), start=abs_start, end=abs_start + len(nm.group(0)),
                          page=page, chunk=chunk),
                metadata={"context": "board_section"},
            ))
    return out
