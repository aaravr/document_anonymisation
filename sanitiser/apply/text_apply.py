"""Apply page-level replacements to plain text.

Strategy: for each page, build a list of (start, end, replacement_string) edits
from the cluster -> mention spans, sort descending by start, then patch in
reverse so earlier offsets remain valid.

The replacement chosen for each individual mention is the variant that best
matches the original surface form: if the mention was "Mr Dodig" we substitute
"Mr Whitmore"; if it was just "Dodig" we substitute "Whitmore".
"""
from __future__ import annotations

import re

from sanitiser.state import CanonicalEntity, Detection, ReplacementRecord, Span


_TITLE_RE = re.compile(r"^(Mr|Mrs|Ms|Miss|Mx|Dr|Prof|Professor|Sir|Dame)\.?$",
                        re.IGNORECASE)


def _pick_variant(original: str, ent: CanonicalEntity) -> str:
    """Return the replacement variant that best matches ``original``'s shape."""
    if ent.entity_type == "PERSON":
        tokens = original.split()
        # Single-token surname case
        if len(tokens) == 1:
            # Use the synthetic last name if we have it (last token of full_name)
            full = ent.replacement_full_name
            return full.split()[-1]
        # Title + surname (e.g. "Mr Dodig")
        if len(tokens) == 2 and _TITLE_RE.match(tokens[0]):
            full = ent.replacement_full_name
            return tokens[0] + " " + full.split()[-1]
        # Initial + surname (e.g. "V. Dodig")
        if len(tokens) == 2 and len(tokens[0]) <= 2 and tokens[0].endswith("."):
            full_parts = ent.replacement_full_name.split()
            if full_parts:
                return full_parts[0][0] + ". " + full_parts[-1]
        return ent.replacement_full_name
    if ent.entity_type == "ORG":
        # Abbreviation match (case-sensitive on uppercase short tokens)
        if (ent.abbreviation and original.strip() == ent.abbreviation
                and ent.replacement_abbreviation):
            return ent.replacement_abbreviation
        if (ent.abbreviation and original.strip().upper() == ent.abbreviation.upper()
                and ent.replacement_abbreviation):
            return ent.replacement_abbreviation
        return ent.replacement_full_name
    return ent.replacement_full_name


def apply_to_pages(
    page_texts: list[str],
    detections_by_page: dict[int, list[Detection]],
    entity_lookup,  # callable: (entity_type, normalised) -> CanonicalEntity | None
    *,
    document_id: str,
) -> tuple[list[str], list[ReplacementRecord]]:
    """Return (rewritten_pages, audit_records)."""
    from sanitiser.resolve.normaliser import normalise

    new_pages: list[str] = []
    audit: list[ReplacementRecord] = []

    for page_index, text in enumerate(page_texts):
        edits: list[tuple[int, int, str, Detection, CanonicalEntity]] = []
        for d in detections_by_page.get(page_index, []):
            ent = entity_lookup(d.entity_type, normalise(d.text, d.entity_type))
            if ent is None:
                continue
            replacement = _pick_variant(d.text, ent)
            # Preserve uppercase shape (e.g. when original is all-caps)
            if d.text.isupper() and any(c.isalpha() for c in d.text):
                replacement = replacement.upper()
            edits.append((d.span.start, d.span.end, replacement, d, ent))
        edits.sort(key=lambda e: (-e[0], e[1]))
        rewritten = text
        for s, e, r, det, ent in edits:
            if s < 0 or e > len(rewritten) or s >= e:
                continue
            rewritten = rewritten[:s] + r + rewritten[e:]
            audit.append(ReplacementRecord(
                document_id=document_id, page=page_index, chunk=det.span.chunk,
                entity_type=det.entity_type, original=det.text, replacement=r,
                canonical_id=ent.canonical_id,
                detectors=[det.detector],
                confidence=det.confidence, start=s, end=e,
                reason=det.detector + " match"
                       + (" (seed_list)" if "seed_list" in det.detector else ""),
            ))
        new_pages.append(rewritten)
    return new_pages, audit
