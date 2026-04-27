"""Deterministic synthetic organisation name generator.

Preserves legal suffix (Ltd, Limited, LLP, PLC, GmbH, ...) where present so
downstream IDP rules that key off the suffix continue to match. Uses a seeded
RNG over a small curated pool of fake but realistic British corporate names.
"""
from __future__ import annotations

import hashlib
import random
import re

# Curated pool of fake organisation stems. These are intentionally generic and
# British-flavoured; extend per region as required.
_FAKE_ORG_STEMS = [
    "Redwood Trading",
    "Northgate Holdings",
    "Oakfield Services",
    "Silverline Capital",
    "Brookstone Consulting",
    "Westbridge Partners",
    "Highmark Solutions",
    "Greenfield Industries",
    "Larkspur Investments",
    "Cotswold Logistics",
    "Pinehurst Advisory",
    "Eastlake Ventures",
    "Marlowe Financial",
    "Ashford Strategies",
    "Branscombe Resources",
    "Kingsbury Asset Management",
    "Foxglove Partners",
    "Stonecroft Consulting",
    "Templeton Holdings",
    "Wycombe Trading",
]

# Recognised suffix patterns. Order matters — match longer first.
_SUFFIX_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\s+limited\.?$", re.IGNORECASE), "Limited"),
    (re.compile(r"\s+ltd\.?$", re.IGNORECASE), "Ltd"),
    (re.compile(r"\s+plc\.?$", re.IGNORECASE), "PLC"),
    (re.compile(r"\s+llp\.?$", re.IGNORECASE), "LLP"),
    (re.compile(r"\s+l\.l\.p\.?$", re.IGNORECASE), "LLP"),
    (re.compile(r"\s+l\.p\.?$", re.IGNORECASE), "LP"),
    (re.compile(r"\s+llc\.?$", re.IGNORECASE), "LLC"),
    (re.compile(r"\s+inc\.?$", re.IGNORECASE), "Inc"),
    (re.compile(r"\s+incorporated\.?$", re.IGNORECASE), "Incorporated"),
    (re.compile(r"\s+corp\.?$", re.IGNORECASE), "Corp"),
    (re.compile(r"\s+corporation\.?$", re.IGNORECASE), "Corporation"),
    (re.compile(r"\s+gmbh\.?$", re.IGNORECASE), "GmbH"),
    (re.compile(r"\s+ag\.?$", re.IGNORECASE), "AG"),
    (re.compile(r"\s+s\.?a\.?$", re.IGNORECASE), "SA"),
    (re.compile(r"\s+n\.?v\.?$", re.IGNORECASE), "NV"),
    (re.compile(r"\s+b\.?v\.?$", re.IGNORECASE), "BV"),
    (re.compile(r"\s+co\.?$", re.IGNORECASE), "Co."),
]


def split_suffix(org_name: str) -> tuple[str, str | None]:
    """Return ``(stem, suffix)`` separating the legal suffix when present."""
    for pat, canonical in _SUFFIX_PATTERNS:
        m = pat.search(org_name.strip())
        if m:
            stem = org_name[: m.start()].strip()
            return stem, canonical
    return org_name.strip(), None


def generate_org_name(original: str, *, seed_value: str | None = None) -> str:
    """Return a deterministic synthetic organisation name preserving suffix.

    ``seed_value`` overrides the seed input (typically the canonical original).
    """
    seed_input = seed_value if seed_value is not None else original
    h = hashlib.sha256(seed_input.encode("utf-8")).digest()
    rng = random.Random(int.from_bytes(h[:8], "big", signed=False))
    stem, suffix = split_suffix(original)
    fake_stem = rng.choice(_FAKE_ORG_STEMS)
    if suffix:
        return f"{fake_stem} {suffix}"
    # No suffix: just use the stem
    return fake_stem


__all__ = ["generate_org_name", "split_suffix"]
