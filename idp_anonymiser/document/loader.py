"""Lightweight loader: reads file bytes and metadata without parsing.

The actual parsing happens in the format-specific extractors. Keeping load and
extract distinct lets us validate file readability up front and report a clean
error before doing any heavy lifting.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from idp_anonymiser.document.type_detector import DocumentType, detect


@dataclass
class LoadedDocument:
    """A handle to an on-disk document plus the detected type."""

    path: Path
    doc_type: DocumentType
    raw_bytes: Optional[bytes] = None  # populated for small text formats
    size_bytes: int = 0


# Files at or below this size are read fully into memory at load time. Above
# this, the extractor opens the file lazily.
_INLINE_BYTES_THRESHOLD = 5 * 1024 * 1024  # 5 MiB


def load(path: str | Path, hint: str | None = None) -> LoadedDocument:
    """Load a document from disk, returning a :class:`LoadedDocument`.

    Raises :class:`FileNotFoundError` if the file does not exist and
    :class:`PermissionError` if it is not readable.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if not p.is_file():
        raise IsADirectoryError(f"Input path is not a file: {path}")
    size = p.stat().st_size
    doc_type = detect(p, hint=hint)

    raw: Optional[bytes] = None
    # Only inline small text-ish formats; binary formats are opened by extractor
    if doc_type in {DocumentType.TXT, DocumentType.CSV, DocumentType.JSON} and size <= _INLINE_BYTES_THRESHOLD:
        raw = p.read_bytes()

    return LoadedDocument(path=p, doc_type=doc_type, raw_bytes=raw, size_bytes=size)
