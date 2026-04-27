"""ReplacementGenerator: turn :class:`ResolvedEntity` lists into :class:`Replacement` plans.

Modes:

* **synthetic** — every entity gets a fake-but-plausible value via the
  per-entity-type generator. Default.
* **mask** — every entity becomes a token like ``[ORG_001]``. Useful when
  downstream consumers must not see anything that looks like real PII.
* **hybrid** — high-precision identifiers (LEI/IBAN/etc.) get synthetic
  values, free-form text (PERSON/ORG/ADDRESS) gets a mask token. Useful when
  you want to keep extraction patterns working but avoid leaking name shape.

Determinism:

* Same canonical original always maps to the same replacement, within the
  configured consistency scope.
* The mapping is cached in a :class:`MappingStore` keyed by an HMAC of the
  canonical original — the store never sees raw originals (unless the caller
  explicitly enables debug mode).
* Cross-entity coupling: when both PERSON and ORG are detected, the email
  generator stitches them together so e.g. "michael.brown@redwood-trading.example".
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass

from idp_anonymiser.agent.state import (
    AnonymisationMode,
    AnonymisationRequest,
    CanonicalEntity,
    Replacement,
    ResolvedEntity,
)
from idp_anonymiser.detection.entity_resolution import canonicalise
from idp_anonymiser.replacement import id_generator
from idp_anonymiser.replacement.address_generator import (
    generate_address,
    generate_postcode,
)
from idp_anonymiser.replacement.faker_provider import FakerProvider
from idp_anonymiser.replacement.mapping_store import (
    InMemoryMappingStore,
    MappingStore,
    hash_value,
)
from idp_anonymiser.replacement.org_generator import generate_org_name

logger = logging.getLogger(__name__)


# Default mask token formats per entity type.
_MASK_TOKENS: dict[str, str] = {
    "PERSON": "[PERSON_{i:03d}]",
    "ORG": "[ORG_{i:03d}]",
    "ADDRESS": "[ADDRESS_{i:03d}]",
    "EMAIL": "[EMAIL_{i:03d}]",
    "PHONE": "[PHONE_{i:03d}]",
    "DATE_OF_BIRTH": "[DOB_{i:03d}]",
    "GENERIC_DATE": "[DATE_{i:03d}]",
    "PASSPORT": "[PASSPORT_{i:03d}]",
    "NATIONAL_ID": "[NATIONAL_ID_{i:03d}]",
    "COMPANY_REG_NO": "[COMPANY_REG_NO_{i:03d}]",
    "TAX_ID": "[TAX_ID_{i:03d}]",
    "LEI": "[LEI_{i:03d}]",
    "BANK_ACCOUNT": "[BANK_ACCOUNT_{i:03d}]",
    "SORT_CODE": "[SORT_CODE_{i:03d}]",
    "IBAN": "[IBAN_{i:03d}]",
    "SWIFT_BIC": "[SWIFT_BIC_{i:03d}]",
    "URL": "[URL_{i:03d}]",
    "CLIENT_ID": "[CLIENT_ID_{i:03d}]",
    "CASE_ID": "[CASE_ID_{i:03d}]",
    "POSTCODE": "[POSTCODE_{i:03d}]",
}


# Entity types treated as "structured identifiers" in hybrid mode.
_STRUCTURED_IDS: set[str] = {
    "EMAIL", "PHONE", "PASSPORT", "NATIONAL_ID", "COMPANY_REG_NO",
    "TAX_ID", "LEI", "BANK_ACCOUNT", "SORT_CODE", "IBAN", "SWIFT_BIC",
    "URL", "CLIENT_ID", "CASE_ID", "POSTCODE", "DATE_OF_BIRTH",
    "GENERIC_DATE",
}


@dataclass
class GenerationContext:
    """Per-document context shared across replacement calls.

    Holds cross-entity links (the first PERSON / ORG seen) so the email and URL
    generators can produce coupled values. Also tracks per-entity links so that
    if multiple ORGs exist, an email tied to ORG_002 still gets ORG_002's
    replacement domain rather than the document-level primary.
    """

    primary_person_replacement: str | None = None
    primary_org_replacement: str | None = None
    person_replacement_by_id: dict[str, str] = None  # entity_id -> replacement
    org_replacement_by_id: dict[str, str] = None

    def __post_init__(self) -> None:
        if self.person_replacement_by_id is None:
            self.person_replacement_by_id = {}
        if self.org_replacement_by_id is None:
            self.org_replacement_by_id = {}


def _preserve_casing(original: str, replacement: str) -> str:
    """If ``original`` is all upper / lower, mirror that in ``replacement``."""
    if not original:
        return replacement
    if original.isupper() and any(c.isalpha() for c in original):
        return replacement.upper()
    if original.islower() and any(c.isalpha() for c in original):
        return replacement.lower()
    return replacement


class ReplacementGenerator:
    """Builds the replacement map for a list of resolved entities."""

    def __init__(
        self,
        *,
        mapping_store: MappingStore | None = None,
        faker: FakerProvider | None = None,
        consistency_scope: str = "document",
    ) -> None:
        self.store = mapping_store or InMemoryMappingStore()
        self.faker = faker or FakerProvider()
        self.scope = consistency_scope

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_from_canonical(
        self,
        canonical: list[CanonicalEntity],
        request: AnonymisationRequest,
    ) -> list[Replacement]:
        """Produce one :class:`Replacement` per :class:`CanonicalEntity`.

        Honours the cross-field links computed by the registry: when a
        canonical EMAIL is related to a canonical PERSON and ORG, the
        replacement email reuses the replacements for those entities so the
        domain matches the synthetic org name.
        """
        ctx = GenerationContext()

        # Pre-pass 1: pick primary PERSON and primary ORG (lowest-page anchor first)
        sorted_by_first_page = sorted(
            canonical,
            key=lambda e: (
                min((p for p in e.pages), default=0),
                min((m.span.start if m.span.start is not None else 1 << 31) for m in e.mentions) if e.mentions else 0,
            ),
        )

        # First, pre-resolve PERSON and ORG replacements so EMAIL/URL can refer back
        for ent in sorted_by_first_page:
            if ent.entity_type in {"PERSON", "ORG"}:
                value = self._synthetic_for_canonical(ent, request, ctx)
                if value is None:
                    continue
                value = _preserve_casing(ent.canonical_original, value)
                if ent.entity_type == "PERSON":
                    ctx.person_replacement_by_id[ent.entity_id] = value
                    if ctx.primary_person_replacement is None:
                        ctx.primary_person_replacement = value
                else:
                    ctx.org_replacement_by_id[ent.entity_id] = value
                    if ctx.primary_org_replacement is None:
                        ctx.primary_org_replacement = value

        # Pass 2: full pass with EMAIL/URL/etc. coupling
        type_counter: Counter = Counter()
        replacements: list[Replacement] = []
        for ent in canonical:
            type_counter[ent.entity_type] += 1
            mask_index = type_counter[ent.entity_type]
            replacement_value, strategy = self._dispatch_canonical(
                ent, request, ctx, mask_index
            )
            replacement_value = _preserve_casing(ent.canonical_original, replacement_value)
            ent.replacement = replacement_value
            ent.replacement_policy = strategy
            original_hash = hash_value(ent.normalised_key, scope=self.scope)
            self.store.put(ent.entity_type, original_hash, replacement_value)
            replacements.append(
                Replacement(
                    entity_id=ent.entity_id,
                    original_hash=original_hash,
                    original_value_for_runtime_only=(
                        ent.canonical_original if request.debug_include_originals else None
                    ),
                    replacement_value=replacement_value,
                    entity_type=ent.entity_type,
                    strategy=strategy,
                )
            )
        return replacements

    def _dispatch_canonical(
        self,
        ent: CanonicalEntity,
        request: AnonymisationRequest,
        ctx: GenerationContext,
        mask_index: int,
    ) -> tuple[str, str]:
        # Cache lookup first so repeated canonical entities reuse stored values
        cached = self.store.get(
            ent.entity_type, hash_value(ent.normalised_key, scope=self.scope)
        )
        if cached is not None:
            return cached, _mode_to_strategy(request.anonymisation_mode, ent.entity_type)

        mode = request.anonymisation_mode
        if mode == "mask":
            return self._mask_for_canonical(ent, mask_index), "mask"
        if mode == "hybrid" and ent.entity_type not in _STRUCTURED_IDS:
            return self._mask_for_canonical(ent, mask_index), "mask"
        synth = self._synthetic_for_canonical(ent, request, ctx)
        if synth is None:
            return self._mask_for_canonical(ent, mask_index), "mask"
        return synth, "synthetic"

    def _mask_for_canonical(self, ent: CanonicalEntity, idx: int) -> str:
        template = _MASK_TOKENS.get(ent.entity_type, "[REDACTED_{i:03d}]")
        return template.format(i=idx)

    def _synthetic_for_canonical(
        self,
        ent: CanonicalEntity,
        request: AnonymisationRequest,
        ctx: GenerationContext,
    ) -> str | None:
        et = ent.entity_type
        original = ent.canonical_original
        if et == "PERSON":
            parts = original.strip().split()
            if len(parts) >= 2:
                return f"{self.faker.fake_first_name(ent.normalised_key)} {self.faker.fake_last_name(ent.normalised_key)}"
            return self.faker.fake_last_name(ent.normalised_key)
        if et == "ORG":
            from idp_anonymiser.replacement.org_generator import generate_org_name

            return generate_org_name(original, seed_value=ent.normalised_key)
        if et == "ADDRESS":
            return generate_address(original)
        if et == "POSTCODE":
            return generate_postcode(original)
        if et == "EMAIL":
            # Prefer linked PERSON/ORG replacements; else fall back to primary.
            person_repl = None
            org_repl = None
            for rid in ent.related_entity_ids:
                if rid in ctx.person_replacement_by_id:
                    person_repl = ctx.person_replacement_by_id[rid]
                if rid in ctx.org_replacement_by_id:
                    org_repl = ctx.org_replacement_by_id[rid]
            return id_generator.generate_email(
                original,
                person_name=person_repl or ctx.primary_person_replacement,
                org_name=org_repl or ctx.primary_org_replacement,
            )
        if et == "URL":
            org_repl = None
            for rid in ent.related_entity_ids:
                if rid in ctx.org_replacement_by_id:
                    org_repl = ctx.org_replacement_by_id[rid]
            return id_generator.generate_url(
                original, org_name=org_repl or ctx.primary_org_replacement
            )
        if et == "PHONE":
            return id_generator.generate_phone(original)
        if et == "IBAN":
            return id_generator.generate_iban(original)
        if et == "SWIFT_BIC":
            return id_generator.generate_swift_bic(original)
        if et == "BANK_ACCOUNT":
            return id_generator.generate_bank_account(original)
        if et == "SORT_CODE":
            return id_generator.generate_sort_code(original)
        if et == "LEI":
            return id_generator.generate_lei(original)
        if et == "PASSPORT":
            return id_generator.generate_passport(original)
        if et == "COMPANY_REG_NO":
            return id_generator.generate_company_reg_no(original)
        if et == "TAX_ID":
            return id_generator.generate_tax_id(original)
        if et == "NATIONAL_ID":
            return id_generator.generate_national_id(original)
        if et == "DATE_OF_BIRTH":
            return id_generator.generate_date(original, dob=True)
        if et == "GENERIC_DATE":
            return id_generator.generate_date(original, dob=False)
        if et == "CLIENT_ID":
            return id_generator.generate_generic_id(original, prefix="CL")
        if et == "CASE_ID":
            return id_generator.generate_generic_id(original, prefix="CA")
        return None

    def generate(
        self,
        resolved: list[ResolvedEntity],
        request: AnonymisationRequest,
    ) -> list[Replacement]:
        """Produce one :class:`Replacement` per resolved entity."""
        ctx = GenerationContext()

        # Pre-pass: pick "primary" person and org so coupled email/URL look right.
        # We pick by first appearance ordered by lowest detection start offset.
        sorted_by_first_offset = sorted(
            resolved,
            key=lambda e: min(
                (d.span.start if d.span.start is not None else 1 << 31)
                for d in e.detections
            ),
        )
        for ent in sorted_by_first_offset:
            if ent.entity_type == "PERSON" and ctx.primary_person_replacement is None:
                ctx.primary_person_replacement = self._person(ent, request, ctx)
            elif ent.entity_type == "ORG" and ctx.primary_org_replacement is None:
                ctx.primary_org_replacement = self._org(ent, request, ctx)

        # Counter used to number mask tokens per type
        type_counter: Counter = Counter()

        replacements: list[Replacement] = []
        for ent in resolved:
            type_counter[ent.entity_type] += 1
            mask_index = type_counter[ent.entity_type]
            replacement_value, strategy = self._dispatch(ent, request, ctx, mask_index)
            replacement_value = _preserve_casing(ent.canonical_value, replacement_value)
            canonical = canonicalise(ent.canonical_value, ent.entity_type)
            original_hash = hash_value(canonical, scope=self.scope)
            self.store.put(ent.entity_type, original_hash, replacement_value)
            replacements.append(
                Replacement(
                    entity_id=ent.entity_id,
                    original_hash=original_hash,
                    original_value_for_runtime_only=(
                        ent.canonical_value if request.debug_include_originals else None
                    ),
                    replacement_value=replacement_value,
                    entity_type=ent.entity_type,
                    strategy=strategy,
                )
            )
        return replacements

    # ------------------------------------------------------------------
    # Strategy selection
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        ent: ResolvedEntity,
        request: AnonymisationRequest,
        ctx: GenerationContext,
        mask_index: int,
    ) -> tuple[str, str]:
        mode: AnonymisationMode = request.anonymisation_mode

        # First, consult the cache so identical entities always reuse the same value.
        canonical = canonicalise(ent.canonical_value, ent.entity_type)
        cached = self.store.get(
            ent.entity_type, hash_value(canonical, scope=self.scope)
        )
        if cached is not None:
            # Find the strategy by re-deriving (cheap), but keep cached value.
            return cached, _mode_to_strategy(mode, ent.entity_type)

        if mode == "mask":
            return self._mask(ent, mask_index), "mask"

        if mode == "hybrid" and ent.entity_type not in _STRUCTURED_IDS:
            # Free-form text masked
            return self._mask(ent, mask_index), "mask"

        synthetic = self._synthetic(ent, request, ctx)
        if synthetic is None:
            # Fallback to mask if no generator available
            return self._mask(ent, mask_index), "mask"
        return synthetic, "synthetic"

    # ------------------------------------------------------------------
    # Generators per entity type
    # ------------------------------------------------------------------

    def _mask(self, ent: ResolvedEntity, idx: int) -> str:
        template = _MASK_TOKENS.get(ent.entity_type, "[REDACTED_{i:03d}]")
        return template.format(i=idx)

    def _synthetic(
        self,
        ent: ResolvedEntity,
        request: AnonymisationRequest,
        ctx: GenerationContext,
    ) -> str | None:
        et = ent.entity_type
        original = ent.canonical_value
        if et == "PERSON":
            return self._person(ent, request, ctx)
        if et == "ORG":
            return self._org(ent, request, ctx)
        if et == "ADDRESS":
            return generate_address(original)
        if et == "POSTCODE":
            return generate_postcode(original)
        if et == "EMAIL":
            return id_generator.generate_email(
                original,
                person_name=ctx.primary_person_replacement,
                org_name=ctx.primary_org_replacement,
            )
        if et == "URL":
            return id_generator.generate_url(original, org_name=ctx.primary_org_replacement)
        if et == "PHONE":
            return id_generator.generate_phone(original)
        if et == "IBAN":
            return id_generator.generate_iban(original)
        if et == "SWIFT_BIC":
            return id_generator.generate_swift_bic(original)
        if et == "BANK_ACCOUNT":
            return id_generator.generate_bank_account(original)
        if et == "SORT_CODE":
            return id_generator.generate_sort_code(original)
        if et == "LEI":
            return id_generator.generate_lei(original)
        if et == "PASSPORT":
            return id_generator.generate_passport(original)
        if et == "COMPANY_REG_NO":
            return id_generator.generate_company_reg_no(original)
        if et == "TAX_ID":
            return id_generator.generate_tax_id(original)
        if et == "NATIONAL_ID":
            return id_generator.generate_national_id(original)
        if et == "DATE_OF_BIRTH":
            return id_generator.generate_date(original, dob=True)
        if et == "GENERIC_DATE":
            return id_generator.generate_date(original, dob=False)
        if et == "CLIENT_ID":
            return id_generator.generate_generic_id(original, prefix="CL")
        if et == "CASE_ID":
            return id_generator.generate_generic_id(original, prefix="CA")
        return None

    def _person(
        self,
        ent: ResolvedEntity,
        request: AnonymisationRequest,
        ctx: GenerationContext,
    ) -> str:
        # Preserve "FirstName LastName" structure when present.
        original = ent.canonical_value.strip()
        parts = original.split()
        if len(parts) >= 2:
            first = self.faker.fake_first_name(original)
            last = self.faker.fake_last_name(original)
            # Preserve a trailing initial / additional middle if original had one
            return f"{first} {last}"
        # Single token — treat as last name only
        return self.faker.fake_last_name(original)

    def _org(
        self,
        ent: ResolvedEntity,
        request: AnonymisationRequest,
        ctx: GenerationContext,
    ) -> str:
        return generate_org_name(ent.canonical_value)


def _mode_to_strategy(mode: AnonymisationMode, entity_type: str) -> str:
    if mode == "mask":
        return "mask"
    if mode == "hybrid" and entity_type not in _STRUCTURED_IDS:
        return "mask"
    return "synthetic"


__all__ = ["ReplacementGenerator"]
