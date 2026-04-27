"""Pydantic data models for the sanitiser pipeline."""
from __future__ import annotations

from typing import Any, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


SeverityLevel = Literal["low", "medium", "high"]


class Span(BaseModel):
    """Char-offset (and optional bbox/page) location of a piece of text."""
    model_config = ConfigDict(extra="forbid")
    text: str
    start: int
    end: int
    page: Optional[int] = None
    chunk: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None


class Detection(BaseModel):
    """A single detected sensitive entity."""
    model_config = ConfigDict(extra="forbid")
    text: str
    entity_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    detector: str
    span: Span
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalEntity(BaseModel):
    """A real-world entity captured in the registry."""
    model_config = ConfigDict(extra="forbid")
    canonical_id: str
    entity_type: str
    full_name: str
    abbreviation: Optional[str] = None  # only for ORG
    variants: list[str] = Field(default_factory=list)  # all observed surface forms
    replacement_full_name: str
    replacement_abbreviation: Optional[str] = None
    replacement_variants: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    detectors: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)


class ReplacementRecord(BaseModel):
    """Audit row for a single replacement applied at a position in a document."""
    model_config = ConfigDict(extra="forbid")
    document_id: str
    page: Optional[int] = None
    chunk: Optional[int] = None
    entity_type: str
    original: str
    replacement: str
    canonical_id: str
    detectors: list[str]
    confidence: float
    start: int
    end: int
    reason: str


class QAFlag(BaseModel):
    """Single flag on the QA report."""
    model_config = ConfigDict(extra="forbid")
    page: Optional[int] = None
    severity: SeverityLevel
    type: str
    text: str
    reason: str
    confidence: float = 0.5


class VisualElement(BaseModel):
    """An image / signature / logo / barcode flagged on a PDF page."""
    model_config = ConfigDict(extra="forbid")
    page: int
    type: str  # "image" | "signature" | "logo" | "stamp" | "qr_code" | "barcode"
    bbox: tuple[float, float, float, float]
    reason: str
    redacted: bool = False


class RunSummary(BaseModel):
    """High-level summary written for each processed document."""
    model_config = ConfigDict(extra="forbid")
    document_id: str
    input_path: str
    output_path: str
    pages: int
    total_detections: int
    unique_canonical_entities: int
    total_replacements: int
    visual_elements_flagged: int
    visual_elements_redacted: int
    qa_flag_count: int
    status: str  # "ok" | "needs_review" | "error"
    elapsed_seconds: float
