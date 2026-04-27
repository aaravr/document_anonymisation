"""Replace redact_visuals with the strict-pass version that handles every
image bbox occurrence (including duplicates of the same xref on a page)."""
import os, ast, re

target = os.path.join(os.path.dirname(__file__), 'sanitiser', 'detect', 'visual.py')
with open(target) as f:
    src = f.read()

# Find old function start
start = src.index('def redact_visuals(')
end = src.index('def detect_graphic_blocks(')
new_block = '''def redact_visuals(pdf_doc, visuals, *, strict_remove_all_images: bool = True) -> int:
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


'''
new_src = src[:start] + new_block + src[end:]
with open(target, 'w') as f:
    f.write(new_src)
ast.parse(new_src)
print('OK', len(new_src), 'bytes')
