"""Orchestrate the full sanitisation pipeline for a single document.

Stages:
1. load -> page-level text + raw_handle
2. chunk per page (only matters for spaCy on huge pages)
3. detect (regex + label_value + spacy + seed_list + board_section)
4. resolve into canonical entities (with abbreviation linking + person variants)
5. assign synthetic replacements via the global Registry
6. apply replacements (TXT/DOCX/PDF rewrite + PDF visual redaction)
7. build QA report
8. write all outputs (sanitised file, audit, replacement_map, qa_report,
   visual_redaction_report, run_summary)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from sanitiser.chunker import chunk_page
from sanitiser.config import Profile, SeedList
from sanitiser.detect import regex_recognisers, label_value, board_section
from sanitiser.detect.seed_list import SeedListMatcher
from sanitiser.detect.spacy_loader import SpacyDetector, SpacyUnavailableError
from sanitiser.detect.visual import detect_visuals, detect_graphic_blocks, detect_large_vector_drawings
from sanitiser.document_loader import LoadedDoc, load
from sanitiser.qa.qa_report import build_qa_report
from sanitiser.replace.registry import Registry
from sanitiser.resolve.normaliser import normalise
from sanitiser.resolve.resolver import EntityResolver
from sanitiser.state import (
    CanonicalEntity, Detection, RunSummary, VisualElement,
)

logger = logging.getLogger(__name__)


def _detect_page(text: str, page_index: int, *, spacy_det: SpacyDetector | None,
                 seed_matcher: SeedListMatcher | None, profile: Profile) -> list[Detection]:
    out: list[Detection] = []
    chunks = chunk_page(page_index, text, max_chars=profile.chunk_chars,
                         overlap=profile.chunk_overlap_chars)
    for ch in chunks:
        out += regex_recognisers.detect_regex(ch.text, page=page_index, chunk=ch.chunk_index,
                                              offset=ch.start_in_page)
        out += label_value.detect_label_values(ch.text, page=page_index, chunk=ch.chunk_index,
                                                offset=ch.start_in_page)
        out += board_section.detect_board_sections(ch.text, page=page_index,
                                                    chunk=ch.chunk_index, offset=ch.start_in_page)
        if seed_matcher:
            out += seed_matcher.detect(ch.text, page=page_index, chunk=ch.chunk_index,
                                        offset=ch.start_in_page)
        if spacy_det and spacy_det.is_available():
            out += spacy_det.detect(ch.text, page=page_index, chunk=ch.chunk_index,
                                     offset=ch.start_in_page)
    return out


def sanitise_document(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    profile: Profile,
    seed_list: SeedList | None = None,
    registry: Registry | None = None,
    document_id: Optional[str] = None,
) -> RunSummary:
    started = time.time()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    loaded: LoadedDoc = load(input_path, doc_id=document_id)
    doc_id = loaded.doc_id
    doc_out_dir = out_dir / doc_id
    doc_out_dir.mkdir(parents=True, exist_ok=True)

    # Initialise spaCy with strict policy
    spacy_det: SpacyDetector | None = None
    if profile.enable_spacy:
        spacy_det = SpacyDetector(model=profile.spacy_model,
                                   fail_if_unavailable=profile.fail_if_spacy_unavailable)
        spacy_det.load()
        if not spacy_det.is_available() and profile.fail_if_spacy_unavailable:
            # load() already raised, but keep an explicit safety net.
            raise SpacyUnavailableError("spaCy required by profile but unavailable")

    seed_matcher = SeedListMatcher(seed_list) if seed_list else None

    # Detection per page
    detections_by_page: dict[int, list[Detection]] = {}
    page_texts: list[str] = [pt.text for pt in loaded.pages]
    for pt in loaded.pages:
        detections_by_page[pt.page_index] = _detect_page(
            pt.text, pt.page_index,
            spacy_det=spacy_det, seed_matcher=seed_matcher, profile=profile,
        )

    # Resolve canonical entities (build clusters across pages)
    all_detections: list[Detection] = []
    for d_list in detections_by_page.values():
        all_detections.extend(d_list)
    resolver = EntityResolver()
    clusters = resolver.resolve(all_detections, page_texts)

    # Assign / fetch synthetic replacements
    if registry is None:
        registry = Registry(seed=profile.seed)
    canonical_entities: list[CanonicalEntity] = [
        registry.replacement_for_cluster(c) for c in clusters
    ]

    # Detect & redact visual elements (PDF only)
    visuals: list[VisualElement] = []
    if loaded.format == "pdf":
        visuals = detect_visuals(
            loaded.raw_handle,
            flag_images=profile.visual_elements.flag_images,
            flag_signatures=profile.visual_elements.flag_signatures,
            flag_logos=profile.visual_elements.flag_logos,
        )
        # Also catch large vector graphic blocks (figures rendered as drawings).
        visuals.extend(detect_graphic_blocks(loaded.raw_handle, min_area_ratio=0.10))
        # Catch medium-sized individual vector drawings (cars, icons, charts).
        visuals.extend(detect_large_vector_drawings(loaded.raw_handle, min_dim_pt=40.0))

    # Build entity_lookup callable
    def entity_lookup(et: str, normalised_value: str) -> CanonicalEntity | None:
        cid = registry._by_normalised.get((et, normalised_value))
        return registry.by_canonical_id.get(cid) if cid else None

    # Apply per format
    if loaded.format == "pdf":
        from sanitiser.apply.pdf_apply import rewrite_pdf
        out_pdf = doc_out_dir / "sanitised.pdf"
        applied, redacted, audit_records = rewrite_pdf(
            loaded.raw_handle, str(out_pdf), detections_by_page, entity_lookup, visuals,
            document_id=doc_id, redact_images=profile.visual_elements.redact_images,
        )
        # Also dump sanitised text for QA scanning
        sanitised_pages = []
        for pi in range(loaded.raw_handle.page_count):
            sanitised_pages.append(loaded.raw_handle.load_page(pi).get_text("text") or "")
        loaded.raw_handle.close()
        sanitised_txt = "\n\n".join(sanitised_pages)
        (doc_out_dir / "sanitised.txt").write_text(sanitised_txt, encoding="utf-8")
        sanitised_for_qa = sanitised_pages

    elif loaded.format == "docx":
        from sanitiser.apply.docx_apply import rewrite_docx
        out_docx = doc_out_dir / "sanitised.docx"
        applied, audit_records = rewrite_docx(
            str(loaded.input_path), str(out_docx), detections_by_page, entity_lookup,
            document_id=doc_id,
        )
        # Re-extract sanitised text
        from docx import Document as _Doc
        new_doc = _Doc(str(out_docx))
        sanitised_txt = "\n".join(p.text for p in new_doc.paragraphs)
        (doc_out_dir / "sanitised.txt").write_text(sanitised_txt, encoding="utf-8")
        sanitised_for_qa = [sanitised_txt]
        redacted = 0

    else:
        from sanitiser.apply.text_apply import apply_to_pages
        new_pages, audit_records = apply_to_pages(
            page_texts, detections_by_page, entity_lookup, document_id=doc_id,
        )
        sanitised_txt = "\n\n".join(new_pages)
        (doc_out_dir / "sanitised.txt").write_text(sanitised_txt, encoding="utf-8")
        applied = len(audit_records)
        redacted = 0
        sanitised_for_qa = new_pages

    # Build QA report
    qa_flags = build_qa_report(
        sanitised_for_qa, visuals,
        flag_capitalised=profile.qa.flag_remaining_capitalised_names,
        flag_org_suffixes=profile.qa.flag_remaining_org_suffixes,
        flag_abbreviations=profile.qa.flag_remaining_abbreviations,
        flag_residual_regex=profile.qa.fail_on_remaining_regex_pii,
    )

    # Write outputs
    (doc_out_dir / "audit.json").write_text(
        json.dumps([r.model_dump() for r in audit_records], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (doc_out_dir / "replacement_map.json").write_text(
        json.dumps(registry.to_export_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (doc_out_dir / "qa_report.json").write_text(
        json.dumps({
            "document_id": doc_id,
            "status": "needs_review" if qa_flags else "clean",
            "flag_count": len(qa_flags),
            "flags": [f.model_dump() for f in qa_flags],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (doc_out_dir / "visual_redaction_report.json").write_text(
        json.dumps({
            "document_id": doc_id,
            "visual_elements": [v.model_dump() for v in visuals],
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    elapsed = time.time() - started
    summary = RunSummary(
        document_id=doc_id,
        input_path=str(loaded.input_path),
        output_path=str(doc_out_dir),
        pages=len(page_texts),
        total_detections=sum(len(v) for v in detections_by_page.values()),
        unique_canonical_entities=len(canonical_entities),
        total_replacements=applied,
        visual_elements_flagged=len(visuals),
        visual_elements_redacted=redacted,
        qa_flag_count=len(qa_flags),
        status="needs_review" if qa_flags else "ok",
        elapsed_seconds=round(elapsed, 3),
    )
    (doc_out_dir / "run_summary.json").write_text(
        json.dumps(summary.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("sanitised %s in %.2fs (%d pages, %d entities)",
                doc_id, elapsed, summary.pages, summary.unique_canonical_entities)
    return summary
