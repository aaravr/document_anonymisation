"""Step-by-step orchestration helpers used by :class:`AnonymisationAgent`.

This module exists so the workflow stages can be unit tested independently of
the agent (which mostly handles I/O and config loading).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    AnonymisationRequest,
    CanonicalEntity,
    Detection,
    ValidationReport,
)
from idp_anonymiser.detection import (
    canonical_registry as _registry,
)
from idp_anonymiser.detection.detector import CompositeDetector, DetectionConfig
from idp_anonymiser.detection.entity_resolution import (
    deduplicate_overlapping,
)
from idp_anonymiser.document import (
    DocumentType,
    detect as detect_type,
    load,
)
from idp_anonymiser.document.docx_extractor import extract_docx
from idp_anonymiser.document.layout_model import ExtractedDocument
from idp_anonymiser.document.pdf_extractor import extract_pdf
from idp_anonymiser.document.text_extractor import (
    extract_csv,
    extract_json,
    extract_txt,
)
from idp_anonymiser.document.xlsx_extractor import extract_xlsx
from idp_anonymiser.replacement.generator import ReplacementGenerator
from idp_anonymiser.replacement.mapping_store import (
    InMemoryMappingStore,
    SqliteMappingStore,
)
from idp_anonymiser.rewrite.docx_rewriter import rewrite_docx
from idp_anonymiser.rewrite.json_rewriter import rewrite_json, write_json
from idp_anonymiser.rewrite.pdf_rewriter import rewrite_pdf
from idp_anonymiser.rewrite.text_rewriter import rewrite_text
from idp_anonymiser.rewrite.xlsx_rewriter import rewrite_xlsx
from idp_anonymiser.validation.layout_similarity import compute_layout_similarity
from idp_anonymiser.validation.leakage_check import (
    check_consistency,
    check_originals_absent,
    cross_page_replacement_uniqueness,
)
from idp_anonymiser.validation.quality_score import QualityInputs, compute_quality_score
from idp_anonymiser.validation.residual_scan import residual_scan

logger = logging.getLogger(__name__)


@dataclass
class WorkflowContext:
    """Carries intermediate state through the workflow."""

    request: AnonymisationRequest
    profile: dict[str, Any]
    doc_type: DocumentType
    extracted: ExtractedDocument
    detections: list[Detection]
    canonical_entities: list[CanonicalEntity]
    plan: AnonymisationPlan
    rewritten_text: Optional[str] = None
    rewritten_doc_path: Optional[str] = None


# ---------------------------------------------------------------------------
# Stage: extract
# ---------------------------------------------------------------------------


def extract(request: AnonymisationRequest, doc_type: DocumentType) -> ExtractedDocument:
    loaded = load(request.input_path, hint=doc_type.value if doc_type else None)
    if doc_type == DocumentType.TXT:
        return extract_txt(loaded)
    if doc_type == DocumentType.CSV:
        return extract_csv(loaded)
    if doc_type == DocumentType.JSON:
        return extract_json(loaded)
    if doc_type == DocumentType.XLSX:
        return extract_xlsx(loaded)
    if doc_type == DocumentType.DOCX:
        return extract_docx(loaded)
    if doc_type == DocumentType.PDF:
        return extract_pdf(loaded)
    raise ValueError(f"Unsupported document type: {doc_type}")


# ---------------------------------------------------------------------------
# Stage: detect
# ---------------------------------------------------------------------------


def detect_entities(
    extracted: ExtractedDocument,
    profile: dict[str, Any],
) -> list[Detection]:
    config = _build_detection_config(profile)
    detector = CompositeDetector(config)
    return detector.detect(extracted)


def _build_detection_config(profile: dict[str, Any]) -> DetectionConfig:
    enabled = tuple(profile.get("enabled_entities", ()) or ())
    disabled = tuple(profile.get("disabled_entities", ()) or ())
    threshold = float(profile.get("confidence_threshold", 0.4))
    return DetectionConfig(
        enabled_entities=enabled,
        disabled_entities=disabled,
        confidence_threshold=threshold,
        enable_regex=profile.get("enable_regex", True),
        enable_label_value=profile.get("enable_label_value", True),
        enable_table=profile.get("enable_table", True),
        enable_spacy=profile.get("enable_spacy", True),
        enable_presidio=profile.get("enable_presidio", False),
        spacy_model=profile.get("spacy_model", "en_core_web_sm"),
    )


# ---------------------------------------------------------------------------
# Stage: resolve canonical
# ---------------------------------------------------------------------------


def resolve_canonical(
    detections: list[Detection],
    request: AnonymisationRequest,
    extracted: ExtractedDocument,
) -> tuple[list[CanonicalEntity], list]:
    """Build the document-level canonical entity registry."""
    deduped = deduplicate_overlapping(detections)

    # Resolve page indices for char-offset detections (PDF case)
    block_index = sorted(
        (b for b in extracted.blocks if b.block_id is not None),
        key=lambda b: b.start,
    )

    def page_resolver(d: Detection) -> int | None:
        if d.span.page is not None:
            return d.span.page
        if d.span.start is None:
            return None
        for b in block_index:
            if b.start <= d.span.start < b.end:
                return b.page
        return None

    mentions = _registry.collect_mentions(deduped, page_resolver=page_resolver)
    reg = _registry.CanonicalEntityRegistry(
        replace_contextual_aliases=request.replace_contextual_aliases,
        fuzzy_threshold=request.fuzzy_alias_threshold,
    )
    reg.ingest(mentions)

    # Safety-net: scan the document text for narrative references to entities
    # we already know about (e.g. "Reference to Acme Holdings as the issuer.").
    def _offset_to_page(offset: int) -> int | None:
        for b in block_index:
            if b.start <= offset < b.end:
                return b.page
        return None

    reg.sweep_text_for_known_aliases(extracted.flat_text, page_resolver=_offset_to_page)
    return reg.canonical_entities(), reg.ambiguous_mentions()


# ---------------------------------------------------------------------------
# Stage: plan
# ---------------------------------------------------------------------------


def build_plan(
    request: AnonymisationRequest,
    detections: list[Detection],
    canonical_entities: list[CanonicalEntity],
    ambiguous: list,
) -> tuple[AnonymisationPlan, ReplacementGenerator]:
    store = _build_mapping_store(request)
    generator = ReplacementGenerator(
        mapping_store=store, consistency_scope=request.consistency_scope
    )
    replacements = generator.generate_from_canonical(canonical_entities, request)
    plan = AnonymisationPlan(
        document_id=request.document_id,
        replacements=replacements,
        detections=detections,
        canonical_entities=canonical_entities,
        ambiguous_mentions=ambiguous,
    )
    return plan, generator


def _build_mapping_store(request: AnonymisationRequest):
    if request.consistency_scope == "document":
        return InMemoryMappingStore()
    # Batch / project: use a SQLite store in the output dir
    db_path = Path(request.output_dir) / f"_idp_mapping_{request.consistency_scope}.sqlite"
    return SqliteMappingStore(db_path)


# ---------------------------------------------------------------------------
# Stage: rewrite
# ---------------------------------------------------------------------------


def rewrite(
    request: AnonymisationRequest,
    doc_type: DocumentType,
    extracted: ExtractedDocument,
    plan: AnonymisationPlan,
    canonical_entities: list[CanonicalEntity],
    output_dir: Path,
) -> tuple[str, Optional[str], int, list[str]]:
    """Apply the plan back to the document. Returns (output_doc_path, output_text_path, applied, warnings)."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    in_path = Path(request.input_path)
    stem = in_path.stem
    warnings: list[str] = []

    # Always write an anonymised text shadow alongside the structured output.
    text_out_path = out_dir / f"{stem}.anonymised.txt"

    # The rewriter consumes a list of "resolved-like" entities with detections.
    # The canonical entities already bundle mentions; we map them to that shape.
    from idp_anonymiser.agent.state import ResolvedEntity, Detection as _Detection

    resolved = [
        ResolvedEntity(
            entity_id=ce.entity_id,
            canonical_value=ce.canonical_original,
            entity_type=ce.entity_type,
            confidence=ce.confidence,
            detections=[
                _Detection(
                    text=m.text,
                    entity_type=m.entity_type,
                    confidence=m.confidence,
                    detector=m.detector,
                    span=m.span,
                    metadata=m.metadata,
                )
                for m in ce.mentions
            ],
        )
        for ce in canonical_entities
    ]

    if doc_type == DocumentType.TXT:
        rewritten, applied = rewrite_text(extracted.flat_text, plan, resolved)
        out_path = out_dir / f"{stem}.anonymised.txt"
        out_path.write_text(rewritten, encoding="utf-8")
        return str(out_path), str(out_path), applied, warnings

    if doc_type == DocumentType.JSON:
        rewritten_tree, applied = rewrite_json(extracted, plan, resolved)
        out_path = out_dir / f"{stem}.anonymised.json"
        write_json(rewritten_tree, str(out_path))
        # Also dump anonymised flat text for downstream IDP testing
        rewritten_text, _ = rewrite_text(extracted.flat_text, plan, resolved)
        text_out_path.write_text(rewritten_text, encoding="utf-8")
        return str(out_path), str(text_out_path), applied, warnings

    if doc_type == DocumentType.CSV:
        # Apply edits on the flat text and reassemble into a DataFrame
        import io
        import pandas as pd

        rewritten_text, applied = rewrite_text(extracted.flat_text, plan, resolved)
        df = extracted.csv_dataframe.copy()
        # Walk the rewritten flat text in the same block order to map back
        block_lookup = {b.block_id: b for b in extracted.blocks if b.block_id and b.block_id != "csv:header"}
        for bid, block in block_lookup.items():
            new_chunk = rewritten_text[block.start : block.start + (block.end - block.start)]
            r_idx = block.metadata.get("row")
            col = block.metadata.get("column")
            if r_idx is None or col is None:
                continue
            try:
                # Length of replacement may differ — recompute via apply on the text rewriter
                # The block's char range in rewritten_text isn't stable, so instead we apply
                # detection-by-detection text edits per row/col.
                pass
            except Exception:
                pass
        # Simpler & robust: rewrite each cell individually.
        for ent in canonical_entities:
            rep = next((r for r in plan.replacements if r.entity_id == ent.entity_id), None)
            if rep is None:
                continue
            for m in ent.mentions:
                if m.span.start is None or m.span.end is None:
                    continue
                # Locate the block
                for b in extracted.blocks:
                    if b.block_id and b.start <= m.span.start and m.span.end <= b.end and b.block_id.startswith("csv:") and b.block_id != "csv:header":
                        r_idx = b.metadata.get("row")
                        col = b.metadata.get("column")
                        if r_idx is None or col is None:
                            continue
                        local_s = m.span.start - b.start
                        local_e = m.span.end - b.start
                        cur = str(df.at[r_idx, col])
                        df.at[r_idx, col] = cur[:local_s] + rep.replacement_value + cur[local_e:]
                        break
        out_path = out_dir / f"{stem}.anonymised.csv"
        df.to_csv(out_path, index=False, sep=extracted.metadata.get("sep", ","))
        text_out_path.write_text(rewritten_text, encoding="utf-8")
        return str(out_path), str(text_out_path), applied, warnings

    if doc_type == DocumentType.XLSX:
        out_path = out_dir / f"{stem}.anonymised.xlsx"
        applied = rewrite_xlsx(str(in_path), str(out_path), extracted, plan, resolved)
        rewritten_text, _ = rewrite_text(extracted.flat_text, plan, resolved)
        text_out_path.write_text(rewritten_text, encoding="utf-8")
        return str(out_path), str(text_out_path), applied, warnings

    if doc_type == DocumentType.DOCX:
        out_path = out_dir / f"{stem}.anonymised.docx"
        applied = rewrite_docx(str(in_path), str(out_path), extracted, plan, resolved)
        rewritten_text, _ = rewrite_text(extracted.flat_text, plan, resolved)
        text_out_path.write_text(rewritten_text, encoding="utf-8")
        return str(out_path), str(text_out_path), applied, warnings

    if doc_type == DocumentType.PDF:
        out_path = out_dir / f"{stem}.anonymised.pdf"
        applied, pdf_warns = rewrite_pdf(str(in_path), str(out_path), extracted, plan, resolved)
        warnings.extend(pdf_warns)
        rewritten_text, _ = rewrite_text(extracted.flat_text, plan, resolved)
        text_out_path.write_text(rewritten_text, encoding="utf-8")
        return str(out_path), str(text_out_path), applied, warnings

    raise ValueError(f"Unsupported document type for rewrite: {doc_type}")


