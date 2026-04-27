"""Chunker for large pages of unstructured text.

For most inputs, one page is one chunk. For very long pages (or TXT inputs
treated as a single page), we split into overlapping char-windows so detectors
that don't tolerate huge inputs (e.g. spaCy's max_length) stay safe.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    page_index: int
    chunk_index: int       # within the page
    start_in_page: int     # absolute char offset within the page text
    end_in_page: int
    text: str


def chunk_page(page_index: int, text: str, *, max_chars: int = 50_000, overlap: int = 1_000) -> list[Chunk]:
    if not text:
        return []
    if len(text) <= max_chars:
        return [Chunk(page_index, 0, 0, len(text), text)]
    out: list[Chunk] = []
    start = 0
    idx = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        out.append(Chunk(page_index, idx, start, end, text[start:end]))
        if end >= len(text):
            break
        start = end - overlap
        idx += 1
    return out
