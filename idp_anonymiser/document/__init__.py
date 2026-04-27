"""Document loading, type detection, and per-format extraction."""
from __future__ import annotations

from idp_anonymiser.document.layout_model import (
    ExtractedDocument,
    ExtractedTextBlock,
    PdfTextSpan,
    XlsxCell,
)
from idp_anonymiser.document.loader import LoadedDocument, load
from idp_anonymiser.document.type_detector import DocumentType, detect

__all__ = [
    "DocumentType",
    "ExtractedDocument",
    "ExtractedTextBlock",
    "LoadedDocument",
    "PdfTextSpan",
    "XlsxCell",
    "detect",
    "load",
]