# ---------------------------------------------------------------------------
# Stage: validate
# ---------------------------------------------------------------------------


def validate(
    *,
    original_text: str,
    rewritten_text: str,
    request: AnonymisationRequest,
    plan: AnonymisationPlan,
    canonical_entities: list[CanonicalEntity],
    doc_format: str,
    input_path: str,
    output_path: str,
    profile: dict[str, Any],
) -> ValidationReport:
    warnings: list[str] = []

    leakage_threshold = int(profile.get("validation", {}).get("max_originals_remaining", 0))
    residual_threshold = int(profile.get("validation", {}).get("max_residual_high_confidence", 0))
    quality_floor = float(profile.get("validation", {}).get("quality_floor", 0.6))

    leaks = check_originals_absent(rewritten_text, canonical_entities)
    residuals = residual_scan(rewritten_text)
    inconsistent = check_consistency(rewritten_text, canonical_entities)
    duplicates = cross_page_replacement_uniqueness(canonical_entities)

    if duplicates:
        warnings.append(
            "Some canonical entities received multiple replacements: "
            + ", ".join(duplicates)
        )

    layout = compute_layout_similarity(
        doc_format=doc_format,
        original_text=original_text,
        rewritten_text=rewritten_text,
        input_path=input_path,
        output_path=output_path,
    )
    warnings.extend(layout.warnings)

    quality = compute_quality_score(
        QualityInputs(
            leaked_originals=len(leaks),
            residual_high_confidence_pii=len(residuals),
            inconsistent_entities=len(inconsistent),
            layout_similarity=layout.score,
            total_canonical_entities=len(canonical_entities),
            replacement_count=len(plan.replacements),
        )
    )

    if leaks:
        warnings.append(
            f"{len(leaks)} original surface form(s) still present after anonymisation."
        )
    if residuals:
        warnings.append(
            f"{len(residuals)} high-confidence PII match(es) found in the anonymised output."
        )
    if inconsistent:
        warnings.append(
            f"{len(inconsistent)} canonical entity replacement(s) not visible in flat text."
        )

    passed = (
        len(leaks) <= leakage_threshold
        and len(residuals) <= residual_threshold
        and quality >= quality_floor
        and not duplicates
    )

    return ValidationReport(
        quality_score=quality,
        residual_high_confidence_pii_count=len(residuals),
        original_values_remaining_count=len(leaks),
        warnings=warnings,
        passed=passed,
        checks={
            "leaked_originals": leaks,
            "residual_pii": [
                {"text": d.text, "type": d.entity_type, "confidence": d.confidence}
                for d in residuals
            ],
            "inconsistent_entities": inconsistent,
            "duplicate_replacements": duplicates,
            "layout_score": layout.score,
            "layout_details": layout.details,
            "pages_affected": sorted({p for e in canonical_entities for p in e.pages}),
        },
    )


__all__ = [
    "WorkflowContext",
    "extract",
    "detect_entities",
    "resolve_canonical",
    "build_plan",
    "rewrite",
    "validate",
    "detect_type",
]
nsistent,
            "duplicate_replacements": duplicates,
            "layout_score": layout.score,
            "layout_details": layout.details,
            "pages_affected": sorted({p for e in canonical_entities for p in e.pages}),
        },
    )


__all__ = [
    "WorkflowContext",
    "extract",
    "detect_entities",
    "resolve_canonical",
    "build_plan",
    "rewrite",
    "validate",
    "detect_type",
]
