"""Layout-aware extraction containers.

These data classes are deliberately not Pydantic models — they hold non-trivial
runtime objects (e.g. ``fitz.Page`` references through ``block_id``) and are an
internal contract between extractor and rewriter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ExtractedTextBlock:
    """A logical block of text from any document format."""

    text: str
    start: int  # offset within the flattened document text
    end: int
    block_id: Optional[str] = None
    page: Optional[int] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PdfTextSpan:
    """A span of text on a PDF page with its bbox."""

    page_index: int
    bbox: tuple[float, float, float, float]
    text: str
    font: Optional[str] = None
    size: Optional[float] = None
    color: Optional[int] = None


@dataclass
class XlsxCell:
    """A cell in an XLSX workbook."""

    sheet_name: str
    row: int  # 1-indexed (openpyxl convention)
    column: int  # 1-indexed
    value: Any
    coordinate: str  # e.g. "B3"


@dataclass
class DocxParagraph:
    """A flattened DOCX paragraph reference."""

    section: str  # 'body' | 'table' | 'header' | 'footer'
    block_index: int  # index into containing collection
    text: str
    table_id: Optional[str] = None
    cell_row: Optional[int] = None
    cell_col: Optional[int] = None


@dataclass
class ExtractedDocument:
    """Result of an extractor.

    ``flat_text`` is the concatenated, normalised text of the document (with
    newlines between blocks), used for detection. The other fields preserve the
    structural information needed by the rewriter.
    """

    flat_text: str
    blocks: list[ExtractedTextBlock] = field(default_factory=list)
    pdf_spans: list[PdfTextSpan] = field(default_factory=list)
    xlsx_cells: list[XlsxCell] = field(default_factory=list)
    docx_paragraphs: list[DocxParagraph] = field(default_factory=list)
    json_data: Any = None  # parsed JSON tree for JSON inputs
    csv_dataframe: Any = None  # pandas.DataFrame for CSV inputs
    metadata: dict[str, Any] = field(default_factory=dict)

    def find_block_for_offset(self, offset: int) -> Optional[ExtractedTextBlock]:
        for b in self.blocks:
            if b.start <= offset < b.end:
                return b
        return None
