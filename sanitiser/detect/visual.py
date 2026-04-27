"""Visual element detector for PDFs (images, logos, signatures, stamps, QR/barcodes).

Approach:
* PyMuPDF surfaces every embedded image bbox via ``page.get_image_info(xrefs=True)``
  (or ``page.get_images()`` + ``page.get_image_rects(xref)``). Each image is
  reported as a generic 'image' element with its page+bbox.
* Heuristic typing:
    - very wide+short image at the top of the page -> likely a logo/banner
    - small square-ish image in the lower right of a page -> likely a signature/stamp
    - aspect ratio ~1:1 with crisp grid (cannot detect without OCR/CV) -> we
      conservatively flag as 'image' and require human review
* The visual element record includes whether redaction was applied.

Out of scope here: pixel-level QR/barcode classification (would require CV
libraries the sandbox doesn't have). The QA/review report covers them.
"""
from __future__ import annotations

from typing import Any

from sanitiser.state import VisualElement


def detect_visuals(pdf_doc, *, flag_images: bool = True, flag_signatures: bool = True,
                   flag_logos: bool = True) -> list[VisualElement]:
    """Walk every page, list every embedded image bbox.

    ``pdf_doc`` is an opened ``fitz.Document``.
    """
    out: list[VisualElement] = []
    for page_index in range(pdf_doc.page_count):
        page = pdf_doc.load_page(page_index)
        page_w, page_h = page.rect.width, page.rect.height
        seen_xrefs = set()
        # Each image_info entry: {'xref': int, 'bbox': (x0,y0,x1,y1), ...}
        try:
            infos = page.get_image_info(xrefs=True)
        except Exception:
            infos = []
        for info in infos:
            xref = info.get("xref")
            bbox = info.get("bbox")
            if not bbox:
                continue
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            x0, y0, x1, y1 = bbox
            w, h = x1 - x0, y1 - y0
            if w <= 1 or h <= 1:
                continue  # ignore tiny artefacts
            kind, reason = _classify_image(x0, y0, x1, y1, w, h, page_w, page_h)
            if kind == "logo" and not flag_logos:
                continue
            if kind == "signature" and not flag_signatures:
                continue
            if kind == "image" and not flag_images:
                continue
            out.append(VisualElement(
                page=page_index, type=kind, bbox=(x0, y0, x1, y1), reason=reason,
            ))
    return out


def _classify_image(x0, y0, x1, y1, w, h, page_w, page_h) -> tuple[str, str]:
    """Heuristic typing for an embedded image bbox."""
    aspect = (w / h) if h > 0 else 0
    # Logo / banner: wide and short, near the top
    if y0 < page_h * 0.15 and aspect > 3:
        return "logo", "Wide low-aspect image near top of page; likely logo or banner"
    # Signature / stamp: small image in lower-right quadrant
    if x0 > page_w * 0.5 and y0 > page_h * 0.6 and w < page_w * 0.4 and h < page_h * 0.2:
        return "signature", "Small image in lower-right; likely signature or stamp"
    # Otherwise generic image (could be photo / chart / logo / figure)
    return "image", "Embedded image; manual review required (may contain photo, logo, signature, QR/barcode)"


def redact_visuals(pdf_doc, visuals, *, strict_remove_all_images: bool = True) -> int:
    """Redact every visual element bbox plus, in strict mode, every embedded
    image found anywhere in the document.

    For strict mode we (a) replace each image xref's pixmap with a 1x1 grey
    so the underlying data is gone, (b) add a redaction annotation over EVERY
    rendered occurrence of that image (the same xref can appear at multiple
    positions on a page — both must be covered), (c) call apply_redactions
    with images=1, graphics=2 to also strip overlapping graphics, and (d)
    draw an opaque grey rect on top so even if the engine draws the image
    later in the rendering pipeline, the visible content is uniform grey.
    """
    import fitz
    if strict_remove_all_images:
        deleted_xrefs = set()
        for page_index in range(pdf_doc.page_count):
            page = pdf_doc.load_page(page_index)
            try:
                infos = page.get_image_info(xrefs=True)
            except Exception:
                infos = []
            for info in infos:
                xref = info.get("xref")
                bbox = info.get("bbox")
                if not bbox:
                    continue
                x0, y0, x1, y1 = bbox
                w, h = x1 - x0, y1 - y0
                if w <= 1 or h <= 1:
                    continue
                # Hard-replace the image data once per xref.
                if xref is not None and xref not in deleted_xrefs:
                    try:
                        grey = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1, 1))
                        grey.set_pixel(0, 0, (237, 237, 237))
                        page.replace_image(xref, pixmap=grey)
                        deleted_xrefs.add(xref)
                    except Exception:
                        try:
                            page.delete_image(xref)
                            deleted_xrefs.add(xref)
                        except Exception:
                            pass
                # Add a VisualElement for EVERY occurrence (xref dedup is
                # appropriate for the data nuke above, NOT for redaction
                # boxes — the same xref can be drawn at many positions).
                already = any(
                    v.page == page_index
                    and abs(v.bbox[0] - x0) < 1 and abs(v.bbox[1] - y0) < 1
                    and abs(v.bbox[2] - x1) < 1 and abs(v.bbox[3] - y1) < 1
                    for v in visuals
                )
                if not already:
                    visuals.append(VisualElement(
                        page=page_index, type="image",
                        bbox=(x0, y0, x1, y1),
                        reason="Strict policy: every embedded image removed",
                    ))

    by_page = {}
    for v in visuals:
        by_page.setdefault(v.page, []).append(v)
    redacted = 0
    for page_index, items in by_page.items():
        page = pdf_doc.load_page(page_index)
        for v in items:
            rect = fitz.Rect(*v.bbox)
            page.add_redact_annot(rect, fill=(0.93, 0.93, 0.93))
            v.redacted = True
            redacted += 1
        page.apply_redactions(images=1, graphics=2)
        # Final overlay: opaque grey rect on top of every visual bbox so
        # vector overlays / late-rendered images cannot bleed through.
        for v in items:
            rect = fitz.Rect(*v.bbox)
            page.draw_rect(rect, color=(0.93, 0.93, 0.93), fill=(0.93, 0.93, 0.93),
                            width=0, overlay=True)
        for v in items:
            rect = fitz.Rect(*v.bbox)
            page.draw_rect(rect, color=(0.7, 0.7, 0.7), width=0.5, overlay=True)
            try:
                label = "[REDACTED " + v.type.upper() + "]"
                page.insert_textbox(rect, label, fontsize=8, fontname="helv",
                                    color=(0.4, 0.4, 0.4), align=1)
            except Exception:
                pass
    return redacted


