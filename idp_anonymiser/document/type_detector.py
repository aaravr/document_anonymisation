"""Document type detection based on filename and a small magic-byte sniff.

Filename hints are authoritative when present (e.g. ``.pdf``). Magic bytes are
used as a tiebreaker for files without extensions or to validate suspicious
extensions.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path


class DocumentType(str, Enum):
    TXT = "txt"
    CSV = "csv"
    JSON = "json"
    XLSX = "xlsx"
    DOCX = "docx"
    PDF = "pdf"
    UNKNOWN = "unknown"


_EXT_MAP: dict[str, DocumentType] = {
    ".txt": DocumentType.TXT,
    ".log": DocumentType.TXT,
    ".md": DocumentType.TXT,
    ".csv": DocumentType.CSV,
    ".tsv": DocumentType.CSV,
    ".json": DocumentType.JSON,
    ".xlsx": DocumentType.XLSX,
    ".xlsm": DocumentType.XLSX,
    ".docx": DocumentType.DOCX,
    ".pdf": DocumentType.PDF,
}


def _sniff_magic(path: Path) -> DocumentType:
    """Read the first few bytes to identify common formats."""
    try:
        with path.open("rb") as fh:
            head = fh.read(8)
    except OSError:
        return DocumentType.UNKNOWN
    if head.startswith(b"%PDF"):
        return DocumentType.PDF
    # XLSX/DOCX are ZIP containers
    if head.startswith(b"PK\x03\x04"):
        # We can't disambiguate XLSX vs DOCX without opening the zip, so trust
        # the extension if any. Default to XLSX (caller should pass hint).
        return DocumentType.XLSX
    if head.lstrip().startswith(b"{") or head.lstrip().startswith(b"["):
        return DocumentType.JSON
    return DocumentType.TXT


def detect(path: str | Path, hint: str | None = None) -> DocumentType:
    """Detect a :class:`DocumentType` for a file path.

    A user-provided ``hint`` (e.g. ``"pdf"``) takes precedence, then the file
    extension, then a magic-byte sniff.
    """
    if hint:
        try:
            return DocumentType(hint.lower())
        except ValueError:
            pass

    p = Path(path)
    ext = p.suffix.lower()
    if ext in _EXT_MAP:
        return _EXT_MAP[ext]

    if not p.exists():
        return DocumentType.UNKNOWN
    return _sniff_magic(p)
