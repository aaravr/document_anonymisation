"""Apply a replacement plan to flat text.

We sort detections by start offset descending so each replacement edits the
string in-place without invalidating the offsets of earlier detections.

When two detections overlap (already deduplicated by entity resolution but a
defensive check kept here) the later-starting one wins.
"""
from __future__ import annotations

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    Detection,
    Replacement,
    ResolvedEntity,
)


def _build_lookup(
    plan: AnonymisationPlan,
    resolved: list[ResolvedEntity],
) -> dict[Detection, Replacement]:
    """Map each Detection to its Replacement via the resolved entity it belongs to."""
    by_entity_id = {r.entity_id: r for r in plan.replacements}
    out: dict[Detection, Replacement] = {}
    for ent in resolved:
        rep = by_entity_id.get(ent.entity_id)
        if rep is None:
            continue
        for det in ent.detections:
            out[id(det)] = rep  # type: ignore[index]
    return out  # type: ignore[return-value]


def rewrite_text(
    text: str,
    plan: AnonymisationPlan,
    resolved: list[ResolvedEntity],
) -> tuple[str, int]:
    """Apply the plan to ``text`` and return (anonymised_text, replacements_applied).

    Detections without char offsets are skipped (the caller should ensure the
    offsets exist for text rewriting).
    """
    by_entity = {r.entity_id: r for r in plan.replacements}

    # Flatten the (detection, replacement) pairs from the resolved list.
    edits: list[tuple[int, int, str]] = []
    for ent in resolved:
        rep = by_entity.get(ent.entity_id)
        if rep is None:
            continue
        for det in ent.detections:
            if det.span.start is None or det.span.end is None:
                continue
            edits.append((det.span.start, det.span.end, rep.replacement_value))

    if not edits:
        return text, 0

    # Sort descending by start so we can patch left without invalidating offsets.
    edits.sort(key=lambda e: e[0], reverse=True)

    out = text
    last_seen_start: int | None = None
    last_seen_end: int | None = None
    applied = 0
    for start, end, replacement in edits:
        # Defensive: if we already replaced an enclosing range, skip overlaps.
        if (
            last_seen_start is not None
            and last_seen_end is not None
            and start < last_seen_end
            and end > last_seen_start
        ):
            continue
        out = out[:start] + replacement + out[end:]
        last_seen_start, last_seen_end = start, end
        applied += 1
    return out, applied


__all__ = ["rewrite_text"]
