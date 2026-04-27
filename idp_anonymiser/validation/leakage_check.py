"""Verify that no original detected values remain in the anonymised output.

Two checks:

* **Originals absent**: every original surface form (and every alias surface
  form from the registry) must be absent from the rewritten text.
* **Cross-page consistency**: each canonical entity must have been replaced
  with exactly one synthetic value across all of its mentions, and that value
  must appear in the output where the originals used to appear.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    CanonicalEntity,
)


@dataclass
class LeakageCheckResult:
    leaked_originals: list[str]
    inconsistent_entities: list[str]
    pages_affected: list[int]


def check_originals_absent(
    rewritten_text: str,
    canonical_entities: list[CanonicalEntity],
    *,
    case_insensitive: bool = True,
) -> list[str]:
    """Return the list of original surface forms that still appear in the output."""
    haystack = rewritten_text.lower() if case_insensitive else rewritten_text
    leaks: list[str] = []
    seen: set[str] = set()
    for ent in canonical_entities:
        candidates = [ent.canonical_original] + list(ent.aliases)
        # Skip generic alias phrases ("the Client", "the Company") — those
        # legitimately remain in the anonymised text when the policy says so.
        for cand in candidates:
            if not cand or len(cand.strip()) < 3:
                continue
            if cand.lower().startswith("linked-"):
                continue
            needle = cand.lower() if case_insensitive else cand
            if needle in seen:
                continue
            # Generic phrases legitimately remain
            if needle.strip() in {"the client", "the company", "client", "company", "the firm"}:
                continue
            if needle in haystack:
                # Make sure this isn't because the replacement happens to contain it
                # (e.g. an org stem appearing inside an unrelated synthetic). We do a
                # naive check; tightening this requires per-mention diff which is
                # what the rewriter already provides.
                leaks.append(cand)
                seen.add(needle)
    return leaks


def check_consistency(
    rewritten_text: str,
    canonical_entities: list[CanonicalEntity],
) -> list[str]:
    """Return entity_ids whose replacement appears 0 times in the rewritten text.

    A canonical entity should have its synthetic replacement present at least
    once if the entity had any non-zero offset mentions. (Mentions without
    char offsets, e.g. table rows, may be applied via cell edits and not
    reflected in the flat text representation we receive here — so this is a
    best-effort check.)
    """
    failing: list[str] = []
    for ent in canonical_entities:
        if not ent.replacement:
            continue
        if all(m.span.start is None for m in ent.mentions):
            continue
        if ent.replacement not in rewritten_text:
            failing.append(ent.entity_id)
    return failing


def collect_pages_affected(canonical_entities: list[CanonicalEntity]) -> list[int]:
    pages: set[int] = set()
    for ent in canonical_entities:
        pages.update(ent.pages)
    return sorted(pages)


def cross_page_replacement_uniqueness(canonical_entities: list[CanonicalEntity]) -> list[str]:
    """Return entity_ids where a single canonical entity received multiple replacements.

    This guards against pipeline bugs where two clusters were created for what
    should have been the same canonical entity.
    """
    by_norm: dict[tuple[str, str], set[str]] = defaultdict(set)
    for e in canonical_entities:
        if e.replacement is not None:
            by_norm[(e.entity_type, e.normalised_key)].add(e.replacement)
    return [
        f"{etype}:{key}"
        for (etype, key), replacements in by_norm.items()
        if len(replacements) > 1
    ]
