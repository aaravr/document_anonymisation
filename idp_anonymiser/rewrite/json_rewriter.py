"""Walk a parsed JSON tree and replace sensitive string leaves.

The rewriter receives the original parsed tree and the per-leaf detections.
For each string leaf at a given json-path, we check whether it had any
detections; if so we apply the same in-place text replacement used by the TXT
rewriter to the leaf string and write the result back into the tree.
"""
from __future__ import annotations

import copy
import json
from typing import Any

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    Detection,
    Replacement,
    ResolvedEntity,
)
from idp_anonymiser.document.layout_model import ExtractedDocument
from idp_anonymiser.rewrite.text_rewriter import rewrite_text


def _set_by_json_path(node: Any, path: str, value: Any) -> None:
    """Set ``value`` at ``path`` (a simple ``$.a.b[0].c`` expression)."""
    # Tokenise: split on '.' and '[]'
    if not path.startswith("$"):
        raise ValueError(f"Invalid JSON path: {path}")
    # Walk
    current = node
    tokens: list[tuple[str, Any]] = []
    i = 1
    while i < len(path):
        c = path[i]
        if c == ".":
            j = i + 1
            while j < len(path) and path[j] not in ".[":
                j += 1
            tokens.append(("key", path[i + 1 : j]))
            i = j
        elif c == "[":
            j = path.index("]", i)
            tokens.append(("idx", int(path[i + 1 : j])))
            i = j + 1
        else:
            raise ValueError(f"Invalid JSON path: {path}")
    if not tokens:
        return
    *parents, last = tokens
    for kind, key in parents:
        if kind == "key":
            current = current[key]
        else:
            current = current[key]
    kind, key = last
    if kind == "key":
        current[key] = value
    else:
        current[key] = value


def rewrite_json(
    extracted: ExtractedDocument,
    plan: AnonymisationPlan,
    resolved: list[ResolvedEntity],
) -> tuple[Any, int]:
    """Return (rewritten_tree, replacements_applied)."""
    if extracted.json_data is None:
        raise ValueError("rewrite_json requires extracted.json_data to be populated")

    # Group detections by the block (=json path) they sit inside, so we can
    # rewrite each leaf independently using char offsets local to that leaf.
    block_for_offset = {
        (b.start, b.end): b for b in extracted.blocks
    }

    by_entity = {r.entity_id: r for r in plan.replacements}
    # Map block_id -> list of (local_start, local_end, replacement)
    edits_per_block: dict[str, list[tuple[int, int, str]]] = {}
    for ent in resolved:
        rep = by_entity.get(ent.entity_id)
        if rep is None:
            continue
        for det in ent.detections:
            if det.span.start is None or det.span.end is None:
                continue
            # Find the block this detection sits inside
            for (bs, be), block in block_for_offset.items():
                if bs <= det.span.start and det.span.end <= be:
                    if block.block_id is None:
                        continue
                    local_start = det.span.start - bs
                    local_end = det.span.end - bs
                    edits_per_block.setdefault(block.block_id, []).append(
                        (local_start, local_end, rep.replacement_value)
                    )
                    break

    rewritten = copy.deepcopy(extracted.json_data)
    applied = 0
    for block in extracted.blocks:
        if block.block_id is None or block.block_id not in edits_per_block:
            continue
        edits = edits_per_block[block.block_id]
        edits.sort(key=lambda e: e[0], reverse=True)
        new_text = block.text
        for s, e, replacement in edits:
            new_text = new_text[:s] + replacement + new_text[e:]
            applied += 1
        # block_id for JSON extractions is the JSON path
        _set_by_json_path(rewritten, block.block_id, new_text)
    return rewritten, applied


def write_json(rewritten: Any, output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(rewritten, fh, ensure_ascii=False, indent=2)


__all__ = ["rewrite_json", "write_json"]
