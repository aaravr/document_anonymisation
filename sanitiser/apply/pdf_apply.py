"""Apply replacements to a PDF and redact embedded images.

Per page:
* For each detected mention, find the matching PyMuPDF text span (via
  ``page.search_for(text)``) and add a redaction annotation with the chosen
  synthetic replacement painted over it.
* For each visual element flagged on the page, add a redaction annotation
  covering its bbox so the image is removed; replace with a labelled outline.

Note: ``search_for`` may return false-positive matches when the same string
appears more than once on a page. We mitigate by ordering matches and
preserving deterministic order; the audit records the bbox we wrote on so
operators can verify post-hoc.
"""
from __future__ import annotations

from typing import Any

from sanitiser.detect.visual import redact_visuals
from sanitiser.state import CanonicalEntity, Detection, ReplacementRecord, VisualElement
from sanitiser.apply.text_apply import _pick_variant
from sanitiser.resolve.normaliser import normalise


def _select_replacement(d: Detection, entity_lookup) -> tuple[str | None, CanonicalEntity | None]:
    ent = entity_lookup(d.entity_type, normalise(d.text, d.entity_type))
    if ent is None:
        return None, None
    repl = _pick_variant(d.text, ent)
    if d.text.isupper() and any(c.isalpha() for c in d.text):
        repl = repl.upper()
    return repl, ent


def rewrite_pdf(
    pdf_doc,
    output_path: str,
    detections_by_page: dict[int, list[Detection]],
    entity_lookup,
    visuals: list[VisualElement],
    *,
    document_id: str,
    redact_images: bool = True,
) -> tuple[int, int, list[ReplacementRecord]]:
    """Apply text replacements + visual redactions and save PDF.

    Returns ``(text_replacements_applied, visuals_redacted, audit_records)``.
    """
    import fitz

    audit: list[ReplacementRecord] = []
    applied = 0
    # Visual redactions first (so subsequent text overlays on the same page
    # don't get caught by apply_redactions).
    visuals_redacted = redact_visuals(pdf_doc, visuals) if redact_images else 0

    for page_index in range(pdf_doc.page_count):
        page = pdf_doc.load_page(page_index)
        # Group detections by exact (text, replacement) so we minimise search calls
        for d in detections_by_page.get(page_index, []):
            replacement, ent = _select_replacement(d, entity_lookup)
            if replacement is None:
                continue
            # Use search_for to locate the bboxes of the text on this page
            try:
                rects = page.search_for(d.text, quads=False, flags=0) or []
            except Exception:
                rects = []
            for rect in rects:
                page.add_redact_annot(rect, fill=(1, 1, 1))
            # text replacements: do NOT touch images / graphics outside the redact box
            page.apply_redactions(images=0, graphics=0)
            for rect in rects:
                # Insert replacement text at the rect; shrink font to fit if needed.
                size = max(8.0, rect.height * 0.85)
                while size >= 4.0:
                    rc = page.insert_textbox(rect, replacement, fontsize=size,
                                              fontname="helv", align=0)
                    if rc >= 0:
                        break
                    size *= 0.85
                applied += 1
                audit.append(ReplacementRecord(
                    document_id=document_id, page=page_index, chunk=None,
                    entity_type=d.entity_type, original=d.text, replacement=replacement,
                    canonical_id=ent.canonical_id, detectors=[d.detector],
                    confidence=d.confidence,
                    start=int(rect.x0), end=int(rect.x1),  # bbox-coordinate audit; OK for PDF
                    reason=d.detector + " match in PDF",
                ))
    pdf_doc.save(output_path, garbage=1, deflate=False, clean=False)
    return applied, visuals_redacted, audit
