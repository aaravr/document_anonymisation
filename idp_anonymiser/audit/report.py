"""Build and serialise the JSON audit report.

The report includes:
* Document identity (id, input filename, output paths)
* Detection summary (count by detector, by entity type)
* Replacement summary (count by entity type, by strategy)
* Validation results (leakage, consistency, layout score)
* Canonical entity registry summary (mentions, aliases, ambiguous mentions)
* Per-entity redaction map (with raw originals only if explicitly enabled)

Writing is atomic — we write to ``<path>.tmp`` then rename — so a crash mid-
write doesn't leave a partial file.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    AnonymisationRequest,
    CanonicalEntity,
    ValidationReport,
)
from idp_anonymiser.audit.redaction_map import RedactionMap
from idp_anonymiser import __version__


def build_audit_report(
    *,
    request: AnonymisationRequest,
    plan: AnonymisationPlan,
    canonical_entities: list[CanonicalEntity],
    validation: ValidationReport,
    detectors_used: list[str],
    input_path: str,
    output_document_path: str,
    output_text_path: str | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detection_count = len(plan.detections)
    detector_breakdown = Counter(d.detector for d in plan.detections)
    type_breakdown = Counter(d.entity_type for d in plan.detections)
    replacement_type_breakdown = Counter(r.entity_type for r in plan.replacements)
    strategy_breakdown = Counter(r.strategy for r in plan.replacements)

    redaction = RedactionMap(
        plan=plan,
        canonical_entities=canonical_entities,
        include_originals=request.debug_include_originals,
    ).to_dict()

    report: dict[str, Any] = {
        "schema_version": "1.0",
        "tool": "idp_anonymiser",
        "tool_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document": {
            "document_id": request.document_id,
            "input_path": os.path.basename(input_path),
            "output_document_path": os.path.basename(output_document_path),
            "output_text_path": (
                os.path.basename(output_text_path) if output_text_path else None
            ),
            "config_profile": request.config_profile,
            "anonymisation_mode": request.anonymisation_mode,
            "consistency_scope": request.consistency_scope,
            "risk_level": request.risk_level,
            "preserve_layout": request.preserve_layout,
            "replace_contextual_aliases": request.replace_contextual_aliases,
        },
        "summary": {
            "total_detections": detection_count,
            "unique_canonical_entities": len(canonical_entities),
            "total_mentions": sum(len(e.mentions) for e in canonical_entities),
            "total_aliases_merged": sum(len(e.aliases) for e in canonical_entities),
            "ambiguous_mentions": len(plan.ambiguous_mentions),
            "total_replacements": len(plan.replacements),
            "pages_affected": sorted({p for e in canonical_entities for p in e.pages}),
            "unresolved_detections": len(plan.unresolved),
        },
        "detectors_used": sorted(set(detectors_used)),
        "detection_breakdown": {
            "by_detector": dict(detector_breakdown),
            "by_entity_type": dict(type_breakdown),
        },
        "replacement_breakdown": {
            "by_entity_type": dict(replacement_type_breakdown),
            "by_strategy": dict(strategy_breakdown),
        },
        "validation": {
            "passed": validation.passed,
            "quality_score": validation.quality_score,
            "residual_high_confidence_pii_count": validation.residual_high_confidence_pii_count,
            "original_values_remaining_count": validation.original_values_remaining_count,
            "warnings": validation.warnings,
            "checks": validation.checks,
        },
        "ambiguous_mentions": [
            {
                "text": m.text,
                "entity_type": m.entity_type,
                "page": m.page,
                "detector": m.detector,
            }
            for m in plan.ambiguous_mentions
        ],
        "redactions": redaction["redactions"],
    }
    if extra:
        report["extra"] = extra
    return report


def write_report(report: dict[str, Any], output_path: str | Path) -> str:
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    tmp.replace(p)
    return str(p)


__all__ = ["build_audit_report", "write_report"]
