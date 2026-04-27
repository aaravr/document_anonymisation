"""YAML profile and seed-list loaders."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
import yaml


@dataclass
class VisualConfig:
    flag_images: bool = True
    flag_signatures: bool = True
    flag_logos: bool = True
    flag_stamps: bool = True
    flag_qr_codes: bool = True
    flag_barcodes: bool = True
    redact_images: bool = True


@dataclass
class QAConfig:
    flag_remaining_capitalised_names: bool = True
    flag_remaining_org_suffixes: bool = True
    flag_remaining_abbreviations: bool = True
    fail_on_remaining_regex_pii: bool = True
    capitalised_min_tokens: int = 2


@dataclass
class Profile:
    profile: str = "strict_test_data"
    enable_spacy: bool = True
    fail_if_spacy_unavailable: bool = True
    spacy_model: str = "en_core_web_sm"
    replace_real_organisations: bool = True
    replace_public_banks: bool = True
    replace_dates: bool = False
    replace_locations: bool = True
    synthetic_replacements: bool = True
    preserve_semantic_type: bool = True
    global_consistency: bool = True
    chunk_chars: int = 50_000
    chunk_overlap_chars: int = 1_000
    seed: int = 42
    visual_elements: VisualConfig = field(default_factory=VisualConfig)
    qa: QAConfig = field(default_factory=QAConfig)
    seed_list_path: Optional[str] = None


def load_profile(path_or_name: str | Path) -> Profile:
    """Load a YAML profile by absolute path or by bundled name (e.g. 'strict_test_data')."""
    p = Path(path_or_name)
    if not p.exists():
        # try bundled
        bundled = Path(__file__).parent / "config" / "profiles" / f"{path_or_name}.yaml"
        if bundled.exists():
            p = bundled
    if not p.exists():
        raise FileNotFoundError(f"Profile not found: {path_or_name}")
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    visual_raw = raw.pop("visual_elements", {}) or {}
    qa_raw = raw.pop("qa", {}) or {}
    return Profile(
        **{**raw, "visual_elements": VisualConfig(**visual_raw), "qa": QAConfig(**qa_raw)}
    )


@dataclass
class SeedList:
    """Operator-supplied lists of known sensitive entities to always replace."""
    persons: list[str] = field(default_factory=list)
    orgs: list[dict[str, Any]] = field(default_factory=list)  # each {name: ..., abbreviation?: ...}
    locations: list[str] = field(default_factory=list)


def load_seed_list(path: str | Path | None) -> SeedList:
    if not path:
        return SeedList()
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    persons = list(raw.get("PERSON", []) or [])
    orgs_in = raw.get("ORG", []) or []
    orgs: list[dict[str, Any]] = []
    for entry in orgs_in:
        if isinstance(entry, str):
            orgs.append({"name": entry, "abbreviation": None})
        elif isinstance(entry, dict):
            for k, v in entry.items():
                abbrev = (v or {}).get("abbreviation") if isinstance(v, dict) else None
                orgs.append({"name": k, "abbreviation": abbrev})
    locations = list(raw.get("LOCATION", []) or raw.get("GPE", []) or [])
    return SeedList(persons=persons, orgs=orgs, locations=locations)
