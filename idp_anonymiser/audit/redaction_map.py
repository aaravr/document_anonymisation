"""A view of the per-entity redaction map for the audit report.

Wraps :class:`AnonymisationPlan` data into the public-facing JSON shape and
ensures the raw originals are stripped unless ``debug_include_originals`` was
set on the request.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from idp_anonymiser.agent.state import AnonymisationPlan, CanonicalEntity, Replacement


@dataclass
class RedactionMap:
    plan: AnonymisationPlan
    canonical_entities: list[CanonicalEntity]
    include_originals: bool = False

    def to_dict(self) -> dict[str, Any]:
        replacements_by_id = {r.entity_id: r for r in self.plan.replacements}
        out: list[dict[str, Any]] = []
        for ent in self.canonical_entities:
            r: Replacement | None = replacements_by_id.get(ent.entity_id)
            row: dict[str, Any] = {
                "entity_id": ent.entity_id,
                "entity_type": ent.entity_type,
                "replacement": r.replacement_value if r else None,
                "replacement_policy": r.strategy if r else "none",
                "original_hash": r.original_hash if r else None,
                "mention_count": len(ent.mentions),
                "alias_count": len(ent.aliases),
                "pages": ent.pages,
                "role_labels": ent.role_labels,
                "related_entity_ids": ent.related_entity_ids,
                "confidence": ent.confidence,
            }
            if self.include_originals:
                row["canonical_original"] = ent.canonical_original
                row["aliases"] = ent.aliases
                row["mentions"] = [
                    {
                        "text": m.text,
                        "page": m.page,
                        "is_alias": m.is_alias,
                        "detector": m.detector,
                    }
                    for m in ent.mentions
                ]
            out.append(row)
        return {"redactions": out}