def detect_graphic_blocks(pdf_doc, *, min_area_ratio: float = 0.05) -> list[VisualElement]:
    """Find dense vector graphic blocks (drawings, charts, figures) on each page.

    Some PDFs render photos/illustrations as vector content rather than raster
    images, so ``get_image_info`` misses them. We look at ``page.get_drawings()``
    and flag any cluster of drawing primitives whose bounding box covers at
    least ``min_area_ratio`` of the page area.
    """
    out: list[VisualElement] = []
    for page_index in range(pdf_doc.page_count):
        page = pdf_doc.load_page(page_index)
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        if not drawings:
            continue
        page_area = page.rect.width * page.rect.height
        # Compute the union bbox of all drawings
        xs0, ys0, xs1, ys1 = [], [], [], []
        for d in drawings:
            r = d.get("rect")
            if r is None:
                continue
            xs0.append(r.x0); ys0.append(r.y0); xs1.append(r.x1); ys1.append(r.y1)
        if not xs0:
            continue
        ux0, uy0 = min(xs0), min(ys0)
        ux1, uy1 = max(xs1), max(ys1)
        bbox_area = (ux1 - ux0) * (uy1 - uy0)
        if page_area > 0 and bbox_area / page_area >= min_area_ratio and len(drawings) >= 20:
            out.append(VisualElement(
                page=page_index, type="graphic",
                bbox=(ux0, uy0, ux1, uy1),
                reason="Dense vector drawing block (" + str(len(drawings)) +
                       " primitives covering " + str(round(bbox_area / page_area * 100)) + "% of page)",
            ))
    return out



def detect_large_vector_drawings(pdf_doc, *, min_dim_pt: float = 40.0) -> list[VisualElement]:
    """Walk every page and flag every individual vector-drawing bbox that is
    at least ``min_dim_pt`` x ``min_dim_pt`` PDF points.

    Catches embedded vector illustrations (cars, icons, charts) that
    ``get_image_info`` misses because they aren't raster images.

    Skips tiny drawings (icons, separators), and skips drawings that cover
    nearly the whole page (page borders / table grids).
    """
    out: list[VisualElement] = []
    for page_index in range(pdf_doc.page_count):
        page = pdf_doc.load_page(page_index)
        page_w, page_h = page.rect.width, page.rect.height
        try:
            drawings = page.get_drawings()
        except Exception:
            continue
        # Cluster drawings by close proximity to their neighbours so multiple
        # path operators that together render one figure get a single bbox.
        rects = []
        for d in drawings:
            r = d.get("rect")
            if r is None:
                continue
            w, h = r.x1 - r.x0, r.y1 - r.y0
            if w < min_dim_pt or h < min_dim_pt:
                continue
            # Skip "page-spanning" rectangles which are usually borders
            if w > page_w * 0.9 and h > page_h * 0.9:
                continue
            rects.append((r.x0, r.y0, r.x1, r.y1))
        # Merge rects that overlap (one figure may have many path drawings).
        merged: list[list[float]] = []
        for r in rects:
            merged_into = False
            for m in merged:
                if r[0] < m[2] and m[0] < r[2] and r[1] < m[3] and m[1] < r[3]:
                    m[0] = min(m[0], r[0]); m[1] = min(m[1], r[1])
                    m[2] = max(m[2], r[2]); m[3] = max(m[3], r[3])
                    merged_into = True
                    break
            if not merged_into:
                merged.append(list(r))
        for m in merged:
            out.append(VisualElement(
                page=page_index, type="graphic", bbox=tuple(m),
                reason="Vector drawing block (>=40pt) — likely figure / illustration",
            ))
    return out
