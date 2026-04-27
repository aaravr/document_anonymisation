"""Pydantic data models for the anonymisation pipeline.

These models form the wire-level contract between the loader, detector, replacement
generator, rewriter, validator, and audit reporter. Keep them stable; downstream
consumers (audit reports, CLI JSON, plan files) depend on the field names.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations / type aliases (kept as Literals for Pydantic friendliness)
# ---------------------------------------------------------------------------

AnonymisationMode = Literal["mask", "synthetic", "hybrid"]
ConsistencyScope = Literal["document", "batch", "project"]
RiskLevel = Literal["low", "medium", "high"]

# Canonical entity types. Keep this list synchronised with detection/replacement.
ENTITY_TYPES: tuple[str, ...] = (
    "PERSON",
    "ORG",
    "ADDRESS",
    "EMAIL",
    "PHONE",
    "DATE_OF_BIRTH",
    "GENERIC_DATE",
    "PASSPORT",
    "NATIONAL_ID",
    "COMPANY_REG_NO",
    "TAX_ID",
    "LEI",
    "BANK_ACCOUNT",
    "IBAN",
    "SWIFT_BIC",
    "URL",
    "CLIENT_ID",
    "CASE_ID",
    "POSTCODE",
    "SORT_CODE",
)


# ---------------------------------------------------------------------------
# Request / span / detection
# ---------------------------------------------------------------------------


class AnonymisationRequest(BaseModel):
    """Inputs to a single anonymisation run.

    ``input_path`` is the file to anonymise (never modified in place).
    ``output_dir`` is where outputs (anonymised file, plan, audit) are written.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str
    input_path: str
    output_dir: str
    document_type_hint: Optional[str] = None
    output_format: Optional[str] = None
    anonymisation_mode: AnonymisationMode = "synthetic"
    consistency_scope: ConsistencyScope = "document"
    risk_level: RiskLevel = "high"
    preserve_layout: bool = True
    config_profile: str = "kyc_default"
    debug_include_originals: bool = Field(
        default=False,
        description=(
            "If True, the audit report and plan include raw original PII values. "
            "Only use for synthetic test corpora."
        ),
    )
    seed: Optional[int] = Field(
        default=None,
        description="Optional override of the deterministic seed (defaults to a hash of document_id).",
    )
    replace_contextual_aliases: bool = Field(
        default=True,
        description=(
            "When True, generic references such as 'the Client', 'the Company', or "
            "surname-only references that unambiguously point to a single canonical "
            "entity are replaced with the same synthetic value as the canonical entity. "
            "When False, only direct surface mentions are replaced."
        ),
    )
    fuzzy_alias_threshold: int = Field(
        default=88,
        ge=50,
        le=100,
        description=(
            "rapidfuzz token_set_ratio threshold above which two PERSON/ORG/ADDRESS "
            "mentions are considered the same canonical entity."
        ),
    )


class DocumentSpan(BaseModel):
    """Position of a piece of text inside a document.

    Different fields are populated depending on the format. For TXT/JSON/CSV the
    char offsets ``start``/``end`` are authoritative. For PDF, ``page`` and ``bbox``
    locate the span on the page. For XLSX, ``sheet_name``/``row``/``column`` apply.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    start: Optional[int] = None
    end: Optional[int] = None
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    block_id: Optional[str] = None
    row: Optional[int] = None
    column: Optional[int] = None
    sheet_name: Optional[str] = None


class Detection(BaseModel):
    """A single PII / client-data detection on a span of text."""

    model_config = ConfigDict(extra="forbid")

    text: str
    entity_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    detector: str
    span: DocumentSpan
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResolvedEntity(BaseModel):
    """A canonical entity merged from one or more :class:`Detection` records."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    canonical_value: str
    entity_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    detections: list[Detection]


class Replacement(BaseModel):
    """A planned substitution for a single resolved entity."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    original_hash: str
    original_value_for_runtime_only: Optional[str] = None
    replacement_value: str
    entity_type: str
    strategy: str  # 'mask' | 'synthetic' | 'hybrid' | 'token'


class Mention(BaseModel):
    """A single mention of an entity within a document.

    Distinct from :class:`Detection` in that a Mention is owned by exactly one
    canonical entity in the registry, may carry the role label that linked it
    (e.g. ``"Client Name"``), and is the unit at which the rewriter operates.
    """

    model_config = ConfigDict(extra="forbid")

    text: str
    normalised: str
    entity_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    detector: str
    page: Optional[int] = None
    chunk_id: Optional[str] = None
    span: DocumentSpan
    role_label: Optional[str] = None  # the label that introduced the value, if any
    is_alias: bool = False  # True if linked via alias logic (e.g. surname-only, "the Client")
    metadata: dict[str, Any] = Field(default_factory=dict)


class CanonicalEntity(BaseModel):
    """A real-world entity, possibly mentioned many times under different surface forms."""

    model_config = ConfigDict(extra="forbid")

    entity_id: str
    entity_type: str
    canonical_original: str  # the most representative original surface form
    normalised_key: str  # the canonical normalised value used for matching
    replacement: Optional[str] = None  # populated after replacement generation
    replacement_policy: str = "synthetic"  # 'synthetic' | 'mask' | 'token'
    mentions: list[Mention] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)  # distinct surface forms
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    role_labels: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)
    pages: list[int] = Field(default_factory=list)
    ambiguous_alias_candidates: list[str] = Field(default_factory=list)


class AnonymisationPlan(BaseModel):
    """The full anonymisation plan for a document."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    replacements: list[Replacement] = Field(default_factory=list)
    detections: list[Detection] = Field(default_factory=list)
    unresolved: list[Detection] = Field(default_factory=list)
    canonical_entities: list[CanonicalEntity] = Field(default_factory=list)
    ambiguous_mentions: list[Mention] = Field(default_factory=list)

    def replacement_for_entity(self, entity_id: str) -> Optional[Replacement]:
        for r in self.replacements:
            if r.entity_id == entity_id:
                return r
        return None


class ValidationReport(BaseModel):
    """Output of the validation stage."""

    model_config = ConfigDict(extra="forbid")

    quality_score: float = Field(ge=0.0, le=1.0)
    residual_high_confidence_pii_count: int = 0
    original_values_remaining_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    passed: bool = True
    checks: dict[str, Any] = Field(default_factory=dict)


class AnonymisationResult(BaseModel):
    """Top-level result returned by :class:`AnonymisationAgent`."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    status: str  # 'ok' | 'warning' | 'error'
    output_document_path: str
    output_text_path: Optional[str] = None
    plan_path: str
    audit_report_path: str
    pii_count: int = 0
    replacement_count: int = 0
    unresolved_count: int = 0
    quality_score: float = 0.0
