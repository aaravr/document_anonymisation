"""Replacement generation: deterministic synthetic surrogates for resolved entities."""
from __future__ import annotations

from idp_anonymiser.replacement.generator import ReplacementGenerator
from idp_anonymiser.replacement.mapping_store import MappingStore, InMemoryMappingStore

__all__ = ["ReplacementGenerator", "MappingStore", "InMemoryMappingStore"]
