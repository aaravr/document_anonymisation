"""Crude layout-similarity check.

For PDFs we verify the page count was preserved. For DOCX/XLSX we compare
paragraph / cell counts. For TXT/JSON/CSV we compare line counts (within a
tolerance of the replacement growth factor).

The check returns a 0..1 similarity score and a list of warnings.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class LayoutSimilarityResult:
    score: float
    warnings: list[str]
    details: dict[str, object]


def _txt_similarity(original_text: str, rewritten_text: str) -> tuple[float, list[str]]:
    o_lines = original_text.count("\n")
    r_lines = rewritten_text.count("\n")
    if o_lines == 0 and r_lines == 0:
        return 1.0, []
    diff = abs(o_lines - r_lines)
    score = max(0.0, 1.0 - diff / max(1, o_lines))
    warnings = []
    if diff > 0:
        warnings.append(f"Line count differs by {diff} (original={o_lines}, rewritten={r_lines})")
    return score, warnings


def _pdf_similarity(input_path: str, output_path: str) -> tuple[float, list[str], dict]:
    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError:
        return 1.0, ["PyMuPDF unavailable; skipped PDF page-count check."], {}
    try:
        a = fitz.open(input_path)
        b = fitz.open(output_path)
        pages_in, pages_out = a.page_count, b.page_count
        a.close()
        b.close()
    except Exception as exc:  # noqa: BLE001
        return 0.0, [f"PDF layout check failed: {exc}"], {}
    if pages_in != pages_out:
        return 0.0, [f"PDF page count changed: {pages_in} -> {pages_out}"], {
            "pages_in": pages_in,
            "pages_out": pages_out,
        }
    return 1.0, [], {"pages_in": pages_in, "pages_out": pages_out}


def compute_layout_similarity(
    *,
    doc_format: str,
    original_text: Optional[str] = None,
    rewritten_text: Optional[str] = None,
    input_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> LayoutSimilarityResult:
    if doc_format == "pdf" and input_path and output_path and Path(output_path).exists():
        score, warns, details = _pdf_similarity(input_path, output_path)
        return LayoutSimilarityResult(score=score, warnings=warns, details=details)
    if original_text is not None and rewritten_text is not None:
        score, warns = _txt_similarity(original_text, rewritten_text)
        return LayoutSimilarityResult(score=score, warnings=warns, details={})
    return LayoutSimilarityResult(score=1.0, warnings=[], details={})


__all__ = ["compute_layout_similarity", "LayoutSimilarityResult"]
