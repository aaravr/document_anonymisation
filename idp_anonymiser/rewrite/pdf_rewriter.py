"""Rewrite a searchable PDF using PyMuPDF redactions + overlays.

Approach (per page, per detection):

1. Locate the PDF span(s) overlapping the detection's char offsets.
2. Add a redaction annotation over the bbox (white fill).
3. After applying redactions, draw the replacement text inside the same bbox.
4. If the replacement does not fit in the bbox at the original font size, we
   shrink the font down to a configured floor; if it still doesn't fit, we
   substitute a compact mask token (e.g. ``[ORG_001]``) instead.

PDF rewriting is the lossiest of all formats. We document the limitations:
multi-line spans, justified text, and complex font fallback are not handled.
The output preserves page count.
"""
from __future__ import annotations

import logging
from typing import Optional

from idp_anonymiser.agent.state import (
    AnonymisationPlan,
    ResolvedEntity,
)
from idp_anonymiser.document.layout_model import ExtractedDocument

logger = logging.getLogger(__name__)


_MIN_FONT_SIZE = 4.0


def _build_pdf_edits(
    extracted: ExtractedDocument,
    plan: AnonymisationPlan,
    resolved: list[ResolvedEntity],
) -> list[tuple[int, tuple[float, float, float, float], str, str, Optional[float]]]:
    """Return (page, bbox, original_text, replacement, original_font_size) tuples.

    We collapse all char-offset detections into the PDF span coordinates via
    the extracted block index.
    """
    by_entity = {r.entity_id: r for r in plan.replacements}
    edits: list[tuple[int, tuple[float, float, float, float], str, str, Optional[float]]] = []

    # Build offset -> block lookup for blocks that are PDF spans
    pdf_blocks = [b for b in extracted.blocks if b.block_id and b.block_id.startswith("pdf:")]
    pdf_blocks.sort(key=lambda b: b.start)

    for ent in resolved:
        rep = by_entity.get(ent.entity_id)
        if rep is None:
            continue
        for det in ent.detections:
            if det.span.start is None or det.span.end is None:
                continue
            for block in pdf_blocks:
                if block.start <= det.span.start and det.span.end <= block.end:
                    bbox = block.metadata.get("bbox")
                    page = block.metadata.get("page")
                    if bbox is None or page is None:
                        continue
                    edits.append(
                        (
                            int(page),
                            tuple(bbox),  # type: ignore[arg-type]
                            block.text,
                            rep.replacement_value,
                            block.metadata.get("size"),
                        )
                    )
                    break
    return edits


def rewrite_pdf(
    input_path: str,
    output_path: str,
    extracted: ExtractedDocument,
    plan: AnonymisationPlan,
    resolved: list[ResolvedEntity],
) -> tuple[int, list[str]]:
    """Rewrite the PDF and return ``(applied_count, warnings)``."""
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("PyMuPDF (fitz) is required for PDF rewriting.") from e

    edits = _build_pdf_edits(extracted, plan, resolved)
    warnings: list[str] = []
    if not edits:
        # Still copy the file so output exists
        with open(input_path, "rb") as src, open(output_path, "wb") as dst:
            dst.write(src.read())
        return 0, warnings

    doc = fitz.open(input_path)
    edits_per_page: dict[int, list] = {}
    for e in edits:
        edits_per_page.setdefault(e[0], []).append(e)

    applied = 0
    for page_index in sorted(edits_per_page):
        page = doc.load_page(page_index)
        # First: add redaction annots for every span on this page
        for _, bbox, _, _, _ in edits_per_page[page_index]:
            rect = fitz.Rect(*bbox)
            page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        # Second: overlay replacement text
        for _, bbox, original_text, replacement, font_size in edits_per_page[page_index]:
            rect = fitz.Rect(*bbox)
            target_text = replacement
            size = float(font_size) if font_size else max(8.0, rect.height * 0.7)
            insert_ok = False
            while size >= _MIN_FONT_SIZE:
                # Try to insert text. PyMuPDF has insert_textbox which returns negative
                # if it didn't fit.
                try:
                    rc = page.insert_textbox(
                        rect,
                        target_text,
                        fontsize=size,
                        fontname="helv",
                        align=0,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("PDF overlay insert failed: %s", exc)
                    rc = -1
                if rc >= 0:
                    insert_ok = True
                    break
                size *= 0.85
            if not insert_ok:
                # Fall back to a compact mask token
                token = f"[{plan.document_id[:6].upper()}_{applied + 1:03d}]"
                try:
                    page.insert_textbox(rect, token, fontsize=8.0, fontname="helv")
                    warnings.append(
                        f"Replacement did not fit on page {page_index}; substituted mask token."
                    )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(
                        f"Failed to overlay replacement on page {page_index}: {exc}"
                    )
            applied += 1

    # garbage=4 + deflate is expensive on large PDFs; use lighter settings.
    # Callers that need maximum compression can re-save the output.
    doc.save(output_path, garbage=1, deflate=False, clean=False)
    doc.close()
    return applied, warnings


__all__ = ["rewrite_pdf"]
