"""Header/table-aware detector.

Operates on the structural side of an :class:`ExtractedDocument`:

* CSV/XLSX: maps sensitive header names to column-wise entity types and
  emits a detection for every value in those columns.
* DOCX tables: when a row's first cell looks like a known label, the
  detections for the rest of the row are emitted with the matching entity
  type.

The point is to avoid relying solely on regex/NER for tabular sensitive data
(account numbers, IDs) where the value alone is ambiguous but the column
header is decisive.
"""
from __future__ import annotations

import re

from idp_anonymiser.agent.state import Detection, DocumentSpan
from idp_anonymiser.detection.label_value_detector import (
    DEFAULT_LABEL_RULES,
    LabelRule,
    _alias_matches,
)
from idp_anonymiser.document.layout_model import ExtractedDocument


def _normalise_header(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _resolve_header_rule(header: str, rules: tuple[LabelRule, ...]) -> LabelRule | None:
    for rule in rules:
        for alias in rule.label_aliases:
            if _alias_matches(header, alias):
                return rule
    return None


def detect_table_entities(
    extracted: ExtractedDocument,
    rules: tuple[LabelRule, ...] = DEFAULT_LABEL_RULES,
) -> list[Detection]:
    """Detect sensitive cells in CSV/XLSX/DOCX-table extractions."""
    out: list[Detection] = []
    fmt = extracted.metadata.get("format")

    if fmt == "csv" and extracted.csv_dataframe is not None:
        out.extend(_detect_csv(extracted, rules))
    elif fmt == "xlsx":
        out.extend(_detect_xlsx(extracted, rules))
    elif fmt == "docx":
        out.extend(_detect_docx_tables(extracted, rules))
    return out


def _detect_csv(extracted: ExtractedDocument, rules: tuple[LabelRule, ...]) -> list[Detection]:
    df = extracted.csv_dataframe
    sensitive_columns: dict[str, LabelRule] = {}
    for col in df.columns:
        rule = _resolve_header_rule(str(col), rules)
        if rule:
            sensitive_columns[str(col)] = rule
    if not sensitive_columns:
        return []
    out: list[Detection] = []
    # Scan blocks tagged with a column we care about
    for block in extracted.blocks:
        col = block.metadata.get("column")
        if col is None or col not in sensitive_columns:
            continue
        rule = sensitive_columns[col]
        out.append(
            Detection(
                text=block.text,
                entity_type=rule.entity_type,
                confidence=0.9,
                detector="table.csv_header",
                span=DocumentSpan(
                    text=block.text, start=block.start, end=block.end
                ),
                metadata={"column": col, "header_rule": list(rule.label_aliases)[0]},
            )
        )
    return out


def _detect_xlsx(extracted: ExtractedDocument, rules: tuple[LabelRule, ...]) -> list[Detection]:
    """For XLSX we treat row 1 as the header row per sheet."""
    if not extracted.xlsx_cells:
        return []
    headers_by_sheet: dict[str, dict[int, LabelRule]] = {}
    for cell in extracted.xlsx_cells:
        if cell.row != 1:
            continue
        if cell.value is None:
            continue
        rule = _resolve_header_rule(str(cell.value), rules)
        if rule is not None:
            headers_by_sheet.setdefault(cell.sheet_name, {})[cell.column] = rule
    if not headers_by_sheet:
        return []
    out: list[Detection] = []
    for block in extracted.blocks:
        if block.metadata.get("sheet_name") is None:
            continue
        sheet = block.metadata["sheet_name"]
        column = block.metadata.get("column")
        row = block.metadata.get("row")
        if row == 1:
            continue  # skip header row itself
        rule = headers_by_sheet.get(sheet, {}).get(column)
        if rule is None:
            continue
        out.append(
            Detection(
                text=block.text,
                entity_type=rule.entity_type,
                confidence=0.9,
                detector="table.xlsx_header",
                span=DocumentSpan(
                    text=block.text, start=block.start, end=block.end
                ),
                metadata={
                    "sheet_name": sheet,
                    "column": column,
                    "row": row,
                    "header_rule": list(rule.label_aliases)[0],
                },
            )
        )
    return out


def _detect_docx_tables(extracted: ExtractedDocument, rules: tuple[LabelRule, ...]) -> list[Detection]:
    """In DOCX tables, treat column 0 of each row as the label and column 1+ as values."""
    if not extracted.docx_paragraphs:
        return []
    # Map (table_id, row, col) -> first paragraph text for that cell
    cells: dict[tuple[str, int, int], list] = {}
    for p in extracted.docx_paragraphs:
        if p.section != "table" or p.table_id is None:
            continue
        cells.setdefault((p.table_id, p.cell_row or 0, p.cell_col or 0), []).append(p)
    out: list[Detection] = []
    # For each row, look at column 0; if it matches a known label, emit detections for the rest
    rows_seen: set[tuple[str, int]] = set()
    for (tid, row, col) in cells.keys():
        rows_seen.add((tid, row))
    for tid, row in rows_seen:
        label_paras = cells.get((tid, row, 0), [])
        if not label_paras:
            continue
        label_text = " ".join(p.text for p in label_paras).strip().rstrip(":")
        rule = _resolve_header_rule(label_text, rules)
        if rule is None:
            continue
        # Emit detection per non-empty value paragraph in the row's other columns.
        # We rely on extracted blocks to obtain the absolute char offsets.
        for block in extracted.blocks:
            md = block.metadata
            if md.get("section") != "table":
                continue
            if md.get("table_index") is None:
                continue
            if f"t{md['table_index']}" != tid:
                continue
            if md.get("row") != row:
                continue
            if md.get("column") == 0:
                continue
            if not block.text.strip():
                continue
            out.append(
                Detection(
                    text=block.text,
                    entity_type=rule.entity_type,
                    confidence=0.9,
                    detector="table.docx_label_row",
                    span=DocumentSpan(
                        text=block.text, start=block.start, end=block.end
                    ),
                    metadata={
                        "table_id": tid,
                        "row": row,
                        "label": label_text,
                    },
                )
            )
    return out


__all__ = ["detect_table_entities"]
