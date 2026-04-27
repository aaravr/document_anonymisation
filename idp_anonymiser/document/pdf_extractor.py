"""Extract text spans from searchable PDFs via PyMuPDF (fitz).

We capture per-span bbox + page so the rewriter can redact and overlay in
place. Scanned PDFs (no text layer) are out of scope for the MVP — the
extractor returns an empty document with an explanatory note in metadata.
"""
from __future__ import annotations

from typing import Any

from idp_anonymiser.document.layout_model import (
    ExtractedDocument,
    ExtractedTextBlock,
    PdfTextSpan,
)
from idp_anonymiser.document.loader import LoadedDocument


def extract_pdf(loaded: LoadedDocument) -> ExtractedDocument:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:  # pragma: no cover - import guard
        raise RuntimeError(
            "PyMuPDF (fitz) is required for PDF extraction. Install with `pip install PyMuPDF`."
        ) from e

    doc = fitz.open(str(loaded.path))
    parts: list[str] = []
    blocks: list[ExtractedTextBlock] = []
    spans: list[PdfTextSpan] = []
    cursor = 0
    text_layer_chars = 0

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        page_dict: dict[str, Any] = page.get_text("dict")  # type: ignore[arg-type]
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:  # 0 = text block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    text_layer_chars += len(text)
                    if not text.strip():
                        continue
                    bbox = tuple(span.get("bbox", (0.0, 0.0, 0.0, 0.0)))
                    spans.append(
                        PdfTextSpan(
                            page_index=page_index,
                            bbox=bbox,  # type: ignore[arg-type]
                            text=text,
                            font=span.get("font"),
                            size=span.get("size"),
                            color=span.get("color"),
                        )
                    )
                    start = cursor
                    parts.append(text)
                    cursor += len(text)
                    end = cursor
                    parts.append("\n")
                    cursor += 1
                    blocks.append(
                        ExtractedTextBlock(
                            text=text,
                            start=start,
                            end=end,
                            block_id=f"pdf:{page_index}:{len(spans) - 1}",
                            page=page_index,
                            metadata={
                                "page": page_index,
                                "bbox": bbox,
                                "span_index": len(spans) - 1,
                                "font": span.get("font"),
                                "size": span.get("size"),
                            },
                        )
                    )
    page_count = doc.page_count
    doc.close()

    return ExtractedDocument(
        flat_text="".join(parts),
        blocks=blocks,
        pdf_spans=spans,
        metadata={
            "format": "pdf",
            "page_count": page_count,
            "text_layer_chars": text_layer_chars,
            "scanned": text_layer_chars == 0,
        },
    )
